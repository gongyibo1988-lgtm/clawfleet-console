from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.config import AppConfig, ServerConfig
from app.ssh_runner import SSHRunner


def parse_disk_usage_percent(raw: str) -> int | None:
    if not raw:
        return None
    tokens = raw.split()
    for token in tokens:
        if token.endswith("%"):
            number = token.rstrip("%")
            if number.isdigit():
                return int(number)
    return None


def parse_runtime_summary(runtime_entry: dict[str, Any] | None) -> tuple[int, int, float]:
    if not runtime_entry:
        return 0, 0, 0.0
    series = runtime_entry.get("agent_timeseries", [])
    if not isinstance(series, list):
        return 0, 0, 0.0
    sessions = sum(int(item.get("sessions", 0)) for item in series if isinstance(item, dict))
    errors = sum(int(item.get("errors", 0)) for item in series if isinstance(item, dict))
    rate = round((errors / sessions) * 100, 2) if sessions > 0 else 0.0
    return sessions, errors, rate


def _disk_risks(details: dict[str, Any], threshold: int = 85) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, value in details.items():
        if not key.startswith("disk_"):
            continue
        usage = parse_disk_usage_percent(str(value))
        if usage is None or usage < threshold:
            continue
        pseudo_path = key.replace("disk_", "").replace("_", "/")
        if not pseudo_path.startswith("/"):
            pseudo_path = "/" + pseudo_path
        rows.append(
            {
                "path": pseudo_path,
                "usage_percent": usage,
                "raw": value,
            }
        )
    return rows


def _risk_level(reasons: list[str]) -> str:
    if not reasons:
        return "ok"
    if "unreachable" in reasons:
        return "critical"
    return "warning"


def build_fleet_overview(
    config: AppConfig,
    status_cache: dict[str, Any],
    runtime_cache: dict[str, Any],
) -> dict[str, Any]:
    servers = [server for server in config.servers if server.enabled]
    status_servers = status_cache.get("servers", {}) if isinstance(status_cache, dict) else {}
    runtime_servers = runtime_cache.get("servers", {}) if isinstance(runtime_cache, dict) else {}
    generated_at = datetime.now(timezone.utc).isoformat()

    nodes: list[dict[str, Any]] = []
    by_type: dict[str, int] = {}
    by_label: dict[str, int] = {}
    reachable_count = 0
    gateway_active_count = 0
    abnormal_count = 0

    for server in servers:
        entry = status_servers.get(server.name, {}) if isinstance(status_servers, dict) else {}
        details = entry.get("details", {}) if isinstance(entry, dict) else {}
        runtime = runtime_servers.get(server.name, {}) if isinstance(runtime_servers, dict) else {}
        sessions_24h, errors_24h, error_rate_24h = parse_runtime_summary(runtime)
        disk_risks = _disk_risks(details)
        reasons: list[str] = []
        reachable = bool(entry.get("reachable"))
        if not reachable:
            reasons.append("unreachable")
        if details.get("gateway_status") != "active":
            reasons.append("gateway_inactive")
        if disk_risks:
            reasons.append("disk_high_usage")
        if error_rate_24h >= 30:
            reasons.append("agent_error_rate_high")
        if reasons:
            abnormal_count += 1
        if reachable:
            reachable_count += 1
        if details.get("gateway_status") == "active":
            gateway_active_count += 1

        by_type[server.type] = by_type.get(server.type, 0) + 1
        for label in server.labels:
            by_label[label] = by_label.get(label, 0) + 1

        nodes.append(
            {
                "name": server.name,
                "ssh_host": server.ssh_host,
                "type": server.type,
                "labels": list(server.labels),
                "enabled": server.enabled,
                "reachable": reachable,
                "captured_at": entry.get("captured_at"),
                "last_heartbeat": entry.get("captured_at"),
                "gateway_status": details.get("gateway_status", "unknown"),
                "gateway_port_listen": details.get("gateway_port_listen", "unknown"),
                "ssh_latency_ms": details.get("ssh_latency_ms"),
                "clock_offset_sec": details.get("clock_offset_sec"),
                "disk_risks": disk_risks,
                "agent_sessions_24h": sessions_24h,
                "agent_errors_24h": errors_24h,
                "agent_error_rate_24h": error_rate_24h,
                "risk_reasons": reasons,
                "risk_level": _risk_level(reasons),
                "error": entry.get("error"),
            }
        )

    total = len(servers)
    online_rate = round((reachable_count / total) * 100, 2) if total else 0.0
    gateway_rate = round((gateway_active_count / total) * 100, 2) if total else 0.0
    return {
        "generated_at": generated_at,
        "summary": {
            "total_nodes": total,
            "reachable_nodes": reachable_count,
            "online_rate": online_rate,
            "gateway_active_nodes": gateway_active_count,
            "gateway_active_rate": gateway_rate,
            "abnormal_nodes": abnormal_count,
        },
        "groups": {
            "by_type": by_type,
            "by_label": by_label,
        },
        "nodes": sorted(nodes, key=lambda item: item["name"]),
    }


