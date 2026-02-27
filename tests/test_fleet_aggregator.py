from app.config import AppConfig, ServerConfig, SyncConfig
from app.fleet_aggregator import build_fleet_overview, parse_disk_usage_percent


def _config() -> AppConfig:
    return AppConfig(
        poll_interval_seconds=5,
        servers=[
            ServerConfig(name="cloud-a", ssh_host="<SSH_USER>@203.0.113.10", type="cloud", labels=["prod", "cn"]),
            ServerConfig(name="edge-1", ssh_host="<SSH_USER>@192.168.1.20", type="edge-local", labels=["edge"]),
        ],
        sync=SyncConfig(),
    )


def test_parse_disk_usage_percent() -> None:
    assert parse_disk_usage_percent("/dev/vda1 40G 34G 6.8G 84% /") == 84
    assert parse_disk_usage_percent("none") is None


def test_build_fleet_overview_summary_and_risks() -> None:
    status_cache = {
        "servers": {
            "cloud-a": {
                "server": "cloud-a",
                "reachable": True,
                "captured_at": "2026-02-27T10:00:00+00:00",
                "details": {
                    "gateway_status": "active",
                    "gateway_port_listen": "yes",
                    "disk__root_files": "/dev/vda1 40G 35G 5G 88% /root/files",
                    "ssh_latency_ms": "12",
                },
            },
            "edge-1": {
                "server": "edge-1",
                "reachable": False,
                "captured_at": "2026-02-27T10:00:01+00:00",
                "details": {
                    "gateway_status": "inactive",
                    "gateway_port_listen": "no",
                },
                "error": "ssh timeout",
            },
        }
    }
    runtime_cache = {
        "servers": {
            "cloud-a": {
                "agent_timeseries": [
                    {"hour": "10:00", "sessions": 10, "errors": 1},
                    {"hour": "11:00", "sessions": 5, "errors": 2},
                ]
            },
            "edge-1": {"agent_timeseries": [{"hour": "10:00", "sessions": 0, "errors": 0}]},
        }
    }
    payload = build_fleet_overview(_config(), status_cache=status_cache, runtime_cache=runtime_cache)
    assert payload["summary"]["total_nodes"] == 2
    assert payload["summary"]["reachable_nodes"] == 1
    assert payload["summary"]["abnormal_nodes"] == 2
    assert payload["groups"]["by_type"]["cloud"] == 1
    assert payload["groups"]["by_type"]["edge-local"] == 1
    by_name = {node["name"]: node for node in payload["nodes"]}
    assert by_name["cloud-a"]["agent_sessions_24h"] == 15
    assert by_name["cloud-a"]["disk_risks"][0]["usage_percent"] == 88
    assert by_name["edge-1"]["risk_level"] == "critical"

