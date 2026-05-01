# Enterprise Football Analytics Engine

End-to-end football data pipeline built on the complete StatsBomb open dataset (3,000+ matches across 30+ competitions). Designed to run on a memory-constrained VPS with production-grade partitioning, incremental ingestion, ML modelling, and automated report delivery.

![CI](https://github.com/YOUR_USERNAME/enterprise_football_engine/actions/workflows/deploy.yml/badge.svg)

---

## Architecture

```
StatsBomb API
      │
      ▼
massive_ingestion.py  ──► PostgreSQL 15 (PostGIS, Partitioned)
      │                        │
      │               xt_model.py  ──► xt_surface table
      │                        │
      │                  dbt run/test
      │                        │
      │              analytics_marts schema
      │                        │
      ├── tactical_models.py   │      (K-Means, press detection)
      ├── predictive_models.py │      (XGBoost xP)
      │                        ▼
      │                  Apache Superset
      │
      └── matchday_push DAG
              │
              ├── report_generator.py  ──► PDF (mplsoccer)
              └── xml_generator.py     ──► SportsCode XML
                        │
                        └── EmailOperator ──► Analyst inbox
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| Infrastructure | Docker Compose v2, .env secrets |
| Database | PostgreSQL 15 + PostGIS 3.4, LIST partitioning |
| Transformation | dbt-postgres 1.7 |
| Ingestion | Python 3.10, statsbombpy, SQLAlchemy bulk insert |
| Orchestration | Apache Airflow 2.8.1 (3 modular DAGs) |
| ML — Tactical | scikit-learn K-Means (set-piece clustering) |
| ML — Predictive | XGBoost (Expected Pass — xP model) |
| Visualisation | Apache Superset 3.1, mplsoccer, matplotlib |
| Reporting | Multi-page PDF (PdfPages), SportsCode XML |
| CI/CD | GitHub Actions → EC2 via SSH |

---

## Models

### xT (Expected Threat)
Value-iteration solution on a 16x12 pitch grid. Per-event delta computed as `xT[end_cell] - xT[start_cell]` for passes and carries; `xT[start_cell]` for shots. Surface stored in `xt_surface` for Superset heatmap.

### xP (Expected Pass — XGBoost)
Binary classifier trained on all passes in the dataset.

Features: `start_x`, `start_y`, `end_x`, `end_y`, `distance`, `angle_to_goal`, `under_pressure`, `minute_bin`

Target: 1 = successful pass (StatsBomb: `pass_outcome IS NULL`)

Typical performance: AUC ~0.74, log-loss ~0.48

### Set-piece clustering (K-Means, k=6)
Clusters corner and free-kick delivery origins. Centroids stored in `set_piece_clusters` for Superset scatter.

### Press trigger detection
Identifies Ball Recovery events followed by 3+ defensive actions within a 5-second window from the same team.

---

## Database Schema

```
dim_competitions   dim_seasons   dim_matches
                                      │
                        dim_players──┤
                        dim_teams  ──┤
                                     │
                               fact_events   (PARTITION BY LIST competition_id)
                                │       │
                          xt_surface   model_registry
                                │
                        set_piece_clusters
                                │
                     analytics_marts.mart_player_metrics
                     analytics_marts.mart_team_summary
                     analytics_marts.mart_match_summary
                     analytics_marts.mart_competition_leaderboard
```

---

## Airflow DAGs

| DAG | Schedule | Description |
|---|---|---|
| `ingestion_pipeline` | Daily 02:00 UTC | Incremental ingest → dbt → xT → Superset |
| `ml_training` | Weekly Sunday 03:00 UTC | Tactical clustering + XGBoost xP training |
| `matchday_push` | Manual trigger | PDF + XML generation + email delivery |

---

## Setup

### Prerequisites
- Docker Desktop (or Docker Engine on Linux)
- 4 GB RAM minimum (8 GB recommended for full dataset)
- Python 3.10+ (for local test runs)

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/enterprise_football_engine.git
cd enterprise_football_engine
cp .env.example .env
# Edit .env with your credentials
```

### 2. Generate Airflow Fernet key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Paste output into AIRFLOW_FERNET_KEY in .env
```

### 3. Start services

```bash
docker compose up -d
# Wait ~60s for all services to initialise
```

### 4. Bootstrap Superset

```bash
docker compose exec airflow-webserver \
  python /opt/airflow/scripts/superset_init.py
```

### 5. Trigger ingestion

Via Airflow UI at `http://localhost:8080` (admin/your_password):
- Enable and trigger `ingestion_pipeline`

Or via CLI:
```bash
docker compose exec airflow-scheduler \
  airflow dags trigger ingestion_pipeline
```

### 6. Generate a match report

```bash
docker compose exec airflow-scheduler \
  airflow dags trigger matchday_push --conf '{"match_id": 3869685}'
```

---

## CI/CD (GitHub Actions → EC2)

Set the following repository secrets:

| Secret | Description |
|---|---|
| `EC2_HOST` | Public IP of your EC2 instance |
| `EC2_USER` | SSH username (e.g. `ubuntu`) |
| `EC2_SSH_KEY` | Private key contents |

On every push to `main`: lint → test → SSH deploy.

---

## Running Tests Locally

```bash
pip install -r requirements.txt pytest pytest-cov

# Requires a local PostgreSQL instance with the schema applied
export POSTGRES_USER=analytics
export POSTGRES_PASSWORD=analytics_test
export POSTGRES_DB=football_db_test
export POSTGRES_HOST=localhost

pytest tests/ -v --cov=scripts
```

---

## Data Source

[StatsBomb Open Data](https://github.com/statsbomb/open-data) — used under the StatsBomb Open Data Licence. This project is not affiliated with StatsBomb.

---

## Project Structure

```
enterprise_football_engine/
├── .github/workflows/deploy.yml
├── dags/
│   ├── ingestion_dag.py
│   ├── ml_dag.py
│   └── matchday_dag.py
├── dbt_project/
│   ├── models/staging/
│   └── models/marts/
├── scripts/
│   ├── init/
│   ├── massive_ingestion.py
│   ├── xt_model.py
│   ├── tactical_models.py
│   ├── predictive_models.py
│   ├── report_generator.py
│   ├── xml_generator.py
│   └── superset_init.py
├── config/superset_config.py
├── tests/
├── docker-compose.yml
├── requirements.txt
└── .env.example
```
