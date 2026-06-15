"""
End-to-end smoke test — WC 2022 Final (Argentina vs France, match_id=3869685).

Sadece 1 maç alır, tüm pipeline'ı çalıştırır, sonuçları terminale basar.
Tam ingestion (3000+ maç) çalıştırmadan önce her şeyin doğru çalıştığını doğrular.

Çalıştırma:
    docker compose exec airflow-webserver python /opt/airflow/scripts/smoke_test.py
"""

import logging
import os
import sys

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

WC_FINAL_MATCH_ID = 3869685
WC_COMPETITION_ID = 43


# ─── 1. Ingestion ─────────────────────────────────────────────────────────────


def step_ingest():
    log.info("=" * 60)
    log.info("ADIM 1: Veri ingestion (sadece WC 2022 Final)")
    log.info("=" * 60)

    import massive_ingestion as mi

    mi.run(competition_filter=[WC_COMPETITION_ID])


# ─── 2. xT model ──────────────────────────────────────────────────────────────


def step_xt():
    log.info("=" * 60)
    log.info("ADIM 2: xT modeli hesaplaniyor")
    log.info("=" * 60)

    import xt_model

    xt_model.run()


# ─── 3. Tactical models ───────────────────────────────────────────────────────


def step_tactical():
    log.info("=" * 60)
    log.info("ADIM 3: Taktiksel modeller (K-Means, pres tespiti)")
    log.info("=" * 60)

    import tactical_models

    tactical_models.run()


# ─── 4. Predictive model ──────────────────────────────────────────────────────


def step_predictive():
    log.info("=" * 60)
    log.info("ADIM 4: XGBoost xP modeli egitiliyor")
    log.info("=" * 60)

    import predictive_models

    predictive_models.run()


# ─── 5. PDF raporu ────────────────────────────────────────────────────────────


def step_report():
    log.info("=" * 60)
    log.info("ADIM 5: PDF raporu olusturuluyor")
    log.info("=" * 60)

    import report_generator

    path = report_generator.run(WC_FINAL_MATCH_ID)
    log.info("PDF: %s", path)
    return path


# ─── 6. XML export ────────────────────────────────────────────────────────────


def step_xml():
    log.info("=" * 60)
    log.info("ADIM 6: SportsCode XML olusturuluyor")
    log.info("=" * 60)

    import xml_generator

    path = xml_generator.run(WC_FINAL_MATCH_ID)
    log.info("XML: %s", path)
    return path


# ─── 7. Sonuc sorguları ───────────────────────────────────────────────────────


def step_verify():
    log.info("=" * 60)
    log.info("ADIM 7: Sonuclar dogrulaniyor")
    log.info("=" * 60)

    import pandas as pd
    from sqlalchemy import create_engine, text

    engine = create_engine(
        f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:5432/{os.environ['POSTGRES_DB']}",
        pool_pre_ping=True,
    )

    checks = {
        "Toplam event sayisi": "SELECT count(*) FROM fact_events",
        "xT degeri olan event sayisi": "SELECT count(*) FROM fact_events WHERE xt_value IS NOT NULL",
        "xT surface hucre sayisi": "SELECT count(*) FROM xt_surface",
        "En yuksek xT (top 5 hucre)": "SELECT grid_col, grid_row, round(xt_value::numeric,4) AS xt FROM xt_surface ORDER BY xt_value DESC LIMIT 5",
        "En yuksek xT action (top 10)": """SELECT fe.event_type, dp.player_name,
                      round(fe.xt_value::numeric,4) AS xt,
                      round(fe.xp_value::numeric,4) AS xp
               FROM fact_events fe
               LEFT JOIN dim_players dp ON fe.player_id = dp.player_id
               WHERE fe.xt_value IS NOT NULL
               ORDER BY fe.xt_value DESC LIMIT 10""",
        "Set piece cluster merkezleri": "SELECT event_type, cluster_label, round(center_x::numeric,1) AS x, round(center_y::numeric,1) AS y, member_count FROM set_piece_clusters ORDER BY event_type, cluster_label",
        "xP model kayitlari": "SELECT model_name, version, metrics->>'auc' AS auc, metrics->>'log_loss' AS log_loss, trained_at FROM model_registry ORDER BY trained_at DESC LIMIT 3",
        "Ingested maclar": "SELECT match_id, competition_id, event_count, status FROM ingested_matches",
    }

    with engine.connect() as conn:
        for title, query in checks.items():
            print(f"\n{'─'*60}")
            print(f"  {title}")
            print(f"{'─'*60}")
            result = pd.read_sql(text(query), conn)
            print(result.to_string(index=False))

    print("\n" + "=" * 60)
    print("  SMOKE TEST TAMAMLANDI")
    print("=" * 60)


# ─── Main ─────────────────────────────────────────────────────────────────────


def run():
    try:
        step_ingest()
        step_xt()
        step_tactical()
        step_predictive()
        step_report()
        step_xml()
        step_verify()
    except Exception as exc:
        log.error("HATA: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    run()
