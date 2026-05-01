"""
DAG: ingestion_pipeline
Schedule: daily at 02:00 UTC

Tasks:
  ingest     — massive_ingestion.py: incremental StatsBomb fetch + bulk insert
  dbt_run    — dbt run (staging + marts)
  dbt_test   — dbt test (schema + data quality)
  xt_model   — xT surface computation + event updates
  superset   — Superset saved-query bootstrap (idempotent)
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

DEFAULT_ARGS = {
    "owner":            "analytics",
    "retries":          2,
    "retry_delay":      timedelta(minutes=5),
    "email_on_failure": True,
    "email_on_retry":   False,
}

DBT_DIR    = "/opt/airflow/dbt_project"
DBT_CMD    = f"cd {DBT_DIR} && dbt"
SCRIPT_DIR = "/opt/airflow/scripts"


def run_ingestion():
    import sys
    sys.path.insert(0, SCRIPT_DIR)
    import massive_ingestion
    massive_ingestion.run()


def run_xt_model():
    import sys
    sys.path.insert(0, SCRIPT_DIR)
    import xt_model
    xt_model.run()


def run_superset_init():
    import sys
    sys.path.insert(0, SCRIPT_DIR)
    import superset_init
    superset_init.run()


with DAG(
    dag_id="ingestion_pipeline",
    description="Incremental StatsBomb ingestion → dbt → xT model → Superset",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2024, 1, 1),
    schedule_interval="0 2 * * *",
    catchup=False,
    max_active_runs=1,
    tags=["ingestion", "dbt", "xt"],
) as dag:

    ingest = PythonOperator(
        task_id="ingest",
        python_callable=run_ingestion,
        execution_timeout=timedelta(hours=6),
    )

    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command=f"{DBT_CMD} run --profiles-dir {DBT_DIR} --project-dir {DBT_DIR}",
        execution_timeout=timedelta(minutes=30),
    )

    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=f"{DBT_CMD} test --profiles-dir {DBT_DIR} --project-dir {DBT_DIR}",
        execution_timeout=timedelta(minutes=15),
    )

    xt_model = PythonOperator(
        task_id="xt_model",
        python_callable=run_xt_model,
        execution_timeout=timedelta(hours=2),
    )

    superset = PythonOperator(
        task_id="superset_init",
        python_callable=run_superset_init,
        execution_timeout=timedelta(minutes=5),
    )

    ingest >> dbt_run >> dbt_test >> xt_model >> superset
