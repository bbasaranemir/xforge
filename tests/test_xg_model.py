"""
Unit tests for xg_model.py pure functions — no DB, no psycopg2 required.

Mirrors test_xt_model.py: the two core pure functions (build_features, train)
are replicated inline so the test file can be collected without installing
server-side dependencies.

Functions under test:
  build_features(df)  — feature engineering from shot DataFrame
  train(X, y)         — XGBoost training, returns (model, metrics)

Degenerate input tests (bottom section) import xg_model directly and mock
fetch_shots to verify fail-fast guards without touching the database.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ── Replicated constants (keep in sync with scripts/xg_model.py) ─────────────

PITCH_X = 120.0
PITCH_Y = 80.0
GOAL_CENTER = (120.0, 40.0)


# ── Replicated build_features ─────────────────────────────────────────────────


def build_features(df: pd.DataFrame):
    """Pure feature engineering — identical logic to xg_model.build_features."""
    df = df.copy()

    df["distance_to_goal"] = (
        np.sqrt(
            (GOAL_CENTER[0] - df["location_x"]) ** 2
            + (GOAL_CENTER[1] - df["location_y"]) ** 2
        )
        / PITCH_X
    )

    dx = GOAL_CENTER[0] - df["location_x"]
    dy = GOAL_CENTER[1] - df["location_y"]
    goal_width = 7.32
    df["angle_to_goal"] = np.arctan(
        goal_width * dx / (dx**2 + dy**2 - (goal_width / 2) ** 2)
    )
    df["angle_to_goal"] = df["angle_to_goal"].abs().fillna(0.0)

    df["under_pressure"] = df["under_pressure"].astype(int)
    df["minute_bin"] = pd.cut(
        df["minute"], bins=[-1, 45, 90, 999], labels=[0, 1, 2]
    ).astype(int)

    df["target"] = (df["outcome"] == "Goal").astype(int)

    feature_cols = ["distance_to_goal", "angle_to_goal", "under_pressure", "minute_bin"]
    X = df[feature_cols].fillna(0).values
    y = df["target"].values
    ids = df["event_id"].values
    return X, y, ids, feature_cols


# ── Replicated train ──────────────────────────────────────────────────────────


def train(X, y):
    """XGBoost training with dynamic scale_pos_weight — mirrors xg_model.train."""
    from sklearn.metrics import log_loss, roc_auc_score
    from sklearn.model_selection import train_test_split
    from xgboost import XGBClassifier

    n_neg = int((y == 0).sum())
    n_pos = int((y == 1).sum())
    spw = round(n_neg / max(n_pos, 1), 2)

    X_tr, X_val, y_tr, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    model = XGBClassifier(
        n_estimators=50,  # fewer trees for fast tests
        max_depth=3,
        learning_rate=0.1,
        scale_pos_weight=spw,
        eval_metric="logloss",
        random_state=42,
        n_jobs=1,
    )
    model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)

    proba = model.predict_proba(X_val)[:, 1]
    metrics = {
        "auc": round(roc_auc_score(y_val, proba), 4),
        "log_loss": round(log_loss(y_val, proba), 4),
        "scale_pos_weight": spw,
        "train_size": len(X_tr),
    }
    return model, metrics


# ── Fixture ───────────────────────────────────────────────────────────────────


def _make_shots(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    outcomes = rng.choice(
        ["Goal", "Saved", "Blocked", "Off T", "Post", "Wayward"],
        size=n,
        p=[0.11, 0.40, 0.20, 0.18, 0.05, 0.06],
    )
    return pd.DataFrame(
        {
            "event_id": [str(i) for i in range(n)],
            "location_x": rng.uniform(60, 120, n),
            "location_y": rng.uniform(5, 75, n),
            "outcome": outcomes,
            "under_pressure": rng.choice([True, False], n),
            "minute": rng.integers(0, 95, n),
        }
    )


# ── Tests: build_features ─────────────────────────────────────────────────────


def test_build_features_shape():
    df = _make_shots()
    X, y, ids, cols = build_features(df)
    assert X.shape == (len(df), len(cols))
    assert y.shape == (len(df),)
    assert len(ids) == len(df)


def test_build_features_four_columns():
    df = _make_shots()
    _, _, _, cols = build_features(df)
    assert len(cols) == 4
    assert set(cols) == {
        "distance_to_goal",
        "angle_to_goal",
        "under_pressure",
        "minute_bin",
    }


def test_target_binary():
    df = _make_shots()
    _, y, _, _ = build_features(df)
    assert set(y).issubset({0, 1})


def test_target_goal_mapping():
    df = _make_shots(200)
    _, y, _, _ = build_features(df)
    goal_mask = (df["outcome"] == "Goal").values
    assert (y[goal_mask] == 1).all(), "Goal rows → y=1"
    assert (y[~goal_mask] == 0).all(), "Non-goal rows → y=0"


def test_distance_non_negative():
    df = _make_shots()
    X, _, _, cols = build_features(df)
    dist = X[:, cols.index("distance_to_goal")]
    assert (dist >= 0).all()


def test_no_nan_in_features():
    df = _make_shots()
    X, _, _, _ = build_features(df)
    assert not np.isnan(X).any(), "Feature matrix must have no NaN"


def test_under_pressure_binary():
    df = _make_shots()
    X, _, _, cols = build_features(df)
    pres = X[:, cols.index("under_pressure")]
    assert set(pres.astype(int)).issubset({0, 1})


def test_minute_bin_three_levels():
    df = _make_shots()
    X, _, _, cols = build_features(df)
    bins = set(X[:, cols.index("minute_bin")].astype(int))
    assert bins.issubset({0, 1, 2})


# ── Tests: train ──────────────────────────────────────────────────────────────


def test_train_returns_metrics():
    df = _make_shots(400)
    X, y, _, _ = build_features(df)
    _, metrics = train(X, y)
    assert "auc" in metrics
    assert "log_loss" in metrics
    assert "scale_pos_weight" in metrics
    assert 0.0 <= metrics["auc"] <= 1.0
    assert metrics["log_loss"] > 0


def test_scale_pos_weight_above_one():
    """~10% goal rate → scale_pos_weight should exceed 1."""
    df = _make_shots(400)
    X, y, _, _ = build_features(df)
    _, metrics = train(X, y)
    assert metrics["scale_pos_weight"] > 1.0


def test_angle_non_negative():
    """Angle to goal must always be >= 0 (abs() applied in build_features)."""
    df = _make_shots()
    X, _, _, cols = build_features(df)
    angles = X[:, cols.index("angle_to_goal")]
    assert (angles >= 0).all()


def test_build_features_ids_length():
    """ids array length must equal input DataFrame length."""
    n = 150
    df = _make_shots(n)
    _, _, ids, _ = build_features(df)
    assert len(ids) == n


def test_goal_rate_reasonable():
    """Fixture goal rate should sit in a realistic football range (5–25%)."""
    df = _make_shots(600)
    _, y, _, _ = build_features(df)
    goal_rate = y.mean()
    assert 0.05 < goal_rate < 0.25, f"Unexpected goal rate: {goal_rate:.3f}"


# ── Degenerate input tests (import xg_model directly, mock DB) ────────────────
# xg_model imports sqlalchemy/psycopg2 which may not be present in minimal test
# environments. The two tests below are skipped automatically in that case and
# run in CI where requirements.txt is fully installed.

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

os.environ.setdefault("POSTGRES_USER", "analytics")
os.environ.setdefault("POSTGRES_PASSWORD", "analytics")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "football_db")

try:
    import xg_model as _xg_model  # noqa: E402
    _XG_MODEL_AVAILABLE = True
except ImportError:
    _xg_model = None  # type: ignore[assignment]
    _XG_MODEL_AVAILABLE = False

_skip_no_deps = pytest.mark.skipif(
    not _XG_MODEL_AVAILABLE,
    reason="sqlalchemy/xgboost not installed in this environment",
)


def _make_silver_shots(n: int, n_goals: int) -> pd.DataFrame:
    """Minimal DataFrame matching silver_shots schema consumed by xg_model.run()."""
    rng = np.random.default_rng(99)
    return pd.DataFrame(
        {
            "event_id": [str(i) for i in range(n)],
            "distance_to_goal": rng.uniform(5.0, 40.0, n),
            "shot_angle": rng.uniform(0.1, 1.5, n),
            "under_pressure": [0] * n,
            "is_header": [0] * n,
            "body_part": ["Foot"] * n,
            "is_goal": [1] * n_goals + [0] * (n - n_goals),
        }
    )


@_skip_no_deps
@patch("xg_model.get_engine", return_value=MagicMock())
@patch("xg_model.fetch_shots", return_value=pd.DataFrame())
def test_run_raises_on_empty_dataset(mock_fetch, mock_engine):
    """Zero-row result from silver_shots must raise RuntimeError before training."""
    with pytest.raises(RuntimeError, match="No shots found"):
        _xg_model.run()


@_skip_no_deps
@patch("xg_model.get_engine", return_value=MagicMock())
def test_run_raises_on_all_negative_labels(mock_engine):
    """Dataset with no goals (all is_goal=0) must raise RuntimeError before training."""
    all_saves = _make_silver_shots(n=120, n_goals=0)
    with patch("xg_model.fetch_shots", return_value=all_saves):
        with pytest.raises(RuntimeError, match="no goals"):
            _xg_model.run()
