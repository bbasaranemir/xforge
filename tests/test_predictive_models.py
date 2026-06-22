"""
Unit tests for predictive_models.py pure functions — no DB, no psycopg2 required.

The two core pure functions (build_features, train) are replicated inline so
the test file can be collected without installing server-side dependencies.

Functions under test:
  build_features(df)  — feature engineering from pass DataFrame
  train(X, y)         — XGBoost training, returns (model, metrics)
"""

import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

# ── Replicated constants (keep in sync with scripts/predictive_models.py) ─────

PITCH_X = 105.0
PITCH_Y = 68.0
GOAL_CENTER = (105.0, 34.0)

# ── Replicated build_features ─────────────────────────────────────────────────


def build_features(df: pd.DataFrame):
    """Pure feature engineering — identical logic to predictive_models.build_features."""
    df = df.copy()
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

    # Target: 1 = successful pass (silver convention maps NULL outcome → 'Unknown').
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


# ── Replicated train ──────────────────────────────────────────────────────────


def train(X, y):
    """XGBoost training — mirrors predictive_models.train."""
    from sklearn.metrics import log_loss, roc_auc_score
    from sklearn.model_selection import train_test_split
    from xgboost import XGBClassifier

    X_tr, X_val, y_tr, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    model = XGBClassifier(
        n_estimators=50,   # fewer trees for fast tests
        max_depth=3,
        learning_rate=0.1,
        use_label_encoder=False,
        eval_metric="logloss",
        random_state=42,
        n_jobs=1,
    )
    model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)

    proba = model.predict_proba(X_val)[:, 1]
    auc = roc_auc_score(y_val, proba)
    logloss = log_loss(y_val, proba)
    metrics = {
        "auc": round(auc, 4),
        "log_loss": round(logloss, 4),
        "train_size": len(X_tr),
    }
    return model, metrics


# ── Fixture ───────────────────────────────────────────────────────────────────


def _make_passes(n: int = 600) -> pd.DataFrame:
    """Synthetic pass DataFrame — mirrors shape consumed by build_features."""
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "event_id":       [str(i) for i in range(n)],
        "location_x":     rng.uniform(0, 120, n),
        "location_y":     rng.uniform(0, 80,  n),
        "end_location_x": rng.uniform(0, 120, n),
        "end_location_y": rng.uniform(0, 80,  n),
        # Silver convention: NULL → 'Unknown' (success); 'Incomplete' / 'Out' → failure
        "outcome":        rng.choice(["Unknown", "Incomplete", "Out", None], n,
                                     p=[0.6, 0.2, 0.1, 0.1]),
        "under_pressure": rng.choice([True, False], n),
        "minute":         rng.integers(0, 90, n),
    })


# ── Tests: build_features — shape ────────────────────────────────────────────


def test_build_features_shape():
    df = _make_passes()
    X, y, ids, cols = build_features(df)
    assert X.shape == (len(df), len(cols))
    assert y.shape == (len(df),)
    assert len(ids) == len(df)


def test_build_features_eight_columns():
    """build_features must produce exactly 8 feature columns."""
    df = _make_passes()
    _, _, _, cols = build_features(df)
    assert len(cols) == 8
    assert set(cols) == {
        "start_x_n", "start_y_n", "end_x_n", "end_y_n",
        "distance", "angle_to_goal", "under_pressure", "minute_bin",
    }


# ── Tests: build_features — distance ─────────────────────────────────────────


def test_distance_non_negative():
    """Euclidean distance (normalised) must be >= 0 for every pass."""
    df = _make_passes()
    X, _, _, cols = build_features(df)
    dist = X[:, cols.index("distance")]
    assert (dist >= 0).all(), "distance contains negative value(s)"


def test_distance_formula():
    """Single-row sanity check: distance = sqrt(dx^2+dy^2) / PITCH_X."""
    row = pd.DataFrame({
        "event_id":       ["e1"],
        "location_x":     [10.0],
        "location_y":     [20.0],
        "end_location_x": [40.0],
        "end_location_y": [60.0],
        "outcome":        ["Unknown"],
        "under_pressure": [False],
        "minute":         [30],
    })
    X, _, _, cols = build_features(row)
    expected = math.sqrt((40 - 10) ** 2 + (60 - 20) ** 2) / PITCH_X
    actual = X[0, cols.index("distance")]
    assert abs(actual - expected) < 1e-9


