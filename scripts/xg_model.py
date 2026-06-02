"""
XGBoost Expected Goals (xG) model.

Features per shot:
  - distance_to_goal  : Euclidean distance from shot location to goal centre (normalised 0-1)
  - angle_to_goal     : angle between shot vector and goal-centre direction (radians)
  - under_pressure    : boolean flag
  - minute_bin        : match phase (0=first half, 1=second half, 2=extra time)

Target:
  - 1 if outcome = 'Goal'
  - 0 otherwise (Saved, Blocked, Off T, Post, Wayward, etc.)

Note on features:
  raw_json is excluded from bulk inserts in massive_ingestion.py (JSONB casting
  limitation), so shot.body_part and shot.type are not available from the DB.
  Location-based features alone yield AUC ~0.77-0.82, consistent with the
  academic literature for coordinate-only xG models.

Note on class imbalance:
  ~10-12% of StatsBomb open-data shots result in goals.
  scale_pos_weight is computed dynamically from the training sample.

Trained model is serialized with joblib and recorded in model_registry.
Predictions are written to fact_events.xg_value (shots only).
"""

import gc
import json
import logging
import os

import joblib
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.model_selection import train_test_split
from sqlalchemy import create_engine, text
from xgboost import XGBClassifier

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_URL = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:{os.environ.get('POSTGRES_PORT', '5432')}"
    f"/{os.environ['POSTGRES_DB']}"
)

MODEL_PATH = "/opt/airflow/reports/xg_model.joblib"
PITCH_X = 120.0
PITCH_Y = 80.0
GOAL_CENTER = (120.0, 40.0)
TRAIN_SAMPLE = 80_000  # shots are fewer than passes (~150k total) — 80k is sufficient
WRITE_CHUNK = 50_000  # rows per commit — consistent with xP pipeline


def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


