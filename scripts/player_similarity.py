import json
import logging
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text

from db_utils import get_engine

log = logging.getLogger(__name__)

# Minimum matches played required to include a player in the model.
# Players with fewer matches produce unreliable feature vectors.
MIN_MATCHES = 3

# Top-N most similar players stored per player.
N_NEIGHBORS = 10

MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "player_similarity_model.joblib"
)

FEATURE_COLS = [
    "shots_per_match",
    "goals_per_match",
    "avg_shot_distance",
    "avg_shot_angle",
    "header_ratio",
    "xg_per_shot",
    "passes_per_match",
    "pass_completion_rate",
    "avg_pass_distance_m",
    "avg_location_x",
    "avg_location_y",
    "under_pressure_ratio",
]


def fetch_player_features(engine) -> pd.DataFrame:
    query = text("""
        WITH shot_agg AS (
            SELECT
                s.player_id,
                COUNT(*)                                                AS total_shots,
                SUM(s.is_goal)                                         AS goals,
                AVG(s.distance_to_goal)                                AS avg_shot_distance,
                AVG(s.shot_angle)                                      AS avg_shot_angle,
                AVG(s.is_header::int)                                  AS header_ratio,
                COUNT(DISTINCT s.match_id)                             AS shot_matches
            FROM analytics_silver.silver_shots s
            GROUP BY s.player_id
        ),
        pass_agg AS (
            SELECT
                p.player_id,
                COUNT(*)                                                AS total_passes,
                AVG(p.is_successful::int)                              AS pass_completion_rate,
                AVG(p.pass_distance_m)                                 AS avg_pass_distance_m,
                AVG(p.location_x)                                      AS avg_location_x,
                AVG(p.location_y)                                      AS avg_location_y,
                AVG(p.under_pressure::int)                             AS under_pressure_ratio,
                COUNT(DISTINCT p.match_id)                             AS matches_played
            FROM analytics_silver.silver_passes p
            GROUP BY p.player_id
        ),
        xg_agg AS (
            SELECT
                s.player_id,
                AVG(fe.xg_value)                                       AS xg_per_shot
            FROM analytics_silver.silver_shots s
            JOIN fact_events fe ON s.event_id = fe.event_id
            WHERE fe.xg_value IS NOT NULL
            GROUP BY s.player_id
        )
        SELECT
            dp.player_id,
            dp.player_name,
            dp.position,
            COALESCE(sa.total_shots, 0)::float
                / NULLIF(pa.matches_played, 0)                         AS shots_per_match,
            COALESCE(sa.goals, 0)::float
                / NULLIF(pa.matches_played, 0)                         AS goals_per_match,
            COALESCE(sa.avg_shot_distance, 0.0)                        AS avg_shot_distance,
            COALESCE(sa.avg_shot_angle, 0.0)                           AS avg_shot_angle,
            COALESCE(sa.header_ratio, 0.0)                             AS header_ratio,
            COALESCE(xa.xg_per_shot, 0.0)                              AS xg_per_shot,
            COALESCE(pa.total_passes, 0)::float
                / NULLIF(pa.matches_played, 0)                         AS passes_per_match,
            COALESCE(pa.pass_completion_rate, 0.0)                     AS pass_completion_rate,
            COALESCE(pa.avg_pass_distance_m, 0.0)                      AS avg_pass_distance_m,
            COALESCE(pa.avg_location_x, 52.5)                          AS avg_location_x,
            COALESCE(pa.avg_location_y, 34.0)                          AS avg_location_y,
            COALESCE(pa.under_pressure_ratio, 0.0)                     AS under_pressure_ratio
        FROM dim_players dp
        LEFT JOIN shot_agg sa ON dp.player_id = sa.player_id
        LEFT JOIN pass_agg pa ON dp.player_id = pa.player_id
        LEFT JOIN xg_agg   xa ON dp.player_id = xa.player_id
        WHERE pa.matches_played >= :min_matches
        ORDER BY dp.player_id
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"min_matches": MIN_MATCHES})
    log.info("Fetched features for %d players (min_matches=%d)", len(df), MIN_MATCHES)
    return df


def build_similarity_model(
    df: pd.DataFrame,
) -> tuple[NearestNeighbors, StandardScaler, np.ndarray]:
    X = df[FEATURE_COLS].fillna(0.0).values.astype(float)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    model = NearestNeighbors(
        n_neighbors=min(N_NEIGHBORS + 1, len(df)),
        metric="cosine",
        algorithm="brute",
    )
    model.fit(X_scaled)
    return model, scaler, X_scaled


def compute_similarity_rows(
    df: pd.DataFrame,
    model: NearestNeighbors,
    X_scaled: np.ndarray,
) -> list[dict]:
    distances, indices = model.kneighbors(X_scaled)
    player_ids = df["player_id"].tolist()
    rows = []
    for i, (dists, idxs) in enumerate(zip(distances, indices)):
        src_pid = player_ids[i]
        rank = 1
        for dist, j in zip(dists, idxs):
            if j == i:
                continue  # skip self
            rows.append(
                {
                    "pid": src_pid,
                    "sid": player_ids[j],
                    "score": round(float(max(0.0, 1.0 - dist)), 6),
                    "rank": rank,
                }
            )
            rank += 1
            if rank > N_NEIGHBORS:
                break
    return rows


def write_similarity_scores(engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO player_similarity_scores
                    (player_id, similar_player_id, similarity_score, rank)
                VALUES (:pid, :sid, :score, :rank)
                ON CONFLICT (player_id, similar_player_id) DO UPDATE
                    SET similarity_score = EXCLUDED.similarity_score,
                        rank             = EXCLUDED.rank,
                        computed_at      = NOW()
            """),
            rows,
        )
    log.info("Upserted %d similarity score rows", len(rows))


def save_model_artifact(
    engine, model: NearestNeighbors, scaler: StandardScaler, n_players: int
) -> None:
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump({"model": model, "scaler": scaler, "features": FEATURE_COLS}, MODEL_PATH)
    log.info("Player similarity model saved to %s", MODEL_PATH)

    metrics = {
        "players_indexed": n_players,
        "n_neighbors": N_NEIGHBORS,
        "features": FEATURE_COLS,
        "min_matches_threshold": MIN_MATCHES,
    }
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO model_registry (model_name, version, metrics, artifact_path)
                VALUES ('player_similarity', '1.0', :m, :path)
            """),
            {"m": json.dumps(metrics), "path": MODEL_PATH},
        )
    log.info("Player similarity model registered: %s", metrics)


def run() -> None:
    engine = get_engine()
    df = fetch_player_features(engine)

    if len(df) == 0:
        log.warning(
            "No players found with min_matches>=%d — skipping similarity computation. "
            "Run with ingest_season=true or ingest more matches first.",
            MIN_MATCHES,
        )
        return

    if len(df) < N_NEIGHBORS + 1:
        log.warning(
            "Only %d players available — similarity results will be limited "
            "(need at least %d for full top-%d neighbours).",
            len(df),
            N_NEIGHBORS + 1,
            N_NEIGHBORS,
        )

    model, scaler, X_scaled = build_similarity_model(df)
    rows = compute_similarity_rows(df, model, X_scaled)
    write_similarity_scores(engine, rows)
    save_model_artifact(engine, model, scaler, len(df))

    log.info(
        "Player similarity complete. Players indexed: %d | Score rows written: %d",
        len(df),
        len(rows),
    )


if __name__ == "__main__":
    run()
