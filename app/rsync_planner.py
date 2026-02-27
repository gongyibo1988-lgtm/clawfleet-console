from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from app.ssh_runner import SSHRunner

ChangeType = Literal["add", "update", "delete"]


@dataclass
class FileRecord:
    path: str
    size: int
    mtime: float
    sha256: str


def _manifest_command(root: str, excludes: list[str]) -> str:
    return rf'''python3 - <<'PY'
import hashlib
import json
import os
import fnmatch
from pathlib import Path

root = Path({json.dumps(root)})
patterns = {json.dumps(excludes)}
if not root.exists():
    print("[]")
    raise SystemExit(0)

def should_skip(rel: str) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch("/" + rel, pattern):
            return True
    return False

records = []
for path in root.rglob("*"):
    if not path.is_file():
        continue
    rel = str(path.relative_to(root))
    if should_skip(rel):
        continue
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    st = path.stat()
    records.append({{"path": rel, "size": st.st_size, "mtime": st.st_mtime, "sha256": h.hexdigest()}})
print(json.dumps(records))
PY'''


def parse_itemized_changes(stdout: str) -> list[dict]:
    changes: list[dict] = []
    for line in stdout.splitlines():
        line = line.rstrip()
        if not line or line.startswith("sending incremental file list"):
            continue
        if line.startswith("deleting "):
            changes.append({"type": "delete", "path": line.removeprefix("deleting ").strip()})
            continue
        if len(line) < 12:
            continue
        tag = line[:11]
        path = line[12:].strip()
        if not path:
            continue
        if tag.startswith(">f+"):
            change_type = "add"
        elif tag.startswith(">f"):
            change_type = "update"
        else:
            continue
        changes.append({"type": change_type, "path": path})
    return changes


def _records_to_map(records: list[dict]) -> dict[str, FileRecord]:
    return {
        row["path"]: FileRecord(
            path=row["path"],
            size=int(row["size"]),
            mtime=float(row["mtime"]),
            sha256=row["sha256"],
        )
        for row in records
    }


def _compare_manifests(source: dict[str, FileRecord], target: dict[str, FileRecord], allow_delete: bool) -> list[dict]:
    changes: list[dict] = []
    for path, src in source.items():
        dst = target.get(path)
        if dst is None:
            changes.append({"type": "add", "path": path})
        elif src.sha256 != dst.sha256:
            changes.append({"type": "update", "path": path})
    if allow_delete:
        for path in target:
            if path not in source:
                changes.append({"type": "delete", "path": path})
    return sorted(changes, key=lambda item: item["path"])


def _find_conflicts(a_to_b: dict[str, list[dict]], b_to_a: dict[str, list[dict]]) -> list[dict]:
    out: list[dict] = []
    for root in a_to_b:
        left = {c["path"] for c in a_to_b.get(root, []) if c["type"] in {"add", "update"}}
        right = {c["path"] for c in b_to_a.get(root, []) if c["type"] in {"add", "update"}}
        both = sorted(left & right)
        for item in both:
            out.append({"root": root, "path": item, "choices": ["keep_a", "keep_b", "keep_both"]})
    return out


def _collect_manifest(runner: SSHRunner, host: str, root: str, excludes: list[str]) -> dict[str, FileRecord]:
    cmd = _manifest_command(root, excludes)
    result = runner.run_ssh(host, cmd, timeout=240)
    if result.returncode != 0:
        raise RuntimeError(f"Manifest failed for {host}:{root}: {result.stderr.strip()}")
    raw = json.loads(result.stdout.strip() or "[]")
    return _records_to_map(raw)


def build_plan(
    runner: SSHRunner,
    mode: str,
    source_host: str,
    target_host: str,
    roots: list[str],
    excludes: list[str],
    allow_delete: bool,
) -> dict:
    by_root: dict[str, dict] = {}

    if mode in {"a_to_b", "b_to_a"}:
        for root in roots:
            source_manifest = _collect_manifest(runner, source_host, root, excludes)
            target_manifest = _collect_manifest(runner, target_host, root, excludes)
            changes = _compare_manifests(source_manifest, target_manifest, allow_delete)
            by_root[root] = {
                "changes": changes,
                "summary": {
                    "add": sum(1 for change in changes if change["type"] == "add"),
                    "update": sum(1 for change in changes if change["type"] == "update"),
                    "delete": sum(1 for change in changes if change["type"] == "delete"),
                },
            }

        return {
            "mode": mode,
            "source_host": source_host,
            "target_host": target_host,
            "roots": roots,
            "by_root": by_root,
            "conflicts": [],
        }

    if mode != "bidirectional":
        raise ValueError(f"Unsupported mode: {mode}")

    a_to_b: dict[str, list[dict]] = {}
    b_to_a: dict[str, list[dict]] = {}
    details: dict[str, dict] = {}

    for root in roots:
        a_manifest = _collect_manifest(runner, source_host, root, excludes)
        b_manifest = _collect_manifest(runner, target_host, root, excludes)
        left = _compare_manifests(a_manifest, b_manifest, allow_delete)
        right = _compare_manifests(b_manifest, a_manifest, allow_delete)
        a_to_b[root] = left
        b_to_a[root] = right
        details[root] = {
            "a_to_b": left,
            "b_to_a": right,
            "a_to_b_summary": {
                "add": sum(1 for change in left if change["type"] == "add"),
                "update": sum(1 for change in left if change["type"] == "update"),
                "delete": sum(1 for change in left if change["type"] == "delete"),
            },
            "b_to_a_summary": {
                "add": sum(1 for change in right if change["type"] == "add"),
                "update": sum(1 for change in right if change["type"] == "update"),
                "delete": sum(1 for change in right if change["type"] == "delete"),
            },
        }

    conflicts = _find_conflicts(a_to_b, b_to_a)
    return {
        "mode": mode,
        "source_host": source_host,
        "target_host": target_host,
        "roots": roots,
        "by_root": details,
        "conflicts": conflicts,
    }


def root_key(root: str) -> str:
    path = PurePosixPath(root)
    text = "_".join(part for part in path.parts if part != "/")
    return text or "root"
