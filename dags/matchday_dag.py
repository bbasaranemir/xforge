"""
DAG: matchday_push
Trigger: manual (or triggered by ingestion_pipeline via TriggerDagRunOperator)

Generates PDF report + SportsCode XML for a given match_id,
then emails the files to the analyst team.

Pass match_id via Airflow DAG run conf:
  {"match_id": 3869685}
"""

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

DEFAULT_ARGS = {
    "owner":            "analytics",
    "retries":          1,
    "retry_delay":      timedelta(minutes=5),
    "email_on_failure": False,
    "email_on_retry":   False,
}

SCRIPT_DIR = "/opt/airflow/scripts"
REPORT_DIR = "/opt/airflow/reports"
RECIPIENT  = os.environ.get("REPORT_RECIPIENT", "analyst@yourclub.com")
SMTP_USER  = os.environ.get("SMTP_USER", "")


def generate_pdf(**context):
    import sys
    sys.path.insert(0, SCRIPT_DIR)
    import report_generator

    match_id = context["dag_run"].conf.get("match_id")
    if not match_id:
        raise ValueError("match_id must be provided in DAG run conf")

    path = report_generator.run(int(match_id))
    context["ti"].xcom_push(key="pdf_path", value=path)
    context["ti"].xcom_push(key="match_id", value=match_id)


def generate_xml(**context):
    import sys
    sys.path.insert(0, SCRIPT_DIR)
    import xml_generator

    match_id = context["dag_run"].conf.get("match_id")
    path     = xml_generator.run(int(match_id))
    context["ti"].xcom_push(key="xml_path", value=path)


def send_report_email(**context):
    import logging
    log = logging.getLogger(__name__)

    if not SMTP_USER or SMTP_USER == "placeholder@gmail.com":
        log.warning("SMTP not configured — skipping email send (mock mode)")
        log.info("PDF: %s", context["ti"].xcom_pull(task_ids="generate_pdf", key="pdf_path"))
        log.info("XML: %s", context["ti"].xcom_pull(task_ids="generate_xml", key="xml_path"))
        return

    from airflow.utils.email import send_email
    pdf_path = context["ti"].xcom_pull(task_ids="generate_pdf", key="pdf_path")
    xml_path = context["ti"].xcom_pull(task_ids="generate_xml", key="xml_path")
    match_id = context["dag_run"].conf.get("match_id")

    send_email(
        to=RECIPIENT,
        subject=f"Football Analytics Report — Match {match_id}",
        html_content=f"""
        <h3>Football Analytics Engine</h3>
        <p>Attached: PDF report and SportsCode XML for match <strong>{match_id}</strong>.</p>
        <ul>
          <li>xT heatmap</li>
          <li>Pass network</li>
          <li>Top-15 xT scatter</li>
          <li>Player xT/xP rankings</li>
        </ul>
        """,
        files=[pdf_path, xml_path],
    )
    log.info("Email sent to %s", RECIPIENT)


with DAG(
    dag_id="matchday_push",
    description="Generate PDF + XML reports for a match and email to analyst team",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2024, 1, 1),
    schedule_interval=None,
    catchup=False,
    max_active_runs=3,
    tags=["report", "xml", "email"],
) as dag:

    gen_pdf = PythonOperator(
        task_id="generate_pdf",
        python_callable=generate_pdf,
        provide_context=True,
        execution_timeout=timedelta(minutes=15),
    )

    gen_xml = PythonOperator(
        task_id="generate_xml",
        python_callable=generate_xml,
        provide_context=True,
        execution_timeout=timedelta(minutes=5),
    )

    send_email = PythonOperator(
        task_id="send_report_email",
        python_callable=send_report_email,
        provide_context=True,
        execution_timeout=timedelta(minutes=5),
    )

    [gen_pdf, gen_xml] >> send_email