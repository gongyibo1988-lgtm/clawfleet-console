from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from app.rsync_planner import root_key
from app.ssh_runner import SSHRunner


def _build_ssh_command(runner: SSHRunner) -> str:
    opts = runner.ssh_options()
    pieces: list[str] = ["ssh"]
    pieces.extend(opts)
    return " ".join(pieces)


def _write_exclude_file(base: Path, excludes: list[str], extra_paths: list[str]) -> Path:
    target = base / "exclude.lst"
    lines = [*excludes]
    for path in extra_paths:
        lines.append(path)
    target.write_text("\n".join(lines) + "\n")
    return target


def _run_rsync(runner: SSHRunner, args: list[str], timeout: int = 3600) -> dict:
    result = runner.run_local(args, timeout=timeout)
    parsed = []
    if result.stdout:
        from app.rsync_planner import parse_itemized_changes

        parsed = parse_itemized_changes(result.stdout)
    return {
        "command": args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "changes": parsed,
    }


def _backup_or_rename_conflict(
    runner: SSHRunner,
    target_host: str,
    target_root: str,
    relative_path: str,
    decision: str,
    source_label: str,
) -> None:
    now = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    src = f"{target_root.rstrip('/')}/{relative_path}"
    backup_dir = f"{target_root.rstrip('/')}/.openclaw_console_conflicts"
    backup_target = f"{backup_dir}/{relative_path}.{now}.bak"
    backup_both = f"{backup_dir}/{relative_path}.{source_label}.conflict.{now}"
    if decision not in {"keep_b", "keep_both"}:
        return
    mode = "copy" if decision == "keep_b" else "move"
    cmd = f"""python3 - <<'PY'
import os
import shutil
from pathlib import Path

src = Path({json.dumps(src)})
dst = Path({json.dumps(backup_target if mode == "copy" else backup_both)})
dst.parent.mkdir(parents=True, exist_ok=True)
if src.exists():
    if {json.dumps(mode)} == "copy":
        shutil.copy2(src, dst)
    else:
        shutil.move(str(src), str(dst))
PY"""
    runner.run_ssh(target_host, cmd, timeout=60)


def execute_single_direction(
    runner: SSHRunner,
    source_host: str,
    target_host: str,
    root: str,
    excludes: list[str],
    allow_delete: bool,
    skip_paths: list[str] | None = None,
) -> dict:
    skip_paths = skip_paths or []
    with TemporaryDirectory(prefix="openclaw-sync-") as temp_dir:
        temp_root = Path(temp_dir) / root_key(root)
        temp_root.mkdir(parents=True, exist_ok=True)
        exclude_file = _write_exclude_file(Path(temp_dir), excludes, skip_paths)
        ssh_cmd = _build_ssh_command(runner)

        pull_cmd = [
            "rsync",
            "-az",
            "--itemize-changes",
            "--exclude-from",
            str(exclude_file),
            "-e",
            ssh_cmd,
            f"{source_host}:{root.rstrip('/')}/",
            f"{temp_root}/",
        ]

        push_cmd = [
            "rsync",
            "-az",
            "--itemize-changes",
            "--exclude-from",
            str(exclude_file),
            "-e",
            ssh_cmd,
            f"{temp_root}/",
            f"{target_host}:{root.rstrip('/')}/",
        ]
        if allow_delete:
            push_cmd.insert(3, "--delete")

        pull_result = _run_rsync(runner, pull_cmd)
        if pull_result["returncode"] != 0:
            return {
                "ok": False,
                "stage": "pull",
                "root": root,
                "result": pull_result,
            }

        push_result = _run_rsync(runner, push_cmd)
        if push_result["returncode"] != 0:
            return {
                "ok": False,
                "stage": "push",
                "root": root,
                "result": push_result,
            }

        return {
            "ok": True,
            "root": root,
            "pull": pull_result,
            "push": push_result,
        }


def execute_plan(
    runner: SSHRunner,
    plan: dict,
    excludes: list[str],
    allow_delete: bool,
    conflict_resolutions: list[dict],
) -> dict:
    mode = plan["mode"]
    source_host = plan["source_host"]
    target_host = plan["target_host"]
    roots = plan["roots"]

    resolution_map = {
        (item["root"], item["path"]): item["decision"]
        for item in conflict_resolutions
        if item.get("decision") in {"keep_a", "keep_b", "keep_both"}
    }

    operations: list[dict] = []

    def run_direction(src: str, dst: str, label: str) -> None:
        for root in roots:
            skip_for_root: list[str] = []
            for conflict in plan.get("conflicts", []):
                if conflict["root"] != root:
                    continue
                decision = resolution_map.get((root, conflict["path"]), "keep_a")
                if label == "a_to_b":
                    if decision in {"keep_b", "keep_both"}:
                        _backup_or_rename_conflict(runner, dst, root, conflict["path"], decision, "a")
                    if decision == "keep_b":
                        skip_for_root.append(conflict["path"])
                elif label == "b_to_a":
                    if decision in {"keep_a", "keep_both"}:
                        _backup_or_rename_conflict(runner, dst, root, conflict["path"], "keep_b" if decision == "keep_a" else "keep_both", "b")
                    if decision == "keep_a":
                        skip_for_root.append(conflict["path"])

            result = execute_single_direction(
                runner=runner,
                source_host=src,
                target_host=dst,
                root=root,
                excludes=excludes,
                allow_delete=allow_delete,
                skip_paths=skip_for_root,
            )
            operations.append({"label": label, "result": result})

    if mode in {"a_to_b", "b_to_a"}:
        run_direction(source_host, target_host, mode)
    elif mode == "bidirectional":
        run_direction(source_host, target_host, "a_to_b")
        run_direction(target_host, source_host, "b_to_a")
    else:
        raise ValueError(f"Unsupported mode: {mode}")

    ok = all(item["result"].get("ok") for item in operations)
    return {"ok": ok, "operations": operations}
