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
    # OCR box format is [ymin, xmin, ymax, xmax]
    ov = {"text_as_written": "30'-0\"", "ocr_box": [100.0, 50.0, 140.0, 550.0]}  # y-center: 120, x-span: 50..550
    parts_valid = [
        {"text_as_written": "15'-0\"", "ocr_box": [105.0, 50.0, 135.0, 290.0]},   # y-center: 120, x-span: 50..290
        {"text_as_written": "15'-0\"", "ocr_box": [102.0, 300.0, 138.0, 550.0]},  # y-center: 120, x-span: 300..550
    ]
    ok, _ = _verify_chain_geometry(ov, parts_valid, "horizontal", y_tol=30.0)
    assert ok, "Colinear tiling parts should pass geometry verification"

    parts_deviant_y = [
        {"text_as_written": "15'-0\"", "ocr_box": [250.0, 50.0, 280.0, 290.0]},   # y-center: 265 deviates > 30px
        {"text_as_written": "15'-0\"", "ocr_box": [102.0, 300.0, 138.0, 550.0]},
    ]
    ok_dev, _ = _verify_chain_geometry(ov, parts_deviant_y, "horizontal", y_tol=30.0)
    assert not ok_dev, "Parts with deviant y-centers should fail geometry verification"


def test_pipeline_step3_consensus_and_suggestions():
    """Step 3: Multi-chain consensus hypothesis ranks 'overall is wrong' first on test4."""
    test4_path = Path("results/test4_result.json")
    if not test4_path.is_file():
        pytest.skip("test4_result.json not present in results/")

    with open(test4_path, encoding="utf-8") as f:
        data = json.load(f)

    res = run_plan(data)
    assert len(res["conflicts"]) >= 2

    # Both chains must propose 50'-0" -> 54'-0" (or 54'0") as #1 suggestion with hypothesis 'overall_is_wrong'
    chain_conflicts = [c for c in res["conflicts"] if "chain" in c["constraint_id"]]
    assert len(chain_conflicts) == 2, f"Expected 2 chain conflicts on test4, got {len(chain_conflicts)}"

    for cf in chain_conflicts:
        top = cf["ranked_suggestions"][0]
        assert top["hypothesis"] == "overall_is_wrong", f"Expected overall_is_wrong, got {top['hypothesis']}"
        assert "54" in top["new_text"], f"Expected 54 in new_text, got {top['new_text']}"
        assert top["cost"] == 0.25, f"Expected cost 0.25, got {top['cost']}"
        assert top["status"] == "complete", f"Expected status complete, got {top['status']}"

    # Verify that partial edits have status 'partial' and remaining violations
    all_suggs = [s for cf in res["conflicts"] for s in cf["ranked_suggestions"]]
    partial_suggs = [s for s in all_suggs if s["status"] == "partial"]
    assert len(partial_suggs) > 0, "Expected at least one partial suggestion"
    for ps in partial_suggs:
        assert ps["remaining_violations"] > 0
        assert ps["remaining_excess_mm"] > 0

    # Verify that duplicate tied edits are dropped (no identical edits repeated in ranked suggestions)
    for cf in res["conflicts"]:
        seen_sigs = set()
        for s in cf["ranked_suggestions"]:
            sig = (s["old_text"], s["new_text"], s["cost"], s["remaining_violations"])
            assert sig not in seen_sigs, f"Duplicate tied edit found: {sig} in {cf['constraint_id']}"
            seen_sigs.add(sig)


def test_pipeline_step4_demo():
    """Step 4: Demo check: 54' overalls -> 0 conflicts; inject 11'->17' -> top-3 contains 11'."""
    test4_path = Path("results/test4_result.json")
    if not test4_path.is_file():
        pytest.skip("test4_result.json not present in results/")

    with open(test4_path, encoding="utf-8") as f:
        data = json.load(f)

    # 4a. Set both overalls to 54'-0" and confirm zero conflicts
    data_corrected = copy.deepcopy(data)
    for item in data_corrected.get("dimensions", []):
        if item.get("text_as_written") == "50'-0\"":
            item["text_as_written"] = "54'-0\""
            item["mm"] = [16459.2]
    for item in data_corrected.get("left_chain", []):
        if item.get("text_as_written") == "50'-0\"":
            item["text_as_written"] = "54'-0\""
            item["mm"] = [16459.2]
    for item in data_corrected.get("right_chain", []):
        if item.get("text_as_written") == "50'-0\"":
            item["text_as_written"] = "54'-0\""
            item["mm"] = [16459.2]

    res_corrected = run_plan(data_corrected)
    assert len(res_corrected["conflicts"]) == 0, f"Expected 0 conflicts after setting overalls to 54'-0\", got {len(res_corrected['conflicts'])}"

    # 4b. Inject 11'-0" -> 17'-0"
    data_injected = copy.deepcopy(data_corrected)
    for item in data_injected.get("dimensions", []):
        if item.get("text_as_written") == "11'-0\"" and "right" in str(item.get("applies_to", "")).lower():
            item["text_as_written"] = "17'-0\""
            item["mm"] = [5181.6]
            break
    for item in data_injected.get("right_chain", []):
        if item.get("text_as_written") == "11'-0\"":
            item["text_as_written"] = "17'-0\""
            item["mm"] = [5181.6]
            break

    res_injected = run_plan(data_injected)
    assert len(res_injected["conflicts"]) == 1, f"Expected exactly 1 conflict, got {len(res_injected['conflicts'])}"

    right_conflict = res_injected["conflicts"][0]
    assert "right" in right_conflict["constraint_id"]

    top3 = right_conflict["ranked_suggestions"][:3]
    top3_new_texts = [s["new_text"] for s in top3]
    assert any("11'-0\"" in nt for nt in top3_new_texts), f"Expected 11'-0\" in top-3 suggestions: {top3_new_texts}"
