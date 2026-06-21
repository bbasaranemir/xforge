# Changelog

All notable changes to xForge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [2.0.0] — 2026-06-22

### Added
- **Dart ingestion microservice** — multi-stage AOT binary (Shelf HTTP on :8090)
- **Adapter pattern** — `StatsBombAdapter` and `OptaAdapter` implement `DataAdapter` interface; adding a provider requires one new file, zero pipeline changes
- **Universal coordinate system** — 105×68 m canonical pitch; StatsBomb (120×80) and Opta (100×100) normalised in dbt Silver layer
- **dbt medallion architecture** — Bronze → Silver → Gold → Marts with strict spatial range tests gating ML
- **XGBoost xG model** — Platt-calibrated CalibratedClassifierCV; Brier score + expected-vs-actual sanity checks
- **xT model** — value-iteration on a 16×12 grid; surface persisted to `xt_surface` table for Superset heatmap
- **xP model** — XGBoost pass success probability, AUC 0.897; server-side cursor streaming for 5M+ row prediction
- **Prometheus + Grafana monitoring** — postgres-exporter, pipeline-overview dashboard
- **`REFRESH MATERIALIZED VIEW CONCURRENTLY`** — zero-downtime BI refresh
- **Bearer token auth middleware** — CI-safe bypass when `API_TOKEN` not set
- **Structured logging** — `stderr.writeln('[xforge] ...')` for container log collection
- **Bulk UPDATE pattern** — executemany for all ML write-back operations (xG, xT, xP)
- **Model serialisation** — joblib persistence to `data/` for xG and xP; `model_registry` table tracks runs
- **`CONTRIBUTING.md`**, **`SECURITY.md`**, **`PULL_REQUEST_TEMPLATE`**, **`dependabot.yml`**

### Changed
- `xg_model.py` and `xt_model.py` now import `get_engine()` from shared `scripts/db_utils.py`
- `docker-compose.yml` credential defaults replaced with fail-loud `?:` syntax (requires populated `.env`)
- `dart_ingestion/pubspec.yaml` dependencies pinned to exact versions for CI reproducibility
- CI workflow (`pipeline_v2.yml`) now triggers on push and PR to `main` (not only `workflow_dispatch`)

### Security
- Dart `/ingest` error responses no longer expose internal exception details
- pgAdmin, Airflow, and Grafana defaults removed — startup fails loudly if `.env` is missing

---

## [1.0.0] — 2025-01-01

### Added
- StatsBomb open-data ingestion pipeline (Python + psycopg2)
- Airflow DAG orchestration
- xT model (3 constants updated to 105×68)
- XML export for Sportscode integration
- Apache Superset BI layer
