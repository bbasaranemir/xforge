import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

import pandas as pd
from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_user = os.environ.get("POSTGRES_USER", "analytics")
_pass = os.environ.get("POSTGRES_PASSWORD", "analytics")
_host = os.environ.get("POSTGRES_HOST", "postgres")
_db   = os.environ.get("POSTGRES_DB",   "football_db")
DB_URL = f"postgresql+psycopg2://{_user}:{_pass}@{_host}:5432/{_db}"
OUTPUT_DIR = Path(__file__).parent.parent / "data"

# StatsBomb events do not carry a real video timestamp.
# minute/second are mapped to milliseconds as a reference offset.
# Hudl/SportsCode uses ms-based in/out points relative to clip start.
CLIP_LEAD_MS = 3000   # pre-roll before action
CLIP_LAG_MS = 5000    # post-roll after action


def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


def fetch_top_xt_actions(engine, top_n: int = 15) -> pd.DataFrame:
    query = text("""
        SELECT
            se.event_id,
            se.match_id,
            se.event_type,
            se.minute,
            se.second,
            se.location_x,
            se.location_y,
            se.end_location_x,
            se.end_location_y,
            se.outcome,
            se.under_pressure,
            fe.xt_value,
            fe.xg_value,
            dp.player_name,
            dt.team_name
        FROM analytics_silver.silver_events se
        JOIN public.fact_events fe       ON se.event_id  = fe.event_id
        LEFT JOIN public.dim_players dp  ON se.player_id = dp.player_id
        LEFT JOIN public.dim_teams   dt  ON se.team_id   = dt.team_id
        WHERE fe.xt_value IS NOT NULL
          AND se.event_type IN ('Pass', 'Carry')
        ORDER BY fe.xt_value DESC
        LIMIT :top_n
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"top_n": top_n})
    log.info("Fetched top %d xT actions", len(df))
    return df


def _game_clock_ms(minute: int, second: int) -> int:
    return (int(minute) * 60 + int(second)) * 1000


def _build_label_text(row: pd.Series) -> str:
    parts = [
        f"xT: {row['xt_value']:.4f}",
        f"xG: {row['xg_value']:.4f}" if pd.notna(row.get("xg_value")) else None,
        f"Type: {row['event_type']}",
        f"Player: {row.get('player_name') or 'Unknown'}",
        f"Team: {row.get('team_name') or 'Unknown'}",
        f"From: ({row['location_x']:.1f}, {row['location_y']:.1f})",
    ]
    if pd.notna(row.get("end_location_x")):
        parts.append(f"To: ({row['end_location_x']:.1f}, {row['end_location_y']:.1f})")
    if row.get("under_pressure"):
        parts.append("Under Pressure: Yes")
    return " | ".join(p for p in parts if p is not None)


def build_xml(df: pd.DataFrame, match_id: int) -> ET.Element:
    root = ET.Element("file")

    # --- ALL_INSTANCES block (SportsCode schema requirement) ---
    all_instances = ET.SubElement(root, "ALL_INSTANCES")

    for rank, (_, row) in enumerate(df.iterrows(), start=1):
        clock_ms = _game_clock_ms(row["minute"], row["second"])
        start_ms = max(0, clock_ms - CLIP_LEAD_MS)
        end_ms = clock_ms + CLIP_LAG_MS

        instance = ET.SubElement(all_instances, "instance")

        ET.SubElement(instance, "ID").text = str(rank)
        ET.SubElement(instance, "start").text = str(start_ms)
        ET.SubElement(instance, "end").text = str(end_ms)
        ET.SubElement(instance, "code").text = f"xT_ACTION_{rank:02d}"

        label = ET.SubElement(instance, "label")
        ET.SubElement(label, "text").text = _build_label_text(row)
        ET.SubElement(label, "pos_x").text = f"{row['location_x']:.2f}"
        ET.SubElement(label, "pos_y").text = f"{row['location_y']:.2f}"

        if pd.notna(row.get("end_location_x")):
            ET.SubElement(label, "end_pos_x").text = f"{row['end_location_x']:.2f}"
            ET.SubElement(label, "end_pos_y").text = f"{row['end_location_y']:.2f}"

        ET.SubElement(label, "xt_value").text = f"{row['xt_value']:.6f}"
        if pd.notna(row.get("xg_value")):
            ET.SubElement(label, "xg_value").text = f"{row['xg_value']:.6f}"
        ET.SubElement(label, "minute").text = str(int(row["minute"]))
        ET.SubElement(label, "second").text = str(int(row["second"]))
        ET.SubElement(label, "player").text = row.get("player_name") or "Unknown"
        ET.SubElement(label, "team").text = row.get("team_name") or "Unknown"
        ET.SubElement(label, "event_type").text = row["event_type"]

    # --- Sequence summary block ---
    summary = ET.SubElement(root, "SEQUENCE")
    ET.SubElement(summary, "match_id").text = str(match_id)
    ET.SubElement(summary, "action_count").text = str(len(df))
    ET.SubElement(summary, "description").text = "Top xT offensive actions — xForge V2"

    return root


def pretty_print(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="unicode")
    return minidom.parseString(raw).toprettyxml(indent="  ", encoding=None)


def write_xml(root: ET.Element, match_id: int) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"top_xt_actions_match_{match_id}.xml"
    out_path.write_text(pretty_print(root), encoding="utf-8")
    log.info("XML written to %s", out_path)
    return out_path


def run():
    engine = get_engine()
    df = fetch_top_xt_actions(engine, top_n=15)

    if df.empty:
        log.warning("No xT data found. Run xt_model.py first.")
        return

    match_id = int(df["match_id"].iloc[0])
    root = build_xml(df, match_id)
    write_xml(root, match_id)

    log.info("XML generation complete.")


if __name__ == "__main__":
    run()
