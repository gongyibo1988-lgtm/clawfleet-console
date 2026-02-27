# Security Policy

## Supported Versions

Security fixes are provided for the latest published version on the default branch.

## Reporting a Vulnerability

- Please do **not** open public issues for security vulnerabilities.
- Report privately to the maintainer with:
  - affected version (`/api/version`)
  - reproduction steps
  - impact assessment
  - optional PoC

## Hardening Notes

- Never commit `config.yaml`, private keys, tokens, or `.env`.
- Keep `sync.excludes` for credential files enabled.
- Review skill sources before installation, and use the built-in pre/post install checks.
