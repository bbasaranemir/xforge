"""
XGBoost Expected Pass (xP) model.

Features per pass:
  - start_x, start_y          : pass origin (normalized 0-1)
  - end_x, end_y              : intended destination (normalized 0-1)
  - distance                  : Euclidean length of pass
  - angle                     : direction relative to goal (radians)
  - under_pressure            : boolean flag
  - minute_bin                : match phase (0=first half, 1=second half, 2=extra time)

Target:
  - 1 if outcome == 'Unknown' (silver layer convention for successful pass)
  - 0 otherwise (Incomplete, Out, etc.)

Trained model is serialized with joblib and recorded in model_registry.
Predictions are written to fact_events.xp_value.
"""

import gc
import json
import logging
import os

import joblib
import numpy as np
import pandas as pd
import psycopg2
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.model_selection import train_test_split
from sqlalchemy import create_engine, text
from xgboost import XGBClassifier

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_user = os.environ.get("POSTGRES_USER", "analytics")
_pass = os.environ.get("POSTGRES_PASSWORD", "analytics")
_host = os.environ.get("POSTGRES_HOST", "postgres")
_db = os.environ.get("POSTGRES_DB", "football_db")
DB_URL = f"postgresql+psycopg2://{_user}:{_pass}@{_host}:5432/{_db}"

# Model artifact stored alongside other pipeline outputs
MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "xp_model.joblib")
# V2: universal 105×68 metric pitch
PITCH_X = 105.0
PITCH_Y = 68.0
GOAL_CENTER = (105.0, 34.0)
TRAIN_SAMPLE = 300_000  # rows for training — plenty for a good AUC
WRITE_CHUNK = (
    50_000  # rows per commit — 5x throughput; index on xp_value IS NULL required
)


def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


def _conn_params() -> dict:
    return dict(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "football_db"),
        user=os.environ.get("POSTGRES_USER", "analytics"),
        password=os.environ.get("POSTGRES_PASSWORD", "analytics"),
    )


# ─── Data preparation ─────────────────────────────────────────────────────────

PASS_COLS = [
    "event_id",
    "location_x",
    "location_y",
    "end_location_x",
    "end_location_y",
    "outcome",
    "under_pressure",
    "minute",
]

# silver_passes already filters event_type='Pass' and non-null coordinates.
# outcome is 'Unknown' for successful passes (silver coalesces NULL → 'Unknown').
SILVER_PASSES = "analytics_silver.silver_passes"


def fetch_passes_sample(engine) -> pd.DataFrame:
    """Load a stratified random sample for training (memory-safe)."""
    q = text(f"""
        SELECT event_id, location_x, location_y,
               end_location_x, end_location_y,
               outcome, under_pressure, minute
        FROM {SILVER_PASSES}
        ORDER BY RANDOM()
        LIMIT {TRAIN_SAMPLE}
    """)
    with engine.connect() as conn:
        df = pd.read_sql(q, conn)
    log.info("Training sample loaded: %d passes", len(df))
    return df


def stream_passes_for_prediction(feature_cols: list, model):
    """Server-side cursor — yields (ids, probas) chunks without RAM spike."""
    conn = psycopg2.connect(**_conn_params())
    conn.autocommit = False
    cur = conn.cursor("xp_pred_cur")
    cur.execute(f"""
        SELECT event_id, location_x, location_y,
               end_location_x, end_location_y,
               outcome, under_pressure, minute
        FROM {SILVER_PASSES}
    """)
    while True:
        rows = cur.fetchmany(WRITE_CHUNK)
        if not rows:
            break
        chunk = pd.DataFrame(rows, columns=PASS_COLS)
        X, _, ids, _ = build_features(chunk)
        probas = model.predict_proba(X)[:, 1]
        yield ids, probas
    cur.close()
    conn.close()


