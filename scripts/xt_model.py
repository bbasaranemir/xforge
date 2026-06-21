import logging
import os
from typing import Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text

from db_utils import get_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

GRID_COLS = 16
GRID_ROWS = 12
PITCH_X = 105.0  # V2: universal 105×68 metric pitch (was 120.0)
PITCH_Y = 68.0  # V2: universal 105×68 metric pitch (was 80.0)
ITERATIONS = 10


def _to_grid(x: float, y: float) -> Tuple[int, int]:
    col = int(min(x / (PITCH_X / GRID_COLS), GRID_COLS - 1))
    row = int(min(y / (PITCH_Y / GRID_ROWS), GRID_ROWS - 1))
    return col, row


def fetch_actions(engine) -> pd.DataFrame:
    query = text(
        """
        SELECT
            event_id,
            event_type,
            location_x,
            location_y,
            end_location_x,
            end_location_y,
            outcome
        FROM analytics_silver.silver_events
        WHERE location_x IS NOT NULL
          AND location_y IS NOT NULL
          AND event_type IN ('Pass', 'Carry', 'Shot')
    """
    )
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    log.info("Fetched %d actions for xT computation", len(df))
    return df


def build_matrices(df: pd.DataFrame):
    n = GRID_COLS * GRID_ROWS

    # shot_count[cell] = shots taken from cell
    # goal_count[cell] = goals scored from cell
    # move_count[from_cell, to_cell] = successful moves between cells
    # action_count[cell] = total on-ball actions from cell

    shot_count = np.zeros(n)
    goal_count = np.zeros(n)
    move_matrix = np.zeros((n, n))
    action_count = np.zeros(n)

    for _, row in df.iterrows():
        col, r = _to_grid(row["location_x"], row["location_y"])
        cell = r * GRID_COLS + col
        action_count[cell] += 1

        if row["event_type"] == "Shot":
            shot_count[cell] += 1
            if row["outcome"] in ("Goal",):
                goal_count[cell] += 1

        elif row["event_type"] in ("Pass", "Carry"):
            # Only successful moves update the transition matrix
            # StatsBomb: successful pass has no outcome label → SQL NULL → pandas NaN
            outcome_ok = pd.isna(row["outcome"]) or row["outcome"] in (
                "Unknown",
                None,
                "",
            )
            if (
                pd.notna(row["end_location_x"])
                and pd.notna(row["end_location_y"])
                and outcome_ok
            ):
                ec, er = _to_grid(row["end_location_x"], row["end_location_y"])
                end_cell = er * GRID_COLS + ec
                move_matrix[cell, end_cell] += 1

    # Normalise shot probability per cell
    shot_prob = np.where(action_count > 0, shot_count / action_count, 0.0)

    # Goal probability given shot
    goal_prob = np.where(shot_count > 0, goal_count / shot_count, 0.0)

    # Transition probability matrix: P(end_cell | start_cell, move action taken)
    move_totals = move_matrix.sum(axis=1, keepdims=True)
    transition = np.where(move_totals > 0, move_matrix / move_totals, 0.0)

    return shot_prob, goal_prob, transition


def solve_xt(shot_prob, goal_prob, transition) -> np.ndarray:
    """
    xT[z] = S[z]*G[z] + (1-S[z]) * sum_z'(T[z,z'] * xT[z'])
    Solved by value iteration for ITERATIONS steps.
    """
    xt = np.zeros(GRID_COLS * GRID_ROWS)

    for _ in range(ITERATIONS):
        xt = shot_prob * goal_prob + (1 - shot_prob) * (transition @ xt)

    return xt


def compute_event_xt(df: pd.DataFrame, xt_surface: np.ndarray) -> pd.DataFrame:
    """
    Per-event xT = xT[end_cell] - xT[start_cell] for successful passes/carries.
    Shots get xT = shot_prob * goal_prob at that cell (already in surface).
    """
    records = []

    for _, row in df.iterrows():
        if pd.isna(row["location_x"]) or pd.isna(row["location_y"]):
            continue

        col, r = _to_grid(row["location_x"], row["location_y"])
        start_cell = r * GRID_COLS + col
        start_xt = xt_surface[start_cell]

        if row["event_type"] == "Shot":
            delta = float(xt_surface[start_cell])
        elif (
            row["event_type"] in ("Pass", "Carry")
            and pd.notna(row["end_location_x"])
            and pd.notna(row["end_location_y"])
            and (pd.isna(row["outcome"]) or row["outcome"] in ("Unknown", None, ""))
        ):
            ec, er = _to_grid(row["end_location_x"], row["end_location_y"])
            end_cell = er * GRID_COLS + ec
            delta = float(xt_surface[end_cell] - start_xt)
        else:
            continue

        records.append({"event_id": row["event_id"], "xt_value": round(delta, 6)})

    return pd.DataFrame(records)


def write_xt_values(engine, xt_df: pd.DataFrame):
    rows = [{"xt": row["xt_value"], "eid": row["event_id"]} for _, row in xt_df.iterrows()]
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE fact_events SET xt_value = :xt WHERE event_id = :eid"),
            rows,
        )
    log.info("Updated xt_value for %d events", len(xt_df))


def write_xt_surface(engine, xt_surface: np.ndarray):
    """Persists the 16x12 xT surface to a dedicated table for Superset heatmap."""
    rows = [
        {"col": col, "row": row, "xt": round(float(xt_surface[row * GRID_COLS + col]), 6)}
        for row in range(GRID_ROWS)
        for col in range(GRID_COLS)
    ]
    with engine.begin() as conn:
        conn.execute(
            text(
                """
            CREATE TABLE IF NOT EXISTS xt_surface (
                grid_col  INTEGER,
                grid_row  INTEGER,
                xt_value  FLOAT,
                PRIMARY KEY (grid_col, grid_row)
            )
        """
            )
        )
        conn.execute(text("TRUNCATE xt_surface"))
        conn.execute(
            text("INSERT INTO xt_surface VALUES (:col, :row, :xt)"),
            rows,
        )
    log.info("xt_surface table written: %d cells", GRID_COLS * GRID_ROWS)


def run():
    engine = get_engine()

    df = fetch_actions(engine)
    shot_prob, goal_prob, transition = build_matrices(df)
    xt_surface = solve_xt(shot_prob, goal_prob, transition)

    log.info(
        "xT surface stats — min: %.4f, max: %.4f, mean: %.4f",
        xt_surface.min(),
        xt_surface.max(),
        xt_surface.mean(),
    )

    xt_df = compute_event_xt(df, xt_surface)
    write_xt_values(engine, xt_df)
    write_xt_surface(engine, xt_surface)

    log.info("xT computation complete.")


if __name__ == "__main__":
    run()
