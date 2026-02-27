from app.config import AppConfig, ServerConfig, SyncConfig
from app.maintenance_actions import run_backup
from app.ssh_runner import CommandResult


class FakeRunner:
    def run_ssh(self, host: str, remote_command: str, timeout: int = 30) -> CommandResult:
        return CommandResult(returncode=0, stdout="status=done", stderr="")


def _config() -> AppConfig:
    return AppConfig(
        poll_interval_seconds=5,
        servers=[
            ServerConfig(name="广州服务器", ssh_host="root@203.0.113.10"),
            ServerConfig(name="雅加达服务器", ssh_host="root@203.0.113.11"),
        ],
        sync=SyncConfig(),
    )


def test_run_backup_all_servers() -> None:
    payload = run_backup(_config(), FakeRunner(), server="all")  # type: ignore[arg-type]
    assert payload["action"] == "backup"
    assert len(payload["servers"]) == 2
    assert payload["servers"]["广州服务器"]["ok"] is True
