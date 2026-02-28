from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import AppConfig, ServerConfig
from app.ssh_runner import SSHRunner


ERROR_MARKERS = ("error", "failed", "iserror=true")


@dataclass
class AgentRuntimeStatus:
    server_name: str
    ssh_host: str
    generated_at: str
    window_hours: int
    agent_timeseries: list[dict[str, Any]]
    agent_rank: list[dict[str, Any]]
    subagent_rank: list[dict[str, Any]]
    errors: list[str]


def _remote_runtime_command(window_hours: int) -> str:
    script = """python3 - <<'PY'
import glob
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone

WINDOW_HOURS = __WINDOW_HOURS__
NOW = datetime.now(timezone.utc)
ROOT = os.path.expanduser('~/.openclaw/agents')
CUT = NOW - timedelta(hours=WINDOW_HOURS)

hour_keys = []
for i in range(WINDOW_HOURS - 1, -1, -1):
    point = (NOW - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
    hour_keys.append(point.strftime('%m-%d %H:00'))

agent_series = {key: {'sessions': 0, 'errors': 0} for key in hour_keys}
agent_rank = []
subagent_stats = defaultdict(lambda: {'calls_24h': 0, 'errors_24h': 0, 'last_seen_at': None})
errors = []

if not os.path.isdir(ROOT):
    errors.append(f'agents directory not found: {ROOT}')
else:
    for agent in sorted(os.listdir(ROOT)):
        sessions_dir = os.path.join(ROOT, agent, 'sessions')
        if not os.path.isdir(sessions_dir):
            continue
        files = sorted(glob.glob(os.path.join(sessions_dir, '*.jsonl')))
        sessions_24h = 0
        errors_24h = 0
        latest_mtime = None
        latest_session_id = None

        for file_path in files:
            try:
                stat = os.stat(file_path)
            except OSError as exc:
                errors.append(f'stat failed: {file_path}: {exc}')
                continue

            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            session_id = os.path.basename(file_path).removesuffix('.jsonl')
            if latest_mtime is None or mtime > latest_mtime:
                latest_mtime = mtime
                latest_session_id = session_id

            if mtime < CUT:
                continue

            sessions_24h += 1
            bucket = mtime.replace(minute=0, second=0, microsecond=0).strftime('%m-%d %H:00')
            if bucket in agent_series:
                agent_series[bucket]['sessions'] += 1

            has_error = False
            try:
                with open(file_path, 'rb') as fh:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    fh.seek(max(0, size - 65536), os.SEEK_SET)
                    sample = fh.read().decode('utf-8', errors='ignore').lower()
                    if any(marker in sample for marker in ('error', 'failed', 'iserror=true')):
                        has_error = True
            except OSError as exc:
                errors.append(f'read failed: {file_path}: {exc}')

            if has_error:
                errors_24h += 1
                if bucket in agent_series:
                    agent_series[bucket]['errors'] += 1

        error_rate = round((errors_24h / sessions_24h) * 100, 2) if sessions_24h else 0.0
        agent_rank.append({
            'agent': agent,
            'sessions_24h': sessions_24h,
            'errors_24h': errors_24h,
            'error_rate': error_rate,
            'last_active_at': latest_mtime.isoformat() if latest_mtime else None,
            'latest_session_id': latest_session_id,
        })

try:
    proc = subprocess.run(
        ['journalctl', '-u', 'openclaw-gateway.service', '--since', f'{WINDOW_HOURS} hours ago', '--no-pager'],
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            lower = line.lower()
            if 'subagent' not in lower and 'agent/embedded' not in lower and 'subagent-registry' not in lower:
                continue

            name = 'unknown'
            if 'agent/embedded' in lower:
                name = 'embedded'
            elif 'subagent-registry' in lower:
                name = 'registry'
            else:
                match = re.search(r'subagent[^a-z0-9_-]*([a-z0-9_-]+)', lower)
                if match:
                    name = match.group(1)

            stat = subagent_stats[name]
            stat['calls_24h'] += 1
            if any(marker in lower for marker in ('error', 'failed', 'iserror=true')):
                stat['errors_24h'] += 1

            ts = line[:15].strip() if len(line) >= 15 else line
            stat['last_seen_at'] = ts
    else:
        errors.append(f'journalctl failed: rc={proc.returncode} stderr={proc.stderr.strip()}')
except Exception as exc:
    errors.append(f'journalctl error: {exc}')

agent_timeseries = []
for key in hour_keys:
    agent_timeseries.append({
        'hour': key,
        'sessions': agent_series[key]['sessions'],
        'errors': agent_series[key]['errors'],
    })

agent_rank.sort(key=lambda item: (-item['sessions_24h'], item['agent']))
subagent_rank = sorted(
    [
        {
            'subagent': k,
            'calls_24h': v['calls_24h'],
            'errors_24h': v['errors_24h'],
            'last_seen_at': v['last_seen_at'],
        }
        for k, v in subagent_stats.items()
    ],
    key=lambda item: (-item['calls_24h'], item['subagent'])
)

print(json.dumps({
    'window_hours': WINDOW_HOURS,
    'generated_at': NOW.isoformat(),
    'agent_timeseries': agent_timeseries,
    'agent_rank': agent_rank,
    'subagent_rank': subagent_rank,
    'errors': errors,
}))
PY"""
    return script.replace("__WINDOW_HOURS__", str(int(window_hours)))


