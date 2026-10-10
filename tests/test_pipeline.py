import copy
import json
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from pipeline import run_plan, _verify_chain_geometry


def test_pipeline_does_not_modify_input():
    sample_plan = {
        "plan_unit": "feet_inches",
        "dimensions": [
            {"text_as_written": "10'x12'", "kind": "room_size", "applies_to": "Bed", "mm": [3048.0, 3657.6]},
            {"text_as_written": "26'0\"", "kind": "overall", "applies_to": "Width", "mm": [7924.8]},
        ],
    }
    input_copy = copy.deepcopy(sample_plan)
    res = run_plan(sample_plan)
    assert sample_plan == input_copy
    assert "room_areas" in res
    assert res["summary"]["total_labels"] == 2


def test_pipeline_computes_room_areas():
    plan = {
        "plan_unit": "feet_inches",
        "dimensions": [
            {"text_as_written": "10'x12'", "kind": "room_size", "applies_to": "Room A", "mm": [3048.0, 3657.6]},
            {"text_as_written": "14'-6\"x11'-0\"", "kind": "room_size", "applies_to": "Room B", "mm": [4419.6, 3352.8]},
        ],
    }
    res = run_plan(plan)
    areas = res["room_areas"]
    assert len(areas) == 2
    # 10 * 12 = 120 sq ft
    assert areas[0]["area_sq_ft"] == 120.0
    # 14.5 * 11 = 159.5 sq ft
    assert areas[1]["area_sq_ft"] == 159.5
    assert res["sum_of_rooms_read_sq_ft"] == 279.5
    assert res["total_area_sq_ft"] == 279.5
    assert res["sum_of_rooms_read_sq_m"] > 0


def test_pipeline_step1_geometry_rejection():
    """Step 1: Chains built from OCR bbox geometry, rejecting ungrounded chains."""
    # 1a. Test test5: width_overall_32 must NOT survive because labels lack colinear tiling bboxes
    t5_path = Path("results/test5_result.json")
    if t5_path.is_file():
        with open(t5_path, encoding="utf-8") as f:
            d5 = json.load(f)
        res5 = run_plan(d5)
        c_ids5 = [c["id"] for c in res5["constraints"]]
        assert "width_overall_32" not in c_ids5, f"width_overall_32 unexpectedly survived in test5: {c_ids5}"
        # Candidate 32' label must remain unverified
        lbl_32 = next((l for l in res5["labels"] if l["text_as_written"] == "32'"), None)
        if lbl_32:
            assert lbl_32["status"] == "unverified"

    # 1b. Test test3: width_overall_7 must NOT survive
    t3_path = Path("results/test3_multipass_run1.json")
    if t3_path.is_file():
        with open(t3_path, encoding="utf-8") as f:
            d3 = json.load(f)
        res3 = run_plan(d3)
        c_ids3 = [c["id"] for c in res3["constraints"]]
        assert not any("width_overall" in cid for cid in c_ids3), f"width_overall_7 survived in test3: {c_ids3}"

    # 1c. Test synthetic OCR geometry verification
    # OCR box format is canonical Box(x0, y0, x1, y1) in page pixels
    ov = {"text_as_written": "30'-0\"", "ocr_box": [50.0, 100.0, 550.0, 140.0]}  # y-center: 120, x-span: 50..550
    parts_valid = [
        {"text_as_written": "15'-0\"", "ocr_box": [50.0, 105.0, 290.0, 135.0]},   # y-center: 120, x-span: 50..290
        {"text_as_written": "15'-0\"", "ocr_box": [300.0, 102.0, 550.0, 138.0]},  # y-center: 120, x-span: 300..550
    ]
    ok, _ = _verify_chain_geometry(ov, parts_valid, "horizontal", y_tol=30.0)
    assert ok, "Colinear tiling parts should pass geometry verification"

    parts_deviant_y = [
        {"text_as_written": "15'-0\"", "ocr_box": [50.0, 250.0, 290.0, 280.0]},   # y-center: 265 deviates > 30px
        {"text_as_written": "15'-0\"", "ocr_box": [300.0, 102.0, 550.0, 138.0]},
    ]
    ok_dev, _ = _verify_chain_geometry(ov, parts_deviant_y, "horizontal", y_tol=30.0)
    assert not ok_dev, "Parts with deviant y-centers should fail geometry verification"


