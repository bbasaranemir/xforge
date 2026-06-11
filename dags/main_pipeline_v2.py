"""
xForge V2 Pipeline DAG
Dart ingestion → dbt Medallion (Bronze→Silver→Gold) → xG + xT models
→ BI MV refresh + Sportscode XML export

Silver test failure BLOCKS gold run — ML never trains on un-normalised data.
xG and xT compute in parallel (independent reads from silver_shots / silver_events).
"""
from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.docker.operators.docker import DockerOperator
from airflow.utils.trigger_rule import TriggerRule

log = logging.getLogger(__name__)

SCRIPTS_DIR = Path("/opt/airflow/scripts")
DBT_DIR = Path("/opt/airflow/dbt_project")
MATCH_ID = 3869685  # 2022 WC Final — override via Airflow Variables for production


def _alert_on_failure(context):
    task_id = context["task_instance"].task_id
    dag_run_id = context["dag_run"].run_id
    log.error("PIPELINE FAILURE: task=%s dag_run=%s", task_id, dag_run_id)
    context["task_instance"].xcom_push(key="failed_task", value=task_id)


DEFAULT_ARGS = {
    "owner": "analytics",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
    "on_failure_callback": _alert_on_failure,
}


def _run_script(script_name: str) -> None:
    path = SCRIPTS_DIR / script_name
    log.info("Executing: %s", path)
    result = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
    )
    if result.stdout:
        log.info(result.stdout)
    if result.stderr:
        log.warning(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"{script_name} exited with code {result.returncode}")


def _run_dbt(command: list[str]) -> None:
    cmd = ["dbt"] + command + ["--profiles-dir", str(DBT_DIR), "--target", "docker"]
    log.info("dbt: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(DBT_DIR), capture_output=True, text=True)
    if result.stdout:
        log.info(result.stdout)
    if result.stderr:
        log.warning(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"dbt {command[0]} {command[1:]} failed (code {result.returncode})")


# ─── Task callables ────────────────────────────────────────────────────────

def task_dbt_deps(**_):          _run_dbt(["deps"])
def task_dbt_run_bronze(**_):    _run_dbt(["run",  "--select", "bronze"])
def task_dbt_test_bronze(**_):   _run_dbt(["test", "--select", "bronze"])
def task_dbt_run_silver(**_):    _run_dbt(["run",  "--select", "silver"])
def task_dbt_test_silver(**_):   _run_dbt(["test", "--select", "silver"])
def task_dbt_run_gold(**_):      _run_dbt(["run",  "--select", "gold"])
def task_dbt_test_gold(**_):     _run_dbt(["test", "--select", "gold"])
def task_xg_model(**_):          _run_script("xg_model.py")
def task_xt_model(**_):          _run_script("xt_model.py")
def task_mv_refresh(**_):        _run_script("refresh_materialized_views.py")
def task_xml_export(**_):        _run_script("xml_generator.py")


def task_pipeline_summary(**context):
    ti = context["task_instance"]
    log.info(
        "V2 pipeline complete. dag_run=%s execution_date=%s",
        context["dag_run"].run_id,
        context["execution_date"],
    )
    ti.xcom_push(key="pipeline_status", value="success")


# ─── DAG definition ────────────────────────────────────────────────────────

with DAG(
    dag_id="football_analytics_pipeline_v2",
    description="Dart ingestion → dbt medallion → XGBoost xG → xT → XML + MV refresh",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2024, 1, 1),
    schedule_interval=None,  # triggered manually or via API
    catchup=False,
    max_active_runs=1,
    tags=["football", "analytics", "v2", "xg", "xt", "dart"],
) as dag:

    # Task 0: Install dbt packages (dbt_utils) — must run before any dbt command
    dbt_deps = PythonOperator(
        task_id="dbt_deps",
        python_callable=task_dbt_deps,
        execution_timeout=timedelta(minutes=5),
    )

    # Task 1: Dart ingestion — HTTP POST to the running dart_ingestion container
    ingest_dart = DockerOperator(
        task_id="ingest_dart",
        image="dart_ingestion:latest",
        # List format avoids shell quoting issues with JSON payload
        command=[
            "curl", "-sf", "-X", "POST",
            "http://dart_ingestion:8090/ingest",
            "-H", "Content-Type: application/json",
            "-d", f'{{"provider":"statsbomb","match_id":{MATCH_ID}}}',
        ],
        network_mode="analytics_net",
        auto_remove=True,
        execution_timeout=timedelta(minutes=20),
    )

    # Tasks 2–3: Bronze
    dbt_run_bronze = PythonOperator(
        task_id="dbt_run_bronze",
        python_callable=task_dbt_run_bronze,
        execution_timeout=timedelta(minutes=10),
    )
    dbt_test_bronze = PythonOperator(
        task_id="dbt_test_bronze",
        python_callable=task_dbt_test_bronze,
        execution_timeout=timedelta(minutes=5),
    )

    # Tasks 4–5: Silver (spatial range tests gate here)
    dbt_run_silver = PythonOperator(
        task_id="dbt_run_silver",
        python_callable=task_dbt_run_silver,
        execution_timeout=timedelta(minutes=10),
    )
    dbt_test_silver = PythonOperator(
        task_id="dbt_test_silver",
        python_callable=task_dbt_test_silver,
        execution_timeout=timedelta(minutes=5),
    )

    # Tasks 6–7: ML models run in parallel — independent silver readers
    xg_compute = PythonOperator(
        task_id="compute_xg_model",
        python_callable=task_xg_model,
        execution_timeout=timedelta(minutes=20),
    )
    xt_compute = PythonOperator(
        task_id="compute_xt_model",
        python_callable=task_xt_model,
        execution_timeout=timedelta(minutes=15),
    )

    # Tasks 8–9: Gold
    dbt_run_gold = PythonOperator(
        task_id="dbt_run_gold",
        python_callable=task_dbt_run_gold,
        execution_timeout=timedelta(minutes=10),
    )
    dbt_test_gold = PythonOperator(
        task_id="dbt_test_gold",
        python_callable=task_dbt_test_gold,
        execution_timeout=timedelta(minutes=5),
    )

    # Tasks 10–11: MV refresh and XML export run in parallel
    mv_refresh = PythonOperator(
        task_id="refresh_materialized_views",
        python_callable=task_mv_refresh,
        execution_timeout=timedelta(minutes=5),
    )
    xml_export = PythonOperator(
        task_id="export_xml",
        python_callable=task_xml_export,
        execution_timeout=timedelta(minutes=5),
    )

    summary = PythonOperator(
        task_id="pipeline_summary",
        python_callable=task_pipeline_summary,
        trigger_rule=TriggerRule.ALL_SUCCESS,
    )

    # ─── DAG dependency chain ─────────────────────────────────────────────
    (
        [dbt_deps, ingest_dart]            # parallel: install packages + ingest data
        >> dbt_run_bronze >> dbt_test_bronze
        >> dbt_run_silver >> dbt_test_silver
        >> [xg_compute, xt_compute]        # parallel ML
        >> dbt_run_gold >> dbt_test_gold
        >> [mv_refresh, xml_export]        # parallel output
        >> summary
    )
