from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.config import AppConfig
from app.fleet_aggregator import parse_disk_usage_percent, parse_runtime_summary

DEFAULT_RULES: list[dict[str, Any]] = [
    {"name": "gateway-inactive", "type": "gateway_inactive", "severity": "critical"},
    {"name": "unreachable-node", "type": "unreachable", "severity": "critical"},
    {"name": "disk-high-usage", "type": "disk_usage_percent", "threshold": 85, "severity": "warning"},
    {"name": "agent-error-rate", "type": "agent_error_rate", "threshold": 30, "severity": "warning"},
]

RULE_TYPES = {"gateway_inactive", "unreachable", "disk_usage_percent", "agent_error_rate"}
SEVERITIES = {"critical", "warning", "info"}


def _coerce_rules(config: AppConfig) -> list[dict[str, Any]]:
    if config.alerts.rules:
        return [dict(rule) for rule in config.alerts.rules if isinstance(rule, dict)]
    return [dict(rule) for rule in DEFAULT_RULES]


def _rule_targets(rule: dict[str, Any], server_name: str) -> bool:
    targets = rule.get("target_servers")
    if targets is None:
        return True
    if not isinstance(targets, list):
        return False
    return server_name in targets


def _event_id(server_name: str, rule_name: str, message: str) -> str:
    value = f"{server_name}|{rule_name}|{message}".encode("utf-8")
    return hashlib.sha1(value).hexdigest()[:16]


def evaluate_alerts(config: AppConfig, status_cache: dict[str, Any], runtime_cache: dict[str, Any]) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    status_servers = status_cache.get("servers", {}) if isinstance(status_cache, dict) else {}
    runtime_servers = runtime_cache.get("servers", {}) if isinstance(runtime_cache, dict) else {}
    rules = _coerce_rules(config)
    events: list[dict[str, Any]] = []

    for server in config.servers:
        if not server.enabled:
            continue
        status_entry = status_servers.get(server.name, {}) if isinstance(status_servers, dict) else {}
        details = status_entry.get("details", {}) if isinstance(status_entry, dict) else {}
        runtime_entry = runtime_servers.get(server.name, {}) if isinstance(runtime_servers, dict) else {}
        sessions_24h, errors_24h, error_rate_24h = parse_runtime_summary(runtime_entry)

        for rule in rules:
            if not _rule_targets(rule, server.name):
                continue
            rule_name = str(rule.get("name", rule.get("type", "unnamed-rule")))
            rule_type = str(rule.get("type", ""))
            severity = str(rule.get("severity", "warning"))
            if rule_type not in RULE_TYPES or severity not in SEVERITIES:
                continue

            matched = False
            message = ""
            value: float | int | None = None
            threshold = rule.get("threshold")

            if rule_type == "unreachable":
                matched = not bool(status_entry.get("reachable"))
                if matched:
                    message = "SSH unreachable"
            elif rule_type == "gateway_inactive":
                matched = bool(status_entry.get("reachable")) and details.get("gateway_status") != "active"
                if matched:
                    message = f"Gateway status is {details.get('gateway_status', 'unknown')}"
            elif rule_type == "disk_usage_percent":
                limit = int(threshold if threshold is not None else 85)
                highest = None
                for key, raw in details.items():
                    if not key.startswith("disk_"):
                        continue
                    usage = parse_disk_usage_percent(str(raw))
                    if usage is None:
                        continue
                    if highest is None or usage > highest:
                        highest = usage
                if highest is not None:
                    value = highest
                    matched = highest >= limit
                    if matched:
                        message = f"Disk usage high: {highest}% >= {limit}%"
                threshold = limit
            elif rule_type == "agent_error_rate":
                limit = float(threshold if threshold is not None else 30)
                value = error_rate_24h
                matched = sessions_24h > 0 and error_rate_24h >= limit
                if matched:
                    message = f"Agent error rate high: {error_rate_24h}% >= {limit}%"
                threshold = limit

            if matched:
                events.append(
                    {
                        "id": _event_id(server.name, rule_name, message),
                        "server": server.name,
                        "rule_name": rule_name,
                        "rule_type": rule_type,
                        "severity": severity,
                        "message": message,
                        "observed_at": generated_at,
                        "value": value,
                        "threshold": threshold,
                    }
                )

    summary = {
        "total": len(events),
        "critical": len([row for row in events if row["severity"] == "critical"]),
        "warning": len([row for row in events if row["severity"] == "warning"]),
        "info": len([row for row in events if row["severity"] == "info"]),
        "by_server": {},
    }
    by_server: dict[str, int] = {}
    for event in events:
        by_server[event["server"]] = by_server.get(event["server"], 0) + 1
    summary["by_server"] = by_server
    return {"generated_at": generated_at, "rules": rules, "events": events, "summary": summary}


def validate_alert_rules(rules: Any, server_names: list[str]) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    if not isinstance(rules, list):
        return {"ok": False, "errors": ["rules must be a list"], "normalized_rules": []}

    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"rules[{index}] must be object")
            continue
        rule_name = str(rule.get("name", "")).strip()
        rule_type = str(rule.get("type", "")).strip()
        severity = str(rule.get("severity", "warning")).strip()
        if not rule_name:
            errors.append(f"rules[{index}].name is required")
        if rule_type not in RULE_TYPES:
            errors.append(f"rules[{index}].type unsupported: {rule_type}")
        if severity not in SEVERITIES:
            errors.append(f"rules[{index}].severity unsupported: {severity}")
        targets = rule.get("target_servers")
        if targets is not None:
            if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
                errors.append(f"rules[{index}].target_servers must be list[string]")
            else:
                unknown = [item for item in targets if item not in server_names]
                if unknown:
                    errors.append(f"rules[{index}].target_servers unknown: {','.join(unknown)}")
        threshold = rule.get("threshold")
        if threshold is not None and not isinstance(threshold, (int, float)):
            errors.append(f"rules[{index}].threshold must be number")
        normalized.append(
            {
                "name": rule_name,
                "type": rule_type,
                "severity": severity,
                "threshold": threshold,
                "target_servers": targets,
            }
        )
    return {"ok": len(errors) == 0, "errors": errors, "normalized_rules": normalized}

