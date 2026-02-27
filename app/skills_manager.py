from __future__ import annotations

import json
import posixpath
import re
import shlex
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.config import AppConfig, ServerConfig
from app.ssh_runner import SSHRunner

MARKET_API_URL = "https://api.github.com/repos/openai/skills/contents/skills/.curated?ref=main"
OPENCLAW_SKILLS_ROOT = "/root/.openclaw/workspace/skills"


def _resolve_servers(config: AppConfig, server: str | None) -> list[ServerConfig]:
    if server in {None, "", "all"}:
        return list(config.servers)
    selected = [item for item in config.servers if item.name == server or item.ssh_host == server]
    if not selected:
        raise ValueError(f"Unknown server: {server}")
    return selected


def _resolve_server(config: AppConfig, server: str) -> ServerConfig:
    selected = _resolve_servers(config, server)
    if len(selected) != 1:
        raise ValueError(f"Server must be specific: {server}")
    return selected[0]


def _list_command() -> str:
    return r"""python3 - <<'PY'
import json
import os
from pathlib import Path
from datetime import datetime, timezone

roots = [
    (Path('/root/.openclaw/workspace/skills'), 'openclaw_workspace'),
]
rows = []
seen = set()
for root, source in roots:
    if not root.exists():
        continue
    for dirpath, _, filenames in os.walk(root, topdown=True, followlinks=False):
        if 'SKILL.md' not in filenames:
            continue
        item = Path(dirpath)
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        try:
            rel = item.relative_to(root)
        except Exception:
            rel = item
        try:
            stat = item.stat()
            installed_ts = int(stat.st_mtime)
            installed_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            installed_ts = 0
            installed_at = None
        rows.append({
            'name': str(rel),
            'path': str(item),
            'source': source,
            'has_skill_md': True,
            'installed_ts': installed_ts,
            'installed_at': installed_at,
        })

rows.sort(key=lambda row: (-int(row.get('installed_ts', 0)), row['name'].lower()))
print(json.dumps({'skills': rows}))
PY"""


def _install_from_repo_command(repo_url: str) -> str:
    return rf"""bash -lc '
set -e
ROOT="{OPENCLAW_SKILLS_ROOT}"
mkdir -p "$ROOT"
name="$(basename "{repo_url}" | sed "s/\.git$//")"
name="$(echo "$name" | tr -cs "a-zA-Z0-9._-" "-" | sed "s/^-//;s/-$//")"
[ -z "$name" ] && name="skill"
target="$ROOT/$name"
if [ -d "$target/.git" ]; then
  (cd "$target" && git pull --ff-only)
else
  rm -rf "$target"
  git clone --depth 1 "{repo_url}" "$target"
fi
if [ ! -f "$target/SKILL.md" ]; then
  first="$(find "$target" -maxdepth 2 -name SKILL.md | head -n 1 || true)"
  if [ -n "$first" ]; then
    mkdir -p "$ROOT/$name"
    cp "$first" "$ROOT/$name/SKILL.md"
  fi
fi
echo "installed_path=$target"
'"""


def _install_from_market_path_command(market_path: str, selected_name: str) -> str:
    slug = (market_path or "").strip()
    if slug.startswith("clawhub:"):
        slug = slug.split(":", 1)[1]
    return rf"""bash -lc '
set -e
SLUG={shlex.quote(slug)}
WORKDIR="/root/.openclaw/workspace"
mkdir -p "$WORKDIR"

if command -v clawhub >/dev/null 2>&1; then
  CLAWHUB_WORKDIR="$WORKDIR" clawhub install "$SLUG" --force \
    || clawhub --workdir "$WORKDIR" install "$SLUG" --force \
    || (cd "$WORKDIR" && clawhub install "$SLUG" --force)
elif command -v openclaw >/dev/null 2>&1; then
  (cd "$WORKDIR" && openclaw skill install "$SLUG")
else
  echo "missing clawhub/openclaw cli on remote server" >&2
  exit 1
fi

echo "installed_slug=$SLUG"
'"""