def parse_runtime_payload(raw_json: str, server: ServerConfig, window_hours: int) -> AgentRuntimeStatus:
    payload = json.loads(raw_json)
    return AgentRuntimeStatus(
        server_name=server.name,
        ssh_host=server.ssh_host,
        generated_at=payload.get("generated_at", datetime.now(timezone.utc).isoformat()),
        window_hours=int(payload.get("window_hours", window_hours)),
        agent_timeseries=payload.get("agent_timeseries", []),
        agent_rank=payload.get("agent_rank", []),
        subagent_rank=payload.get("subagent_rank", []),
        errors=payload.get("errors", []),
    )


def collect_server_agent_runtime(
    runner: SSHRunner,
    server: ServerConfig,
    window_hours: int = 24,
) -> AgentRuntimeStatus:
    now = datetime.now(timezone.utc).isoformat()
    ping = runner.run_ssh(server.ssh_host, "echo ok", timeout=10)
    if ping.returncode != 0 or ping.stdout.strip() != "ok":
        return AgentRuntimeStatus(
            server_name=server.name,
            ssh_host=server.ssh_host,
            generated_at=now,
            window_hours=window_hours,
            agent_timeseries=[],
            agent_rank=[],
            subagent_rank=[],
            errors=[ping.stderr.strip() or "SSH not reachable"],
        )

    result = runner.run_ssh(server.ssh_host, _remote_runtime_command(window_hours), timeout=120)
    if result.returncode != 0:
        return AgentRuntimeStatus(
            server_name=server.name,
            ssh_host=server.ssh_host,
            generated_at=now,
            window_hours=window_hours,
            agent_timeseries=[],
            agent_rank=[],
            subagent_rank=[],
            errors=[result.stderr.strip() or "runtime collector failed"],
        )

    try:
        return parse_runtime_payload(result.stdout, server, window_hours)
    except Exception as exc:
        return AgentRuntimeStatus(
            server_name=server.name,
            ssh_host=server.ssh_host,
            generated_at=now,
            window_hours=window_hours,
            agent_timeseries=[],
            agent_rank=[],
            subagent_rank=[],
            errors=[f"parse runtime payload failed: {exc}"],
        )


def collect_agent_runtime_all(config: AppConfig, runner: SSHRunner, window_hours: int = 24) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    if not config.servers:
        return output

    with ThreadPoolExecutor(max_workers=len(config.servers)) as pool:
        futures = {
            pool.submit(collect_server_agent_runtime, runner, server, window_hours): server
            for server in config.servers
        }
        for future in as_completed(futures):
            server = futures[future]
            try:
                status = future.result()
            except Exception as exc:
                output[server.name] = {
                    "server_name": server.name,
                    "ssh_host": server.ssh_host,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "window_hours": window_hours,
                    "agent_timeseries": [],
                    "agent_rank": [],
                    "subagent_rank": [],
                    "errors": [f"future failed: {exc}"],
                }
                continue

            output[server.name] = {
                "server_name": status.server_name,
                "ssh_host": status.ssh_host,
                "generated_at": status.generated_at,
                "window_hours": status.window_hours,
                "agent_timeseries": status.agent_timeseries,
                "agent_rank": status.agent_rank,
                "subagent_rank": status.subagent_rank,
                "errors": status.errors,
            }

    return output


def summarize_timeseries(series: list[dict[str, Any]]) -> tuple[int, int]:
    sessions = sum(int(item.get("sessions", 0)) for item in series)
    errors = sum(int(item.get("errors", 0)) for item in series)
    return sessions, errors


def normalize_subagent_name(line: str) -> str:
    lower = line.lower()
    if "agent/embedded" in lower:
        return "embedded"
    if "subagent-registry" in lower:
        return "registry"
    if "subagent" in lower:
        import re

        match = re.search(r"subagent[^a-z0-9_-]*([a-z0-9_-]+)", lower)
        if match:
            return match.group(1)
    return "unknown"


def is_error_line(line: str) -> bool:
    lower = line.lower()
    return any(marker in lower for marker in ERROR_MARKERS)