def _find_server(config: AppConfig, server_name: str) -> ServerConfig:
    matched = [server for server in config.servers if server.name == server_name or server.ssh_host == server_name]
    if not matched:
        raise ValueError(f"Unknown server: {server_name}")
    return matched[0]


def _check_command(runner: SSHRunner, host: str, command: str, timeout: int = 10) -> bool:
    result = runner.run_ssh(host, command, timeout=timeout)
    return result.returncode == 0 and result.stdout.strip() == "ok"


def run_node_check(config: AppConfig, runner: SSHRunner, server_name: str) -> dict[str, Any]:
    server = _find_server(config, server_name)
    now = datetime.now(timezone.utc).isoformat()
    checks: list[dict[str, Any]] = []

    start = time.perf_counter()
    ping_result = runner.run_ssh(server.ssh_host, "echo ok", timeout=10)
    latency_ms = int((time.perf_counter() - start) * 1000)
    ssh_ok = ping_result.returncode == 0 and ping_result.stdout.strip() == "ok"
    checks.append(
        {
            "name": "ssh_connectivity",
            "ok": ssh_ok,
            "detail": ping_result.stderr.strip() if not ssh_ok else f"latency={latency_ms}ms",
            "hint": "检查 ssh_host、密钥路径与网络连通性",
        }
    )
    if not ssh_ok:
        return {
            "generated_at": now,
            "server_name": server.name,
            "ssh_host": server.ssh_host,
            "score": 0,
            "checks": checks,
            "suggestions": ["SSH 不可达，请先修复连接后重试。"],
        }

    command_checks = [
        ("command_openclaw", "command -v openclaw >/dev/null 2>&1 && echo ok"),
        ("command_rsync", "command -v rsync >/dev/null 2>&1 && echo ok"),
        ("command_systemctl", "command -v systemctl >/dev/null 2>&1 && echo ok"),
        ("command_journalctl", "command -v journalctl >/dev/null 2>&1 && echo ok"),
        ("command_ss", "command -v ss >/dev/null 2>&1 && echo ok"),
    ]
    for check_name, command in command_checks:
        ok = _check_command(runner, server.ssh_host, command)
        checks.append(
            {
                "name": check_name,
                "ok": ok,
                "detail": "ok" if ok else "missing command",
                "hint": "安装缺失工具后可提升管理能力",
            }
        )

    path_checks = [
        ("path_files_dir", "test -d /root/files && echo ok"),
        ("path_workspace_dir", "test -d /root/.openclaw/workspace && echo ok"),
        ("path_agents_dir", "test -d /root/.openclaw/agents && echo ok"),
    ]
    for check_name, command in path_checks:
        ok = _check_command(runner, server.ssh_host, command)
        checks.append(
            {
                "name": check_name,
                "ok": ok,
                "detail": "ok" if ok else "missing directory",
                "hint": "请确认 OpenClaw 目录结构是否完整",
            }
        )

    perm_ok = _check_command(
        runner,
        server.ssh_host,
        "bash -lc 'touch /tmp/.clawfleet_probe && rm -f /tmp/.clawfleet_probe && echo ok'",
    )
    checks.append(
        {
            "name": "permission_temp_write",
            "ok": perm_ok,
            "detail": "ok" if perm_ok else "write failed",
            "hint": "检查用户权限和 /tmp 写入权限",
        }
    )

    ok_count = len([item for item in checks if item["ok"]])
    score = int((ok_count / len(checks)) * 100) if checks else 0
    suggestions = [item["hint"] for item in checks if not item["ok"]][:5]
    return {
        "generated_at": now,
        "server_name": server.name,
        "ssh_host": server.ssh_host,
        "score": score,
        "checks": checks,
        "suggestions": suggestions,
    }
