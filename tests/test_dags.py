"""
DAG integrity tests.

Imports each DAG module and verifies:
  - The DAG object is loadable (no syntax / import errors)
  - Expected task IDs are present
  - No cycles exist in the task graph

Skipped automatically when apache-airflow is not installed (e.g. CI
without the full Airflow dependency tree).
"""

import importlib
import sys
import os
import pytest

airflow = pytest.importorskip("airflow", reason="apache-airflow not installed — skipping DAG tests")

# Make dags/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dags"))

# Minimal Airflow env vars so DAG modules don't crash on import
os.environ.setdefault("AIRFLOW__CORE__EXECUTOR", "SequentialExecutor")
os.environ.setdefault("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "sqlite:///:memory:")
os.environ.setdefault("AIRFLOW__CORE__FERNET_KEY", "a" * 32)
os.environ.setdefault("POSTGRES_USER", "analytics")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "football_db_test")
os.environ.setdefault("POSTGRES_HOST", "localhost")


def _load_dag(module_name: str):
    """Import a DAG module and return its DAG object."""
    mod = importlib.import_module(module_name)
    # Find the DAG object defined at module level
    dags = [v for v in vars(mod).values() if hasattr(v, "task_ids")]
    assert dags, f"No DAG found in {module_name}"
    return dags[0]


# ── ingestion_pipeline ────────────────────────────────────────────────────────


def test_ingestion_dag_loads():
    dag = _load_dag("ingestion_dag")
    assert dag.dag_id == "ingestion_pipeline"


def test_ingestion_dag_has_required_tasks():
    dag = _load_dag("ingestion_dag")
    task_ids = set(dag.task_ids)
    assert "ingest" in task_ids
    assert "xt_model" in task_ids


def test_ingestion_dag_no_cycles():
    dag = _load_dag("ingestion_dag")
    # DAG.test_cycle() raises if a cycle exists; returns None if clean
    assert dag.test_cycle() is None


# ── ml_training ───────────────────────────────────────────────────────────────


def test_ml_dag_loads():
    dag = _load_dag("ml_dag")
    assert dag.dag_id == "ml_training"


def test_ml_dag_has_required_tasks():
    dag = _load_dag("ml_dag")
    task_ids = set(dag.task_ids)
    assert "tactical_models" in task_ids
    assert "predictive_models" in task_ids


def test_ml_dag_no_cycles():
    dag = _load_dag("ml_dag")
    assert dag.test_cycle() is None


# ── matchday_push ─────────────────────────────────────────────────────────────


def test_matchday_dag_loads():
    dag = _load_dag("matchday_dag")
    assert dag.dag_id == "matchday_push"


def test_matchday_dag_has_required_tasks():
    dag = _load_dag("matchday_dag")
    task_ids = set(dag.task_ids)
    assert "generate_pdf" in task_ids
    assert "generate_xml" in task_ids
    assert "send_report_email" in task_ids


def test_matchday_dag_no_cycles():
    dag = _load_dag("matchday_dag")
    assert dag.test_cycle() is None
