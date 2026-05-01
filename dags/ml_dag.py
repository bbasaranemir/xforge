"""
DAG: ml_training
Schedule: weekly on Sunday at 03:00 UTC

Tasks:
  tactical   — set-piece clustering (K-Means) + press trigger detection
  predictive — XGBoost xP model training + prediction write-back
  dbt_run    — refresh marts that include xP values
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

DEFAULT_ARGS = {
    "owner":            "analytics",
    "retries":          1,
    "retry_delay":      timedelta(minutes=10),
    "email_on_failure": True,
    "email_on_retry":   False,
}

SCRIPT_DIR = "/opt/airflow/scripts"
DBT_DIR    = "/opt/airflow/dbt_project"
DBT_CMD    = f"cd {DBT_DIR} && dbt"


def run_tactical():
    import sys
    sys.path.insert(0, SCRIPT_DIR)
    import tactical_models
    tactical_models.run()


def run_predictive():
    import sys
    sys.path.insert(0, SCRIPT_DIR)
    import predictive_models
    predictive_models.run()


with DAG(
    dag_id="ml_training",
    description="Weekly tactical clustering + XGBoost xP model training",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2024, 1, 1),
    schedule_interval="0 3 * * 0",
    catchup=False,
    max_active_runs=1,
    tags=["ml", "xgboost", "tactical"],
) as dag:

    tactical = PythonOperator(
        task_id="tactical_models",
        python_callable=run_tactical,
        execution_timeout=timedelta(hours=1),
    )

    predictive = PythonOperator(
        task_id="predictive_models",
        python_callable=run_predictive,
        execution_timeout=timedelta(hours=2),
    )

    dbt_refresh = BashOperator(
        task_id="dbt_refresh_marts",
        bash_command=(
            f"{DBT_CMD} run --profiles-dir {DBT_DIR} --project-dir {DBT_DIR} "
            "--select marts.mart_player_metrics marts.mart_competition_leaderboard"
        ),
        execution_timeout=timedelta(minutes=20),
    )

    # Tactical and predictive can run in parallel; dbt waits for both
    [tactical, predictive] >> dbt_refresh
