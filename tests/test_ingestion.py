import uuid
import pandas as pd
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

os.environ.setdefault("POSTGRES_USER", "analytics")
os.environ.setdefault("POSTGRES_PASSWORD", "analytics_test")
os.environ.setdefault("POSTGRES_DB", "football_db_test")
os.environ.setdefault("POSTGRES_HOST", "localhost")

import massive_ingestion as mi


def _sample_events():
    return pd.DataFrame({
        "id":              [str(uuid.uuid4()) for _ in range(5)],
        "team_id":         [1, 1, 2, 2, 1],
        "team":            ["Arsenal"] * 3 + ["Chelsea"] * 2,
        "player_id":       [10, 10, 20, 20, 10],
        "player":          ["A", "A", "B", "B", "A"],
        "position":        [{"name": "Forward"}] * 5,
        "type":            [{"name": "Pass"}] * 5,
        "minute":          [1, 5, 10, 15, 20],
        "second":          [0] * 5,
        "location":        [[60, 40]] * 5,
        "under_pressure":  [False] * 5,
        "pass_end_location":    [[80, 40], [85, 30], None, None, [70, 40]],
        "carry_end_location":   [None] * 5,
        "shot_end_location":    [None] * 5,
        "pass_outcome":         [None, "Incomplete", None, None, None],
        "shot_outcome":         [None] * 5,
        "duel_outcome":         [None] * 5,
        "clearance_outcome":    [None] * 5,
    })


def test_flatten_events_columns():
    df   = _sample_events()
    flat = mi.flatten_events(df, match_id=999, competition_id=43)
    expected = {
        "event_id", "match_id", "competition_id", "team_id", "player_id",
        "event_type", "minute", "second", "location_x", "location_y",
        "end_location_x", "end_location_y", "outcome", "under_pressure",
        "xt_value", "xp_value", "raw_json",
    }
    assert expected.issubset(set(flat.columns))


def test_flatten_events_match_id():
    df   = _sample_events()
    flat = mi.flatten_events(df, match_id=1234, competition_id=2)
    assert (flat["match_id"] == 1234).all()
    assert (flat["competition_id"] == 2).all()


def test_flatten_end_location():
    df   = _sample_events()
    flat = mi.flatten_events(df, match_id=1, competition_id=1)
    assert flat.iloc[0]["end_location_x"] == 80.0
    assert flat.iloc[2]["end_location_x"] is None or pd.isna(flat.iloc[2]["end_location_x"])


def test_coord_helper():
    assert mi._coord([10.5, 20.3], 0) == 10.5
    assert mi._coord([10.5, 20.3], 1) == 20.3
    assert mi._coord(None, 0) is None
    assert mi._coord([], 0) is None
