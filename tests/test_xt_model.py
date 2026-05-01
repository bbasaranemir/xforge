import numpy as np
import pandas as pd
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

os.environ.setdefault("POSTGRES_USER", "analytics")
os.environ.setdefault("POSTGRES_PASSWORD", "analytics_test")
os.environ.setdefault("POSTGRES_DB", "football_db_test")
os.environ.setdefault("POSTGRES_HOST", "localhost")

import xt_model


def _make_events():
    return pd.DataFrame({
        "event_id":       ["a", "b", "c"],
        "event_type":     ["Pass", "Shot", "Carry"],
        "location_x":     [60.0, 110.0, 40.0],
        "location_y":     [40.0, 40.0, 30.0],
        "end_location_x": [80.0, None, 55.0],
        "end_location_y": [40.0, None, 30.0],
        "outcome":        [None, "Goal", None],
    })


def test_to_grid_clamps():
    col, row = xt_model._to_grid(119.9, 79.9)
    assert col == xt_model.GRID_COLS - 1
    assert row == xt_model.GRID_ROWS - 1


def test_to_grid_origin():
    col, row = xt_model._to_grid(0.0, 0.0)
    assert col == 0
    assert row == 0


def test_build_matrices_shape():
    df = _make_events()
    shot_prob, goal_prob, transition = xt_model.build_matrices(df)
    n = xt_model.GRID_COLS * xt_model.GRID_ROWS
    assert shot_prob.shape  == (n,)
    assert goal_prob.shape  == (n,)
    assert transition.shape == (n, n)


def test_solve_xt_range():
    df = _make_events()
    sp, gp, tr = xt_model.build_matrices(df)
    surface = xt_model.solve_xt(sp, gp, tr)
    assert surface.min() >= 0.0
    assert surface.max() <= 1.0


def test_compute_event_xt_returns_dataframe():
    df      = _make_events()
    sp, gp, tr = xt_model.build_matrices(df)
    surface = xt_model.solve_xt(sp, gp, tr)
    result  = xt_model.compute_event_xt(df, surface)
    assert isinstance(result, pd.DataFrame)
    assert "event_id"  in result.columns
    assert "xt_value" in result.columns
