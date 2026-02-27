# Open Source Release Plan

## Project Name (Recommended)

- **ClawFleet Console**

## Repository Name (Recommended)

- `clawfleet-console`

## One-time Local Init (already done in this folder)

```bash
git init
```

## Pre-publish Checklist

- [ ] `config.yaml` is not present in git
- [ ] no private key paths / private IPs in tracked files
- [ ] tests pass
- [ ] `VERSION` and `CHANGELOG.md` updated

## Publish Commands (after GitHub login once)

```bash
git add .
git commit -m "release: v0.3.0 open-source prep"
git branch -M main
git remote add origin <YOUR_GITHUB_REPO_URL>
git push -u origin main
```

## First Release Tag

```bash
git tag v0.3.0
git push origin v0.3.0
```
