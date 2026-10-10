"""Unit tests for the evaluation harness in eval_runs.py."""

import sys
from pathlib import Path

# Add backend to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from eval_runs import (
    load_expected_items,
    evaluate_run_categorized,
    categorize_item,
)


def test_load_expected_items_test4_and_test3():
    # Test loading test4 (room_labels + dimension_lines format)
    p4, items4, u4 = load_expected_items("test4")
    assert p4 is not None
    assert len(items4) == 35
    assert u4 == "feet_inches"
    cats4 = {item["category"] for item in items4}
    assert cats4 == {"room", "left_chain", "right_chain", "other_dim"}

    # Test loading test3 (dimensions format with metric units)
    p3, items3, u3 = load_expected_items("test3")
    assert p3 is not None
    assert len(items3) == 11
    assert u3 == "metres"

    # Test loading test2 (dimensions format with nested alternative readings)
    p2, items2, u2 = load_expected_items("test2")
    assert p2 is not None
    assert len(items2) == 14
    assert u2 == "metres"


def test_evaluate_run_categorized_alternatives():
    # Expected item has alternatives: [['3.784', '3.704']]
    exp_items = [
        {"name": "right side, upper", "values": [["3.784", "3.704"]], "category": "other_dim"}
    ]
    # Predict first alternative
    pred_dims_1 = [{"text_as_written": "3.784", "kind": "segment"}]
    mask, metrics = evaluate_run_categorized(pred_dims_1, "metres", exp_items)
    assert mask == [True]
    assert metrics["total"]["hits"] == 1
    assert metrics["total"]["extras"] == 0

    # Predict second alternative
    pred_dims_2 = [{"text_as_written": "3.704", "kind": "segment"}]
    mask, metrics = evaluate_run_categorized(pred_dims_2, "metres", exp_items)
    assert mask == [True]
    assert metrics["total"]["hits"] == 1


def test_evaluate_run_categorized_room_pairs_reversed():
    # Expected room is 14'-6" x 11'-0"
    exp_items = [
        {"name": "BED ROOM", "values": ["14'-6\"", "11'-0\""], "category": "room"}
    ]
    # Prediction has dimensions written in reverse order: 11'-0"x14'-6"
    pred_dims = [{"text_as_written": "11'-0\"x14'-6\"", "kind": "room_size"}]
    mask, metrics = evaluate_run_categorized(pred_dims, "feet_inches", exp_items)
    assert mask == [True]
    assert metrics["total"]["hits"] == 1


def test_evaluate_run_categorized_cross_category_fallback():
    # Expected item was tagged 'room' because of name, but model predicted it as 'other_dim'
    exp_items = [
        {"name": "bedroom 1 left arrow", "values": ["1.935"], "category": "room"}
    ]
    pred_dims = [
        {"text_as_written": "1.935", "kind": "segment", "position": "middle-left"}
    ]
    mask, metrics = evaluate_run_categorized(pred_dims, "metres", exp_items)
    assert mask == [True]
    assert metrics["total"]["hits"] == 1
    assert metrics["total"]["extras"] == 0


def test_evaluate_run_categorized_extras_and_precision():
    exp_items = [
        {"name": "width", "values": ["10'-0\""], "category": "other_dim"}
    ]
    pred_dims = [
        {"text_as_written": "10'-0\"", "kind": "segment", "legibility": "clear"},
        {"text_as_written": "99'-0\"", "kind": "segment", "legibility": "clear"},  # Extra
    ]
    mask, metrics = evaluate_run_categorized(pred_dims, "feet_inches", exp_items)
    assert mask == [True]
    assert metrics["total"]["hits"] == 1
    assert metrics["total"]["extras"] == 1
    assert metrics["total"]["clear_extras"] == 1
    assert metrics["total"]["precision"] == 50.0  # 1 hit out of 2 total predictions
