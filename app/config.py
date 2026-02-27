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
class AppConfig:
    poll_interval_seconds: int
    servers: list[ServerConfig]
    sync: SyncConfig

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_CONFIG = AppConfig(
    poll_interval_seconds=5,
    servers=[
        ServerConfig(name="server-1", ssh_host="root@203.0.113.10", gateway_port=18789),
        ServerConfig(name="server-2", ssh_host="root@203.0.113.11", gateway_port=18789),
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
    sync = _to_sync(merged.get("sync", {}))
    poll_interval = int(merged.get("poll_interval_seconds", 5))
    if poll_interval < 1:
        raise ConfigError("poll_interval_seconds must be >= 1")

    return AppConfig(
        poll_interval_seconds=poll_interval,
        servers=servers,
        sync=sync,
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
