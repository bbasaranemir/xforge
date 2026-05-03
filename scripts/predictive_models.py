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
  - 1 if pass_outcome IS NULL (StatsBomb convention for successful pass)
  - 0 otherwise (Incomplete, Out, Unknown, etc.)

Trained model is serialized with joblib and recorded in model_registry.
Predictions are written to fact_events.xp_value.
"""

import json
import logging
import os

import joblib
import numpy as np
import pandas as pd
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

MODEL_PATH   = "/opt/airflow/reports/xp_model.joblib"
PITCH_X      = 120.0
PITCH_Y      = 80.0
GOAL_CENTER  = (120.0, 40.0)


def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


# ─── Data preparation ─────────────────────────────────────────────────────────

def fetch_passes(engine) -> pd.DataFrame:
    q = text("""
        SELECT event_id, location_x, location_y,
               end_location_x, end_location_y,
               outcome, under_pressure, minute
        FROM fact_events
        WHERE event_type = 'Pass'
          AND location_x     IS NOT NULL
          AND location_y     IS NOT NULL
          AND end_location_x IS NOT NULL
          AND end_location_y IS NOT NULL
    """)
    with engine.connect() as conn:
        return pd.read_sql(q, conn)


def build_features(df: pd.DataFrame) -> tuple:
    df = df.copy()

    df["start_x_n"]    = df["location_x"] / PITCH_X
    df["start_y_n"]    = df["location_y"] / PITCH_Y
    df["end_x_n"]      = df["end_location_x"] / PITCH_X
    df["end_y_n"]      = df["end_location_y"] / PITCH_Y
    df["distance"]     = np.sqrt(
        (df["end_location_x"] - df["location_x"]) ** 2
        + (df["end_location_y"] - df["location_y"]) ** 2
    ) / PITCH_X

    # Angle between pass vector and direction toward goal center
    dx_pass  = df["end_location_x"] - df["location_x"]
    dy_pass  = df["end_location_y"] - df["location_y"]
    dx_goal  = GOAL_CENTER[0] - df["location_x"]
    dy_goal  = GOAL_CENTER[1] - df["location_y"]
    dot      = dx_pass * dx_goal + dy_pass * dy_goal
    _mp      = np.sqrt(dx_pass**2 + dy_pass**2)
    _mg      = np.sqrt(dx_goal**2 + dy_goal**2)
    mag_p    = np.where(_mp == 0, np.nan, _mp)
    mag_g    = np.where(_mg == 0, np.nan, _mg)
    df["angle_to_goal"] = np.arccos(np.clip(dot / (mag_p * mag_g), -1.0, 1.0)).fillna(0)

    df["under_pressure"] = df["under_pressure"].astype(int)
    df["minute_bin"]     = pd.cut(df["minute"], bins=[-1, 45, 90, 999], labels=[0, 1, 2]).astype(int)

    # Target: 1 = successful pass (NULL outcome in StatsBomb)
    df["target"] = df["outcome"].isna().astype(int)

    feature_cols = ["start_x_n", "start_y_n", "end_x_n", "end_y_n",
                    "distance", "angle_to_goal", "under_pressure", "minute_bin"]

    X = df[feature_cols].values
    y = df["target"].values
    ids = df["event_id"].values

    return X, y, ids, feature_cols


# ─── Training ─────────────────────────────────────────────────────────────────

def train(X, y) -> tuple:
    X_tr, X_val, y_tr, y_val = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

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
        X_tr, y_tr,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    proba    = model.predict_proba(X_val)[:, 1]
    auc      = roc_auc_score(y_val, proba)
    logloss  = log_loss(y_val, proba)
    metrics  = {"auc": round(auc, 4), "log_loss": round(logloss, 4), "train_size": len(X_tr)}

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


def write_xp_values(engine, ids: np.ndarray, probas: np.ndarray) -> None:
    rows = [{"xp": round(float(p), 6), "eid": str(i)} for i, p in zip(ids, probas)]
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE fact_events SET xp_value = :xp WHERE event_id = :eid"),
            rows,
        )
    log.info("Updated xp_value for %d passes", len(rows))


# ─── Entry point ─────────────────────────────────────────────────────────────

def run() -> None:
    engine  = get_engine()
    df      = fetch_passes(engine)

    if len(df) < 500:
        log.warning("Insufficient pass data (%d rows); skipping xP training.", len(df))
        return

    X, y, ids, feature_cols = build_features(df)
    model, metrics           = train(X, y)

    save_model(engine, model, metrics, feature_cols)

    probas = model.predict_proba(X)[:, 1]
    write_xp_values(engine, ids, probas)

    log.info("xP modelling complete.")


if __name__ == "__main__":
    run()
