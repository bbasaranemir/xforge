"""
Tactical analysis layer.

  1. Set-piece delivery clustering — K-Means on corner/free-kick origin
     coordinates. Clusters reveal preferred delivery zones.
  2. Press trigger detection — identifies high-press sequences: 3+ defensive
     actions within a 5-second window following a ball recovery.

Results are written back to set_piece_clusters and fact_events (press_trigger flag
stored in model_registry as JSON metadata).
"""

import json
import logging
import os

import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_URL = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:{os.environ.get('POSTGRES_PORT', '5432')}"
    f"/{os.environ['POSTGRES_DB']}"
)

# Only Corner is a genuine set-piece delivery type detectable from StatsBomb open data.
# Free kicks cannot be distinguished without the pass_type sub-field (not persisted).
# Shot locations are clustered separately under SHOT_CLUSTER_TYPES for shot-map analysis.
SET_PIECE_TYPES = ("Corner",)
SHOT_CLUSTER_TYPES = ("Shot",)
PRESS_TYPES = ("Pressure", "Interception", "Tackle", "Block")
N_CLUSTERS = 6
PRESS_WINDOW_SEC = 5


def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


# ─── Set-piece clustering ─────────────────────────────────────────────────────


def fetch_set_pieces(engine) -> pd.DataFrame:
    """
    StatsBomb stores corner kicks as event_type='Pass' originating from the
    corner-flag positions (pitch is 120×80; corners at x<3 or x>117, y<3 or y>77).
    Free kicks cannot be distinguished without the pass_type sub-field (not persisted).

    Shots are fetched separately for shot-map clustering (not classified as set pieces).
    """
    q = text(
        """
        SELECT event_id, 'Corner' AS event_type,
               location_x, location_y,
               end_location_x, end_location_y, match_id, team_id
        FROM fact_events
        WHERE event_type = 'Pass'
          AND location_x IS NOT NULL AND location_y IS NOT NULL
          AND (
              (location_x <  3  AND (location_y <  3  OR location_y > 77))
              OR
              (location_x > 117 AND (location_y <  3  OR location_y > 77))
          )

        UNION ALL

        -- Shot locations clustered separately for shot-map analysis (not a set piece)
        SELECT event_id, 'Shot' AS event_type,
               location_x, location_y,
               end_location_x, end_location_y, match_id, team_id
        FROM fact_events
        WHERE event_type = 'Shot'
          AND location_x IS NOT NULL AND location_y IS NOT NULL
    """
    )
    with engine.connect() as conn:
        return pd.read_sql(q, conn)


def cluster_set_pieces(df: pd.DataFrame) -> dict:
    """
    Cluster Corner deliveries (set-piece analysis) and Shot locations (shot-map analysis)
    separately. SET_PIECE_TYPES clusters appear in set_piece_clusters labelled as
    genuine set pieces; SHOT_CLUSTER_TYPES clusters are labelled 'Shot' to distinguish.
    """
    results = {}
    all_types = SET_PIECE_TYPES + SHOT_CLUSTER_TYPES
    for et in all_types:
        subset = df[df["event_type"] == et].copy()
        if len(subset) < N_CLUSTERS:
            log.warning("Not enough %s events for clustering (%d)", et, len(subset))
            continue

        coords = subset[["location_x", "location_y"]].values
        scaler = StandardScaler()
        scaled = scaler.fit_transform(coords)

        km = KMeans(n_clusters=N_CLUSTERS, n_init=10, random_state=42)
        labels = km.fit_predict(scaled)
        centers = scaler.inverse_transform(km.cluster_centers_)

        subset = subset.copy()
        subset["cluster_label"] = labels
        results[et] = (subset, centers)

        category = "set-piece" if et in SET_PIECE_TYPES else "shot-map"
        log.info(
            "%s (%s): %d events → %d clusters", et, category, len(subset), N_CLUSTERS
        )

    return results


def save_clusters(engine, cluster_results: dict) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM set_piece_clusters"))

        if not cluster_results:
            log.info("No cluster results to save — skipping INSERT.")
            return

        rows = []
        for et, (subset, centers) in cluster_results.items():
            for label, center in enumerate(centers):
                count = int((subset["cluster_label"] == label).sum())
                rows.append(
                    {
                        "event_type": et,
                        "cluster_label": label,
                        "center_x": round(float(center[0]), 2),
                        "center_y": round(float(center[1]), 2),
                        "member_count": count,
                    }
                )

        if rows:
            conn.execute(
                text(
                    """
                    INSERT INTO set_piece_clusters
                        (event_type, cluster_label, center_x, center_y, member_count)
                    VALUES
                        (:event_type, :cluster_label, :center_x, :center_y, :member_count)
                """
                ),
                rows,
            )
    log.info("Saved %d cluster centroids", len(rows))


# ─── Press trigger detection ─────────────────────────────────────────────────


def fetch_press_candidates(engine) -> pd.DataFrame:
    """Fetch all defensive + recovery actions ordered by match and time."""
    q = text(
        """
        SELECT event_id, match_id, team_id, event_type,
               minute, second
        FROM fact_events
        WHERE event_type IN ('Pressure', 'Interception', 'Tackle', 'Block', 'Ball Recovery')
        ORDER BY match_id, minute, second
    """
    )
    with engine.connect() as conn:
        return pd.read_sql(q, conn)


def detect_press_triggers(df: pd.DataFrame) -> pd.DataFrame:
    """
    A press trigger is a Ball Recovery followed by 3+ defensive actions
    from the same team within PRESS_WINDOW_SEC seconds.
    Returns a DataFrame of event_ids flagged as press triggers.
    """
    df = df.copy()
    df["time_sec"] = df["minute"] * 60 + df["second"].fillna(0)

    triggered = []

    for (match_id, team_id), group in df.groupby(["match_id", "team_id"]):
        group = group.sort_values("time_sec").reset_index(drop=True)
        recovery = group[group["event_type"] == "Ball Recovery"]

        for _, rec in recovery.iterrows():
            window = group[
                (group["time_sec"] >= rec["time_sec"])
                & (group["time_sec"] <= rec["time_sec"] + PRESS_WINDOW_SEC)
                & (group["event_type"].isin(PRESS_TYPES))
            ]
            if len(window) >= 3:
                triggered.extend(window["event_id"].tolist())

    return pd.DataFrame({"event_id": list(set(triggered))})


def save_press_metadata(engine, triggers: pd.DataFrame) -> None:
    metrics = {"press_trigger_count": len(triggers)}
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO model_registry (model_name, version, metrics)
                VALUES ('press_trigger_detector', '1.0', :m)
            """
            ),
            {"m": json.dumps(metrics)},
        )
    log.info("Press triggers detected: %d", len(triggers))


# ─── Entry point ─────────────────────────────────────────────────────────────


def run() -> None:
    engine = get_engine()

    sp_df = fetch_set_pieces(engine)
    clusters = cluster_set_pieces(sp_df)
    save_clusters(engine, clusters)

    press_df = fetch_press_candidates(engine)
    triggers = detect_press_triggers(press_df)
    save_press_metadata(engine, triggers)

    log.info("Tactical modelling complete.")


if __name__ == "__main__":
    run()
