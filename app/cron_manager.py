from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import AppConfig, ServerConfig
from app.ssh_runner import SSHRunner

TEXT_EXTENSIONS = {".md", ".txt", ".log", ".json", ".yaml", ".yml", ".csv"}
CRON_CACHE_DIR = ".cache/cron_outputs"
ERROR_MARKERS = ("error", "failed", "non-zero", "exit", "traceback")


def build_job_id(source: str, schedule: str, command: str, user: str | None) -> str:
    payload = f"{source}|{schedule}|{command}|{user or ''}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _extract_redirect_paths(command: str) -> list[str]:
    candidates: list[str] = []
    patterns = [
        r"(?:^|\s)(?:>>|>|2>>|2>|&>)(?:\s*)(/[^\s;|&]+)",
        r"(?:^|\s)1>>(?:\s*)(/[^\s;|&]+)",
        r"(?:^|\s)1>(?:\s*)(/[^\s;|&]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, command):
            path = match.group(1).strip().strip("'\"")
            if path.startswith("/"):
                candidates.append(path)
    unique: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def extract_output_hints(command: str) -> list[str]:
    return [path for path in _extract_redirect_paths(command) if Path(path).suffix.lower() in TEXT_EXTENSIONS]


def parse_cron_lines(source: str, lines: list[str], has_user_field: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s+", line, maxsplit=6 if has_user_field else 5)
        if not parts:
            continue
        schedule = ""
        user: str | None = None
        command = ""
        if parts[0].startswith("@"):
            if has_user_field:
                if len(parts) < 3:
                    continue
                schedule = parts[0]
                user = parts[1]
                command = " ".join(parts[2:])
            else:
                if len(parts) < 2:
                    continue
                schedule = parts[0]
                command = " ".join(parts[1:])
        else:
            if has_user_field:
                if len(parts) < 7:
                    continue
                schedule = " ".join(parts[:5])
                user = parts[5]
                command = parts[6]
            else:
                if len(parts) < 6:
                    continue
                schedule = " ".join(parts[:5])
                command = parts[5]
        command = command.strip()
        if not command:
            continue
        rows.append(
            {
                "job_id": build_job_id(source, schedule, command, user),
                "source": source,
                "schedule": schedule,
                "command": command,
                "user": user,
                "last_seen_log_at": None,
                "summary": {"runs_24h": 0, "errors_24h": 0, "last_status": "unknown"},
                "output_hints": extract_output_hints(command),
            }
        )
    return rows


def _job_keywords(command: str) -> list[str]:
    tokens = [token for token in re.split(r"\s+", command) if token]
    if not tokens:
        return []
    keywords: list[str] = []
    first = os.path.basename(tokens[0].strip("'\""))
    if first:
        keywords.append(first.lower())
    for token in tokens[1:4]:
        cleaned = token.strip("'\"")
        if cleaned.startswith("/"):
            keywords.append(os.path.basename(cleaned).lower())
    unique: list[str] = []
    seen: set[str] = set()
    for item in keywords:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def summarize_job_logs(job: dict[str, Any], log_lines_24h: list[str], log_lines_7d: list[str] | None = None) -> dict[str, Any]:
    keywords = _job_keywords(str(job.get("command", "")))
    if not keywords:
        return {
            "runs_24h": 0,
            "errors_24h": 0,
            "runs_7d": 0,
            "errors_7d": 0,
            "last_status": "unknown",
            "last_run_at": None,
        }
    if log_lines_7d is None:
        log_lines_7d = log_lines_24h
    matches_24h = [line for line in log_lines_24h if any(key in line.lower() for key in keywords)]
    errors_24h = [line for line in matches_24h if any(marker in line.lower() for marker in ERROR_MARKERS)]
    matches_7d = [line for line in log_lines_7d if any(key in line.lower() for key in keywords)]
    errors_7d = [line for line in matches_7d if any(marker in line.lower() for marker in ERROR_MARKERS)]
    last_status = "unknown"
    if matches_7d:
        last_status = "error" if matches_7d[-1] in errors_7d else "ok"
    return {
        "runs_24h": len(matches_24h),
        "errors_24h": len(errors_24h),
        "runs_7d": len(matches_7d),
        "errors_7d": len(errors_7d),
        "last_status": last_status,
        "last_run_at": None,
    }


def is_safe_output_path(remote_path: str) -> bool:
    if not remote_path or not remote_path.startswith("/"):
        return False
    if ".." in Path(remote_path).parts:
        return False
    return Path(remote_path).suffix.lower() in TEXT_EXTENSIONS


def build_local_output_path(project_root: Path, server_name: str, remote_path: str) -> Path:
    server_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", server_name).strip("-") or "server"
    suffix = Path(remote_path).suffix
    stem = remote_path.strip("/").replace("/", "__")
    filename = f"{stem}{suffix}" if not stem.endswith(suffix) else stem
    return project_root / CRON_CACHE_DIR / server_slug / filename


def _resolve_server(config: AppConfig, server: str) -> ServerConfig:
    matched = [item for item in config.servers if item.name == server or item.ssh_host == server]
    if not matched:
        raise ValueError(f"Unknown server: {server}")
    return matched[0]


def _remote_cron_list_command() -> str:
    script = r"""python3 - <<'PY'
import glob
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone

ERROR_MARKERS = ("error", "failed", "non-zero", "exit", "traceback")
TEXT_EXTS = {".md", ".txt", ".log", ".json", ".yaml", ".yml", ".csv"}

def build_job_id(source, schedule, command, user):
    payload = f"{source}|{schedule}|{command}|{user or ''}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

def extract_hints(command):
    candidates = []
    patterns = [
        r"(?:^|\s)(?:>>|>|2>>|2>|&>)(?:\s*)(/[^\s;|&]+)",
        r"(?:^|\s)1>>(?:\s*)(/[^\s;|&]+)",
        r"(?:^|\s)1>(?:\s*)(/[^\s;|&]+)",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, command):
            value = m.group(1).strip().strip("'\"")
            if value.startswith("/") and os.path.splitext(value)[1].lower() in TEXT_EXTS:
                candidates.append(value)
    unique = []
    seen = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique

def parse_lines(source, lines, has_user):
    out = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s+", line, maxsplit=6 if has_user else 5)
        schedule = ""
        user = None
        command = ""
        if parts[0].startswith("@"):
            if has_user:
                if len(parts) < 3:
                    continue
                schedule = parts[0]
                user = parts[1]
                command = " ".join(parts[2:])
            else:
                if len(parts) < 2:
                    continue
                schedule = parts[0]
                command = " ".join(parts[1:])
        else:
            if has_user:
                if len(parts) < 7:
                    continue
                schedule = " ".join(parts[:5])
                user = parts[5]
                command = parts[6]
            else:
                if len(parts) < 6:
                    continue
                schedule = " ".join(parts[:5])
                command = parts[5]
        command = command.strip()
        if not command:
            continue
        out.append({
            "job_id": build_job_id(source, schedule, command, user),
            "source": source,
            "schedule": schedule,
            "command": command,
            "user": user,
            "last_seen_log_at": None,
            "summary": {"runs_24h": 0, "errors_24h": 0, "runs_7d": 0, "errors_7d": 0, "last_status": "unknown"},
            "output_hints": extract_hints(command),
        })
    return out

def keywords(command):
    tokens = [t for t in re.split(r"\s+", command) if t]
    if not tokens:
        return []
    values = []
    first = os.path.basename(tokens[0].strip("'\""))
    if first:
        values.append(first.lower())
    for t in tokens[1:4]:
        c = t.strip("'\"")
        if c.startswith("/"):
            values.append(os.path.basename(c).lower())
    uniq = []
    seen = set()
    for x in values:
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq

def read_logs(hours, fallback_lines):
    lines = []
    try:
        proc = subprocess.run(
            ["journalctl", "--since", f"{hours} hours ago", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=30, check=False
        )
        if proc.returncode == 0 and proc.stdout.strip():
            lines.extend(proc.stdout.splitlines())
    except Exception:
        pass
    if not lines and os.path.exists("/var/log/cron"):
        try:
            proc = subprocess.run(["tail", "-n", str(fallback_lines), "/var/log/cron"], capture_output=True, text=True, timeout=10, check=False)
            if proc.returncode == 0 and proc.stdout.strip():
                lines.extend(proc.stdout.splitlines())
        except Exception:
            pass
    return lines

jobs = []
errors = []
try:
    proc = subprocess.run(["crontab", "-l", "-u", "root"], capture_output=True, text=True, timeout=10, check=False)
    if proc.returncode == 0:
        jobs.extend(parse_lines("root_crontab", proc.stdout.splitlines(), has_user=False))
except Exception as exc:
    errors.append(f"root crontab read failed: {exc}")

if os.path.exists("/etc/crontab"):
    try:
        with open("/etc/crontab", "r", encoding="utf-8", errors="ignore") as fh:
            jobs.extend(parse_lines("etc_crontab", fh.read().splitlines(), has_user=True))
    except Exception as exc:
        errors.append(f"/etc/crontab read failed: {exc}")

for file_path in sorted(glob.glob("/etc/cron.d/*")):
    if not os.path.isfile(file_path):
        continue
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
            rows = parse_lines("etc_cron_d", fh.read().splitlines(), has_user=True)
            jobs.extend(rows)
    except Exception as exc:
        errors.append(f"{file_path} read failed: {exc}")

log_lines_24h = read_logs(24, 2000)
log_lines_7d = read_logs(24 * 7, 12000)
for job in jobs:
    keys = keywords(job["command"])
    if not keys:
        continue
    matched_24 = [line for line in log_lines_24h if any(k in line.lower() for k in keys)]
    matched_7d = [line for line in log_lines_7d if any(k in line.lower() for k in keys)]
    err_24 = [line for line in matched_24 if any(m in line.lower() for m in ERROR_MARKERS)]
    err_7d = [line for line in matched_7d if any(m in line.lower() for m in ERROR_MARKERS)]
    job["summary"]["runs_24h"] = len(matched_24)
    job["summary"]["errors_24h"] = len(err_24)
    job["summary"]["runs_7d"] = len(matched_7d)
    job["summary"]["errors_7d"] = len(err_7d)
    if matched_7d:
        job["summary"]["last_status"] = "error" if matched_7d[-1] in err_7d else "ok"
    else:
        job["summary"]["last_status"] = "unknown"

jobs.sort(key=lambda row: (row.get("source", ""), row.get("schedule", ""), row.get("command", "")))
print(json.dumps({
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "jobs": jobs,
    "errors": errors,
}))
PY"""
    return script


def _remote_cron_detail_command(job: dict[str, Any], lines: int) -> str:
    payload = json.dumps(
        {
            "command": str(job.get("command", "")),
            "schedule": str(job.get("schedule", "")),
            "job_id": str(job.get("job_id", "")),
            "output_hints": list(job.get("output_hints", [])),
            "summary": dict(job.get("summary", {})),
            "lines": max(20, min(1000, int(lines))),
        }
    )
    return f"""python3 - <<'PY'
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone

ERROR_MARKERS = ("error", "failed", "non-zero", "exit", "traceback")
TEXT_EXTS = {{".md", ".txt", ".log", ".json", ".yaml", ".yml", ".csv"}}
data = json.loads({json.dumps(payload)})
command = data.get("command", "")
output_hints = data.get("output_hints", [])
line_limit = int(data.get("lines", 200))

def keywords(command):
    tokens = [t for t in re.split(r"\\s+", command) if t]
    if not tokens:
        return []
    values = []
    first = os.path.basename(tokens[0].strip("'\\\""))
    if first:
        values.append(first.lower())
    for t in tokens[1:4]:
        c = t.strip("'\\\"")
        if c.startswith("/"):
            values.append(os.path.basename(c).lower())
    uniq = []
    seen = set()
    for x in values:
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq

def read_logs(hours, fallback_lines):
    lines = []
    try:
        proc = subprocess.run(
            ["journalctl", "--since", f"{{hours}} hours ago", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=25, check=False
        )
        if proc.returncode == 0 and proc.stdout.strip():
            lines.extend(proc.stdout.splitlines())
    except Exception:
        pass
    if not lines and os.path.exists("/var/log/cron"):
        try:
            proc = subprocess.run(["tail", "-n", str(fallback_lines), "/var/log/cron"], capture_output=True, text=True, timeout=10, check=False)
            if proc.returncode == 0 and proc.stdout.strip():
                lines.extend(proc.stdout.splitlines())
        except Exception:
            pass
    return lines

def line_date_key(line):
    head = line[:10]
    if re.match(r"\\d{{4}}-\\d{{2}}-\\d{{2}}", head):
        return head
    return line[:6].strip()

logs_24h = read_logs(24, 3000)
logs_7d = read_logs(24 * 7, 12000)
keys = keywords(command)
matched_24 = [line for line in logs_24h if any(k in line.lower() for k in keys)] if keys else []
matched_7d = [line for line in logs_7d if any(k in line.lower() for k in keys)] if keys else []
recent = matched_7d[-line_limit:]
errors_24 = [line for line in matched_24 if any(marker in line.lower() for marker in ERROR_MARKERS)]
errors_7d = [line for line in matched_7d if any(marker in line.lower() for marker in ERROR_MARKERS)]
status = "unknown"
if matched_7d:
    status = "error" if matched_7d[-1] in errors_7d else "ok"

buckets = defaultdict(list)
for line in matched_7d:
    buckets[line_date_key(line)].append(line)
daily_buckets = []
for date_key in sorted(buckets.keys(), reverse=True):
    logs = buckets[date_key]
    errs = [line for line in logs if any(marker in line.lower() for marker in ERROR_MARKERS)]
    daily_buckets.append(
        {{
            "date": date_key,
            "runs": len(logs),
            "errors": len(errs),
            "logs": logs[-line_limit:],
        }}
    )

files = []
for path in output_hints:
    path = str(path)
    suffix = os.path.splitext(path)[1].lower()
    if suffix not in TEXT_EXTS:
        continue
    row = {{"remote_path": path, "exists": False, "size_bytes": None, "modified_at": None}}
    if os.path.exists(path) and os.path.isfile(path):
        st = os.stat(path)
        row["exists"] = True
        row["size_bytes"] = st.st_size
        row["modified_at"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    files.append(row)

print(json.dumps({{
    "job_id": data.get("job_id"),
    "schedule": data.get("schedule"),
    "command": command,
    "recent_logs": recent,
    "summary": {{
        "runs_24h": len(matched_24),
        "errors_24h": len(errors_24),
        "runs_7d": len(matched_7d),
        "errors_7d": len(errors_7d),
        "last_status": status,
        "last_run_at": None,
    }},
    "daily_buckets": daily_buckets,
    "output_files": files,
}}))
PY"""


def collect_cron_jobs(config: AppConfig, runner: SSHRunner) -> dict[str, Any]:
    payload: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "servers": {}}
    command = _remote_cron_list_command()
    for server in config.servers:
        result = runner.run_ssh(server.ssh_host, command, timeout=120)
        if result.returncode != 0:
            payload["servers"][server.name] = {
                "server_name": server.name,
                "jobs": [],
                "error": result.stderr.strip() or "cron collect failed",
            }
            continue
        try:
            parsed = json.loads(result.stdout or "{}")
            jobs = parsed.get("jobs", [])
            errors = parsed.get("errors", [])
            if not isinstance(jobs, list):
                jobs = []
            if not isinstance(errors, list):
                errors = []
            payload["servers"][server.name] = {
                "server_name": server.name,
                "jobs": jobs,
                "error": " | ".join(str(item) for item in errors) if errors else None,
            }
        except Exception as exc:
            payload["servers"][server.name] = {
                "server_name": server.name,
                "jobs": [],
                "error": f"parse cron payload failed: {exc}",
            }
    return payload


def get_cron_job_detail(
    config: AppConfig,
    runner: SSHRunner,
    server: str,
    job_id: str,
    lines: int = 200,
) -> dict[str, Any]:
    selected = _resolve_server(config, server)
    listing = collect_cron_jobs(config, runner)
    server_payload = listing.get("servers", {}).get(selected.name, {})
    jobs = server_payload.get("jobs", [])
    job = next((item for item in jobs if str(item.get("job_id")) == job_id), None)
    if job is None:
        raise ValueError(f"Unknown job_id: {job_id}")
    detail_command = _remote_cron_detail_command(job, lines)
    result = runner.run_ssh(selected.ssh_host, detail_command, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "cron detail failed")
    detail = json.loads(result.stdout or "{}")
    return {
        "server_name": selected.name,
        "job_id": job_id,
        "schedule": detail.get("schedule", job.get("schedule")),
        "command": detail.get("command", job.get("command")),
        "recent_logs": detail.get("recent_logs", []),
        "summary": detail.get("summary", job.get("summary", {})),
        "daily_buckets": detail.get("daily_buckets", []),
        "output_files": detail.get("output_files", []),
        "error": None,
    }


def open_cron_output_file(
    project_root: Path,
    config: AppConfig,
    runner: SSHRunner,
    server: str,
    remote_path: str,
) -> dict[str, Any]:
    selected = _resolve_server(config, server)
    remote_path = remote_path.strip()
    if not is_safe_output_path(remote_path):
        raise ValueError("remote_path is not allowed")
    local_path = build_local_output_path(project_root, selected.name, remote_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    copy_command = [
        "scp",
        *runner.ssh_options(),
        f"{selected.ssh_host}:{remote_path}",
        str(local_path),
    ]
    copied = runner.run_local(copy_command, timeout=120)
    if copied.returncode != 0:
        raise RuntimeError(copied.stderr.strip() or "copy output file failed")

    opened = subprocess.run(["open", "-a", "TextEdit", str(local_path)], text=True, capture_output=True)
    if opened.returncode != 0:
        raise RuntimeError(opened.stderr.strip() or "open TextEdit failed")

    return {
        "ok": True,
        "server": selected.name,
        "remote_path": remote_path,
        "local_path": str(local_path),
    }
