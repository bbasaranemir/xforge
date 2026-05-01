"""
xT (Expected Threat) surface computation via value iteration.

Updates fact_events.xt_value and persists the 16x12 grid to xt_surface.
Designed for the full dataset: reads only the required columns to keep RAM low.
"""

import logging
import os
from typing import Tuple

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_URL = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:{os.environ.get('POSTGRES_PORT', '5432')}"
    f"/{os.environ['POSTGRES_DB']}"
)

GRID_COLS = 16
GRID_ROWS = 12
PITCH_X   = 120.0
PITCH_Y   = 80.0
ITERATIONS = 15


def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


def _to_grid(x: float, y: float) -> Tuple[int, int]:
    col = int(min(x / (PITCH_X / GRID_COLS), GRID_COLS - 1))
    row = int(min(y / (PITCH_Y / GRID_ROWS), GRID_ROWS - 1))
    return col, row


def fetch_actions(engine) -> pd.DataFrame:
    q = text("""
        SELECT event_id, event_type,
               location_x, location_y,
               end_location_x, end_location_y,
               outcome
        FROM fact_events
        WHERE location_x IS NOT NULL
          AND location_y IS NOT NULL
          AND event_type IN ('Pass', 'Carry', 'Shot')
    """)
    with engine.connect() as conn:
        df = pd.read_sql(q, conn)
    log.info("Fetched %d actions for xT", len(df))
    return df


def build_matrices(df: pd.DataFrame):
    n = GRID_COLS * GRID_ROWS
    shot_count   = np.zeros(n)
    goal_count   = np.zeros(n)
    move_matrix  = np.zeros((n, n))
    action_count = np.zeros(n)

    for _, row in df.iterrows():
        col, r = _to_grid(row["location_x"], row["location_y"])
        cell   = r * GRID_COLS + col
        action_count[cell] += 1

        if row["event_type"] == "Shot":
            shot_count[cell] += 1
            if row["outcome"] == "Goal":
                goal_count[cell] += 1

        elif row["event_type"] in ("Pass", "Carry"):
            success = pd.isna(row["outcome"]) or row["outcome"] in ("Unknown", None, "")
            if success and pd.notna(row["end_location_x"]) and pd.notna(row["end_location_y"]):
                ec, er   = _to_grid(row["end_location_x"], row["end_location_y"])
                end_cell = er * GRID_COLS + ec
                move_matrix[cell, end_cell] += 1

    shot_prob  = np.where(action_count > 0, shot_count / action_count, 0.0)
    goal_prob  = np.where(shot_count > 0,   goal_count / shot_count,   0.0)
    totals     = move_matrix.sum(axis=1, keepdims=True)
    transition = np.where(totals > 0, move_matrix / totals, 0.0)

    return shot_prob, goal_prob, transition


def solve_xt(shot_prob, goal_prob, transition) -> np.ndarray:
    xt = np.zeros(GRID_COLS * GRID_ROWS)
    for _ in range(ITERATIONS):
        xt = shot_prob * goal_prob + (1 - shot_prob) * (transition @ xt)
    return xt


def compute_event_xt(df: pd.DataFrame, surface: np.ndarray) -> pd.DataFrame:
    records = []
    for _, row in df.iterrows():
        if pd.isna(row["location_x"]) or pd.isna(row["location_y"]):
            continue
        col, r      = _to_grid(row["location_x"], row["location_y"])
        start_cell  = r * GRID_COLS + col
        start_val   = surface[start_cell]

        if row["event_type"] == "Shot":
            delta = float(surface[start_cell])
        elif (
            row["event_type"] in ("Pass", "Carry")
            and pd.notna(row["end_location_x"])
            and pd.notna(row["end_location_y"])
            and (pd.isna(row["outcome"]) or row["outcome"] in ("Unknown", None, ""))
        ):
            ec, er   = _to_grid(row["end_location_x"], row["end_location_y"])
            end_cell = er * GRID_COLS + ec
            delta    = float(surface[end_cell] - start_val)
        else:
            continue

        records.append({"event_id": row["event_id"], "xt_value": round(delta, 6)})

    return pd.DataFrame(records)


def write_xt_values(engine, xt_df: pd.DataFrame) -> None:
    with engine.begin() as conn:
        for _, row in xt_df.iterrows():
            conn.execute(
                text("UPDATE fact_events SET xt_value = :v WHERE event_id = :eid"),
                {"v": row["xt_value"], "eid": row["event_id"]},
            )
    log.info("Updated xt_value for %d events", len(xt_df))


def write_xt_surface(engine, surface: np.ndarray) -> None:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE xt_surface"))
        rows = []
        for row in range(GRID_ROWS):
            for col in range(GRID_COLS):
                rows.append({"col": col, "row": row, "xt": round(float(surface[row * GRID_COLS + col]), 6)})
        conn.execute(
            text("INSERT INTO xt_surface (grid_col, grid_row, xt_value) VALUES (:col, :row, :xt)"),
            rows,
        )
    log.info("xt_surface written: %d cells", GRID_COLS * GRID_ROWS)


def run() -> None:
    engine = get_engine()
    df     = fetch_actions(engine)

    shot_prob, goal_prob, transition = build_matrices(df)
    surface = solve_xt(shot_prob, goal_prob, transition)

    log.info("xT surface — min=%.4f max=%.4f mean=%.4f", surface.min(), surface.max(), surface.mean())

    xt_df = compute_event_xt(df, surface)
    write_xt_values(engine, xt_df)
    write_xt_surface(engine, surface)

    log.info("xT computation complete.")


if __name__ == "__main__":
    run()
