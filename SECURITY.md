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
- Bearer tokens are compared in constant time (XOR-based `_secureEquals`) to prevent timing attacks

### Authorization / Tenancy

xForge is **single-tenant by design**. The Bearer token is the authorization boundary — any valid token holder has read/write access to all ingested data. There is no per-club or per-user row-level filtering. Multi-tenant isolation (per-club data segregation) would require adding a `club_id` column to `fact_events` and a tenant-to-token mapping table; this is a planned extension for a SaaS deployment scenario.

### Network Security
- All adapter upstream requests enforce HTTPS-only (scheme check + RFC1918 blocklist) to prevent SSRF against cloud metadata endpoints (e.g. `169.254.169.254`) and internal networks
- Request bodies are capped at 10 MB to prevent memory-exhaustion DoS
- Rate limiting: 60 requests per minute per IP via in-process sliding window (production deployments should replace with Redis-backed limiting for horizontal scaling)

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
