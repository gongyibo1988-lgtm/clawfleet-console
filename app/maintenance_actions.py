from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import AppConfig
from app.ssh_runner import SSHRunner


def _resolve_servers(config: AppConfig, server: str | None) -> list:
    if server in {None, "", "all"}:
        return list(config.servers)
    selected = [item for item in config.servers if item.name == server or item.ssh_host == server]
    if not selected:
        raise ValueError(f"Unknown server: {server}")
    return selected


def _update_command() -> str:
    return r"""bash -lc '
set +e
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "started_at=$NOW"
if command -v openclaw >/dev/null 2>&1; then
  openclaw update 2>&1 || openclaw self-update 2>&1 || true
  openclaw --version 2>/dev/null | head -n 1 | sed "s/^/openclaw_version=/" || true
else
  echo "openclaw update skipped: command not found"
fi

for root in /root/.codex/skills /root/.agents/skills; do
  if [ -d "$root/.git" ]; then
    (cd "$root" && git pull --ff-only 2>&1) || true
  fi
  if [ -d "$root" ]; then
    find "$root" -mindepth 1 -maxdepth 2 -type d | while read -r d; do
      if [ -d "$d/.git" ]; then
        (cd "$d" && git pull --ff-only 2>&1) || true
      fi
    done
  fi
done
echo "status=done"
'"""


def _backup_command() -> str:
    return r"""bash -lc '
set -e
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="/root/files/openclaw-backups"
mkdir -p "$BACKUP_DIR"
TARGET="$BACKUP_DIR/openclaw-backup-$TS.tgz"
tar -czf "$TARGET" /root/.openclaw /root/.codex/skills /root/.agents/skills 2>/dev/null || tar -czf "$TARGET" /root/.openclaw /root/.codex/skills 2>/dev/null || true
echo "backup_file=$TARGET"
echo "status=done"
'"""


def run_update(config: AppConfig, runner: SSHRunner, server: str | None) -> dict[str, Any]:
    output: dict[str, Any] = {"action": "update", "generated_at": datetime.now(timezone.utc).isoformat(), "servers": {}}
    for item in _resolve_servers(config, server):
        result = runner.run_ssh(item.ssh_host, _update_command(), timeout=240)
        output["servers"][item.name] = {
            "server_name": item.name,
            "ssh_host": item.ssh_host,
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    return output


def run_backup(config: AppConfig, runner: SSHRunner, server: str | None) -> dict[str, Any]:
    output: dict[str, Any] = {"action": "backup", "generated_at": datetime.now(timezone.utc).isoformat(), "servers": {}}
    for item in _resolve_servers(config, server):
        result = runner.run_ssh(item.ssh_host, _backup_command(), timeout=180)
        output["servers"][item.name] = {
            "server_name": item.name,
            "ssh_host": item.ssh_host,
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    return output
