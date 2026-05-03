"""
xT (Expected Threat) surface computation via value iteration.

Memory-efficient:
  - psycopg2 server-side cursor for true streaming (no RAM spike)
  - execute_values() bulk-insert into staging table
  - Single UPDATE fact_events FROM staging (one pass, index-friendly)
"""

import logging
import os
from typing import Tuple

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

GRID_COLS  = 16
GRID_ROWS  = 12
PITCH_X    = 120.0
PITCH_Y    = 80.0
ITERATIONS = 15
CHUNK_SIZE = 50_000

QUERY_COLS = ["event_id", "event_type", "location_x", "location_y",
              "end_location_x", "end_location_y", "outcome"]

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


def _conn_params() -> dict:
    return dict(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def get_engine():
    p = _conn_params()
    url = (f"postgresql+psycopg2://{p['user']}:{p['password']}"
           f"@{p['host']}:{p['port']}/{p['dbname']}")
    return create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=0)


def _stream_chunks(cursor_name: str):
    """True server-side cursor — fetches CHUNK_SIZE rows at a time."""
    conn = psycopg2.connect(**_conn_params())
    conn.autocommit = False
    cur = conn.cursor(cursor_name)
    cur.execute(QUERY)
    while True:
        rows = cur.fetchmany(CHUNK_SIZE)
        if not rows:
            break
        yield pd.DataFrame(rows, columns=QUERY_COLS)
    cur.close()
    conn.close()


def _to_grid_vec(x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    cols = np.minimum((x / (PITCH_X / GRID_COLS)).astype(int), GRID_COLS - 1)
    rows = np.minimum((y / (PITCH_Y / GRID_ROWS)).astype(int), GRID_ROWS - 1)
    return cols, rows


def build_matrices():
    n            = GRID_COLS * GRID_ROWS
    shot_count   = np.zeros(n)
    goal_count   = np.zeros(n)
    move_matrix  = np.zeros((n, n))
    action_count = np.zeros(n)
    total        = 0

    for chunk in _stream_chunks("xt_build_cur"):
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
            ec, er = _to_grid_vec(ex, ey)
            np.add.at(move_matrix, (cells[mask_move], er * GRID_COLS + ec), 1)

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


def compute_and_write_xt(surface: np.ndarray) -> None:
    """
    1. Stream events via server-side cursor
    2. Bulk-insert (event_id, xt_value) into a temp staging table
    3. ONE big UPDATE fact_events FROM staging  ← fast, single pass
    """
    conn = psycopg2.connect(**_conn_params())
    conn.autocommit = False
    cur = conn.cursor()

    # Staging table on the SAME connection as the final UPDATE
    cur.execute("DROP TABLE IF EXISTS _xt_staging")
    cur.execute(
        "CREATE TEMP TABLE _xt_staging "
        "(event_id TEXT, xt_value DOUBLE PRECISION) ON COMMIT PRESERVE ROWS"
    )
    conn.commit()
    log.info("Staging table created.")

    total_written = 0
    for chunk in _stream_chunks("xt_write_cur"):
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

        tuples = []
        if mask_shot.any():
            tuples.extend(
                (str(eid), round(float(v), 6))
                for eid, v in zip(chunk.loc[mask_shot, "event_id"], start_vals[mask_shot])
            )
        if mask_move.any():
            ex = chunk.loc[mask_move, "end_location_x"].to_numpy(dtype=float)
            ey = chunk.loc[mask_move, "end_location_y"].to_numpy(dtype=float)
            ec, er   = _to_grid_vec(ex, ey)
            end_vals = surface[er * GRID_COLS + ec]
            deltas   = end_vals - start_vals[mask_move]
            tuples.extend(
                (str(eid), round(float(d), 6))
                for eid, d in zip(chunk.loc[mask_move, "event_id"], deltas)
            )

        if tuples:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO _xt_staging (event_id, xt_value) VALUES %s",
                tuples,
                page_size=5000,
            )
            total_written += len(tuples)
            if total_written % 500_000 < len(tuples):
                log.info("Staged %d xT records so far...", total_written)

    conn.commit()
    log.info("All %d records staged. Running bulk UPDATE...", total_written)

    cur.execute(
        "UPDATE fact_events fe "
        "SET xt_value = s.xt_value "
        "FROM _xt_staging s "
        "WHERE fe.event_id = s.event_id"
    )
    conn.commit()
    log.info("Bulk UPDATE complete — %d events updated.", total_written)
    cur.close()
    conn.close()


def write_xt_surface(engine, surface: np.ndarray) -> None:
    rows = [
        {"col": col, "row": row,
         "xt": round(float(surface[row * GRID_COLS + col]), 6)}
        for row in range(GRID_ROWS)
        for col in range(GRID_COLS)
    ]
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE xt_surface"))
        conn.execute(
            text("INSERT INTO xt_surface (grid_col, grid_row, xt_value) "
                 "VALUES (:col, :row, :xt)"),
            rows,
        )
    log.info("xt_surface written: %d cells", GRID_COLS * GRID_ROWS)


def run() -> None:
    engine = get_engine()

    log.info("Building xT matrices (server-side cursor, %d rows/chunk)...", CHUNK_SIZE)
    shot_prob, goal_prob, transition = build_matrices()

    log.info("Solving xT surface (%d iterations)...", ITERATIONS)
    surface = solve_xt(shot_prob, goal_prob, transition)
    log.info("xT surface — min=%.4f max=%.4f mean=%.4f",
             surface.min(), surface.max(), surface.mean())

    log.info("Computing and staging xT values...")
    compute_and_write_xt(surface)

    write_xt_surface(engine, surface)
    log.info("xT computation complete.")


if __name__ == "__main__":
    run()
