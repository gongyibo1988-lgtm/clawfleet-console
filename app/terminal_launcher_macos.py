from __future__ import annotations

import subprocess
import shlex


def open_terminal_for_host(ssh_host: str, ssh_key_path: str | None = None) -> tuple[bool, str]:
    command = [
        "ssh",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "PreferredAuthentications=publickey",
    ]
    if ssh_key_path:
        command.extend(["-i", ssh_key_path])
    command.append(ssh_host)
    shell_command = " ".join(shlex.quote(part) for part in command)

    script = f'''
    tell application "Terminal"
      activate
      do script "{shell_command}"
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], text=True, capture_output=True)
    if result.returncode != 0:
        return False, result.stderr.strip() or "Failed to run osascript"
    return True, "ok"
