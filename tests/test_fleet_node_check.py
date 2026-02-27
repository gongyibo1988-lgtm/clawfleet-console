from app.config import AppConfig, ServerConfig, SyncConfig
from app.fleet_aggregator import run_node_check
from app.ssh_runner import CommandResult


class FakeRunner:
    def run_ssh(self, host: str, remote_command: str, timeout: int = 30) -> CommandResult:
        _ = timeout
        if "echo ok" in remote_command:
            return CommandResult(returncode=0, stdout="ok\n", stderr="")
        return CommandResult(returncode=0, stdout="ok\n", stderr="")


def _config() -> AppConfig:
    return AppConfig(
        poll_interval_seconds=5,
        servers=[ServerConfig(name="server-a", ssh_host="<SSH_USER>@203.0.113.10")],
        sync=SyncConfig(),
    )


def test_run_node_check_returns_score_and_checks() -> None:
    payload = run_node_check(_config(), FakeRunner(), "server-a")
    assert payload["server_name"] == "server-a"
    assert payload["score"] == 100
    assert len(payload["checks"]) >= 5
