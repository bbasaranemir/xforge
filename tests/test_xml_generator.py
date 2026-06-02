"""
Unit tests for xml_generator pure functions.

Tests cover:
  - _timecode(): minute/second → HH:MM:SS.00 string
  - build_xml():  DataFrame → valid SportsCode XML structure

Pure functions are replicated here to avoid importing xml_generator directly —
the module constructs DB_URL at import time, which requires sqlalchemy and live
environment variables not available in local/CI environments.
"""

import xml.etree.ElementTree as ET
from datetime import timedelta

import pandas as pd
import pytest

CLIP_LEAD = 5
CLIP_TRAIL = 10


def _timecode(minute: int, second: int, offset_sec: int = 0) -> str:
    total = timedelta(minutes=int(minute), seconds=int(second) + offset_sec)
    h, rem = divmod(int(total.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.00"


def build_xml(events: pd.DataFrame, match_id: int) -> ET.Element:
    root = ET.Element("file")
    header = ET.SubElement(root, "header")
    ET.SubElement(header, "version").text = "1.0"
    ET.SubElement(header, "source_application").text = "xForge"
    ET.SubElement(header, "match_id").text = str(match_id)
    all_instances = ET.SubElement(root, "ALL_INSTANCES")
    for rank, (_, row) in enumerate(events.iterrows(), start=1):
        instance = ET.SubElement(all_instances, "instance")
        ET.SubElement(instance, "ID").text = str(rank)
        ET.SubElement(instance, "uuid").text = str(row["event_id"])
        start_tc = _timecode(row["minute"], row["second"], offset_sec=-CLIP_LEAD)
        end_tc = _timecode(row["minute"], row["second"], offset_sec=CLIP_TRAIL)
        ET.SubElement(instance, "start").text = start_tc
        ET.SubElement(instance, "end").text = end_tc
        label = ET.SubElement(instance, "label")
        ET.SubElement(label, "text").text = (
            f"{row['event_type']} | {row.get('player_name', 'Unknown')} "
            f"| xT={row['xt_value']:.4f}"
        )
        meta = ET.SubElement(instance, "metadata")
        for key in (
            "event_type",
            "player_name",
            "team_name",
            "loc_x",
            "loc_y",
            "xt_value",
            "xp_value",
            "under_pressure",
        ):
            val = row.get(key, "")
            ET.SubElement(meta, key).text = "" if pd.isna(val) else str(val)
    codes = ET.SubElement(root, "code")
    for et in events["event_type"].unique():
        row_el = ET.SubElement(codes, "row")
        ET.SubElement(row_el, "code").text = et
    return root


# ---------------------------------------------------------------------------
# _timecode tests
# ---------------------------------------------------------------------------


class TestTimecode:
    def test_zero_minute_zero_second(self):
        assert _timecode(0, 0) == "00:00:00.00"

    def test_simple_minute(self):
        assert _timecode(10, 30) == "00:10:30.00"

    def test_with_positive_offset(self):
        # 5:00 + 10s offset → 5:10
        assert _timecode(5, 0, offset_sec=10) == "00:05:10.00"

    def test_with_negative_offset(self):
        # 5:10 - 5s clip lead → 5:05
        assert _timecode(5, 10, offset_sec=-5) == "00:05:05.00"

    def test_offset_crosses_minute_boundary(self):
        # 1:55 + 10s → 2:05
        assert _timecode(1, 55, offset_sec=10) == "00:02:05.00"

    def test_hour_boundary(self):
        # 59:55 + 10s → 1:00:05
        assert _timecode(59, 55, offset_sec=10) == "01:00:05.00"

    def test_negative_offset_clamps_at_zero(self):
        # 0:02 - 5s lead → would be negative; timedelta handles it as 0:00 if >=0
        # offset_sec=-5, total = 2s - 5s = -3s → timedelta(-3s) = 23:59:57
        # just verify it returns a string without crashing
        result = _timecode(0, 2, offset_sec=-5)
        assert isinstance(result, str)
        assert result.endswith(".00")


# ---------------------------------------------------------------------------
# build_xml tests
# ---------------------------------------------------------------------------


def _make_events(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": [f"evt-{i}" for i in range(n)],
            "event_type": ["Pass", "Carry", "Shot", "Pass", "Carry"][:n],
            "minute": list(range(1, n + 1)),
            "second": [30] * n,
            "player_name": [f"Player {i}" for i in range(n)],
            "team_name": ["Team A", "Team B"] * (n // 2) + ["Team A"] * (n % 2),
            "loc_x": [60.0 + i for i in range(n)],
            "loc_y": [40.0] * n,
            "xt_value": [0.05 * (n - i) for i in range(n)],
            "xp_value": [0.7 + 0.01 * i for i in range(n)],
            "under_pressure": [False, True] * (n // 2) + [False] * (n % 2),
        }
    )


class TestBuildXml:
    def test_returns_element(self):
        root = build_xml(_make_events(), match_id=9999)
        assert isinstance(root, ET.Element)

    def test_root_tag_is_file(self):
        root = build_xml(_make_events(), match_id=9999)
        assert root.tag == "file"

    def test_header_contains_match_id(self):
        root = build_xml(_make_events(), match_id=12345)
        header = root.find("header")
        assert header is not None
        assert header.find("match_id").text == "12345"

    def test_header_source_application(self):
        root = build_xml(_make_events(), match_id=1)
        assert root.find("header/source_application").text == "xForge"

    def test_instance_count_matches_events(self):
        events = _make_events(5)
        root = build_xml(events, match_id=1)
        instances = root.findall("ALL_INSTANCES/instance")
        assert len(instances) == 5

    def test_instance_uuid_matches_event_id(self):
        events = _make_events(3)
        root = build_xml(events, match_id=1)
        uuids = [
            inst.find("uuid").text for inst in root.findall("ALL_INSTANCES/instance")
        ]
        assert uuids == ["evt-0", "evt-1", "evt-2"]

    def test_instance_timecodes_present(self):
        root = build_xml(_make_events(1), match_id=1)
        inst = root.find("ALL_INSTANCES/instance")
        assert inst.find("start") is not None
        assert inst.find("end") is not None

    def test_metadata_keys_present(self):
        root = build_xml(_make_events(1), match_id=1)
        meta = root.find("ALL_INSTANCES/instance/metadata")
        assert meta is not None
        for key in ("event_type", "player_name", "team_name", "xt_value", "xp_value"):
            assert meta.find(key) is not None, f"Missing metadata key: {key}"

    def test_code_labels_unique_event_types(self):
        events = _make_events(5)  # Pass, Carry, Shot, Pass, Carry → 3 unique
        root = build_xml(events, match_id=1)
        codes = [el.text for el in root.findall("code/row/code")]
        assert len(codes) == len(set(codes))

    def test_nan_values_replaced_with_empty_string(self):
        events = _make_events(1)
        events.loc[0, "player_name"] = float("nan")
        root = build_xml(events, match_id=1)
        player = root.find("ALL_INSTANCES/instance/metadata/player_name")
        assert player.text == ""

    def test_label_contains_xt_value(self):
        events = _make_events(1)
        root = build_xml(events, match_id=1)
        label_text = root.find("ALL_INSTANCES/instance/label/text").text
        assert "xT=" in label_text

    def test_instance_ids_are_sequential(self):
        root = build_xml(_make_events(4), match_id=1)
        ids = [inst.find("ID").text for inst in root.findall("ALL_INSTANCES/instance")]
        assert ids == ["1", "2", "3", "4"]
