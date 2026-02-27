from app.alert_engine import evaluate_alerts, validate_alert_rules
from app.config import AlertsConfig, AppConfig, ServerConfig, SyncConfig


def _config() -> AppConfig:
    return AppConfig(
        poll_interval_seconds=5,
        servers=[
            ServerConfig(name="cloud-a", ssh_host="<SSH_USER>@203.0.113.10"),
            ServerConfig(name="edge-1", ssh_host="<SSH_USER>@192.168.1.20", type="edge-local"),
        ],
        sync=SyncConfig(),
        alerts=AlertsConfig(
            rules=[
                {"name": "gw", "type": "gateway_inactive", "severity": "critical"},
                {"name": "disk", "type": "disk_usage_percent", "severity": "warning", "threshold": 80},
                {"name": "agent", "type": "agent_error_rate", "severity": "warning", "threshold": 20},
            ]
        ),
    )


def test_evaluate_alerts_matches_rules() -> None:
    status_cache = {
        "servers": {
            "cloud-a": {
                "reachable": True,
                "details": {
                    "gateway_status": "inactive",
                    "disk__root_files": "/dev/vda1 40G 35G 5G 88% /root/files",
                },
            },
            "edge-1": {
                "reachable": False,
                "details": {"gateway_status": "inactive"},
            },
        }
    }
    runtime_cache = {
        "servers": {
            "cloud-a": {"agent_timeseries": [{"hour": "10:00", "sessions": 10, "errors": 3}]},
            "edge-1": {"agent_timeseries": [{"hour": "10:00", "sessions": 0, "errors": 0}]},
        }
    }
    payload = evaluate_alerts(_config(), status_cache=status_cache, runtime_cache=runtime_cache)
    assert payload["summary"]["total"] >= 3
    assert payload["summary"]["critical"] >= 1
    assert payload["summary"]["warning"] >= 1


def test_validate_alert_rules() -> None:
    result = validate_alert_rules(
        rules=[{"name": "ok", "type": "unreachable", "severity": "critical", "target_servers": ["cloud-a"]}],
        server_names=["cloud-a", "edge-1"],
    )
    assert result["ok"] is True
    bad = validate_alert_rules(
        rules=[{"name": "", "type": "bad_type", "severity": "bad", "target_servers": ["missing"]}],
        server_names=["cloud-a"],
    )
    assert bad["ok"] is False
    assert len(bad["errors"]) >= 3

