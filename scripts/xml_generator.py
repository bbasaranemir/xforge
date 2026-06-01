"""
SportsCode / Hudl-compatible XML event export.

Queries the top xT and xP actions for a given match and serialises them
into the standard SportsCode XML schema that video analysis software can
import directly for clip tagging.
"""

import logging
import os
import xml.etree.ElementTree as ET
from datetime import timedelta

import pandas as pd
from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_URL = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:{os.environ.get('POSTGRES_PORT', '5432')}"
    f"/{os.environ['POSTGRES_DB']}"
)

REPORT_DIR = "/opt/airflow/reports"
CLIP_LEAD = 5  # seconds before event
CLIP_TRAIL = 10  # seconds after event
TOP_N = 25


def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


def fetch_top_events(engine, match_id: int) -> pd.DataFrame:
    q = text(
        """
        SELECT
            fe.event_id,
            fe.event_type,
            fe.minute,
            COALESCE(fe.second, 0) AS second,
            dp.player_name,
            dt.team_name,
            round(fe.location_x::numeric, 1)     AS loc_x,
            round(fe.location_y::numeric, 1)     AS loc_y,
            round(fe.xt_value::numeric,  4)      AS xt_value,
            round(fe.xp_value::numeric,  4)      AS xp_value,
            fe.under_pressure
        FROM fact_events fe
        LEFT JOIN dim_players dp ON fe.player_id = dp.player_id
        LEFT JOIN dim_teams   dt ON fe.team_id   = dt.team_id
        WHERE fe.match_id   = :mid
          AND fe.xt_value  IS NOT NULL
          AND fe.event_type IN ('Pass', 'Carry', 'Shot')
        ORDER BY fe.xt_value DESC
        LIMIT :n
    """
    )
    with engine.connect() as conn:
        return pd.read_sql(q, conn, params={"mid": match_id, "n": TOP_N})


def _timecode(minute: int, second: int, offset_sec: int = 0) -> str:
    total = timedelta(minutes=int(minute), seconds=int(second) + offset_sec)
    h, rem = divmod(int(total.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.00"


def build_xml(events: pd.DataFrame, match_id: int) -> ET.Element:
    root = ET.Element("file")

    # Header
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "version").text = "1.0"
    ET.SubElement(header, "source_application").text = "xForge"
    ET.SubElement(header, "match_id").text = str(match_id)

    # Code window definitions
    all_instances = ET.SubElement(root, "ALL_INSTANCES")

    for rank, (_, row) in enumerate(events.iterrows(), start=1):
        instance = ET.SubElement(all_instances, "instance")

        ET.SubElement(instance, "ID").text = str(rank)
        ET.SubElement(instance, "uuid").text = str(row["event_id"])

        start_tc = _timecode(row["minute"], row["second"], offset_sec=-CLIP_LEAD)
        end_tc = _timecode(row["minute"], row["second"], offset_sec=CLIP_TRAIL)
        ET.SubElement(instance, "start").text = start_tc
        ET.SubElement(instance, "end").text = end_tc

        label = ET.SubElement(instance, "label")
        ET.SubElement(label, "text").text = (
            f"{row['event_type']} | {row.get('player_name', 'Unknown')} "
            f"| xT={row['xt_value']:.4f}"
        )

        # Custom metadata
        meta = ET.SubElement(instance, "metadata")
        for key in (
            "event_type",
            "player_name",
            "team_name",
            "loc_x",
            "loc_y",
            "xt_value",
            "xp_value",
            "under_pressure",
        ):
            val = row.get(key, "")
            ET.SubElement(meta, key).text = "" if pd.isna(val) else str(val)

    # Code labels section (unique label types for tagging panel)
    codes = ET.SubElement(root, "code")
    for et in events["event_type"].unique():
        row_el = ET.SubElement(codes, "row")
        ET.SubElement(row_el, "code").text = et

    return root


def write_xml(root: ET.Element, match_id: int) -> str:
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"match_{match_id}_sportscode.xml")

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)

    log.info("XML written: %s (%d events)", path, len(root.find("ALL_INSTANCES")))
    return path


def run(match_id: int) -> str:
    engine = get_engine()
    events = fetch_top_events(engine, match_id)

    if events.empty:
        log.warning("No xT events found for match_id=%d; XML not generated.", match_id)
        return ""

    root = build_xml(events, match_id)
    return write_xml(root, match_id)


if __name__ == "__main__":
    import sys

    run(int(sys.argv[1]))
