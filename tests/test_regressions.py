"""Regression tests for bugs found while reviewing the multi-pass reader.

Each test fails on the earlier code and passes after the fixes.
"""

import contextlib
import io
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from ocr_engine import find_matching_ocr_boxes  # noqa: E402
from parse import format_mm, parse_label  # noqa: E402
from reader import select_strip_orientation  # noqa: E402


def _ft(text):
    return [format_mm(v, "feet_inches") for v in parse_label(text, "feet_inches")]


def _item(text):
    return {"text_as_written": text, "legibility": "clear"}


# --- parsing --------------------------------------------------------------

def test_missing_foot_mark_with_dash_keeps_the_feet():
    # OCR often drops the ' mark: "12-6\"" must be 12 ft 6 in, not 6 in
    assert _ft('12-6"') == ["12'6\""]
    assert _ft('11\u00d712-6"') == ["11'0\"", "12'6\""]


def test_dropped_one_in_half_inch_is_repaired():
    # The model sometimes writes 8'7 1/2" as 8'7/2"
    assert _ft("8'7/2\"") == ["8'7 1/2\""]
    assert _ft("10'7/8\"x12'") == ["10'7 1/2\"", "12'0\""]


def test_half_repair_does_not_touch_correct_labels():
    assert _ft("8'7 1/2\"") == ["8'7 1/2\""]
    assert _ft("12'-6\"") == ["12'6\""]
    assert _ft("10'-0\"x14'-0\"") == ["10'0\"", "14'0\""]


# --- orientation selection -------------------------------------------------

def test_orientation_tie_break_uses_ccw_sum_on_both_sides():
    bad = [_item("10'"), _item("19'"), _item("30'")]    # sums to 29 ft, overall 30 ft
    good = [_item("10'"), _item("20'"), _item("30'")]   # sums to 30 ft
    for side in ("left", "right"):
        with contextlib.redirect_stdout(io.StringIO()):
            _, chosen, _ = select_strip_orientation(bad, good, side, "feet_inches", [])
        assert chosen == "ccw", f"side={side}"


# --- OCR verification ------------------------------------------------------

def test_ocr_fragment_does_not_verify_a_longer_hallucinated_label():
    detections = [{"text": "12'", "confidence": 0.9, "box": [0, 0, 1, 1]}]
    assert find_matching_ocr_boxes("12'4\" x 12'1\"", detections, "feet_inches") == []


def test_ocr_exact_and_numeric_matches_still_work():
    detections = [{"text": "14'-6\"x11'-0\"", "confidence": 0.9, "box": [0, 0, 1, 1]}]
    assert len(find_matching_ocr_boxes("14'-6\"x11'-0\"", detections, "feet_inches")) == 1
    assert len(find_matching_ocr_boxes("11'-0\"x14'-6\"", detections, "feet_inches")) == 1