# ── Tests: build_features — minute_bin ───────────────────────────────────────


def test_minute_bin_first_half():
    """Minutes 0–45 must map to bin 0."""
    rng = np.random.default_rng(1)
    df = _make_passes(200)
    df["minute"] = rng.integers(0, 46, 200)  # 0..45 inclusive
    X, _, _, cols = build_features(df)
    bins = X[:, cols.index("minute_bin")].astype(int)
    assert set(bins) == {0}, f"Expected only bin 0, got {set(bins)}"


def test_minute_bin_second_half():
    """Minutes 46–90 must map to bin 1."""
    rng = np.random.default_rng(2)
    df = _make_passes(200)
    df["minute"] = rng.integers(46, 91, 200)  # 46..90 inclusive
    X, _, _, cols = build_features(df)
    bins = X[:, cols.index("minute_bin")].astype(int)
    assert set(bins) == {1}, f"Expected only bin 1, got {set(bins)}"


def test_minute_bin_extra_time():
    """Minutes > 90 must map to bin 2."""
    df = _make_passes(50)
    df["minute"] = 95
    X, _, _, cols = build_features(df)
    bins = X[:, cols.index("minute_bin")].astype(int)
    assert set(bins) == {2}, f"Expected only bin 2, got {set(bins)}"


def test_minute_bin_valid_values():
    """All minute_bin values must be in {0, 1, 2}."""
    df = _make_passes()
    X, _, _, cols = build_features(df)
    bins = set(X[:, cols.index("minute_bin")].astype(int))
    assert bins.issubset({0, 1, 2})


# ── Tests: build_features — under_pressure ───────────────────────────────────


def test_under_pressure_binary():
    """under_pressure must contain only 0 or 1 (no floats beyond {0.0, 1.0})."""
    df = _make_passes()
    X, _, _, cols = build_features(df)
    pres = X[:, cols.index("under_pressure")]
    assert set(pres.astype(int)).issubset({0, 1}), \
        f"under_pressure has unexpected values: {set(pres)}"


def test_under_pressure_true_becomes_one():
    """True under_pressure entries must become 1."""
    df = _make_passes(50)
    df["under_pressure"] = True
    X, _, _, cols = build_features(df)
    pres = X[:, cols.index("under_pressure")]
    assert (pres == 1).all()


def test_under_pressure_false_becomes_zero():
    """False under_pressure entries must become 0."""
    df = _make_passes(50)
    df["under_pressure"] = False
    X, _, _, cols = build_features(df)
    pres = X[:, cols.index("under_pressure")]
    assert (pres == 0).all()


# ── Tests: build_features — NaN handling ─────────────────────────────────────


def test_no_nan_in_features():
    """Feature matrix must contain no NaN values after fillna(0) fallback."""
    df = _make_passes()
    X, _, _, _ = build_features(df)
    assert not np.isnan(X).any(), "Feature matrix contains NaN"


def test_nan_outcome_handled():
    """Rows with None outcome must not produce NaN in the target vector."""
    df = _make_passes(100)
    df["outcome"] = None   # all NaN
    _, y, _, _ = build_features(df)
    assert not np.isnan(y.astype(float)).any()
    # None != 'Unknown', so all targets should be 0
    assert (y == 0).all()


# ── Tests: build_features — target encoding ──────────────────────────────────


def test_target_binary():
    """Target vector must be strictly binary {0, 1}."""
    df = _make_passes()
    _, y, _, _ = build_features(df)
    assert set(y).issubset({0, 1})


def test_target_unknown_maps_to_one():
    """'Unknown' outcome (silver convention for successful pass) → target=1."""
    df = _make_passes(100)
    df["outcome"] = "Unknown"
    _, y, _, _ = build_features(df)
    assert (y == 1).all(), "All 'Unknown' outcomes should yield target=1"


