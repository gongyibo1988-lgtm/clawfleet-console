# Contributing

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

## Quality Checks

Run before opening a PR:

```bash
python3 -m py_compile app/*.py
python -m pytest -q
```

## PR Guidelines

- Keep diffs focused and reviewable.
- Include tests for behavior changes.
- Do not include secrets or local machine paths.
- Update `CHANGELOG.md` and `VERSION` when preparing a release.
