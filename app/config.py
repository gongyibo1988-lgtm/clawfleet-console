from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    pass


@dataclass
class ServerConfig:
    name: str
    ssh_host: str
    gateway_port: int = 18789
    service_name: str = "openclaw-gateway.service"
    type: str = "cloud"
    labels: list[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class SyncConfig:
    roots: list[str] = field(default_factory=lambda: ["/root/files", "/root/.openclaw/workspace"])
    excludes: list[str] = field(
        default_factory=lambda: [
            "**/.env",
            "**/credentials/**",
            "**/openclaw.json",
            "**/auth-profiles.json",
            "**/.codex/**",
        ]
    )
    allow_delete: bool = False
    ssh_key_path: str | None = "/ABS/PATH/TO/YOUR/SSH_PRIVATE_KEY"


@dataclass
class AlertsConfig:
    rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SecurityConfig:
    enable_auth: bool = True
    username: str = "admin"
    password: str = "change-me"
    session_ttl_seconds: int = 12 * 60 * 60
    operation_confirm_code: str = "CHANGE_ME_CONFIRM_CODE"
    confirm_ttl_seconds: int = 120
    prefer_macos_biometric: bool = True


@dataclass
class AppConfig:
    poll_interval_seconds: int
    servers: list[ServerConfig]
    sync: SyncConfig
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_CONFIG = AppConfig(
    poll_interval_seconds=5,
    servers=[
        ServerConfig(name="server-1", ssh_host="<SSH_USER>@203.0.113.10", gateway_port=18789),
        ServerConfig(name="server-2", ssh_host="<SSH_USER>@203.0.113.11", gateway_port=18789),
    ],
    sync=SyncConfig(),
)


def _merge_dict(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _to_server(item: Any) -> ServerConfig:
    if not isinstance(item, dict):
        raise ConfigError("servers entries must be objects")
    try:
        return ServerConfig(
            name=str(item["name"]),
            ssh_host=str(item["ssh_host"]),
            gateway_port=int(item.get("gateway_port", 18789)),
            service_name=str(item.get("service_name", "openclaw-gateway.service")),
            type=str(item.get("type", "cloud")),
            labels=[str(label) for label in item.get("labels", [])] if isinstance(item.get("labels", []), list) else [],
            enabled=bool(item.get("enabled", True)),
        )
    except KeyError as exc:
        raise ConfigError(f"Missing server field: {exc}") from exc


def _to_sync(item: Any) -> SyncConfig:
    if not isinstance(item, dict):
        raise ConfigError("sync must be an object")
    roots = item.get("roots", ["/root/files", "/root/.openclaw/workspace"])
    excludes = item.get("excludes", SyncConfig().excludes)
    if not isinstance(roots, list) or not all(isinstance(row, str) for row in roots):
        raise ConfigError("sync.roots must be a list of strings")
    if not isinstance(excludes, list) or not all(isinstance(row, str) for row in excludes):
        raise ConfigError("sync.excludes must be a list of strings")

    return SyncConfig(
        roots=roots,
        excludes=excludes,
        allow_delete=bool(item.get("allow_delete", False)),
        ssh_key_path=str(item["ssh_key_path"]) if item.get("ssh_key_path") else None,
    )


def _to_alerts(item: Any) -> AlertsConfig:
    if not isinstance(item, dict):
        raise ConfigError("alerts must be an object")
    rules = item.get("rules", [])
    if not isinstance(rules, list):
        raise ConfigError("alerts.rules must be a list")
    normalized_rules: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise ConfigError("alerts.rules entries must be objects")
        normalized_rules.append(dict(rule))
    return AlertsConfig(rules=normalized_rules)


def _to_security(item: Any) -> SecurityConfig:
    if not isinstance(item, dict):
        raise ConfigError("security must be an object")
    return SecurityConfig(
        enable_auth=bool(item.get("enable_auth", True)),
        username=str(item.get("username", "admin")),
        password=str(item.get("password", "change-me")),
        session_ttl_seconds=int(item.get("session_ttl_seconds", 12 * 60 * 60)),
        operation_confirm_code=str(item.get("operation_confirm_code", "CHANGE_ME_CONFIRM_CODE")),
        confirm_ttl_seconds=int(item.get("confirm_ttl_seconds", 120)),
        prefer_macos_biometric=bool(item.get("prefer_macos_biometric", True)),
    )


def _validate(merged: dict[str, Any]) -> AppConfig:
    servers_raw = merged.get("servers")
    if not isinstance(servers_raw, list) or not servers_raw:
        raise ConfigError("servers must be a non-empty list")
    servers = [_to_server(item) for item in servers_raw]
    names = [item.name for item in servers]
    hosts = [item.ssh_host for item in servers]
    if len(set(names)) != len(names):
        raise ConfigError("servers[].name must be unique")
    if len(set(hosts)) != len(hosts):
        raise ConfigError("servers[].ssh_host must be unique")
    for server in servers:
        if server.type not in {"cloud", "edge-local"}:
            raise ConfigError("servers[].type must be cloud or edge-local")
    sync = _to_sync(merged.get("sync", {}))
    alerts = _to_alerts(merged.get("alerts", {}))
    security = _to_security(merged.get("security", {}))
    poll_interval = int(merged.get("poll_interval_seconds", 5))
    if poll_interval < 1:
        raise ConfigError("poll_interval_seconds must be >= 1")
    if security.session_ttl_seconds < 60:
        raise ConfigError("security.session_ttl_seconds must be >= 60")
    if security.confirm_ttl_seconds < 10:
        raise ConfigError("security.confirm_ttl_seconds must be >= 10")
    if security.enable_auth and (not security.username.strip() or not security.password.strip()):
        raise ConfigError("security.username/password must be non-empty when auth enabled")
    if not security.operation_confirm_code.strip():
        raise ConfigError("security.operation_confirm_code must be non-empty")

    return AppConfig(
        poll_interval_seconds=poll_interval,
        servers=servers,
        sync=sync,
        alerts=alerts,
        security=security,
    )


def load_config(project_root: Path) -> AppConfig:
    config_yaml = project_root / "config.yaml"
    example_yaml = project_root / "config.example.yaml"

    source_path = config_yaml if config_yaml.exists() else example_yaml
    if not source_path.exists():
        return DEFAULT_CONFIG

    data = yaml.safe_load(source_path.read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Invalid config format in {source_path}")

    merged = _merge_dict(DEFAULT_CONFIG.to_dict(), data)
    return _validate(merged)
