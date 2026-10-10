"""Regression for merge_passes: no model, no OCR, synthetic pass outputs.

Fails on the old merge (kitchen listed twice, lower bedroom's band reading
credited to the upper bedroom) and passes after dedupe.py is wired in.
The room names below are test data only; no room name appears in the code under test.
"""
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import floorplan_reader_v2 as v2  # noqa: E402
import parse  # noqa: E402
import reader  # noqa: E402

v2.parse_label = parse.parse_label  # keep both parsers identical in this test


def it(text, app, kind, pos, direction="none"):
    return {"text_as_written": text, "applies_to": app, "kind": kind, "position": pos,
            "direction": direction, "legibility": "clear"}


def merged():
    f = {"plan_unit": "feet_inches", "unit_evidence": "marks", "dimensions": [
        it("11'x12'-6\"", "Master Bed Room", "room_size", "top-left"),
        it("12'-6\"", "KITCHEN", "room_size", "middle-right"),
        it("11'x12'-6\"", "Bed Room", "room_size", "bottom-left"),
    ]}
    top = [it("11'x12'-6\"", "Master Bed Room", "room_size", "top-left"),
           it("10' x 12'-6\"", "KITCHEN", "room_size", "top-right")]
    bottom = [it("11'x12'-6\"", "Bed Room", "room_size", "bottom-left")]
    return reader.merge_passes(f, [], [], top, bottom, ocr_detections=None)["dimensions"]


def test_fragment_and_full_reading_are_one_label():
    side = [d for d in merged() if "kitchen" in d["applies_to"].lower()]
    assert len(side) == 1
    assert len(side[0]["mm"]) == 2


def test_band_reading_is_credited_to_the_right_room():
    by_name = {d["applies_to"]: d for d in merged()}
    assert "H_bottom" not in by_name["Master Bed Room"]["sources"]
    assert "H_bottom" in by_name["Bed Room"]["sources"]
    assert "H_top" not in by_name["Bed Room"]["sources"]