def test_pipeline_step1_boxes_and_strip_mapping():
    """Step 1: Unique in-page OCR boxes, no sharing, and strip coordinate inversion."""
    from reader import map_strip_box_to_page
    from geometry import Box

    # 1a. Test coordinate mapping
    # Local box on rotated strip in canonical Box(x0, y0, x1, y1)
    # Strip cropped at (x0=0, y0=40), width=123, height=984, scale=2.0
    box_local = [50.0, 100.0, 120.0, 150.0]
    mapped_ccw = map_strip_box_to_page(box_local, "left", "ccw", strip_w=123, strip_h=984, crop_x0=0, crop_y0=40, scale=2.0)
    assert isinstance(mapped_ccw, Box)
    assert mapped_ccw.x0 >= 0.0 and mapped_ccw.x1 <= 123.0
    assert mapped_ccw.y0 >= 40.0 and mapped_ccw.y1 <= 40.0 + 984.0

    # CW rotation:
    mapped_cw = map_strip_box_to_page(box_local, "right", "cw", strip_w=123, strip_h=984, crop_x0=700, crop_y0=40, scale=2.0)
    assert isinstance(mapped_cw, Box)
    assert mapped_cw.x0 >= 700.0 and mapped_cw.x1 <= 700.0 + 123.0
    assert mapped_cw.y0 >= 40.0 and mapped_cw.y1 <= 40.0 + 984.0

    # 1b. Test saved test4 result: all boxes are inside page bounds and strictly non-shared
    test4_path = Path("results/test4_result.json")
    if not test4_path.is_file():
        pytest.skip("test4_result.json not present in results/")

    with open(test4_path, encoding="utf-8") as f:
        data = json.load(f)

    # test4 image dimensions: 820 x 1024
    img_w, img_h = 820, 1024
    chain_boxes = []
    for dim in data.get("dimensions", []):
        box = dim.get("ocr_box")
        if box:
            x0, y0, x1, y1 = box
            assert 0 <= x0 <= img_w and 0 <= x1 <= img_w, f"Box {box} out of width bounds [0, {img_w}]"
            assert 0 <= y0 <= img_h and 0 <= y1 <= img_h, f"Box {box} out of height bounds [0, {img_h}]"
            if dim.get("chain_side") or "chain" in str(dim.get("applies_to", "")).lower():
                chain_boxes.append(tuple(box))

    # Strict uniqueness invariant
    assert len(chain_boxes) == len(set(chain_boxes)), f"Duplicate/shared boxes found in chain labels: {len(chain_boxes)} vs {len(set(chain_boxes))} unique"


def test_pipeline_step2_shared_chain_rule_and_corroboration():
    """Step 2: Shared chain rule from reader.py, 60% box corroboration, and constraint survival."""
    from reader import is_valid_chain

    # 2a. Rule validity check
    assert not is_valid_chain([100.0, 200.0]), "Fewer than 3 values must fail"
    assert not is_valid_chain([102.0, 42.0, 83.0]), "test5 values (102 < 1.5*83) must fail"
    assert is_valid_chain([600.0, 174.0, 90.0]), "test4 values (600 >= 1.5*174) must pass"

    # 2b. Check test5: left_chain drops (membership uncertain)
    t5_path = Path("results/test5_result.json")
    if t5_path.is_file():
        with open(t5_path, encoding="utf-8") as f:
            d5 = json.load(f)
        res5 = run_plan(d5)
        c_ids5 = [c["id"] for c in res5["constraints"]]
        assert "left_chain" not in c_ids5, f"test5 left_chain unexpectedly survived: {c_ids5}"
        assert any(l["status"] == "membership uncertain" for l in res5["labels"])

    # 2c. Check test2: right_chain drops (membership uncertain)
    t2_path = Path("results/test2_multipass_run1.json")
    if t2_path.is_file():
        with open(t2_path, encoding="utf-8") as f:
            d2 = json.load(f)
        res2 = run_plan(d2)
        c_ids2 = [c["id"] for c in res2["constraints"]]
        assert "right_chain" not in c_ids2, f"test2 right_chain unexpectedly survived: {c_ids2}"
        assert any(l["status"] == "membership uncertain" for l in res2["labels"])

    # 2d. Check test4: left_chain survives (66.7% >= 60% boxes), right_chain drops (28.6% < 60% boxes)
    t4_path = Path("results/test4_result.json")
    if t4_path.is_file():
        with open(t4_path, encoding="utf-8") as f:
            d4 = json.load(f)
        res4 = run_plan(d4)
        c_ids4 = [c["id"] for c in res4["constraints"]]
        assert "left_chain" in c_ids4, f"test4 left_chain must survive, got {c_ids4}"
        assert "right_chain" not in c_ids4, f"test4 right_chain must drop under 60% rule, got {c_ids4}"