def _parse_clawhub_search_output(stdout: str, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("NAME ") or upper.startswith("AUTHOR ") or set(line) <= {"-", " "}:
            continue
        if "NO SKILLS" in upper or "NO RESULT" in upper:
            continue
        match = re.match(r"^([a-z0-9][a-z0-9._-]{1,})\b", line, flags=re.IGNORECASE)
        if not match:
            continue
        slug = match.group(1)
        if slug.lower() in {"name", "author", "rating", "downloads", "description"}:
            continue
        desc = line[len(slug) :].strip()
        rows.append(
            {
                "name": slug,
                "slug": slug,
                "path": f"clawhub:{slug}",
                "score": None,
                "description": desc,
                "source": "clawhub",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _search_clawhub_cli(prompt: str, limit: int) -> list[dict[str, Any]]:
    commands = [
        ["clawhub", "search", prompt, "--limit", str(limit)],
        ["openclaw", "skill", "search", prompt],
    ]
    errors: list[str] = []
    for cmd in commands:
        try:
            result = subprocess.run(cmd, text=True, capture_output=True, timeout=40)
        except FileNotFoundError:
            errors.append(f"command not found: {cmd[0]}")
            continue
        except subprocess.TimeoutExpired:
            errors.append(f"command timeout: {' '.join(cmd)}")
            continue
        if result.returncode != 0:
            errors.append(result.stderr.strip() or f"command failed: {' '.join(cmd)}")
            continue
        parsed = _parse_clawhub_search_output(result.stdout, limit=limit)
        if parsed:
            return parsed
    raise RuntimeError("clawhub search failed: " + " | ".join(errors))


def _normalize_market_skill_slug(value: str) -> str:
    raw = (value or "").strip()
    if raw.startswith("clawhub:"):
        raw = raw.split(":", 1)[1]
    raw = raw.strip().strip("/")
    if "/" in raw:
        raw = raw.split("/")[-1]
    if not raw:
        raise ValueError("invalid market skill slug")
    return raw


def _parse_market_detail_output(slug: str, stdout: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    summary = ""
    features: list[str] = []
    examples: list[str] = []
    section = ""
    in_code_block = False

    for raw in lines:
        line = raw.strip()
        lower = line.lower()
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip().lower()
            if any(token in heading for token in ["when to use", "what it does", "功能", "能力"]):
                section = "features"
            elif any(token in heading for token in ["example", "usage", "run", "用法", "示例"]):
                section = "examples"
            continue
        if any(token in lower for token in ["feature", "capabilit", "what it does", "功能"]):
            section = "features"
            continue
        if any(token in lower for token in ["example", "usage", "how to use", "示例", "用法"]):
            section = "examples"
            continue
        if lower.startswith("description:") or lower.startswith("简介:"):
            text = line.split(":", 1)[1].strip()
            if text:
                summary = text
            continue

        is_list_item = line.startswith("- ") or line.startswith("* ") or re.match(r"^\d+[.)]\s+", line)
        text = re.sub(r"^[-*]\s+|^\d+[.)]\s+", "", line).strip()
        if not text:
            continue
        if in_code_block and section == "examples":
            examples.append(text)
            continue
        if section == "features" and is_list_item:
            features.append(text)
            continue
        if section == "examples" and is_list_item:
            examples.append(text)
            continue
        if not summary and len(text) > 10 and not is_list_item:
            summary = text

    if not summary and lines:
        summary = lines[0][:200]
    if not features:
        features = [line.strip("-* ").strip() for line in lines if line.startswith(("- ", "* "))][:4]
    if not examples:
        examples = [line.strip("-* ").strip() for line in lines if "http" in line.lower() or "example" in line.lower()][:3]
    if not features and summary:
        match = re.search(r"use when\s+(.*)", summary, flags=re.IGNORECASE)
        if match:
            segment = match.group(1).strip().rstrip(".")
            parts = re.split(r",\s*|;\s*|\s+or\s+", segment)
            features = [part.strip() for part in parts if part.strip()][:5]
    if not examples:
        examples = [
            f"clawhub install {slug}",
            f"clawhub inspect {slug} --file SKILL.md",
        ]

    return {
        "name": slug,
        "summary": summary or "暂无简介",
        "features": features[:6],
        "examples": examples[:4],
        "raw_excerpt": "\n".join(lines[:20]),
    }


def get_market_skill_detail(market_path: str | None, market_name: str | None = None) -> dict[str, Any]:
    seed = (market_name or "").strip() or (market_path or "").strip()
    slug = _normalize_market_skill_slug(seed)
    commands: list[tuple[list[str], int]] = [
        (["clawhub", "inspect", slug, "--file", "SKILL.md"], 120),
        (["clawhub", "inspect", slug], 45),
        (["clawhub", "show", slug], 45),
    ]
    errors: list[str] = []
    fallback_detail: dict[str, Any] | None = None
    for cmd, timeout_sec in commands:
        try:
            result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout_sec)
        except FileNotFoundError:
            errors.append(f"command not found: {cmd[0]}")
            continue
        except subprocess.TimeoutExpired:
            errors.append(f"command timeout: {cmd[0]}")
            continue
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if "Non-error was thrown" in stderr and "Timeout" in stderr:
                errors.append(f"command timeout: {cmd[0]}")
            else:
                errors.append(stderr.splitlines()[0] if stderr else f"command failed: {cmd[0]}")
            continue
        parsed = _parse_market_detail_output(slug, result.stdout)
        parsed["source"] = "clawhub"
        parsed["market_path"] = f"clawhub:{slug}"
        has_rich = bool(parsed.get("features")) or bool(parsed.get("examples"))
        if has_rich:
            return parsed
        fallback_detail = parsed

    if fallback_detail is not None:
        return fallback_detail

    brief = " | ".join(error for error in errors if error)[:240]
    raise RuntimeError(f"fetch market detail failed: {brief or 'unknown error'}")


def _skill_tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", text.lower()) if token]


def _score_candidate(name: str, prompt: str) -> int:
    name_lc = name.lower()
    tokens = _skill_tokens(prompt)
    if not tokens:
        return 0
    score = sum(3 for token in tokens if token in name_lc)
    if name_lc == prompt.lower().strip():
        score += 100
    if tokens and name_lc.startswith(tokens[0]):
        score += 5
    return score


def _fetch_market_catalog() -> list[dict[str, str]]:
    request = urllib.request.Request(MARKET_API_URL, headers={"User-Agent": "openclaw-console"})
    with urllib.request.urlopen(request, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "dir":
            continue
        name = str(item.get("name", "")).strip()
        path = str(item.get("path", "")).strip()
        if not name or not path:
            continue
        rows.append({"name": name, "path": path})
    return rows


def search_market_skills(prompt: str, limit: int = 5) -> dict[str, Any]:
    query = prompt.strip()
    if not query:
        raise ValueError("prompt is required")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    candidates = _search_clawhub_cli(query, limit=limit)
    return {
        "prompt": query,
        "limit": limit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": "clawhub",
        "candidates": candidates,
    }


def _normalize_skill_name(skill_name: str) -> str:
    value = skill_name.strip().replace("\\", "/").strip("/")
    if not value or ".." in value.split("/"):
        raise ValueError("invalid skill_name")
    return value


def _extract_skill_base_name(skill_name: str) -> str:
    value = (skill_name or "").strip().replace("\\", "/").strip("/")
    if not value:
        return ""
    return value.split("/")[0]


def list_skills(config: AppConfig, runner: SSHRunner) -> dict[str, Any]:
    result: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "servers": {}}
    official_names: set[str] = set()
    try:
        official_names = {_extract_skill_base_name(item["name"]) for item in _fetch_market_catalog()}
        official_names = {item for item in official_names if item}
    except Exception:
        official_names = set()
    for server in config.servers:
        cmd = _list_command()
        response = runner.run_ssh(server.ssh_host, cmd, timeout=120)
        if response.returncode != 0:
            result["servers"][server.name] = {
                "server_name": server.name,
                "ssh_host": server.ssh_host,
                "skills": [],
                "error": response.stderr.strip() or "list skills failed",
            }
            continue
        try:
            payload = json.loads(response.stdout or "{}")
            skills = payload.get("skills", [])
            if not isinstance(skills, list):
                skills = []
            for item in skills:
                if not isinstance(item, dict):
                    continue
                base = _extract_skill_base_name(str(item.get("name", "")))
                item["skill_type"] = "official" if base in official_names else "custom"
            skills.sort(
                key=lambda row: (
                    -int((row or {}).get("installed_ts") or 0),
                    str((row or {}).get("name", "")).lower(),
                )
            )
        except Exception as exc:
            result["servers"][server.name] = {
                "server_name": server.name,
                "ssh_host": server.ssh_host,
                "skills": [],
                "error": f"parse skills failed: {exc}",
            }
            continue
        result["servers"][server.name] = {
            "server_name": server.name,
            "ssh_host": server.ssh_host,
            "skills": skills,
            "error": None,
        }
    return result


def install_skill(
    config: AppConfig,
    runner: SSHRunner,
    server: str | None,
    repo_url: str | None,
    prompt: str | None,
    market_path: str | None = None,
    market_name: str | None = None,
) -> dict[str, Any]:
    if not repo_url and not prompt and not market_path:
        raise ValueError("repo_url or prompt or market_path is required")

    resolved_market_path = (market_path or "").strip()
    resolved_market_name = (market_name or "").strip()
    resolved_prompt = (prompt or "").strip()
    if not repo_url and not resolved_market_path and resolved_prompt:
        suggestion = search_market_skills(resolved_prompt, limit=1)
        candidates = suggestion.get("candidates", [])
        if not candidates:
            raise ValueError("no market skill candidate found")
        resolved_market_path = str(candidates[0]["path"])
        resolved_market_name = str(candidates[0]["name"])
    elif resolved_market_path and not resolved_market_name:
        resolved_market_name = posixpath.basename(resolved_market_path.rstrip("/"))

    output: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "servers": {}}
    selected = _resolve_servers(config, server)
    for item in selected:
        if repo_url:
            command = _install_from_repo_command(repo_url)
            mode = "repo"
        else:
            command = _install_from_market_path_command(resolved_market_path, resolved_market_name)
            mode = "market_selected"
        result = runner.run_ssh(item.ssh_host, command, timeout=240)
        output["servers"][item.name] = {
            "server_name": item.name,
            "ssh_host": item.ssh_host,
            "mode": mode,
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "selected_market_path": resolved_market_path or None,
            "selected_market_name": resolved_market_name or None,
        }
    return output


def copy_skill_between_servers(
    config: AppConfig,
    runner: SSHRunner,
    source_server: str,
    target_server: str,
    skill_name: str,
) -> dict[str, Any]:
    source = _resolve_server(config, source_server)
    target = _resolve_server(config, target_server)
    if source.name == target.name:
        raise ValueError("source_server and target_server must be different")

    normalized = _normalize_skill_name(skill_name)
    source_path = posixpath.join(OPENCLAW_SKILLS_ROOT, normalized)
    target_path = posixpath.join(OPENCLAW_SKILLS_ROOT, normalized)
    target_parent = posixpath.dirname(target_path)

    mkdir = runner.run_ssh(target.ssh_host, f"mkdir -p {shlex.quote(target_parent)}", timeout=30)
    if mkdir.returncode != 0:
        raise RuntimeError(mkdir.stderr.strip() or "failed to create target directory")

    ssh_command = "ssh " + " ".join(shlex.quote(opt) for opt in runner.ssh_options())
    with TemporaryDirectory(prefix="openclaw-skill-copy-") as temp_dir:
        local_stage = Path(temp_dir) / normalized
        local_stage.mkdir(parents=True, exist_ok=True)

        pull_command = [
            "rsync",
            "-az",
            "--delete",
            "-e",
            ssh_command,
            f"{source.ssh_host}:{source_path}/",
            f"{local_stage}/",
        ]
        pull_result = runner.run_local(pull_command, timeout=240)
        if pull_result.returncode != 0:
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_server": source.name,
                "target_server": target.name,
                "skill_name": normalized,
                "ok": False,
                "returncode": pull_result.returncode,
                "stdout": pull_result.stdout.strip(),
                "stderr": pull_result.stderr.strip(),
            }

        push_command = [
        "rsync",
        "-az",
        "--delete",
        "-e",
        ssh_command,
        f"{local_stage}/",
        f"{target.ssh_host}:{target_path}/",
        ]
        result = runner.run_local(push_command, timeout=240)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_server": source.name,
        "target_server": target.name,
        "skill_name": normalized,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def copy_skills_between_servers(
    config: AppConfig,
    runner: SSHRunner,
    source_server: str,
    target_server: str,
    skill_names: list[str],
) -> dict[str, Any]:
    if not isinstance(skill_names, list) or not skill_names:
        raise ValueError("skill_names must be a non-empty list")

    normalized_names: list[str] = []
    seen: set[str] = set()
    for item in skill_names:
        if not isinstance(item, str):
            raise ValueError("skill_names must contain strings only")
        normalized = _normalize_skill_name(item)
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_names.append(normalized)

    results: list[dict[str, Any]] = []
    ok_count = 0
    for name in normalized_names:
        payload = copy_skill_between_servers(
            config=config,
            runner=runner,
            source_server=source_server,
            target_server=target_server,
            skill_name=name,
        )
        results.append(payload)
        if payload.get("ok"):
            ok_count += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_server": source_server,
        "target_server": target_server,
        "skill_names": normalized_names,
        "ok": ok_count == len(results),
        "ok_count": ok_count,
        "total": len(results),
        "results": results,
    }


