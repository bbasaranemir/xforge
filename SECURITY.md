# Security Policy

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Email **kircaali036@gmail.com** with:
- A clear description of the vulnerability
- Steps to reproduce
- Potential impact assessment

You will receive a response within 48 hours. If the issue is confirmed, a fix will be released within 14 days for critical issues, 30 days for others.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2.x     | Active    |
| 1.x     | End-of-life — no security patches |

## Security Posture

### Secrets Management
- All credentials are environment variables; no defaults exist in code
- Copy `.env.example` to `.env` and populate with strong values before `docker-compose up`
- GitHub Actions uses repository Secrets for CI credentials

### Authentication
- Dart ingestion service supports Bearer token auth via `API_TOKEN` env var
- When `API_TOKEN` is unset, auth is bypassed — intended for local dev and CI only
- Production deployments **must** set `API_TOKEN`

### Dependencies
- Python dependencies are pinned in `requirements.txt`
- Dart dependencies are pinned to exact versions in `pubspec.yaml`
- Dependabot is configured to open PRs for outdated dependencies weekly

### Running a Security Audit Locally

```bash
# Python — bandit static analysis
pip install bandit
bandit -ll -r scripts/

# Python — known CVEs
pip install pip-audit
pip-audit -r requirements.txt

# Dart — check for outdated packages
cd dart_ingestion && dart pub outdated
```
