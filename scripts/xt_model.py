import logging
import os

import numpy as np
import pandas as pd
from sqlalchemy import text

from db_utils import get_engine

log = logging.getLogger(__name__)

GRID_COLS = 16
GRID_ROWS = 12
PITCH_X = 105.0  # V2: universal 105×68 metric pitch (was 120.0)
PITCH_Y = 68.0  # V2: universal 105×68 metric pitch (was 80.0)
ITERATIONS = 10


def _cells_from_xy(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    cols = np.minimum((x / (PITCH_X / GRID_COLS)).astype(int), GRID_COLS - 1)
    rows = np.minimum((y / (PITCH_Y / GRID_ROWS)).astype(int), GRID_ROWS - 1)
    return rows * GRID_COLS + cols


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
    shot_count = np.zeros(n)
    goal_count = np.zeros(n)
    move_matrix = np.zeros((n, n))
    action_count = np.zeros(n)

    cells = _cells_from_xy(df["location_x"].values, df["location_y"].values)
    np.add.at(action_count, cells, 1)

    shot_mask = df["event_type"].values == "Shot"
    np.add.at(shot_count, cells[shot_mask], 1)
    goal_mask = shot_mask & (df["outcome"].values == "Goal")
    np.add.at(goal_count, cells[goal_mask], 1)

    # StatsBomb: successful pass outcome is NULL (pandas NaN); Opta uses "Unknown"/""
    outcome_ok = (df["outcome"].isna() | df["outcome"].isin(["Unknown", ""])).values
    move_mask = (
        df["event_type"].isin(["Pass", "Carry"]).values
        & outcome_ok
        & df["end_location_x"].notna().values
        & df["end_location_y"].notna().values
    )
    if move_mask.any():
        end_cells = _cells_from_xy(
            df.loc[move_mask, "end_location_x"].values,
            df.loc[move_mask, "end_location_y"].values,
        )
        np.add.at(move_matrix, (cells[move_mask], end_cells), 1)

    shot_prob = np.where(action_count > 0, shot_count / action_count, 0.0)
    goal_prob = np.where(shot_count > 0, goal_count / shot_count, 0.0)
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
    """Per-event xT delta: end_cell − start_cell for passes/carries; surface value for shots."""
    df = df.dropna(subset=["location_x", "location_y"]).copy()

    start_cells = _cells_from_xy(df["location_x"].values, df["location_y"].values)

    shot_mask = df["event_type"].values == "Shot"
    outcome_ok = (df["outcome"].isna() | df["outcome"].isin(["Unknown", ""])).values
    move_mask = (
        df["event_type"].isin(["Pass", "Carry"]).values
        & outcome_ok
        & df["end_location_x"].notna().values
        & df["end_location_y"].notna().values
    )

    deltas = np.full(len(df), np.nan)
    deltas[shot_mask] = xt_surface[start_cells[shot_mask]]

    if move_mask.any():
        end_cells = _cells_from_xy(
            df.loc[move_mask, "end_location_x"].values,
            df.loc[move_mask, "end_location_y"].values,
        )
        deltas[move_mask] = xt_surface[end_cells] - xt_surface[start_cells[move_mask]]

    valid = ~np.isnan(deltas)
    return pd.DataFrame({
        "event_id": df["event_id"].values[valid],
        "xt_value": np.round(deltas[valid], 6),
    })


def write_xt_values(engine, xt_df: pd.DataFrame):
    rows = xt_df.rename(columns={"xt_value": "xt", "event_id": "eid"}).to_dict("records")
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
