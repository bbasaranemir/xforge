"""
Unit tests for xml_generator pure functions.

Imports directly from xml_generator — DB-dependent functions (fetch_top_xt_actions, run)
are not tested here; pure functions (_game_clock_ms, _build_label_text, build_xml,
pretty_print, write_xml) are tested without a live database.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

os.environ.setdefault("POSTGRES_USER", "analytics")
os.environ.setdefault("POSTGRES_PASSWORD", "analytics_test")
os.environ.setdefault("POSTGRES_DB", "football_db_test")
os.environ.setdefault("POSTGRES_HOST", "localhost")

import xml.etree.ElementTree as ET

import pandas as pd
import pytest

from xml_generator import (
    _game_clock_ms,
    _build_label_text,
    build_xml,
    pretty_print,
    CLIP_LEAD_MS,
    CLIP_LAG_MS,
)


def _make_events(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "event_id":       [f"evt-{i}" for i in range(n)],
        "event_type":     ["Pass", "Carry", "Shot"][:n],
        "minute":         list(range(1, n + 1)),
        "second":         [30] * n,
        "player_name":    [f"Player {i}" for i in range(n)],
        "team_name":      ["Team A"] * n,
        "location_x":     [60.0 + i for i in range(n)],
        "location_y":     [34.0] * n,
        "end_location_x": [70.0 + i for i in range(n)],
        "end_location_y": [34.0] * n,
        "xt_value":       [0.05 * (n - i) for i in range(n)],
        "xg_value":       [None] * n,
        "under_pressure": [False] * n,
    })


class TestGameClockMs:
    def test_zero_zero(self):
        assert _game_clock_ms(0, 0) == 0

    def test_one_minute(self):
        assert _game_clock_ms(1, 0) == 60_000

    def test_minute_and_second(self):
        assert _game_clock_ms(1, 30) == 90_000

    def test_integer_coercion(self):
        # floats from pandas should still work
        assert _game_clock_ms(1.0, 30.0) == 90_000

    def test_large_value(self):
        assert _game_clock_ms(90, 0) == 5_400_000


class TestBuildLabelText:
    def _row(self, **kwargs):
        base = {
            "event_type": "Pass",
            "player_name": "Messi",
            "team_name": "Argentina",
            "location_x": 80.0,
            "location_y": 34.0,
            "end_location_x": 95.0,
            "end_location_y": 34.0,
            "xt_value": 0.12,
            "xg_value": None,
            "under_pressure": False,
        }
        base.update(kwargs)
        return pd.Series(base)

    def test_contains_xt(self):
        assert "xT:" in _build_label_text(self._row())

    def test_contains_event_type(self):
        assert "Pass" in _build_label_text(self._row())

    def test_contains_player_name(self):
        assert "Messi" in _build_label_text(self._row())

    def test_under_pressure_shown(self):
        text = _build_label_text(self._row(under_pressure=True))
        assert "Under Pressure" in text

    def test_no_under_pressure_hidden(self):
        text = _build_label_text(self._row(under_pressure=False))
        assert "Under Pressure" not in text

    def test_xg_shown_when_present(self):
        text = _build_label_text(self._row(xg_value=0.25))
        assert "xG:" in text

    def test_xg_hidden_when_null(self):
        text = _build_label_text(self._row(xg_value=None))
        assert "xG:" not in text


class TestBuildXml:
    def test_returns_element(self):
        root = build_xml(_make_events(), match_id=9999)
        assert isinstance(root, ET.Element)

    def test_root_tag_is_file(self):
        root = build_xml(_make_events(), match_id=9999)
        assert root.tag == "file"

    def test_instance_count_matches_events(self):
        events = _make_events(3)
        root = build_xml(events, match_id=1)
        instances = root.findall("ALL_INSTANCES/instance")
        assert len(instances) == 3

    def test_instance_ids_are_sequential(self):
        root = build_xml(_make_events(3), match_id=1)
        ids = [inst.find("ID").text for inst in root.findall("ALL_INSTANCES/instance")]
        assert ids == ["1", "2", "3"]

    def test_start_ms_applies_clip_lead(self):
        events = _make_events(1)
        root = build_xml(events, match_id=1)
        inst = root.find("ALL_INSTANCES/instance")
        clock_ms = _game_clock_ms(events.iloc[0]["minute"], events.iloc[0]["second"])
        expected_start = max(0, clock_ms - CLIP_LEAD_MS)
        assert inst.find("start").text == str(expected_start)

    def test_end_ms_applies_clip_lag(self):
        events = _make_events(1)
        root = build_xml(events, match_id=1)
        inst = root.find("ALL_INSTANCES/instance")
        clock_ms = _game_clock_ms(events.iloc[0]["minute"], events.iloc[0]["second"])
        expected_end = clock_ms + CLIP_LAG_MS
        assert inst.find("end").text == str(expected_end)

    def test_label_contains_xt_value(self):
        root = build_xml(_make_events(1), match_id=1)
        label_text = root.find("ALL_INSTANCES/instance/label/text").text
        assert "xT:" in label_text

    def test_sequence_block_present(self):
        root = build_xml(_make_events(), match_id=42)
        seq = root.find("SEQUENCE")
        assert seq is not None
        assert seq.find("match_id").text == "42"

    def test_action_count_in_sequence(self):
        events = _make_events(3)
        root = build_xml(events, match_id=1)
        count = root.find("SEQUENCE/action_count").text
        assert count == "3"

    def test_empty_df_produces_no_instances(self):
        empty = _make_events(0)
        root = build_xml(empty, match_id=1)
        assert len(root.findall("ALL_INSTANCES/instance")) == 0


class TestPrettyPrint:
    def test_returns_string(self):
        root = build_xml(_make_events(1), match_id=1)
        result = pretty_print(root)
        assert isinstance(result, str)

    def test_contains_xml_declaration(self):
        root = build_xml(_make_events(1), match_id=1)
        result = pretty_print(root)
        assert "<?xml" in result