def test_pipeline_step3_suggestions_support_tiebreak_and_demo():
    """Step 3: Tie-break by support / no OCR first, collapse identical labels, and demo #1 rank."""
    import checks

    unit = "feet_inches"
    # Overalls 54'-0", 11'-0" -> 17'-0" injected
    labels = {
        "ov": checks.Label("ov", "54'-0\"", unit, mm=16459.2, support=1, ocr_verified=True),
        "dim_8": checks.Label("dim_8", "7'-6\"", unit, mm=2286.0, support=1, ocr_verified=True),
        "dim_9": checks.Label("dim_9", "14'-6\"", unit, mm=4419.6, support=1, ocr_verified=True),
        "dim_10": checks.Label("dim_10", "17'-0\"", unit, mm=5181.6, support=1, ocr_verified=False),
        "dim_11": checks.Label("dim_11", "3'-0\"", unit, mm=914.4, support=3, ocr_verified=False),
        "dim_12": checks.Label("dim_12", "3'-0\"", unit, mm=914.4, support=1, ocr_verified=False),
        "dim_13": checks.Label("dim_13", "7'-6\"", unit, mm=2286.0, support=1, ocr_verified=False),
        "dim_14": checks.Label("dim_14", "7'-6\"", unit, mm=2286.0, support=1, ocr_verified=False),
    }
    parts = ["dim_8", "dim_9", "dim_10", "dim_11", "dim_12", "dim_13", "dim_14"]
    c = checks.chain_constraint("right_chain", "ov", parts, 152.4)

    suggs = checks.suggest(labels, [c])
    assert len(suggs) > 0

    # 3a. Identical labels (dim_8, dim_13, dim_14) must collapse into one suggestion listing 'one of 3 identical'
    sugg_7_6 = next((s for s in suggs if s.old_text == "7'-6\""), None)
    assert sugg_7_6 is not None, "Expected collapsed suggestion for 7'-6\""
    assert "one of 3 identical" in sugg_7_6.details, f"Expected collapsing in details, got: {sugg_7_6.details}"

    # 3b. True fix (17'-0" -> 11'-0") ranks #1 because it has no OCR (ocr_verified=False)
    top1 = suggs[0]
    assert top1.label_id == "dim_10", f"Expected dim_10 to rank #1, got {top1.label_id}"
    assert top1.new_text == "11'-0\"", f"Expected new_text 11'-0\", got {top1.new_text}"
    assert top1.cost == 0.5
    assert not top1.ocr_verified


def test_step1_canonical_box_type():
    """Step 1: Define one Box type with named fields (x0, y0, x1, y1) in page pixels."""
    from geometry import Box

    b = Box(10.0, 20.0, 110.0, 220.0)
    assert b.x0 == 10.0
    assert b.y0 == 20.0
    assert b.x1 == 110.0
    assert b.y1 == 220.0
    assert b.xc == 60.0
    assert b.yc == 120.0
    assert b.width == 100.0
    assert b.height == 200.0
    assert b.to_list() == [10.0, 20.0, 110.0, 220.0]
    assert b.to_dict() == {"x0": 10.0, "y0": 20.0, "x1": 110.0, "y1": 220.0}

    # Normalized coords (x0 <= x1, y0 <= y1)
    b_norm = Box.from_coords(110.0, 220.0, 10.0, 20.0)
    assert b_norm == b

    # Parsing from dict / list
    b_from_dict = Box.from_any({"x0": 10.0, "y0": 20.0, "x1": 110.0, "y1": 220.0})
    assert b_from_dict == b
    b_from_list = Box.from_any([10.0, 20.0, 110.0, 220.0])
    assert b_from_list == b


