import pytest

from app.config import ConfigError, _validate


def _base_config() -> dict:
    return {
        "poll_interval_seconds": 5,
        "servers": [
            {"name": "server-1", "ssh_host": "<SSH_USER>@203.0.113.10"},
            {"name": "server-2", "ssh_host": "<SSH_USER>@203.0.113.11"},
        ],
        "sync": {
            "roots": ["/root/files"],
            "excludes": ["**/.env"],
            "allow_delete": False,
            "ssh_key_path": None,
        },
    }


def test_validate_rejects_duplicate_server_names() -> None:
    payload = _base_config()
    payload["servers"][1]["name"] = payload["servers"][0]["name"]
    with pytest.raises(ConfigError, match="servers\\[\\]\\.name must be unique"):
        _validate(payload)


def test_validate_rejects_duplicate_server_hosts() -> None:
    payload = _base_config()
    payload["servers"][1]["ssh_host"] = payload["servers"][0]["ssh_host"]
    with pytest.raises(ConfigError, match="servers\\[\\]\\.ssh_host must be unique"):
        _validate(payload)
