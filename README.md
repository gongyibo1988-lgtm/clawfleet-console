# OpenClaw Tencent Console

Local web console for Tencent Lighthouse multi-server operations:

- Dashboard for SSH reachability, system summary, and OpenClaw gateway status
- Agent/Subagent runtime analytics (24h trends + rank tables)
- One-click maintenance actions (`更新` / `备份`) across all servers
- Skills page to list installed skills per server, search Top 5 market candidates before install, and copy selected skills between servers
- Cron page to inspect root/system scheduled jobs, 24h+7d execution summary, date-collapsed logs, and open output text files in TextEdit
- Sync plan and execution (choose any source/target pair, or bidirectional with conflict decisions)
- One-click SSH terminal launch on macOS Terminal

## Quick Start

```bash
cd 2026-02-26-openclaw-tencent-console
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
python -m app.main --host 127.0.0.1 --port 8088
```

Open `http://127.0.0.1:8088`.

## Config

Edit `config.yaml`.

- `servers`: one or more SSH hosts with display names
- `sync.roots`: roots to sync (defaults to `/root/files`, `/root/.openclaw/workspace`)
- `sync.excludes`: glob exclusions for secret/sensitive files
- `sync.allow_delete`: default delete policy
- `sync.ssh_key_path`: local key path for `ssh` / `rsync`

## Recommended `~/.ssh/config` (redacted template)

```sshconfig
Host claw-a
  HostName <SERVER_A_PUBLIC_IP>
  User <SSH_USER>
  Port <SSH_PORT>
  IdentityFile /ABS/PATH/TO/YOUR/SSH_PRIVATE_KEY
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
  ServerAliveInterval 10
  ServerAliveCountMax 2

Host claw-b
  HostName <SERVER_B_PUBLIC_IP>
  User <SSH_USER>
  Port <SSH_PORT>
  IdentityFile /ABS/PATH/TO/YOUR/SSH_PRIVATE_KEY
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
  ServerAliveInterval 10
  ServerAliveCountMax 2
```

## Test

```bash
cd 2026-02-26-openclaw-tencent-console
python3 -m pytest -q
```

## Versioning

- This project uses SemVer (`MAJOR.MINOR.PATCH`).
- Current version is stored in `VERSION`.
- Runtime version API: `GET /api/version`.
- Release notes are tracked in `CHANGELOG.md`.

## Open Source

- Recommended project name: `ClawFleet Console`
- See `OPEN_SOURCE_RELEASE.md` for publish checklist and commands.
