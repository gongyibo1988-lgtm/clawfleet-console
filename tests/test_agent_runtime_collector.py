from app.agent_runtime_collector import (
    is_error_line,
    normalize_subagent_name,
    parse_runtime_payload,
    summarize_timeseries,
)
from app.config import ServerConfig


def test_summarize_timeseries() -> None:
    sessions, errors = summarize_timeseries(
        [
            {"hour": "02-26 10:00", "sessions": 3, "errors": 1},
            {"hour": "02-26 11:00", "sessions": 2, "errors": 0},
        ]
    )
    assert sessions == 5
    assert errors == 1


def test_normalize_subagent_name() -> None:
    assert normalize_subagent_name("foo agent/embedded bar") == "embedded"
    assert normalize_subagent_name("subagent-registry loaded") == "registry"
    assert normalize_subagent_name("subagent worker-1 started") == "worker-1"
    assert normalize_subagent_name("plain log line") == "unknown"


def test_is_error_line() -> None:
    assert is_error_line("isError=true in payload")
    assert is_error_line("task FAILED with code 1")
    assert not is_error_line("all healthy")


def test_parse_runtime_payload() -> None:
    server = ServerConfig(name="server-a", ssh_host="<SSH_USER>@203.0.113.10")
    status = parse_runtime_payload(
        '{"window_hours":24,"agent_timeseries":[{"hour":"02-26 10:00","sessions":1,"errors":0}],'
        '"agent_rank":[{"agent":"main","sessions_24h":1,"errors_24h":0,"error_rate":0.0,'
        '"last_active_at":"2026-02-26T10:00:00+00:00","latest_session_id":"abc"}],'
        '"subagent_rank":[{"subagent":"embedded","calls_24h":3,"errors_24h":1,"last_seen_at":"Feb 26 10:00"}],'
        '"errors":[]}',
        server=server,
        window_hours=24,
    )
    assert status.server_name == "server-a"
    assert status.window_hours == 24
    assert status.agent_timeseries[0]["sessions"] == 1
    assert status.agent_rank[0]["agent"] == "main"
    assert status.subagent_rank[0]["subagent"] == "embedded"