def sync_skills_incremental(
    config: AppConfig,
    runner: SSHRunner,
    servers: list[str] | None = None,
) -> dict[str, Any]:
    selected_servers: list[ServerConfig] = []
    if servers:
        seen: set[str] = set()
        for row in servers:
            item = _resolve_server(config, row)
            if item.name in seen:
                continue
            seen.add(item.name)
            selected_servers.append(item)
    else:
        selected_servers = list(config.servers)

    if len(selected_servers) < 2:
        raise ValueError("at least 2 servers are required for skills sync")

    listed = list_skills(config, runner)
    listed_servers = listed.get("servers", {})
    server_names = [item.name for item in selected_servers]
    eligible_names = [name for name in server_names if isinstance(listed_servers.get(name), dict) and not listed_servers[name].get("error")]
    if len(eligible_names) < 2:
        raise ValueError("at least 2 healthy servers are required for skills sync")

    by_server: dict[str, dict[str, dict[str, Any]]] = {}
    catalog: dict[str, dict[str, Any]] = {}
    for server_name in eligible_names:
        rows = listed_servers.get(server_name, {}).get("skills", [])
        skill_map: dict[str, dict[str, Any]] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            skill_map[name] = item
            ts = int(item.get("installed_ts") or 0)
            current = catalog.get(name)
            if current is None or ts > int(current.get("installed_ts") or 0):
                catalog[name] = {
                    "source_server": server_name,
                    "installed_ts": ts,
                }
        by_server[server_name] = skill_map

    actions: list[dict[str, Any]] = []
    for skill_name, source_info in sorted(catalog.items(), key=lambda row: row[0].lower()):
        source_server = str(source_info["source_server"])
        source_ts = int(source_info.get("installed_ts") or 0)
        for target_server in eligible_names:
            if target_server == source_server:
                continue
            current = by_server.get(target_server, {}).get(skill_name)
            target_ts = int((current or {}).get("installed_ts") or -1)
            if target_ts >= source_ts:
                continue
            actions.append(
                {
                    "skill_name": skill_name,
                    "source_server": source_server,
                    "target_server": target_server,
                    "source_installed_ts": source_ts,
                    "target_installed_ts": target_ts if current else None,
                }
            )

    results: list[dict[str, Any]] = []
    ok_count = 0
    for item in actions:
        result = copy_skill_between_servers(
            config=config,
            runner=runner,
            source_server=item["source_server"],
            target_server=item["target_server"],
            skill_name=item["skill_name"],
        )
        results.append(result)
        if result.get("ok"):
            ok_count += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "servers": eligible_names,
        "total_actions": len(actions),
        "ok": ok_count == len(actions),
        "ok_count": ok_count,
        "results": results,
    }
