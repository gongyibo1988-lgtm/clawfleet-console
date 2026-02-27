from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import time

from app.parsers import parse_kv_output
from app.config import AppConfig, ServerConfig
from app.ssh_runner import SSHRunner


@dataclass
class ServerStatus:
    server: str
    reachable: bool
    captured_at: str
    details: dict
    error: str | None = None


def build_remote_status_command(server: ServerConfig, sync_roots: list[str]) -> str:
    roots = " ".join(f'"{root}"' for root in sync_roots)
    cmd = rf'''bash -lc '
set +e
printf "hostname=%s\n" "$(hostname 2>/dev/null)"
printf "remote_epoch=%s\n" "$(date +%s 2>/dev/null)"
printf "uptime=%s\n" "$(uptime 2>/dev/null | tr -s " " | sed "s/^ //")"
printf "loadavg=%s\n" "$(cat /proc/loadavg 2>/dev/null | awk "{{print $1\" \"$2\" \"$3}}")"
printf "mem_total_mb=%s\n" "$(awk "/MemTotal:/ {{printf \"%.0f\", \$2/1024}}" /proc/meminfo 2>/dev/null)"
printf "mem_avail_mb=%s\n" "$(awk "/MemAvailable:/ {{printf \"%.0f\", \$2/1024}}" /proc/meminfo 2>/dev/null)"
printf "gateway_status=%s\n" "$(systemctl is-active {server.service_name} 2>/dev/null || echo unknown)"
printf "gateway_port_listen=%s\n" "$(ss -lnt 2>/dev/null | grep -q :{server.gateway_port} && echo yes || echo no)"
printf "openclaw_version=%s\n" "$(openclaw --version 2>/dev/null | head -n 1)"
printf "openclaw_health=%s\n" "$(openclaw health 2>/dev/null | head -n 1)"
printf "gateway_log_tail=%s\n" "$(journalctl -u {server.service_name} -n 20 --no-pager 2>/dev/null | tail -n 5 | tr "\n" "|" | sed "s/|$//")"
for root in {roots}; do
  value="$(df -h "$root" 2>/dev/null | tail -n 1 | tr -s " " | sed "s/^ //")"
  safe_key="$(echo "$root" | sed "s#[^a-zA-Z0-9]#_#g")"
  printf "disk_%s=%s\n" "$safe_key" "$value"
done
'
'''
    return cmd


def collect_server_status(runner: SSHRunner, server: ServerConfig, sync_roots: list[str]) -> ServerStatus:
    now = datetime.now(timezone.utc).isoformat()
    try:
        ping_start = time.perf_counter()
        ping = runner.run_ssh(server.ssh_host, "echo ok", timeout=10)
        latency_ms = int((time.perf_counter() - ping_start) * 1000)
        if ping.returncode != 0 or ping.stdout.strip() != "ok":
            return ServerStatus(
                server=server.name,
                reachable=False,
                captured_at=now,
                details={},
                error=ping.stderr.strip() or "SSH not reachable",
            )

        result = runner.run_ssh(server.ssh_host, build_remote_status_command(server, sync_roots), timeout=40)
        if result.returncode != 0:
            return ServerStatus(
                server=server.name,
                reachable=True,
                captured_at=now,
                details={},
                error=result.stderr.strip() or "Status command failed",
            )

        parsed = parse_kv_output(result.stdout)
        parsed["ssh_latency_ms"] = str(latency_ms)
        remote_epoch = parsed.get("remote_epoch")
        if remote_epoch and str(remote_epoch).isdigit():
            clock_offset = int(datetime.now(timezone.utc).timestamp()) - int(str(remote_epoch))
            parsed["clock_offset_sec"] = str(clock_offset)
        return ServerStatus(
            server=server.name,
            reachable=True,
            captured_at=now,
            details=parsed,
        )
    except Exception as exc:
        return ServerStatus(
            server=server.name,
            reachable=False,
            captured_at=now,
            details={},
            error=f"collector error: {exc}",
        )


def collect_all_status(config: AppConfig, runner: SSHRunner) -> dict[str, dict]:
    payload: dict[str, dict] = {}
    enabled_servers = [server for server in config.servers if server.enabled]
    if not enabled_servers:
        return payload
    with ThreadPoolExecutor(max_workers=len(enabled_servers)) as pool:
        futures = {
            pool.submit(collect_server_status, runner, server, config.sync.roots): server
            for server in enabled_servers
        }
        for future in as_completed(futures):
            server = futures[future]
            try:
                status = future.result()
            except Exception as exc:
                now = datetime.now(timezone.utc).isoformat()
                payload[server.name] = {
                    "server": server.name,
                    "reachable": False,
                    "captured_at": now,
                    "details": {},
                    "error": f"future error: {exc}",
                }
                continue
            payload[status.server] = {
                "server": status.server,
                "reachable": status.reachable,
                "captured_at": status.captured_at,
                "details": status.details,
                "error": status.error,
            }
    return payload