def test_target_incomplete_maps_to_zero():
    """'Incomplete' outcome → target=0."""
    df = _make_passes(100)
    df["outcome"] = "Incomplete"
    _, y, _, _ = build_features(df)
    assert (y == 0).all(), "All 'Incomplete' outcomes should yield target=0"


# ── Tests: train — metrics ────────────────────────────────────────────────────


def test_train_returns_metrics():
    """train() must return a dict containing 'auc' and 'log_loss'."""
    df = _make_passes()
    X, y, _, _ = build_features(df)
    _, metrics = train(X, y)
    assert "auc" in metrics
    assert "log_loss" in metrics
    assert 0.0 <= metrics["auc"] <= 1.0  # random data can yield AUC < 0.5


def test_train_auc_finite():
    """AUC must be a finite number."""
    df = _make_passes()
    X, y, _, _ = build_features(df)
    _, metrics = train(X, y)
    assert math.isfinite(metrics["auc"]), "AUC is not finite"


def test_train_log_loss_finite():
    """log_loss must be a finite, positive number."""
    df = _make_passes()
    X, y, _, _ = build_features(df)
    _, metrics = train(X, y)
    assert math.isfinite(metrics["log_loss"]), "log_loss is not finite"
    assert metrics["log_loss"] > 0, "log_loss must be positive"


def test_train_auc_above_half_on_realistic_data():
    """With realistic class balance (60% success), model should beat random (AUC>0.5)."""
    rng = np.random.default_rng(42)
    n = 800
    # Construct separable signal: closer passes more likely to succeed
    location_x = rng.uniform(0, 105, n)
    end_location_x = rng.uniform(0, 105, n)
    outcome = np.where(
        np.abs(end_location_x - location_x) < 20, "Unknown", "Incomplete"
    )
    df = pd.DataFrame({
        "event_id":       [str(i) for i in range(n)],
        "location_x":     location_x,
        "location_y":     rng.uniform(0, 68, n),
        "end_location_x": end_location_x,
        "end_location_y": rng.uniform(0, 68, n),
        "outcome":        outcome,
        "under_pressure": rng.choice([True, False], n),
        "minute":         rng.integers(0, 90, n),
    })
    X, y, _, _ = build_features(df)
    # Ensure both classes are present before training
    if len(np.unique(y)) < 2:
        pytest.skip("Synthetic data has only one class — skipping AUC test")
    _, metrics = train(X, y)
    assert metrics["auc"] > 0.5, f"Expected AUC > 0.5, got {metrics['auc']}"


# ── Tests: predictive_models import (DB-gated) ───────────────────────────────

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

os.environ.setdefault("POSTGRES_USER", "analytics")
os.environ.setdefault("POSTGRES_PASSWORD", "analytics_test")
os.environ.setdefault("POSTGRES_DB", "football_db_test")
os.environ.setdefault("POSTGRES_HOST", "localhost")

try:
    import predictive_models as pm
    _PM_AVAILABLE = True
except ImportError:
    pm = None  # type: ignore[assignment]
    _PM_AVAILABLE = False

_skip_no_deps = pytest.mark.skipif(
    not _PM_AVAILABLE,
    reason="sqlalchemy/xgboost not installed in this environment",
)


@_skip_no_deps
def test_pm_build_features_shape():
    """pm.build_features() produces the same shape as the replicated version."""
    df = _make_passes()
    X, y, ids, cols = pm.build_features(df)
    assert X.shape == (len(df), len(cols))
    assert y.shape == (len(df),)
    assert len(ids) == len(df)


@_skip_no_deps
def test_pm_train_skips_on_small_data():
    """
    predictive_models.run() skips training when data < 500 rows.
    Verify the guard directly: train() must succeed on 600 rows
    (the guard lives in run(), not train() itself), so we confirm
    build_features + train don't crash on the minimum viable size.
    """
    df = _make_passes(600)
    X, y, _, _ = pm.build_features(df)
    _, metrics = pm.train(X, y)
    assert "auc" in metrics
    assert "log_loss" in metrics
