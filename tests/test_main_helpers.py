import pytest

from app.config import AppConfig, ServerConfig, SyncConfig
from app.main import _normalize_copy_skill_names, _resolve_sync_servers


def _config(servers: list[ServerConfig]) -> AppConfig:
    return AppConfig(
        poll_interval_seconds=5,
        servers=servers,
        sync=SyncConfig(),
    )


def test_normalize_copy_skill_names_prefers_list() -> None:
    result = _normalize_copy_skill_names(
        skill_name="legacy",
        skill_names=["one", "", "two"],
    )
    assert result == ["one", "two"]


def test_normalize_copy_skill_names_invalid() -> None:
    with pytest.raises(ValueError, match="skill_names must be non-empty list"):
        _normalize_copy_skill_names(skill_name="", skill_names=[])


def test_resolve_sync_servers_requires_explicit_when_more_than_two() -> None:
    config = _config(
        [
            ServerConfig(name="s1", ssh_host="root@203.0.113.10"),
            ServerConfig(name="s2", ssh_host="root@203.0.113.11"),
            ServerConfig(name="s3", ssh_host="root@203.0.113.12"),
        ]
    )
    with pytest.raises(ValueError, match="required when servers > 2"):
        _resolve_sync_servers(config, mode="one_way", source_server_input=None, target_server_input=None)


def test_resolve_sync_servers_supports_lookup_by_name() -> None:
    config = _config(
        [
            ServerConfig(name="s1", ssh_host="root@203.0.113.10"),
            ServerConfig(name="s2", ssh_host="root@203.0.113.11"),
        ]
    )
    src, dst = _resolve_sync_servers(config, mode="one_way", source_server_input="s2", target_server_input="s1")
    assert src.name == "s2"
    assert dst.name == "s1"
