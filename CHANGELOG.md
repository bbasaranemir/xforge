# Changelog

All notable changes to xForge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [3.0.0] — 2026-06-26

### Added
- **Wyscout v3 adapter** — `dart_ingestion/lib/adapters/wyscout_adapter.dart`; file_path and URL+Bearer token support; `wyscout_100x100` coordinate system; 22 unit tests covering provider metadata, type mapping, under_pressure detection, outcome logic, and edge cases
- **`coord_normalise` macro extension** — `wyscout_100x100` WHEN branch added (`x * 105/100`, `y * 68/100`); the single macro now covers all three providers
- **`silver_pass_links` dbt model** — joins `silver_passes` × `fact_events`; extracts `raw_json→pass→recipient→id` for StatsBomb and Wyscout; NULL for Opta F24 (no recipient field in the feed)
- **`mart_pressing_metrics` dbt mart** — PPDA (Passes Per Defensive Action) per team per match; `High < 10 / Medium < 15 / Low ≥ 15` pressing intensity label; joins `silver_events` (defensive actions) with opponent passes from `silver_passes`
- **`mart_pass_network` dbt mart** — player-to-player pass pairs built on `silver_pass_links`; `HAVING count(*) ≥ 2` noise filter; from/to player names, pass_count, successful_passes, avg_pass_distance_m, pass_completion_rate
- **Player Similarity / Recruitment model** — `scripts/player_similarity.py`; StandardScaler + NearestNeighbors(cosine, brute); 12 aggregated features (shots, goals, xG, pass completion, location, pressure); `max(0, 1 − dist)` score clip to [0, 1]; ON CONFLICT DO UPDATE idempotent write-back; `model_registry` registration
- **`player_similarity_scores` table** — added to `02_v2_migration.sql`; `PRIMARY KEY (player_id, similar_player_id)` with `(player_id, rank)` composite index
- **16 player similarity unit tests** — `tests/test_player_similarity.py`; covers `build_similarity_model`, `compute_similarity_rows`, feature column integrity, NaN handling
- **CI step** — `Compute Player Similarity model` added to `pipeline_v2.yml` after xP model (reads xg_value; writes player_similarity_scores)

### Changed
- `bronze/schema.yml` and `staging/schema.yml` — `wyscout` and `wyscout_100x100` added to `accepted_values` tests
- `dart_ingestion/lib/main.dart` — `case 'wyscout': adapter = WyscoutAdapter()` branch added to provider switch
- Overall test coverage raised from 58% to 59% (floor: 50%)

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
