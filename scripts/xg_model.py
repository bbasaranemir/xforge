import logging
import os

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.preprocessing import LabelEncoder
from sqlalchemy import text

from db_utils import get_engine

log = logging.getLogger(__name__)

# Features must match silver_shots view columns
FEATURE_COLS = [
    "distance_to_goal",
    "shot_angle",
    "under_pressure",
    "is_header",
    "body_part_encoded",
]

# AUC below this threshold indicates data quality problems — fail fast
AUC_FLOOR = 0.55

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "xg_model.joblib")


# sklearn 1.4 CyHalfBinomialLoss requires float64; XGBoost outputs float32.
# Defined at module level so joblib/pickle can locate the class by name.
class _XGBFloat64(xgb.XGBClassifier):
    def predict_proba(self, X):
        return super().predict_proba(X).astype(np.float64)


def fetch_shots(engine) -> pd.DataFrame:
    query = text(
        """
        SELECT
            s.event_id,
            s.distance_to_goal,
            s.shot_angle,
            s.under_pressure::int       AS under_pressure,
            s.is_header::int            AS is_header,
            s.body_part,
            s.is_goal
        FROM analytics_silver.silver_shots s
        WHERE s.distance_to_goal IS NOT NULL
          AND s.shot_angle IS NOT NULL
    """
    )
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    log.info("Fetched %d shots for xG model", len(df))
    return df


def engineer_features(df: pd.DataFrame) -> tuple:
    df = df.copy()
    le = LabelEncoder()
    df["body_part_encoded"] = le.fit_transform(df["body_part"].fillna("Unknown"))
    return df, le


def train_xgboost(X: np.ndarray, y: np.ndarray) -> CalibratedClassifierCV:
    """
    XGBoost with Platt (sigmoid) calibration.
    Calibration ensures xG=0.3 means ~30% conversion probability,
    not just a relative ranking. This is a requirement for proper xG.
    """
    base_clf = _XGBFloat64(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        random_state=42,
    )
    # cv must not exceed the number of positive-class samples to avoid empty
    # folds; single-match datasets (~38 shots, ~4 goals) need cv=2 or 3.
    cv = min(5, max(2, int(y.sum())))
    calibrated = CalibratedClassifierCV(base_clf, cv=cv, method="sigmoid")
    calibrated.fit(X, y)
    return calibrated


def evaluate_model(model, X: np.ndarray, y: np.ndarray) -> dict:
    proba = model.predict_proba(X)[:, 1]
    metrics = {
        "roc_auc": round(roc_auc_score(y, proba), 4),
        "brier_score": round(brier_score_loss(y, proba), 4),
        # Calibration sanity check: expected goals should approximate actual goals
        "expected_goals": round(float(proba.sum()), 2),
        "actual_goals": int(y.sum()),
        "total_shots": len(y),
    }
    log.info("xG model metrics: %s", metrics)
    return metrics


def write_xg_values(engine, event_ids: list, xg_values: np.ndarray):
    rows = [
        {"xg": round(float(xg), 6), "eid": eid} for eid, xg in zip(event_ids, xg_values)
    ]
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE fact_events SET xg_value = :xg WHERE event_id = :eid"),
            rows,
        )
    log.info("Updated xg_value for %d shot events", len(rows))


def run():
    engine = get_engine()
    df = fetch_shots(engine)

    if len(df) == 0:
        raise RuntimeError(
            "No shots found in silver_shots — xG model cannot train on an empty dataset. "
            "Ensure at least one match has been ingested and silver dbt models have run."
        )

    if len(df) < 50:
        log.warning(
            "Only %d shots — xG reliability is low. "
            "Expand ingestion to a full season (64+ matches) for production-grade AUC.",
            len(df),
        )

    df, _le = engineer_features(df)
    X = df[FEATURE_COLS].values.astype(float)
    y = df["is_goal"].values.astype(int)

    if y.sum() == 0:
        raise RuntimeError(
            "Training dataset contains no goals (all is_goal=0). "
            "xG model requires positive examples — check silver_shots.is_goal column."
        )

    model = train_xgboost(X, y)
    metrics = evaluate_model(model, X, y)

    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    log.info("xG model saved to %s", MODEL_PATH)

    if metrics["roc_auc"] < AUC_FLOOR:
        raise RuntimeError(
            f"xG model AUC {metrics['roc_auc']} below threshold {AUC_FLOOR}. "
            "Check input data quality or expand training set."
        )

    xg_preds = model.predict_proba(X)[:, 1]
    write_xg_values(engine, df["event_id"].tolist(), xg_preds)

    log.info(
        "xG computation complete. Expected: %.2f goals | Actual: %d goals",
        metrics["expected_goals"],
        metrics["actual_goals"],
    )


if __name__ == "__main__":
    run()
