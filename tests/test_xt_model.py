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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_matrices():
    """Minimal valid matrices for testing solve_xt without a DB connection."""
    n = xt_model.GRID_COLS * xt_model.GRID_ROWS
    shot_prob  = np.zeros(n)
    goal_prob  = np.zeros(n)
    transition = np.zeros((n, n))
    # Put a shot with 30% conversion rate in the top-right cell (near goal)
    shot_prob[-1]  = 0.5
    goal_prob[-1]  = 0.3
    return shot_prob, goal_prob, transition


def _make_surface():
    """Simple xT surface where value increases toward the right (goal side)."""
    n = xt_model.GRID_COLS * xt_model.GRID_ROWS
    surface = np.zeros(n)
    for row in range(xt_model.GRID_ROWS):
        for col in range(xt_model.GRID_COLS):
            surface[row * xt_model.GRID_COLS + col] = col / xt_model.GRID_COLS
    return surface


# ---------------------------------------------------------------------------
# _to_grid_vec
# ---------------------------------------------------------------------------

def test_to_grid_clamps():
    """Corner coordinates should clamp to max grid index."""
    col, row = xt_model._to_grid_vec(np.array([119.9]), np.array([79.9]))
    assert col[0] == xt_model.GRID_COLS - 1
    assert row[0] == xt_model.GRID_ROWS - 1


def test_to_grid_origin():
    """Origin (0, 0) should map to grid cell (0, 0)."""
    col, row = xt_model._to_grid_vec(np.array([0.0]), np.array([0.0]))
    assert col[0] == 0
    assert row[0] == 0


def test_to_grid_vectorised():
    """Vectorised input returns arrays of the same length."""
    xs = np.array([0.0, 60.0, 119.9])
    ys = np.array([0.0, 40.0, 79.9])
    cols, rows = xt_model._to_grid_vec(xs, ys)
    assert len(cols) == 3
    assert len(rows) == 3
    assert cols[1] == int(60.0 / (xt_model.PITCH_X / xt_model.GRID_COLS))


# ---------------------------------------------------------------------------
# build_matrices shape (offline — no DB)
# ---------------------------------------------------------------------------

def test_build_matrices_shape():
    """Offline: verify that hand-built matrices have the expected shapes."""
    shot_prob, goal_prob, transition = _make_matrices()
    n = xt_model.GRID_COLS * xt_model.GRID_ROWS
    assert shot_prob.shape  == (n,)
    assert goal_prob.shape  == (n,)
    assert transition.shape == (n, n)


# ---------------------------------------------------------------------------
# solve_xt
# ---------------------------------------------------------------------------

def test_solve_xt_range():
    """xT surface values must lie in [0, 1]."""
    sp, gp, tr = _make_matrices()
    surface = xt_model.solve_xt(sp, gp, tr)
    assert surface.min() >= 0.0
    assert surface.max() <= 1.0


def test_solve_xt_shape():
    """solve_xt must return a flat array of length GRID_COLS * GRID_ROWS."""
    sp, gp, tr = _make_matrices()
    surface = xt_model.solve_xt(sp, gp, tr)
    assert surface.shape == (xt_model.GRID_COLS * xt_model.GRID_ROWS,)


def test_solve_xt_near_goal_higher():
    """Cell near goal should have higher xT than centre of pitch."""
    sp, gp, tr = _make_matrices()
    surface = xt_model.solve_xt(sp, gp, tr)
    near_goal_cell = xt_model.GRID_COLS * xt_model.GRID_ROWS - 1
    centre_cell    = (xt_model.GRID_ROWS // 2) * xt_model.GRID_COLS + xt_model.GRID_COLS // 2
    assert surface[near_goal_cell] >= surface[centre_cell]


# ---------------------------------------------------------------------------
# xT delta computation (core logic used by compute_and_write_xt)
# ---------------------------------------------------------------------------

def test_xt_delta_forward_pass_positive():
    """A forward pass (higher x) should produce a non-negative xT delta."""
    surface = _make_surface()

    x_start, y_start = np.array([60.0]), np.array([40.0])
    x_end,   y_end   = np.array([80.0]), np.array([40.0])

    cs, rs = xt_model._to_grid_vec(x_start, y_start)
    ce, re = xt_model._to_grid_vec(x_end,   y_end)

    start_val = surface[rs[0] * xt_model.GRID_COLS + cs[0]]
    end_val   = surface[re[0] * xt_model.GRID_COLS + ce[0]]
    assert end_val - start_val >= 0.0


def test_xt_shot_value_non_negative():
    """xT assigned to a shot must be non-negative."""
    surface = _make_surface()
    x, y = np.array([110.0]), np.array([40.0])
    c, r = xt_model._to_grid_vec(x, y)
    shot_xt = surface[r[0] * xt_model.GRID_COLS + c[0]]
    assert shot_xt >= 0.0
