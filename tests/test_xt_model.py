"""
Unit tests for xT model pure functions.

DB-dependent functions (build_matrices, compute_and_write_xt) are skipped;
only the pure numeric/grid functions are tested here.
"""

import numpy as np
import pytest

GRID_COLS = 16
GRID_ROWS = 12
PITCH_X = 120.0
PITCH_Y = 80.0
ITERATIONS = 15


def _to_grid_vec(x, y):
    cols = np.minimum((x / (PITCH_X / GRID_COLS)).astype(int), GRID_COLS - 1)
    rows = np.minimum((y / (PITCH_Y / GRID_ROWS)).astype(int), GRID_ROWS - 1)
    return cols, rows


def solve_xt(shot_prob, goal_prob, transition):
    xt = np.zeros(GRID_COLS * GRID_ROWS)
    for _ in range(ITERATIONS):
        xt = shot_prob * goal_prob + (1 - shot_prob) * (transition @ xt)
    return xt


class TestToGridVec:
    def test_origin_maps_to_zero_cell(self):
        c, r = _to_grid_vec(np.array([0.0]), np.array([0.0]))
        assert c[0] == 0 and r[0] == 0

    def test_pitch_centre_maps_to_middle(self):
        c, r = _to_grid_vec(np.array([60.0]), np.array([40.0]))
        assert c[0] == 8 and r[0] == 6

    def test_far_corner_clamps_to_max_cell(self):
        c, r = _to_grid_vec(np.array([120.0]), np.array([80.0]))
        assert c[0] == GRID_COLS - 1
        assert r[0] == GRID_ROWS - 1

    def test_output_within_bounds(self):
        rng = np.random.default_rng(42)
        x = rng.uniform(0, 120, 1000)
        y = rng.uniform(0, 80, 1000)
        c, r = _to_grid_vec(x, y)
        assert (c >= 0).all() and (c < GRID_COLS).all()
        assert (r >= 0).all() and (r < GRID_ROWS).all()

    def test_vectorised_batch(self):
        xs = np.array([0.0, 60.0, 119.9])
        ys = np.array([0.0, 40.0, 79.9])
        c, r = _to_grid_vec(xs, ys)
        assert len(c) == 3 and len(r) == 3


class TestSolveXt:
    def _make_inputs(self, seed=0):
        n = GRID_COLS * GRID_ROWS
        rng = np.random.default_rng(seed)
        shot_prob = rng.uniform(0, 0.15, n)
        goal_prob = rng.uniform(0, 0.4, n)
        raw = rng.uniform(0, 1, (n, n))
        row_sums = raw.sum(axis=1, keepdims=True)
        transition = raw / row_sums
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
