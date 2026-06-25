"""
Unit tests for xt_model pure functions.

Imports directly from xt_model — DB-dependent functions (fetch_actions,
write_xt_values, write_xt_surface, run) are skipped; pure numeric functions
(_cells_from_xy, build_matrices, solve_xt, compute_event_xt) are tested fully.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

os.environ.setdefault("POSTGRES_USER", "analytics")
os.environ.setdefault("POSTGRES_PASSWORD", "analytics_test")
os.environ.setdefault("POSTGRES_DB", "football_db_test")
os.environ.setdefault("POSTGRES_HOST", "localhost")

import numpy as np
import pandas as pd
import pytest

from xt_model import (
    GRID_COLS,
    GRID_ROWS,
    PITCH_X,
    PITCH_Y,
    _cells_from_xy,
    solve_xt,
    build_matrices,
    compute_event_xt,
)


class TestCellsFromXy:
    def test_origin_maps_to_cell_zero(self):
        cells = _cells_from_xy(np.array([0.0]), np.array([0.0]))
        assert cells[0] == 0

    def test_pitch_centre_maps_to_middle(self):
        # centre ≈ (52.5, 34) → col=8, row=6 → cell 6*16+8=104
        cells = _cells_from_xy(np.array([PITCH_X / 2]), np.array([PITCH_Y / 2]))
        expected_col = 8
        expected_row = 6
        assert cells[0] == expected_row * GRID_COLS + expected_col

    def test_far_corner_clamps_to_max_cell(self):
        cells = _cells_from_xy(np.array([PITCH_X]), np.array([PITCH_Y]))
        assert cells[0] == (GRID_ROWS - 1) * GRID_COLS + (GRID_COLS - 1)

    def test_output_within_bounds(self):
        rng = np.random.default_rng(42)
        x = rng.uniform(0, PITCH_X, 1000)
        y = rng.uniform(0, PITCH_Y, 1000)
        cells = _cells_from_xy(x, y)
        assert (cells >= 0).all()
        assert (cells < GRID_COLS * GRID_ROWS).all()

    def test_vectorised_batch(self):
        xs = np.array([0.0, PITCH_X / 2, PITCH_X - 0.1])
        ys = np.array([0.0, PITCH_Y / 2, PITCH_Y - 0.1])
        cells = _cells_from_xy(xs, ys)
        assert len(cells) == 3


class TestSolveXt:
    def _make_inputs(self, seed=0):
        n = GRID_COLS * GRID_ROWS
        rng = np.random.default_rng(seed)
        shot_prob = rng.uniform(0, 0.15, n)
        goal_prob = rng.uniform(0, 0.4, n)
        raw = rng.uniform(0, 1, (n, n))
        transition = raw / raw.sum(axis=1, keepdims=True)
        return shot_prob, goal_prob, transition

    def test_output_shape(self):
        sp, gp, tr = self._make_inputs()
        xt = solve_xt(sp, gp, tr)
        assert xt.shape == (GRID_COLS * GRID_ROWS,)

    def test_values_between_zero_and_one(self):
        sp, gp, tr = self._make_inputs()
        xt = solve_xt(sp, gp, tr)
        assert (xt >= 0.0).all() and (xt <= 1.0).all()

    def test_zero_shot_prob_yields_zero_surface(self):
        n = GRID_COLS * GRID_ROWS
        sp = np.zeros(n)
        gp = np.zeros(n)
        tr = np.eye(n)
        xt = solve_xt(sp, gp, tr)
        np.testing.assert_array_equal(xt, np.zeros(n))

    def test_high_goal_zone_has_highest_xt(self):
        n = GRID_COLS * GRID_ROWS
        sp = np.zeros(n)
        gp = np.zeros(n)
        tr = np.zeros((n, n))
        hot_cell = 100
        sp[hot_cell] = 0.8
        gp[hot_cell] = 0.9
        xt = solve_xt(sp, gp, tr)
        assert xt[hot_cell] == pytest.approx(0.72, rel=1e-3)
        assert xt[np.arange(n) != hot_cell].sum() == 0.0

    def test_deterministic_across_runs(self):
        sp, gp, tr = self._make_inputs(seed=7)
        xt1 = solve_xt(sp, gp, tr)
        xt2 = solve_xt(sp, gp, tr)
        np.testing.assert_array_equal(xt1, xt2)


class TestBuildMatrices:
    def _make_df(self):
        return pd.DataFrame({
            "event_type":     ["Shot", "Pass", "Carry", "Shot"],
            "location_x":     [100.0,  30.0,   60.0,   95.0],
            "location_y":     [34.0,   20.0,   34.0,   40.0],
            "end_location_x": [None,   60.0,   80.0,   None],
            "end_location_y": [None,   34.0,   34.0,   None],
            "outcome":        ["Goal", None,   None,  "Saved"],
        })

    def test_returns_three_arrays(self):
        sp, gp, tr = build_matrices(self._make_df())
        assert sp.shape == (GRID_COLS * GRID_ROWS,)
        assert gp.shape == (GRID_COLS * GRID_ROWS,)
        assert tr.shape == (GRID_COLS * GRID_ROWS, GRID_COLS * GRID_ROWS)

    def test_shot_prob_between_zero_and_one(self):
        sp, _, _ = build_matrices(self._make_df())
        assert (sp >= 0).all() and (sp <= 1).all()

    def test_transition_rows_sum_to_one_or_zero(self):
        _, _, tr = build_matrices(self._make_df())
        row_sums = tr.sum(axis=1)
        assert np.all((np.isclose(row_sums, 1.0) | np.isclose(row_sums, 0.0)))

    def test_empty_df_returns_zero_arrays(self):
        empty = pd.DataFrame(
            columns=["event_type", "location_x", "location_y",
                     "end_location_x", "end_location_y", "outcome"]
        ).astype({"location_x": float, "location_y": float})
        sp, gp, tr = build_matrices(empty)
        assert sp.sum() == 0.0
        assert gp.sum() == 0.0


class TestComputeEventXt:
    def _surface(self):
        n = GRID_COLS * GRID_ROWS
        rng = np.random.default_rng(0)
        return rng.uniform(0, 0.3, n)

    def _make_df(self):
        return pd.DataFrame({
            "event_id":       ["a", "b", "c"],
            "event_type":     ["Shot", "Pass", "Carry"],
            "location_x":     [100.0, 30.0, 60.0],
            "location_y":     [34.0,  20.0, 34.0],
            "end_location_x": [None,  60.0, 80.0],
            "end_location_y": [None,  34.0, 34.0],
            "outcome":        ["Saved", None, None],
        })

    def test_output_has_event_id_and_xt_value(self):
        result = compute_event_xt(self._make_df(), self._surface())
        assert "event_id" in result.columns
        assert "xt_value" in result.columns

    def test_shot_included(self):
        result = compute_event_xt(self._make_df(), self._surface())
        assert "a" in result["event_id"].values

    def test_successful_pass_included(self):
        result = compute_event_xt(self._make_df(), self._surface())
        assert "b" in result["event_id"].values

    def test_xt_values_are_floats(self):
        result = compute_event_xt(self._make_df(), self._surface())
        assert result["xt_value"].dtype == float

    def test_empty_df_returns_empty(self):
        empty = pd.DataFrame(
            columns=["event_id", "event_type", "location_x", "location_y",
                     "end_location_x", "end_location_y", "outcome"]
        ).astype({"location_x": float, "location_y": float})
        result = compute_event_xt(empty, self._surface())
        assert len(result) == 0