def test_step2_monotonicity_invariant():
    """Step 2: Box positions are monotonic in chain_index; if fails, mark membership uncertain."""
    # 2a. Monotonic sequence (decreasing y along chain index 0, 1, 2)
    plan_monotonic = {
        "plan_unit": "feet_inches",
        "dimensions": [
            {"text_as_written": "50'-0\"", "kind": "overall", "chain_side": "left", "chain_index": 3, "mm": [15240.0], "ocr_box": [16.0, 500.0, 48.0, 600.0]},
            {"text_as_written": "15'-0\"", "kind": "segment", "chain_side": "left", "chain_index": 0, "mm": [4572.0], "ocr_box": [16.0, 800.0, 48.0, 900.0]}, # yc = 850
            {"text_as_written": "20'-0\"", "kind": "segment", "chain_side": "left", "chain_index": 1, "mm": [6096.0], "ocr_box": [16.0, 400.0, 48.0, 500.0]}, # yc = 450
            {"text_as_written": "15'-0\"", "kind": "segment", "chain_side": "left", "chain_index": 2, "mm": [4572.0], "ocr_box": [16.0, 100.0, 48.0, 200.0]}, # yc = 150
        ],
        "left_chain": [
            {"text_as_written": "15'-0\"", "kind": "segment", "chain_side": "left", "chain_index": 0, "mm": [4572.0], "ocr_box": [16.0, 800.0, 48.0, 900.0]},
            {"text_as_written": "20'-0\"", "kind": "segment", "chain_side": "left", "chain_index": 1, "mm": [6096.0], "ocr_box": [16.0, 400.0, 48.0, 500.0]},
            {"text_as_written": "15'-0\"", "kind": "segment", "chain_side": "left", "chain_index": 2, "mm": [4572.0], "ocr_box": [16.0, 100.0, 48.0, 200.0]},
            {"text_as_written": "50'-0\"", "kind": "overall", "chain_side": "left", "chain_index": 3, "mm": [15240.0], "ocr_box": [16.0, 500.0, 48.0, 600.0]},
        ],
    }
    res_m = run_plan(plan_monotonic)
    assert "left_chain" in [c["id"] for c in res_m["constraints"]], "Monotonic chain must survive"

    # 2b. Non-monotonic / zigzag sequence (y goes 850 -> 150 -> 450)
    plan_non_monotonic = copy.deepcopy(plan_monotonic)
    # swap boxes of segment 1 and 2
    plan_non_monotonic["dimensions"][2]["ocr_box"] = [16.0, 100.0, 48.0, 200.0]  # idx 1 has yc = 150
    plan_non_monotonic["dimensions"][3]["ocr_box"] = [16.0, 400.0, 48.0, 500.0]  # idx 2 has yc = 450
    plan_non_monotonic["left_chain"][1]["ocr_box"] = [16.0, 100.0, 48.0, 200.0]
    plan_non_monotonic["left_chain"][2]["ocr_box"] = [16.0, 400.0, 48.0, 500.0]

    res_nm = run_plan(plan_non_monotonic)
    assert "left_chain" not in [c["id"] for c in res_nm["constraints"]], "Non-monotonic chain must be rejected"
    assert any(l["status"] == "membership uncertain" for l in res_nm["labels"])


def test_step4_room_area_deduplication():
    """Step 4: Dedupe key = size AND applies_to AND position cell. Two same-name same-size rooms in different cells both count."""
    # 4a. Two identical name & size rooms in different cells both count
    plan = {
        "plan_unit": "feet_inches",
        "dimensions": [
            {"text_as_written": "14'-6\"x11'-0\"", "kind": "room_size", "applies_to": "BED ROOM", "position": "top-right", "mm": [4419.6, 3352.8]},
            {"text_as_written": "14'-6\"x11'-0\"", "kind": "room_size", "applies_to": "BED ROOM", "position": "middle-right", "mm": [4419.6, 3352.8]},
            {"text_as_written": "14'-6\"x11'-0\"", "kind": "room_size", "applies_to": "BED ROOM", "position": "top-right", "mm": [4419.6, 3352.8]}, # exact duplicate -> ignored
        ],
    }
    res = run_plan(plan)
    assert len(res["room_areas"]) == 2, f"Expected 2 rooms from different cells, got {len(res['room_areas'])}"
    # Each is 14.5 * 11 = 159.5 sq ft, total = 319.0 sq ft
    assert res["total_area_sq_ft"] == 319.0

    # 4b. Recompute test4 and expect 1511.3 sq ft
    test4_path = Path("results/test4_result.json")
    if test4_path.is_file():
        with open(test4_path, encoding="utf-8") as f:
            t4 = json.load(f)
        r4 = run_plan(t4)
        assert r4["total_area_sq_ft"] == 1511.3, f"Expected 1511.3, got {r4['total_area_sq_ft']}"
        assert r4["stated_envelope_ratio_pct"] == 100.8
        assert r4["chain_sum_envelope_ratio_pct"] == 93.3
        assert ">100%" in r4["envelope_flag"]


