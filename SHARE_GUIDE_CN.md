# OpenClaw 双服务器控制台（脱敏分享说明）

这份说明用于直接转发给朋友，不包含你的私有 IP、密钥路径、账号信息。

## 1) 解压与启动

```bash
unzip 2026-02-26-openclaw-tencent-console-share.zip
cd 2026-02-26-openclaw-tencent-console
cp config.example.yaml config.yaml
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8088
```

浏览器打开：`http://127.0.0.1:8088`

## 2) 必改配置（`config.yaml`）

把以下占位符替换为你朋友自己的环境：

- `<SERVER_A_PUBLIC_IP>`、`<SERVER_B_PUBLIC_IP>`
- `<SSH_USER>`（常见为 `root`）
- `<SSH_PORT>`（常见为 `22`）
- `/ABS/PATH/TO/YOUR/SSH_PRIVATE_KEY`

默认同步目录：

- `/root/files`
- `/root/.openclaw/workspace`

## 3) 推荐 SSH 别名（可选）

在 `~/.ssh/config` 中配置两个 Host（例如 `claw-a` / `claw-b`），再把 `config.yaml` 的 `ssh_host` 改成别名即可。

## 4) 安全注意事项

- 不要把 `config.yaml`、私钥、token 提交到仓库或发给他人。
- 已默认排除敏感同步项：`.env`、`credentials`、`openclaw.json`、`auth-profiles.json`。
- 如果启用 `allow_delete`，请先在同步页做 dry-run 并确认变更。

## 5) 功能入口

- 总览：服务器状态、Gateway、Agent/Subagent 运行分析
- 同步：A↔B 同步计划、冲突处理、执行
- 技能：市场检索安装、已装技能（按安装时间/官方与自装）、跨服务器多选复制
- 定时任务：cron 列表、7 天执行摘要、按日期折叠日志、输出文件本地 TextEdit 打开