def _conn_params() -> dict:
    return dict(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


# ─── Data preparation ─────────────────────────────────────────────────────────

SHOT_COLS = [
    "event_id",
    "location_x",
    "location_y",
    "outcome",
    "under_pressure",
    "minute",
]

SHOT_WHERE = """
    event_type = 'Shot'
    AND location_x IS NOT NULL
    AND location_y IS NOT NULL
"""


def fetch_shots_sample(engine) -> pd.DataFrame:
    """Load a stratified random sample for training (memory-safe)."""
    q = text(
        f"""
        SELECT event_id, location_x, location_y,
               outcome, under_pressure, minute
        FROM fact_events
        WHERE {SHOT_WHERE}
        ORDER BY RANDOM()
        LIMIT {TRAIN_SAMPLE}
        """
    )
    with engine.connect() as conn:
        df = pd.read_sql(q, conn)
    log.info("Training sample loaded: %d shots", len(df))
    return df


def stream_shots_for_prediction(model):
    """Server-side cursor — yields (ids, probas) chunks without RAM spike."""
    conn = psycopg2.connect(**_conn_params())
    conn.autocommit = False
    cur = conn.cursor("xg_pred_cur")
    cur.execute(
        f"""
        SELECT event_id, location_x, location_y,
               outcome, under_pressure, minute
        FROM fact_events
        WHERE {SHOT_WHERE}
          AND xg_value IS NULL
        """
    )
    while True:
        rows = cur.fetchmany(WRITE_CHUNK)
        if not rows:
            break
        chunk = pd.DataFrame(rows, columns=SHOT_COLS)
        X, _, ids, _ = build_features(chunk)
        probas = model.predict_proba(X)[:, 1]
        yield ids, probas
    cur.close()
    conn.close()


def build_features(df: pd.DataFrame) -> tuple:
    df = df.copy()

    # Distance from shot location to goal centre (normalised by pitch length)
    df["distance_to_goal"] = (
        np.sqrt(
            (GOAL_CENTER[0] - df["location_x"]) ** 2
            + (GOAL_CENTER[1] - df["location_y"]) ** 2
        )
        / PITCH_X
    )

    # Angle between shot-to-goal vector and the pitch horizontal axis.
    # Shots directly in front of goal → small angle; wide angles → harder.
    dx = GOAL_CENTER[0] - df["location_x"]
    dy = GOAL_CENTER[1] - df["location_y"]
    goal_width = 7.32  # metres; StatsBomb pitch scale ≈ 1 unit per metre
    # Half-angle subtended by the goal from the shot location
    df["angle_to_goal"] = np.arctan(
        goal_width * dx / (dx**2 + dy**2 - (goal_width / 2) ** 2)
    )
    df["angle_to_goal"] = df["angle_to_goal"].abs().fillna(0.0)

    df["under_pressure"] = df["under_pressure"].astype(int)
    df["minute_bin"] = pd.cut(
        df["minute"], bins=[-1, 45, 90, 999], labels=[0, 1, 2]
    ).astype(int)

    # Target: 1 = goal
    df["target"] = (df["outcome"] == "Goal").astype(int)

    feature_cols = [
        "distance_to_goal",
        "angle_to_goal",
        "under_pressure",
        "minute_bin",
    ]

    X = df[feature_cols].fillna(0).values
    y = df["target"].values
    ids = df["event_id"].values

    return X, y, ids, feature_cols


# ─── Training ─────────────────────────────────────────────────────────────────


def train(X, y) -> tuple:
    # Compute class weight dynamically — avoids hardcoded assumption about goal rate
    n_neg = int((y == 0).sum())
    n_pos = int((y == 1).sum())
    spw = round(n_neg / max(n_pos, 1), 2)
    log.info(
        "Class balance: %d goals / %d non-goals → scale_pos_weight=%.2f",
        n_pos,
        n_neg,
        spw,
    )

    X_tr, X_val, y_tr, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=spw,
        eval_metric="logloss",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(
        X_tr,
        y_tr,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    proba = model.predict_proba(X_val)[:, 1]
    auc = roc_auc_score(y_val, proba)
    logloss = log_loss(y_val, proba)
    accuracy = float((model.predict(X_val) == y_val).mean())
    metrics = {
        "auc": round(auc, 4),
        "log_loss": round(logloss, 4),
        "accuracy": round(accuracy, 4),
        "train_size": len(X_tr),
        "scale_pos_weight": spw,
    }

    log.info("=== xG Model Metrics ===")
    log.info("AUC:      %.4f", auc)
    log.info("Log-loss: %.4f", logloss)
    log.info("Accuracy: %.4f", accuracy)
    return model, metrics


# ─── Persistence ──────────────────────────────────────────────────────────────


def save_model(engine, model, metrics: dict, feature_cols: list) -> None:
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump({"model": model, "features": feature_cols}, MODEL_PATH)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO model_registry (model_name, version, metrics, artifact_path)
                VALUES ('xg_model', '1.0', :m, :path)
                """
            ),
            {"m": json.dumps(metrics), "path": MODEL_PATH},
        )
    log.info("xG model saved: %s", MODEL_PATH)


def write_xg_values_chunked(engine, model) -> None:
    """Stream all shots, predict in chunks, write with individual commits."""
    total = 0
    for ids, probas in stream_shots_for_prediction(model):
        rows = [{"xg": round(float(p), 6), "eid": str(i)} for i, p in zip(ids, probas)]
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE fact_events SET xg_value = :xg WHERE event_id = :eid"),
                rows,
            )
        total += len(rows)
        log.info("xG written: %d rows committed", total)
    log.info("xG write complete — %d shots updated", total)


# ─── Entry point ─────────────────────────────────────────────────────────────


def run() -> None:
    engine = get_engine()

    # ── Early-exit: skip if model exists and all predictions are written ──────
    if os.path.exists(MODEL_PATH):
        with engine.connect() as conn:
            remaining = conn.execute(
                text(
                    f"SELECT COUNT(*) FROM fact_events WHERE {SHOT_WHERE} AND xg_value IS NULL"
                )
            ).scalar()
        if remaining == 0:
            log.info("xG already complete (model exists, 0 rows remaining) — skipping.")
            return
        log.info("xG resume: %d shots still need predictions.", remaining)

    # ── Phase 1: Train on a memory-safe sample ────────────────────────────────
    df = fetch_shots_sample(engine)
    if len(df) < 200:
        log.warning("Insufficient shot data (%d rows); skipping xG training.", len(df))
        return

    X, y, _, feature_cols = build_features(df)
    model, metrics = train(X, y)
    save_model(engine, model, metrics, feature_cols)

    # Free training data before prediction phase — critical for low-RAM envs
    del df, X, y
    gc.collect()
    log.info("Training data freed. Starting prediction phase...")

    # ── Phase 2: Predict + write all shots in chunks (no OOM) ────────────────
    write_xg_values_chunked(engine, model)

    log.info("xG modelling complete.")


if __name__ == "__main__":
    run()
