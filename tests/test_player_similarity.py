"""
Unit tests for player_similarity pure functions.

Imports directly from player_similarity — DB-dependent functions (fetch_player_features,
write_similarity_scores, save_model_artifact, run) are skipped; pure numeric functions
(build_similarity_model, compute_similarity_rows) are tested fully.
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

from player_similarity import (
    FEATURE_COLS,
    N_NEIGHBORS,
    build_similarity_model,
    compute_similarity_rows,
)


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _make_players(n: int = 20, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "player_id":            list(range(1, n + 1)),
            "player_name":          [f"Player {i}" for i in range(1, n + 1)],
            "position":             ["MF"] * n,
            "shots_per_match":      rng.uniform(0.5, 3.0, n),
            "goals_per_match":      rng.uniform(0.0, 0.5, n),
            "avg_shot_distance":    rng.uniform(10.0, 25.0, n),
            "avg_shot_angle":       rng.uniform(0.2, 1.5, n),
            "header_ratio":         rng.uniform(0.0, 0.3, n),
            "xg_per_shot":          rng.uniform(0.05, 0.25, n),
            "passes_per_match":     rng.uniform(20.0, 80.0, n),
            "pass_completion_rate": rng.uniform(0.6, 0.95, n),
            "avg_pass_distance_m":  rng.uniform(8.0, 30.0, n),
            "avg_location_x":       rng.uniform(30.0, 80.0, n),
            "avg_location_y":       rng.uniform(15.0, 55.0, n),
            "under_pressure_ratio": rng.uniform(0.1, 0.5, n),
        }
    )


# ---------------------------------------------------------------------------
# TestBuildSimilarityModel
# ---------------------------------------------------------------------------

class TestBuildSimilarityModel:
    def test_returns_three_objects(self):
        df = _make_players(15)
        result = build_similarity_model(df)
        assert len(result) == 3

    def test_scaled_matrix_shape(self):
        n = 15
        df = _make_players(n)
        _, _, X_scaled = build_similarity_model(df)
        assert X_scaled.shape == (n, len(FEATURE_COLS))

    def test_scaler_centres_features(self):
        df = _make_players(50)
        _, scaler, X_scaled = build_similarity_model(df)
        # StandardScaler: column means should be near 0
        assert np.abs(X_scaled.mean(axis=0)).max() < 1e-10

    def test_model_fitted_does_not_raise(self):
        df = _make_players(15)
        model, _, X_scaled = build_similarity_model(df)
        # Should not raise
        distances, _ = model.kneighbors(X_scaled[:1])
        assert distances.shape[1] >= 1

    def test_handles_fewer_players_than_n_neighbors(self):
        # With only 5 players n_neighbors must not exceed len(df)
        df = _make_players(5)
        model, _, X_scaled = build_similarity_model(df)
        distances, indices = model.kneighbors(X_scaled)
        # Should return at most 5 neighbours (including self)
        assert indices.shape[1] <= 5


# ---------------------------------------------------------------------------
# TestComputeSimilarityRows
# ---------------------------------------------------------------------------

class TestComputeSimilarityRows:
    def _build(self, n: int = 20):
        df = _make_players(n)
        model, _, X_scaled = build_similarity_model(df)
        return df, model, X_scaled

    def test_returns_list_of_dicts(self):
        df, model, X = self._build()
        rows = compute_similarity_rows(df, model, X)
        assert isinstance(rows, list)
        assert isinstance(rows[0], dict)

    def test_row_has_required_keys(self):
        df, model, X = self._build()
        rows = compute_similarity_rows(df, model, X)
        assert {"pid", "sid", "score", "rank"}.issubset(rows[0].keys())

    def test_no_self_similarity(self):
        df, model, X = self._build()
        rows = compute_similarity_rows(df, model, X)
        assert all(r["pid"] != r["sid"] for r in rows)

    def test_similarity_score_in_unit_interval(self):
        # Cosine distance ∈ [0, 2]; score = max(0, 1−dist) so always in [0, 1].
        df, model, X = self._build()
        rows = compute_similarity_rows(df, model, X)
        scores = [r["score"] for r in rows]
        assert all(0.0 <= s <= 1.0 + 1e-9 for s in scores)

    def test_rank_starts_at_one(self):
        df, model, X = self._build()
        rows = compute_similarity_rows(df, model, X)
        # Group by source player and check min rank == 1
        by_player: dict[int, list[int]] = {}
        for r in rows:
            by_player.setdefault(r["pid"], []).append(r["rank"])
        assert all(min(ranks) == 1 for ranks in by_player.values())

    def test_rank_does_not_exceed_n_neighbors(self):
        df, model, X = self._build(25)
        rows = compute_similarity_rows(df, model, X)
        assert all(r["rank"] <= N_NEIGHBORS for r in rows)

    def test_at_most_n_neighbors_per_player(self):
        df, model, X = self._build(25)
        rows = compute_similarity_rows(df, model, X)
        by_player: dict[int, int] = {}
        for r in rows:
            by_player[r["pid"]] = by_player.get(r["pid"], 0) + 1
        assert all(count <= N_NEIGHBORS for count in by_player.values())

    def test_empty_df_returns_empty_rows(self):
        df = _make_players(1)
        model, _, X_scaled = build_similarity_model(df)
        rows = compute_similarity_rows(df, model, X_scaled)
        # Single player: no neighbours to pair with
        assert rows == []

    def test_deterministic_across_calls(self):
        df, model, X = self._build()
        rows1 = compute_similarity_rows(df, model, X)
        rows2 = compute_similarity_rows(df, model, X)
        assert rows1 == rows2


# ---------------------------------------------------------------------------
# TestFeatureColsIntegrity
# ---------------------------------------------------------------------------

class TestFeatureColsIntegrity:
    def test_all_feature_cols_present_in_synthetic_df(self):
        df = _make_players(5)
        missing = [c for c in FEATURE_COLS if c not in df.columns]
        assert missing == [], f"Missing columns: {missing}"

    def test_no_nan_after_fillna(self):
        df = _make_players(10)
        # Introduce some NaN to mimic players with zero shots
        df.loc[0, "xg_per_shot"] = float("nan")
        X = df[FEATURE_COLS].fillna(0.0).values.astype(float)
        assert not np.isnan(X).any()
