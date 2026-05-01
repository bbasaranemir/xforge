# ⚽ Enterprise Football Analytics Engine

[![CI/CD](https://github.com/bbasaranemir/enterprise-football-engine/actions/workflows/deploy.yml/badge.svg)](https://github.com/bbasaranemir/enterprise-football-engine/actions)
[![Python 3.10](https://img.shields.io/badge/python-3.10-blue.svg)](https://www.python.org/downloads/release/python-3100/)
[![Airflow 2.8](https://img.shields.io/badge/airflow-2.8.1-017CEE.svg)](https://airflow.apache.org/)
[![dbt 1.7](https://img.shields.io/badge/dbt-1.7-FF694B.svg)](https://www.getdbt.com/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Production-grade football analytics pipeline that ingests **3,000+ matches** from StatsBomb open data, builds ML models (xT, xP, tactical clustering), generates PDF/XML match reports, and serves interactive dashboards — all orchestrated by Apache Airflow.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATION (Airflow)                  │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐    │
│  │ ingestion_   │  │ ml_training  │  │ matchday_push      │    │
│  │ pipeline     │  │ (weekly)     │  │ (manual trigger)   │    │
│  │ (daily)      │  │              │  │                    │    │
│  └──────┬───────┘  └──────┬───────┘  └────────┬───────────┘    │
│         │                 │                   │                 │
└─────────┼─────────────────┼───────────────────┼─────────────────┘
          │                 │                   │
          ▼                 ▼                   ▼
┌─────────────────┐  ┌───────────┐  ┌───────────────────────┐
│  StatsBomb API  │  │ ML Models │  │  Report Generation    │
│  ───────────    │  │ ─────────-│  │  ─────────────────    │
│  3000+ matches  │  │ xP (XGB)  │  │  PDF (mplsoccer)     │
│  incremental    │  │ K-Means   │  │  XML (SportsCode)    │
│  ~2.5s/match    │  │ clusters  │  │  Email delivery      │
└────────┬────────┘  └─────┬─────┘  └───────────────────────┘
         │                 │
         ▼                 ▼
┌──────────────────────────────────────┐
│          PostgreSQL 15               │
│  ┌────────────┐  ┌────────────────┐  │
│  │ dim_*      │  │ fact_events    │  │
│  │ tables     │  │ (partitioned)  │  │
│  └────────────┘  └────────────────┘  │
│  ┌────────────┐  ┌────────────────┐  │
│  │ xt_surface │  │ model_registry │  │
│  └────────────┘  └────────────────┘  │
└──────────┬───────────────────────────┘
           │
     ┌─────┴─────┐
     ▼           ▼
┌─────────┐ ┌─────────┐ ┌─────────┐
│  dbt    │ │Superset │ │Grafana  │
│  1.7    │ │  3.1    │ │  11.x   │
│ staging │ │dashbords│ │monitor  │
│  marts  │ │ 11 SQL  │ │  ops    │
└─────────┘ └─────────┘ └─────────┘
```

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Orchestration** | Apache Airflow 2.8.1 | 3 modular DAGs, LocalExecutor |
| **Database** | PostgreSQL 15 Alpine | LIST partitioning by competition |
| **Transformation** | dbt-postgres 1.7 | 4 staging + 4 mart models |
| **Ingestion** | statsbombpy + SQLAlchemy | Incremental, ~2.5s/match |
| **ML — Threat** | NumPy value iteration | xT surface (16x12 grid) |
| **ML — Predictive** | XGBoost | Expected Pass (xP), AUC ~0.74 |
| **ML — Tactical** | scikit-learn K-Means | Set-piece clustering (k=6) |
| **Visualization** | Apache Superset 3.1 | 11 saved SQL queries |
| **Monitoring** | Grafana 11 + Prometheus | Container & pipeline metrics |
| **Reporting** | matplotlib PdfPages | Multi-page PDF match reports |
| **Interchange** | Custom XML generator | SportsCode-compatible timeline |
| **CI/CD** | GitHub Actions | lint → test → deploy |
| **Infrastructure** | Docker Compose | 8 services, .env secrets |

---

## ML Models

### xT — Expected Threat
Value-iteration on a **16x12 pitch grid**. Per-event delta: `xT[end] - xT[start]` for passes/carries, `xT[start]` for shots. Surface stored in `xt_surface` for Superset heatmaps.

### xP — Expected Pass (XGBoost)
Binary classifier on all passes. Features: `start_x/y`, `end_x/y`, `distance`, `angle_to_goal`, `under_pressure`, `minute_bin`. Typical AUC ~0.74, log-loss ~0.48.

### Set-Piece Clustering (K-Means, k=6)
Clusters corner/free-kick delivery origins. Centroids in `set_piece_clusters`.

### Press Trigger Detection
Ball Recovery events followed by 3+ defensive actions within 5 seconds from the same team.

---

## Airflow DAGs

| DAG | Schedule | Pipeline |
|-----|----------|----------|
| `ingestion_pipeline` | Daily 02:00 UTC | `ingest → dbt_run → dbt_test → xt_model → superset_init` |
| `ml_training` | Weekly Sun 03:00 | `tactical_models → predictive_models` |
| `matchday_push` | Manual trigger | `ingest_match → generate_pdf → generate_xml → send_email` |

---

## Quick Start

### Prerequisites
- Docker & Docker Compose v2
- 4 GB RAM minimum (8 GB recommended)
- Git

### 1. Clone & configure

```bash
git clone https://github.com/bbasaranemir/enterprise-football-engine.git
cd enterprise-football-engine
cp .env.example .env
# Edit .env — set strong passwords and a Fernet key:
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. Launch

```bash
make up        # builds images + starts all services
make status    # check container health
```

### 3. Trigger ingestion

```bash
make ingest    # unpause + trigger ingestion_pipeline
make logs      # follow scheduler logs
```

### 4. Generate a match report

```bash
make report MATCH_ID=3869685
# Output: reports/match_3869685.pdf + reports/match_3869685_sportscode.xml
```

### 5. Access dashboards

| Service | URL | Credentials |
|---------|-----|-------------|
| Airflow | http://localhost:8080 | admin / (your .env password) |
| Superset | http://localhost:8088 | admin / admin123 |
| Grafana | http://localhost:3000 | admin / admin |
| pgAdmin | http://localhost:5050 | admin@admin.com / admin |

---

## GitHub Codespaces

This repo includes a `.devcontainer` config. Click **Code → Codespaces → New** on GitHub and all services start automatically with forwarded ports.

---

## CI/CD

Every push to `main` triggers:

```
lint (black + isort + flake8) → test (pytest + coverage) → deploy (SSH to EC2)
```

Set these repository secrets for deployment:

| Secret | Description |
|--------|-------------|
| `EC2_HOST` | EC2 public IP |
| `EC2_USER` | SSH username |
| `EC2_SSH_KEY` | Private key contents |

---

## Project Structure

```
enterprise-football-engine/
├── .devcontainer/            # Codespaces auto-setup
├── .github/workflows/        # CI/CD pipeline
├── config/
│   ├── grafana/              # Grafana dashboards & datasources
│   └── superset_config.py    # Superset settings
├── dags/
│   ├── ingestion_dag.py      # Daily ETL pipeline
│   ├── ml_dag.py             # Weekly model training
│   └── matchday_dag.py       # On-demand report generation
├── dbt_project/
│   └── models/
│       ├── staging/          # stg_events, stg_passes, stg_shots
│       └── marts/            # player_metrics, team_summary, etc.
├── scripts/
│   ├── init/                 # SQL schema + partitioning
│   ├── massive_ingestion.py  # Incremental StatsBomb loader
│   ├── xt_model.py           # Expected Threat value iteration
│   ├── predictive_models.py  # XGBoost xP trainer
│   ├── tactical_models.py    # K-Means clustering
│   ├── report_generator.py   # PDF report (mplsoccer)
│   ├── xml_generator.py      # SportsCode XML export
│   └── superset_init.py      # Saved queries bootstrap
├── tests/                    # pytest suite
├── docker-compose.yml        # 8 services
├── Dockerfile.airflow        # Custom Airflow image
├── Makefile                  # Developer shortcuts
├── requirements.txt
└── .env.example
```

---

## Database Schema

```
dim_competitions ─┐
dim_seasons ──────┤
dim_matches ──────┤
dim_players ──────┼──► fact_events (PARTITION BY LIST competition_id)
dim_teams ────────┘         │
                            ├──► xt_surface
                            ├──► model_registry
                            ├──► set_piece_clusters
                            │
                            └──► analytics_marts.*
                                  ├── mart_player_metrics
                                  ├── mart_team_summary
                                  ├── mart_match_summary
                                  └── mart_competition_leaderboard
```

---

## Data Source

[StatsBomb Open Data](https://github.com/statsbomb/open-data) — used under the StatsBomb Open Data Licence. This project is not affiliated with StatsBomb.

---

## License

MIT