def build_features(df: pd.DataFrame) -> tuple:
    df = df.copy()
    # psycopg2 server-side cursor returns NUMERIC as decimal.Decimal; cast to float.
    for col in ("location_x", "location_y", "end_location_x", "end_location_y"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["start_x_n"] = df["location_x"] / PITCH_X
    df["start_y_n"] = df["location_y"] / PITCH_Y
    df["end_x_n"] = df["end_location_x"] / PITCH_X
    df["end_y_n"] = df["end_location_y"] / PITCH_Y
    df["distance"] = (
        np.sqrt(
            (df["end_location_x"] - df["location_x"]) ** 2
            + (df["end_location_y"] - df["location_y"]) ** 2
        )
        / PITCH_X
    )

    # Angle between pass vector and direction toward goal center
    dx_pass = df["end_location_x"] - df["location_x"]
    dy_pass = df["end_location_y"] - df["location_y"]
    dx_goal = GOAL_CENTER[0] - df["location_x"]
    dy_goal = GOAL_CENTER[1] - df["location_y"]
    dot = dx_pass * dx_goal + dy_pass * dy_goal
    _mp = np.sqrt(dx_pass**2 + dy_pass**2)
    _mg = np.sqrt(dx_goal**2 + dy_goal**2)
    mag_p = np.where(_mp == 0, np.nan, _mp)
    mag_g = np.where(_mg == 0, np.nan, _mg)
    df["angle_to_goal"] = np.arccos(np.clip(dot / (mag_p * mag_g), -1.0, 1.0)).fillna(0)

    df["under_pressure"] = df["under_pressure"].astype(int)
    df["minute_bin"] = pd.cut(
        df["minute"], bins=[-1, 45, 90, 999], labels=[0, 1, 2]
    ).astype(int)

    # Target: 1 = successful pass.
    # Silver coalesces NULL outcome → 'Unknown'; Opta successful passes also map to 'Unknown'.
    df["target"] = (df["outcome"] == "Unknown").astype(int)

    feature_cols = [
        "start_x_n",
        "start_y_n",
        "end_x_n",
        "end_y_n",
        "distance",
        "angle_to_goal",
        "under_pressure",
        "minute_bin",
    ]

    X = df[feature_cols].values
    y = df["target"].values
    ids = df["event_id"].values

    return X, y, ids, feature_cols


# ─── Training ─────────────────────────────────────────────────────────────────


def train(X, y) -> tuple:
    X_tr, X_val, y_tr, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = XGBClassifier(
        n_estimators=400,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        use_label_encoder=False,
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
    metrics = {
        "auc": round(auc, 4),
        "log_loss": round(logloss, 4),
        "train_size": len(X_tr),
    }

    log.info("xP model — AUC=%.4f  log_loss=%.4f", auc, logloss)
    return model, metrics


# ─── Persistence ──────────────────────────────────────────────────────────────


def save_model(engine, model, metrics: dict, feature_cols: list) -> None:
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump({"model": model, "features": feature_cols}, MODEL_PATH)

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO model_registry (model_name, version, metrics, artifact_path)
                VALUES ('xp_model', '1.0', :m, :path)
            """),
            {"m": json.dumps(metrics), "path": MODEL_PATH},
        )
    log.info("xP model saved: %s", MODEL_PATH)


def write_xp_values_chunked(engine, model, feature_cols: list) -> None:
    """Stream all passes, predict in chunks, write with individual commits."""
    total = 0
    for ids, probas in stream_passes_for_prediction(feature_cols, model):
        rows = [{"xp": round(float(p), 6), "eid": str(i)} for i, p in zip(ids, probas)]
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE fact_events SET xp_value = :xp WHERE event_id = :eid"),
                rows,
            )
        total += len(rows)
        log.info("xP written: %d / ~total (chunk committed)", total)
    log.info("xP write complete — %d passes updated", total)


# ─── Entry point ─────────────────────────────────────────────────────────────


def run() -> None:
    engine = get_engine()

    # ── Early-exit: skip if model exists and all xP predictions are written ──
    if os.path.exists(MODEL_PATH):
        with engine.connect() as conn:
            remaining = conn.execute(
                text("""
                    SELECT COUNT(*) FROM fact_events
                    WHERE event_type = 'Pass'
                      AND location_x IS NOT NULL AND end_location_x IS NOT NULL
                      AND xp_value IS NULL
                """)
            ).scalar()
        if remaining == 0:
            log.info("xP already complete (model exists, 0 rows remaining) — skipping.")
            return
        log.info("xP resume: %d passes still need predictions.", remaining)

    # ── Phase 1: Train on a memory-safe sample ────────────────────────────────
    df = fetch_passes_sample(engine)
    if len(df) < 500:
        log.warning("Insufficient pass data (%d rows); skipping xP training.", len(df))
        return

    X, y, _, feature_cols = build_features(df)
    model, metrics = train(X, y)
    save_model(engine, model, metrics, feature_cols)

    # Free training data before prediction phase — critical for low-RAM envs
    del df, X, y
    gc.collect()
    log.info("Training data freed from memory. Starting prediction phase...")

    # ── Phase 2: Predict + write all passes in chunks (no OOM) ───────────────
    write_xp_values_chunked(engine, model, feature_cols)

    log.info("xP modelling complete.")


if __name__ == "__main__":
    run()
