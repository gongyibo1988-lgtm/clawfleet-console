# OpenClaw Tencent Console

Local web console for Tencent Lighthouse multi-server operations:

- Dashboard for SSH reachability, system summary, and OpenClaw gateway status
- Fleet page for hybrid-cloud overview (cloud + edge-local nodes)
- Agent/Subagent runtime analytics (24h trends + rank tables)
- Local alert center with rule validation + event feed
- One-click maintenance actions (`更新` / `备份`) across all servers
- Skills page to list installed skills per server, search Top 5 market candidates before install, and copy selected skills between servers
- Cron page to inspect root/system scheduled jobs, 24h+7d execution summary, date-collapsed logs, and open output text files in TextEdit
- Sync plan and execution (choose any source/target pair, or bidirectional with conflict decisions)
- One-click SSH terminal launch on macOS Terminal
- Built-in security: session auth + CSRF + high-risk action second-factor (macOS biometric preferred, fallback code)

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
- `servers[].type`: `cloud` or `edge-local`
- `servers[].labels`: node tags for grouping/filtering
- `servers[].enabled`: whether to include node in polling
- `sync.roots`: roots to sync (defaults to `/root/files`, `/root/.openclaw/workspace`)
- `sync.excludes`: glob exclusions for secret/sensitive files
- `sync.allow_delete`: default delete policy
- `sync.ssh_key_path`: local key path for `ssh` / `rsync`
- `alerts.rules`: local alert rules (gateway/disk/agent error rate/unreachable)
- `security.enable_auth`: protect all `/api/*` with login session
- `security.username` / `security.password`: console login credentials
- `security.operation_confirm_code`: second-factor code for high-risk operations
- `security.prefer_macos_biometric`: use macOS system auth dialog first on confirm

> Default login flow now uses macOS biometric auth (Touch ID/system auth dialog).  
> Keep `security.password` as emergency fallback only, and update `security.operation_confirm_code` in `config.yaml`.

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
