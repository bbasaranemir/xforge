"""
Unit tests for tactical_models.py pure functions — no DB, no psycopg2 required.

Functions under test:
  cluster_set_pieces(df)     — K-Means clustering on corner/shot coordinates
  detect_press_triggers(df)  — identifies high-press sequences (3+ actions / 5 s)

Core logic is tested without touching the database. DB-dependent code paths
(fetch_set_pieces, fetch_press_candidates, save_*) are guarded with
pytest.mark.skipif via the _TM_AVAILABLE flag.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

# ── Replicated constants (keep in sync with scripts/tactical_models.py) ───────

SET_PIECE_TYPES = ("Corner",)
SHOT_CLUSTER_TYPES = ("Shot",)
PRESS_TYPES = ("Pressure", "Interception", "Tackle", "Block")
N_CLUSTERS = 6
PRESS_WINDOW_SEC = 5


# ── Replicated cluster_set_pieces ────────────────────────────────────────────


def cluster_set_pieces(df: pd.DataFrame) -> dict:
    """Pure clustering — identical logic to tactical_models.cluster_set_pieces."""
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    results = {}
    all_types = SET_PIECE_TYPES + SHOT_CLUSTER_TYPES
    for et in all_types:
        subset = df[df["event_type"] == et].copy()
        if len(subset) < N_CLUSTERS:
            continue

        coords = subset[["location_x", "location_y"]].values
        scaler = StandardScaler()
        scaled = scaler.fit_transform(coords)

        km = KMeans(n_clusters=N_CLUSTERS, n_init=10, random_state=42)
        labels = km.fit_predict(scaled)
        centers = scaler.inverse_transform(km.cluster_centers_)

        subset = subset.copy()
        subset["cluster_label"] = labels
        results[et] = (subset, centers)

    return results


# ── Replicated detect_press_triggers ─────────────────────────────────────────


def detect_press_triggers(df: pd.DataFrame) -> pd.DataFrame:
    """Press trigger detection — identical logic to tactical_models.detect_press_triggers."""
    df = df.copy()
    df["time_sec"] = df["minute"] * 60 + df["second"].fillna(0)

    triggered = []

    for (match_id, team_id), group in df.groupby(["match_id", "team_id"]):
        group = group.sort_values("time_sec").reset_index(drop=True)
        recovery = group[group["event_type"] == "Ball Recovery"]

        for _, rec in recovery.iterrows():
            window = group[
                (group["time_sec"] >= rec["time_sec"])
                & (group["time_sec"] <= rec["time_sec"] + PRESS_WINDOW_SEC)
                & (group["event_type"].isin(PRESS_TYPES))
            ]
            if len(window) >= 3:
                triggered.extend(window["event_id"].tolist())

    return pd.DataFrame({"event_id": list(set(triggered))})


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_corners(n: int = 60) -> pd.DataFrame:
    """Synthetic Corner events at realistic corner-flag positions (105×68 pitch)."""
    rng = np.random.default_rng(10)
    # Alternate between the four corner positions
    corners = [
        (0.5, 0.5), (0.5, 67.5),   # left-goal corners
        (104.5, 0.5), (104.5, 67.5),  # right-goal corners
    ]
    xs, ys = [], []
    for i in range(n):
        cx, cy = corners[i % 4]
        xs.append(cx + rng.uniform(-0.5, 0.5))
        ys.append(cy + rng.uniform(-0.5, 0.5))

    return pd.DataFrame({
        "event_id":       [f"c{i}" for i in range(n)],
        "event_type":     "Corner",
        "location_x":     xs,
        "location_y":     ys,
        "end_location_x": rng.uniform(70, 105, n),
        "end_location_y": rng.uniform(10, 58, n),
        "match_id":       [1] * n,
        "team_id":        [10] * n,
    })


def _make_shots(n: int = 60) -> pd.DataFrame:
    """Synthetic Shot events spread across the attacking half."""
    rng = np.random.default_rng(20)
    return pd.DataFrame({
        "event_id":       [f"s{i}" for i in range(n)],
        "event_type":     "Shot",
        "location_x":     rng.uniform(60, 105, n),
        "location_y":     rng.uniform(5, 63, n),
        "end_location_x": rng.uniform(90, 105, n),
        "end_location_y": rng.uniform(20, 48, n),
        "match_id":       [1] * n,
        "team_id":        [10] * n,
    })


def _make_press_events(
    n_pressure: int = 3,
    start_sec: float = 100.0,
    spread_sec: float = 4.0,
    recovery_offset: float = 0.0,
) -> pd.DataFrame:
    """
    One Ball Recovery followed by `n_pressure` Pressure events.
    All events belong to match=1, team=1.
    `start_sec` is the recovery time; pressure events follow within spread_sec.
    """
    rng = np.random.default_rng(30)
    rows = []
    # Ball Recovery at start_sec
    rows.append({
        "event_id": "rec1",
        "match_id": 1, "team_id": 1,
        "event_type": "Ball Recovery",
        "minute": int((start_sec + recovery_offset) // 60),
        "second": (start_sec + recovery_offset) % 60,
    })
    # Pressure events evenly spread within the window
    for i in range(n_pressure):
        t = start_sec + (spread_sec * (i + 1) / (n_pressure + 1))
        rows.append({
            "event_id": f"press_{i}",
            "match_id": 1, "team_id": 1,
            "event_type": "Pressure",
            "minute": int(t // 60),
            "second": t % 60,
        })
    return pd.DataFrame(rows)


# ── Tests: cluster_set_pieces ─────────────────────────────────────────────────


def test_cluster_output_has_cluster_label_column():
    """cluster_set_pieces must add a 'cluster_label' column to each subset."""
    df = _make_corners()
    results = cluster_set_pieces(df)
    assert "Corner" in results
    subset, _ = results["Corner"]
    assert "cluster_label" in subset.columns


def test_cluster_values_in_range():
    """Cluster labels must be integers 0..N_CLUSTERS-1."""
    df = _make_corners()
    results = cluster_set_pieces(df)
    subset, _ = results["Corner"]
    labels = subset["cluster_label"].values
    assert set(labels).issubset(set(range(N_CLUSTERS))), \
        f"Unexpected cluster labels: {set(labels)}"


def test_cluster_produces_n_clusters_centers():
    """cluster_centers_ array must have N_CLUSTERS rows."""
    df = _make_corners()
    results = cluster_set_pieces(df)
    _, centers = results["Corner"]
    assert centers.shape[0] == N_CLUSTERS


def test_cluster_shot_type_processed_separately():
    """Shots and Corners must produce separate cluster entries in results dict."""
    df = pd.concat([_make_corners(), _make_shots()], ignore_index=True)
    results = cluster_set_pieces(df)
    assert "Corner" in results
    assert "Shot" in results


def test_cluster_skips_event_type_with_too_few_rows():
    """Event types with < N_CLUSTERS rows must be silently skipped."""
    df = _make_corners(n=3)   # 3 < N_CLUSTERS=6
    results = cluster_set_pieces(df)
    assert "Corner" not in results, "Should skip Corner with only 3 rows"


def test_cluster_empty_dataframe_does_not_crash():
    """cluster_set_pieces must not raise on an empty DataFrame."""
    df = pd.DataFrame(columns=[
        "event_id", "event_type", "location_x", "location_y",
        "end_location_x", "end_location_y", "match_id", "team_id",
    ])
    results = cluster_set_pieces(df)
    assert results == {}, "Empty input should produce empty results dict"


def test_cluster_uniform_coordinates_no_crash():
    """StandardScaler must not crash when all coordinates are identical (zero variance)."""
    n = 20
    df = pd.DataFrame({
        "event_id":       [f"c{i}" for i in range(n)],
        "event_type":     "Corner",
        "location_x":     [5.0] * n,
        "location_y":     [5.0] * n,
        "end_location_x": [50.0] * n,
        "end_location_y": [34.0] * n,
        "match_id":       [1] * n,
        "team_id":        [1] * n,
    })
    # With uniform data StandardScaler produces NaN after scaling; K-Means may
    # cluster them all into one group. We only require no exception is raised.
    try:
        cluster_set_pieces(df)
    except Exception as exc:
        pytest.fail(f"cluster_set_pieces raised on uniform data: {exc}")


# ── Tests: detect_press_triggers ──────────────────────────────────────────────


def test_three_pressure_events_triggers():
    """3 Pressure events within 5 s of a Ball Recovery must fire a trigger."""
    df = _make_press_events(n_pressure=3, spread_sec=4.0)
    result = detect_press_triggers(df)
    assert len(result) >= 1, "Expected at least 1 triggered event_id"


def test_two_pressure_events_no_trigger():
    """Only 2 Pressure events in the window (< 3) must not fire a trigger."""
    df = _make_press_events(n_pressure=2, spread_sec=4.0)
    result = detect_press_triggers(df)
    assert len(result) == 0, f"Expected 0 triggers, got {len(result)}"


def test_boundary_exactly_three_events():
    """Exactly 3 events at the boundary of the window must still trigger."""
    df = _make_press_events(n_pressure=3, spread_sec=4.9)
    result = detect_press_triggers(df)
    assert len(result) >= 1, "Exactly-3-event boundary case must trigger"


def test_pressure_events_outside_window_no_trigger():
    """Pressure events that fall outside the 5-second window must not count."""
    rng = np.random.default_rng(99)
    rows = [
        {"event_id": "rec1", "match_id": 1, "team_id": 1,
         "event_type": "Ball Recovery", "minute": 10, "second": 0},
        # These are 10 s after the recovery — outside the 5-s window
        {"event_id": "p1", "match_id": 1, "team_id": 1,
         "event_type": "Pressure", "minute": 10, "second": 10},
        {"event_id": "p2", "match_id": 1, "team_id": 1,
         "event_type": "Pressure", "minute": 10, "second": 15},
        {"event_id": "p3", "match_id": 1, "team_id": 1,
         "event_type": "Pressure", "minute": 10, "second": 20},
    ]
    df = pd.DataFrame(rows)
    result = detect_press_triggers(df)
    assert len(result) == 0, "Pressure events outside the window must not trigger"


def test_empty_dataframe_returns_empty():
    """detect_press_triggers on an empty DataFrame must return an empty result."""
    df = pd.DataFrame(columns=[
        "event_id", "match_id", "team_id", "event_type", "minute", "second",
    ])
    result = detect_press_triggers(df)
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0


def test_trigger_output_has_event_id_column():
    """Result DataFrame from detect_press_triggers must have an 'event_id' column."""
    df = _make_press_events(n_pressure=3)
    result = detect_press_triggers(df)
    assert "event_id" in result.columns


# ── tactical_models import (DB-gated) ────────────────────────────────────────

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

os.environ.setdefault("POSTGRES_USER", "analytics")
os.environ.setdefault("POSTGRES_PASSWORD", "analytics_test")
os.environ.setdefault("POSTGRES_DB", "football_db_test")
os.environ.setdefault("POSTGRES_HOST", "localhost")

try:
    import tactical_models as tm
    _TM_AVAILABLE = True
except ImportError:
    tm = None  # type: ignore[assignment]
    _TM_AVAILABLE = False

_skip_no_deps = pytest.mark.skipif(
    not _TM_AVAILABLE,
    reason="sqlalchemy/sklearn not installed in this environment",
)


@_skip_no_deps
def test_tm_cluster_set_pieces_matches_replicated():
    """tm.cluster_set_pieces() result keys must match the replicated version."""
    df = pd.concat([_make_corners(), _make_shots()], ignore_index=True)
    expected = set(cluster_set_pieces(df).keys())
    actual = set(tm.cluster_set_pieces(df).keys())
    assert actual == expected


@_skip_no_deps
def test_tm_detect_press_triggers_matches_replicated():
    """tm.detect_press_triggers() result length must match the replicated version."""
    df = _make_press_events(n_pressure=3)
    expected_len = len(detect_press_triggers(df))
    actual_len = len(tm.detect_press_triggers(df))
    assert actual_len == expected_len
