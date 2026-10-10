"""Unit tests for multi-pass reader components."""

import sys
from pathlib import Path

# Add backend to sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from reader import (
    _has_multiple_dims,
    _split_fallback_items,
    select_strip_orientation,
    merge_passes,
    _make_key,
)


def test_has_multiple_dims():
    # Valid single dimensions
    assert not _has_multiple_dims("50'-0\"")
    assert not _has_multiple_dims("6'-6\"")
    assert not _has_multiple_dims("7'-6\"")
    assert not _has_multiple_dims("12'-9\"")
    assert not _has_multiple_dims("14'-8\"")

    # Joined / multiple dimensions
    assert _has_multiple_dims("7'-6\"-14'-6\"")
    assert _has_multiple_dims("7'-6\" - 14'-6\"")
    assert _has_multiple_dims("7'-6\"-7'-6\"-3'-0\"-3'-0\"")
    assert _has_multiple_dims("8'-0\"x5'-0\"")


def test_split_fallback_items():
    raw = [
        {"text_as_written": "7'-6\"-14'-6\"", "legibility": "clear"},
        {"text_as_written": "50'-0\"", "legibility": "clear"},
    ]
    split = _split_fallback_items(raw, "feet_inches")
    assert len(split) == 3
    assert split[0]["text_as_written"] == "7'-6\""
    assert split[0]["legibility"] == "partly_unclear"
    assert split[0]["source"] == "split_fallback"
    assert split[1]["text_as_written"] == "14'-6\""
    assert split[1]["legibility"] == "partly_unclear"
    assert split[1]["source"] == "split_fallback"
    assert split[2]["text_as_written"] == "50'-0\""
    assert split[2]["legibility"] == "clear"


def test_select_strip_orientation_rejects_multi_dim():
    # cw has concatenated string, ccw has clean single items
    cw_items = [
        {"text_as_written": "7'-6\"-14'-6\"-50'-0\"", "legibility": "clear"}
    ]
    ccw_items = [
        {"text_as_written": "7'-6\"", "legibility": "clear"},
        {"text_as_written": "14'-6\"", "legibility": "clear"},
        {"text_as_written": "50'-0\"", "legibility": "clear"},
    ]
    items, chosen, scores = select_strip_orientation(
        cw_items, ccw_items, "right", "feet_inches", []
    )
    assert chosen == "ccw"
    assert len(items) == 3
    assert scores["cw"]["has_multi"] is True
    assert scores["ccw"]["has_multi"] is False


def test_merge_passes_deduplicates_by_pos_cell_and_applies():
    # Pass F has TOILET (top-left) and BATH (top-right) with identical sizes 8'-0"x5'-0"
    pass_f = {
        "plan_unit": "feet_inches",
        "dimensions": [
            {
                "text_as_written": "8'-0\"x5'-0\"",
                "applies_to": "TOILET",
                "kind": "room_size",
                "direction": "none",
                "position": "top-left",
                "legibility": "clear",
            },
            {
                "text_as_written": "8'-0\"x5'-0\"",
                "applies_to": "BATH",
                "kind": "room_size",
                "direction": "none",
                "position": "top-right",
                "legibility": "clear",
            },
            {
                "text_as_written": "12'-9\"",
                "applies_to": "segment 1",
                "kind": "segment",
                "direction": "horizontal",
                "position": "bottom-left",
                "legibility": "clear",
            },
            {
                "text_as_written": "12'-9\"",
                "applies_to": "segment 2",
                "kind": "segment",
                "direction": "horizontal",
                "position": "bottom-center",
                "legibility": "clear",
            },
        ],
    }

    merged = merge_passes(pass_f, [], [], [], [])
    dims = merged["dimensions"]

    # Both rooms must be preserved (not merged together)
    toilet = [d for d in dims if d.get("applies_to") == "TOILET"]
    bath = [d for d in dims if d.get("applies_to") == "BATH"]
    assert len(toilet) == 1
    assert len(bath) == 1

    # Both segments must be preserved (not merged together)
    segments = [d for d in dims if d.get("text_as_written") == "12'-9\""]
    assert len(segments) == 2


def test_find_content_bbox():
    from PIL import Image
    # 100x100 white image with a dark rectangle from (20, 25) to (80, 85)
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    for x in range(20, 80):
        for y in range(25, 85):
            img.putpixel((x, y), (0, 0, 0))

    from reader import find_content_bbox
    bbox = find_content_bbox(img, border_pct=0.02)
    assert bbox[0] == 20
    assert bbox[1] == 25
    assert bbox[2] == 80
    assert bbox[3] == 85


def test_strip_only_and_support():
    # Pass F has a vertical 7'-6" on left
    pass_f = {
        "plan_unit": "feet_inches",
        "dimensions": [
            {
                "text_as_written": "7'-6\"",
                "applies_to": "left wall",
                "kind": "overall",
                "direction": "vertical",
                "position": "top-left",
                "legibility": "clear",
            }
        ],
    }

    # Strip left has 7'-6" (corroborated by Pass F) and 99'-0" (uncorroborated phantom)
    strip_left = [
        {
            "text_as_written": "7'-6\"",
            "chain_index": 0,
            "chain_side": "left",
            "direction": "vertical",
            "position": "middle-left",
            "applies_to": "left vertical chain #1",
            "kind": "segment",
            "legibility": "clear",
            "mm": [2286.0],
        },
        {
            "text_as_written": "99'-0\"",
            "chain_index": 1,
            "chain_side": "left",
            "direction": "vertical",
            "position": "middle-left",
            "applies_to": "left vertical chain #2",
            "kind": "segment",
            "legibility": "clear",
            "mm": [30175.2],
        },
    ]

    merged = merge_passes(pass_f, strip_left, [], [], [])
    dims = merged["dimensions"]

    item_76 = [d for d in dims if d.get("text_as_written") == "7'-6\""][0]
    item_99 = [d for d in dims if d.get("text_as_written") == "99'-0\""][0]

    # Corroborated item has support=2, status="verified"
    assert item_76["support"] == 2
    assert item_76["status"] == "verified"

    # Uncorroborated strip item is marked strip_only, partly_unclear, support=1, status="unverified"
    assert item_99["source"] == "strip_only"
    assert item_99["legibility"] == "partly_unclear"
    assert item_99["support"] == 1
    assert item_99["status"] == "unverified"

