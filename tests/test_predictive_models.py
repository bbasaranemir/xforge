import numpy as np
import pandas as pd
import pytest
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

os.environ.setdefault("POSTGRES_USER", "analytics")
os.environ.setdefault("POSTGRES_PASSWORD", "analytics_test")
os.environ.setdefault("POSTGRES_DB", "football_db_test")
os.environ.setdefault("POSTGRES_HOST", "localhost")

import predictive_models as pm


def _make_passes(n=600):
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "event_id":       [str(i) for i in range(n)],
        "location_x":     rng.uniform(0, 120, n),
        "location_y":     rng.uniform(0, 80,  n),
        "end_location_x": rng.uniform(0, 120, n),
        "end_location_y": rng.uniform(0, 80,  n),
        "outcome":        [None if rng.random() > 0.3 else "Incomplete" for _ in range(n)],
        "under_pressure": rng.choice([True, False], n),
        "minute":         rng.integers(0, 90, n),
    })


def test_build_features_shape():
    df = _make_passes()
    X, y, ids, cols = pm.build_features(df)
    assert X.shape == (len(df), len(cols))
    assert y.shape == (len(df),)
    assert len(ids) == len(df)


def test_target_binary():
    df = _make_passes()
    _, y, _, _ = pm.build_features(df)
    assert set(y).issubset({0, 1})


def test_train_returns_metrics():
    df = _make_passes()
    X, y, _, _ = pm.build_features(df)
    _, metrics = pm.train(X, y)
    assert "auc" in metrics
    assert "log_loss" in metrics
    assert 0.0 <= metrics["auc"] <= 1.0  # random data can yield AUC < 0.5
