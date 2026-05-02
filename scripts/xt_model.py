"""
xT (Expected Threat) surface computation via value iteration.

Updates fact_events.xt_value and persists the 16x12 grid to xt_surface.
Memory-efficient: chunked reads + vectorised numpy ops + bulk UPDATE.
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

GRID_COLS  = 16
GRID_ROWS  = 12
PITCH_X    = 120.0
PITCH_Y    = 80.0
ITERATIONS = 15
CHUNK_SIZE = 100_000

QUERY = """
    SELECT event_id, event_type,
           location_x, location_y,
           end_location_x, end_location_y,
           outcome
    FROM fact_events
    WHERE location_x IS NOT NULL
      AND location_y IS NOT NULL
      AND event_type IN ('Pass', 'Carry', 'Shot')
"""


def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


def _to_grid_vec(x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    cols = np.minimum((x / (PITCH_X / GRID_COLS)).astype(int), GRID_COLS - 1)
    rows = np.minimum((y / (PITCH_Y / GRID_ROWS)).astype(int), GRID_ROWS - 1)
    return cols, rows


def build_matrices(engine):
    n            = GRID_COLS * GRID_ROWS
    shot_count   = np.zeros(n)
    goal_count   = np.zeros(n)
    move_matrix  = np.zeros((n, n))
    action_count = np.zeros(n)
    total        = 0

    with engine.connect() as conn:
        for chunk in pd.read_sql(QUERY, conn, chunksize=CHUNK_SIZE):
            total += len(chunk)
            lx = chunk["location_x"].to_numpy(dtype=float)
            ly = chunk["location_y"].to_numpy(dtype=float)
            c, r = _to_grid_vec(lx, ly)
            cells = r * GRID_COLS + c
            np.add.at(action_count, cells, 1)
            mask_shot = chunk["event_type"].to_numpy() == "Shot"
            np.add.at(shot_count, cells[mask_shot], 1)
            mask_goal = mask_shot & (chunk["outcome"].fillna("").to_numpy() == "Goal")
            np.add.at(goal_count, cells[mask_goal], 1)
            mask_move = (
                chunk["event_type"].isin(["Pass", "Carry"]).to_numpy()
                & chunk["end_location_x"].notna().to_numpy()
                & chunk["end_location_y"].notna().to_numpy()
                & (chunk["outcome"].isna() | chunk["outcome"].isin(["Unknown", ""])).to_numpy()
            )
            if mask_move.any():
                ex = chunk.loc[mask_move, "end_location_x"].to_numpy(dtype=float)
                ey = chunk.loc[mask_move, "end_location_y"].to_numpy(dtype=float)
                ec, er    = _to_grid_vec(ex, ey)
                end_cells = er * GRID_COLS + ec
                src_cells = cells[mask_move]
                for s, e in zip(src_cells, end_cells):
                    move_matrix[s, e] += 1

    log.info("Matrix build complete: %d actions processed", total)
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


def compute_and_write_xt(engine, surface: np.ndarray) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TEMP TABLE tmp_xt_update "
            "(event_id TEXT, xt_value DOUBLE PRECISION) ON COMMIT PRESERVE ROWS"
        ))

    total_written = 0
    with engine.connect() as conn:
        for chunk in pd.read_sql(QUERY, conn, chunksize=CHUNK_SIZE):
            records = []
            lx = chunk["location_x"].to_numpy(dtype=float)
            ly = chunk["location_y"].to_numpy(dtype=float)
            c, r = _to_grid_vec(lx, ly)
            start_cells = r * GRID_COLS + c
            start_vals  = surface[start_cells]
            mask_shot = chunk["event_type"].to_numpy() == "Shot"
            mask_move = (
                chunk["event_type"].isin(["Pass", "Carry"]).to_numpy()
                & chunk["end_location_x"].notna().to_numpy()
                & chunk["end_location_y"].notna().to_numpy()
                & (chunk["outcome"].isna() | chunk["outcome"].isin(["Unknown", ""])).to_numpy()
            )
            shot_idx = np.where(mask_shot)[0]
            for i in shot_idx:
                records.append({"eid": str(chunk.iloc[i]["event_id"]), "v": round(float(start_vals[i]), 6)})
            move_idx = np.where(mask_move)[0]
            if len(move_idx):
                ex = chunk.iloc[move_idx]["end_location_x"].to_numpy(dtype=float)
                ey = chunk.iloc[move_idx]["end_location_y"].to_numpy(dtype=float)
                ec, er    = _to_grid_vec(ex, ey)
                end_cells = er * GRID_COLS + ec
                end_vals  = surface[end_cells]
                for j, i in enumerate(move_idx):
                    records.append({"eid": str(chunk.iloc[i]["event_id"]), "v": round(float(end_vals[j] - start_vals[i]), 6)})
            if records:
                with engine.begin() as wconn:
                    wconn.execute(text("INSERT INTO tmp_xt_update VALUES (:eid, :v)"), records)
                total_written += len(records)
                log.info("Buffered %d xT records (total: %d)", len(records), total_written)

    log.info("Running bulk UPDATE for %d events...", total_written)
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE fact_events fe SET xt_value = t.xt_value "
            "FROM tmp_xt_update t WHERE fe.event_id::text = t.event_id"
        ))
    log.info("Updated xt_value for %d events", total_written)


def write_xt_surface(engine, surface: np.ndarray) -> None:
    rows = [
        {"col": col, "row": row, "xt": round(float(surface[row * GRID_COLS + col]), 6)}
        for row in range(GRID_ROWS)
        for col in range(GRID_COLS)
    ]
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE xt_surface"))
        conn.execute(text("INSERT INTO xt_surface (grid_col, grid_row, xt_value) VALUES (:col, :row, :xt)"), rows)
    log.info("xt_surface written: %d cells", GRID_COLS * GRID_ROWS)


def run() -> None:
    engine = get_engine()
    log.info("Building xT matrices (chunked, %d rows/chunk)...", CHUNK_SIZE)
    shot_prob, goal_prob, transition = build_matrices(engine)
    log.info("Solving xT surface (%d iterations)...", ITERATIONS)
    surface = solve_xt(shot_prob, goal_prob, transition)
    log.info("xT surface — min=%.4f max=%.4f mean=%.4f", surface.min(), surface.max(), surface.mean())
    log.info("Computing and writing event xT values (chunked)...")
    compute_and_write_xt(engine, surface)
    write_xt_surface(engine, surface)
    log.info("xT computation complete.")


if __name__ == "__main__":
    run()
