from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class SSHRunner:
    def __init__(self, ssh_key_path: str | None = None):
        self.ssh_key_path = ssh_key_path

    def ssh_options(self) -> list[str]:
        options = [
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ServerAliveInterval=10",
            "-o",
            "ServerAliveCountMax=2",
            "-o",
            "ConnectTimeout=10",
        ]
        if self.ssh_key_path:
            options.extend(["-i", self.ssh_key_path])
        return options

    def run_ssh(self, host: str, remote_command: str, timeout: int = 30) -> CommandResult:
        cmd = ["ssh", *self.ssh_options(), host, remote_command]
        try:
            completed = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
            return CommandResult(
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(returncode=124, stdout="", stderr=f"ssh timeout after {timeout}s")
        except FileNotFoundError:
            return CommandResult(returncode=127, stdout="", stderr="ssh binary not found")

    def run_local(self, command: list[str], timeout: int = 60) -> CommandResult:
        try:
            completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
            return CommandResult(
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(returncode=124, stdout="", stderr=f"command timeout after {timeout}s")
        except FileNotFoundError:
            binary = command[0] if command else "command"
            return CommandResult(returncode=127, stdout="", stderr=f"{binary} not found")
