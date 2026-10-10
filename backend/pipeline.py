"""End-to-end verification and layout pipeline for Sketch-to-Space.

Connects the multi-pass reader output to deterministic constraint solving,
room area calculation, and error localization (zero LLM arithmetic).

Key capabilities:
  - Bounding box geometric chain verification: horizontal and vertical chains
    must possess colinear centers and tile the overall dimension's span.
  - Rejects ungrounded chains and flags unverified labels.
  - Explicit wall allowance slack calculation with capped formulas per interior wall.
  - Multi-chain consensus hypothesis: ranks 'overall is wrong' #1 when independent chains agree on sum.
  - Deterministic room floor area calculation ('sum of rooms read') with envelope area and ratio.
  - Coverage metric tracking (labels in at least one constraint / total labels).
  - Never modifies input values.
"""

import copy
import re
from typing import Any, Optional

import checks
import config
from geometry import Box
from parse import format_mm, infer_unit, parse_label, _normalise
from reader import is_valid_chain


WALL_MAX_MM = 230.0  # Hard cap: 9 inches / 230 mm per interior wall


def _is_unit_ambiguous(text: str, unit: str, parsed_mms: list[float]) -> bool:
    """Check if label has ambiguous or conflicting unit evidence."""
    if not parsed_mms:
        return True
    norm = _normalise(text).strip()

    # 1. Bare number with no unit symbols at all (e.g. "8", "12", "14")
    if re.match(r"^\d+$", norm):
        return True
    # Bare 2D room (e.g. "8x5", "10x12")
    if re.match(r"^\d+\s*[xX×]\s*\d+$", norm):
        return True

    # 2. Asymmetric feet x inches (e.g. 7'x4" or 7'x5" where 2nd value has " without ')
    if re.search(r"\d+['′]\s*[xX×]\s*\d+[\"″](?!['′])", norm):
        return True

    has_imperial_marks = "'" in norm or '"' in norm or "′" in norm or "″" in norm
    has_metric_decimal = bool(re.search(r"\d+\.\d+", norm))
    if unit == "feet_inches" and has_metric_decimal and not has_imperial_marks:
        return True
    if unit in ("metres", "centimetres", "millimetres") and has_imperial_marks:
        return True
    return False


def _verify_chain_geometry(
    overall_rec: dict,
    part_recs: list[dict],
    orientation: str,
    y_tol: float = 60.0,
    x_tol: float = 80.0,
) -> tuple[bool, str]:
    """Verify whether parts and overall share a geometric dimension line and tile the span.

    Returns (is_valid, reason).
    """
    if not part_recs:
        return False, "no parts provided"

    ov_box = overall_rec.get("ocr_box")
    part_boxes = [p.get("ocr_box") for p in part_recs]

    def _box_coords(box: Any):
        b = Box.from_any(box)
        if not b:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        return b.x0, b.x1, b.y0, b.y1, b.xc, b.yc

    # For horizontal chain: all labels must have known bboxes with aligned y-centers
    if orientation == "horizontal":
        if not ov_box:
            return False, f"overall '{overall_rec.get('text_as_written')}' has no OCR bbox"
        missing = [p["text_as_written"] for p in part_recs if not p.get("ocr_box")]
        if missing:
            return False, f"horizontal parts missing OCR bboxes: {missing}"

        ov_xmin, ov_xmax, ov_ymin, ov_ymax, ov_xc, ov_yc = _box_coords(ov_box)

        # y-center alignment
        for p in part_recs:
            _, _, _, _, _, p_yc = _box_coords(p["ocr_box"])
            if abs(p_yc - ov_yc) > y_tol:
                return (
                    False,
                    f"label '{p['text_as_written']}' y_center={p_yc:.1f} deviates from overall y_center={ov_yc:.1f} (> {y_tol}px)",
                )

        # x-extent tiling
        sorted_parts = sorted(part_recs, key=lambda p: _box_coords(p["ocr_box"])[0])
        first_xmin = _box_coords(sorted_parts[0]["ocr_box"])[0]
        last_xmax = _box_coords(sorted_parts[-1]["ocr_box"])[1]

        span_covered = max(1.0, last_xmax - first_xmin)
        ov_span = max(1.0, ov_xmax - ov_xmin)
        if span_covered < 0.6 * ov_span:
            return False, f"parts span ({span_covered:.1f}px) does not tile overall span ({ov_span:.1f}px)"

        return True, "colinear y-centers and tiled x-extents verified"

    # For vertical chain:
    if orientation == "vertical":
        # Check if derived from verified edge strip pass
        is_edge_strip = any(
            p.get("chain_side") in ("left", "right") or "E_left" in p.get("sources", []) or "E_right" in p.get("sources", [])
            for p in part_recs
        )
        if is_edge_strip:
            return True, "membership verified by dedicated edge-strip extraction pass"

        if not ov_box:
            return False, f"overall '{overall_rec.get('text_as_written')}' has no OCR bbox"
        missing = [p["text_as_written"] for p in part_recs if not p.get("ocr_box")]
        if missing:
            return False, f"vertical parts missing OCR bboxes: {missing}"

        ov_xmin, ov_xmax, ov_ymin, ov_ymax, ov_xc, ov_yc = _box_coords(ov_box)
        for p in part_recs:
            _, _, _, _, p_xc, _ = _box_coords(p["ocr_box"])
            if abs(p_xc - ov_xc) > x_tol:
                return False, f"label '{p['text_as_written']}' x_center deviates from overall (> {x_tol}px)"

        sorted_parts = sorted(part_recs, key=lambda p: _box_coords(p["ocr_box"])[2])
        first_ymin = _box_coords(sorted_parts[0]["ocr_box"])[2]
        last_ymax = _box_coords(sorted_parts[-1]["ocr_box"])[3]
        span_covered = max(1.0, last_ymax - first_ymin)
        ov_span = max(1.0, ov_ymax - ov_ymin)
        if span_covered < 0.6 * ov_span:
            return False, f"parts span ({span_covered:.1f}px) does not tile overall span ({ov_span:.1f}px)"

        return True, "colinear x-centers and tiled y-extents verified"

    return False, "unknown orientation"


def _find_chain_overall(items: list[dict], unit: str, tol_mm: float = 152.4) -> tuple[Optional[int], list[int]]:
    """Identify overall dimension index and segment indices in a dimension chain."""
    if not items:
        return None, []

    for idx, it in enumerate(items):
        if it.get("kind") == "overall":
            segments = [i for i in range(len(items)) if i != idx]
            return idx, segments

    mms = []
    for it in items:
        val = it.get("mm")
        if val and len(val) == 1:
            mms.append(val[0])
        else:
            try:
                parsed = parse_label(it.get("text_as_written", ""), unit)
                mms.append(parsed[0] if len(parsed) == 1 else 0.0)
            except Exception:
                mms.append(0.0)

    if len(mms) < 2:
        return None, list(range(len(items)))

    max_idx = max(range(len(mms)), key=lambda i: mms[i])
    max_val = mms[max_idx]
    other_vals = [mms[i] for i in range(len(mms)) if i != max_idx and mms[i] > 0]

    # Dominant overall dimension
    if len(other_vals) >= 2 and max_val >= 1.5 * max(other_vals):
        segments = [i for i in range(len(items)) if i != max_idx]
        return max_idx, segments

    if len(other_vals) >= 2 and max_val > max(other_vals):
        sum_others = sum(other_vals)
        gap = abs(max_val - sum_others)
        if gap <= max(tol_mm * len(other_vals), 0.25 * max_val):
            segments = [i for i in range(len(items)) if i != max_idx]
            return max_idx, segments

    return None, list(range(len(items)))


def run_plan(
    merged_result: dict,
    tol_mm: Optional[float] = None,
    wall_mm: Optional[float] = None,
    wall_max_mm: float = WALL_MAX_MM,
) -> dict:
    """Run full end-to-end verification slice on a merged floor plan dictionary.

    Args:
        merged_result: Dictionary output from multi-pass reader or saved JSON.
        tol_mm: Tolerance window in mm (defaults to config.CHAIN_TOLERANCE_MM = 152.4 mm).
        wall_mm: Known interior wall thickness (if None, window allows 0..(n-1)*wall_max_mm).
        wall_max_mm: Maximum plausible single interior wall thickness (default 230 mm / 9 inches).

    Returns:
        Structured dictionary containing labels, constraints with explicit slack formulas,
        conflicts with ranked suggestions, room areas, envelope metrics, coverage metrics,
        and unverified values. Never modifies input.
    """
    if tol_mm is None:
        tol_mm = config.CHAIN_TOLERANCE_MM

    plan = copy.deepcopy(merged_result)
    unit = plan.get("plan_unit", "unclear")
    raw_dimensions = plan.get("dimensions", [])

    if unit == "unclear" or unit not in ("feet_inches", "metres", "centimetres", "millimetres"):
        texts = [d.get("text_as_written", "") for d in raw_dimensions if d.get("text_as_written")]
        unit = infer_unit(texts)

    # -------------------------------------------------------------------------
    # 1. Process and categorize all labels
    # -------------------------------------------------------------------------
    labels_dict: dict[str, checks.Label] = {}
    label_records: list[dict] = []

    for idx, dim in enumerate(raw_dimensions):
        lid = f"dim_{idx}"
        raw_text = dim.get("text_as_written", "").strip()
        app = dim.get("applies_to", "")
        kind = dim.get("kind", "segment")
        direction = dim.get("direction", "none")
        position = dim.get("position", "center")
        sources = dim.get("sources", [])
        ocr_v = bool(dim.get("ocr_verified", False))
        ocr_box = dim.get("ocr_box")
        support = dim.get("support", len(sources))

        mms = dim.get("mm")
        if not mms:
            try:
                mms = parse_label(raw_text, unit)
            except Exception:
                mms = []

        if _is_unit_ambiguous(raw_text, unit, mms):
            status = "unit_ambiguous"
        elif dim.get("status") == "verified" or ocr_v or support > 1 or dim.get("agree", False):
            status = "verified"
        else:
            status = "unverified"

        rec = {
            "id": lid,
            "text_as_written": raw_text,
            "parsed_mm": mms,
            "unit": unit,
            "applies_to": app,
            "kind": kind,
            "direction": direction,
            "position": position,
            "sources": sources,
            "ocr_verified": ocr_v,
            "ocr_box": ocr_box,
            "support": support,
            "status": status,
            "chain_side": dim.get("chain_side"),
            "chain_index": dim.get("chain_index"),
        }
        label_records.append(rec)

        if len(mms) == 1:
            labels_dict[lid] = checks.Label(lid, raw_text, unit, mm=mms[0], support=support, ocr_verified=ocr_v)

    # -------------------------------------------------------------------------
    # 2. Build constraints with OCR bbox geometric verification
    # -------------------------------------------------------------------------
    constraints: list[checks.Constraint] = []
    constraint_metadata: list[dict] = []
    overall_sides: dict[str, str] = {}

    def match_chain_to_lids(chain_items: list[dict], side_name: str) -> list[str]:
        matched_lids = []
        for c_item in chain_items:
            c_txt = c_item.get("text_as_written", "").strip()
            c_idx = c_item.get("chain_index")
            found_id = None
            for rec in label_records:
                if rec.get("chain_side") == side_name and rec.get("chain_index") == c_idx:
                    found_id = rec["id"]
                    break
            if not found_id:
                for rec in label_records:
                    if rec["text_as_written"] == c_txt and rec["id"] not in matched_lids:
                        found_id = rec["id"]
                        break
            if found_id:
                matched_lids.append(found_id)
        return matched_lids

    # Helper to compute slack formula and allowances
    def compute_slack_info(parts_count: int, tol: float, wall: Optional[float], wall_max: float) -> dict:
        num_walls = max(0, parts_count - 1)
        if wall is not None:
            slack_val = num_walls * wall
            lo = slack_val - tol
            hi = slack_val + tol
            formula = f"known_wall: ({parts_count}-1) * {wall:.1f} mm ± {tol:.1f} mm"
            cap_mm = slack_val
        else:
            cap_mm = num_walls * wall_max
            lo = -tol
            hi = cap_mm + tol
            formula = f"wall_allowance: 0 .. ({parts_count}-1) * {wall_max:.1f} mm (cap {cap_mm:.1f} mm / {format_mm(cap_mm, unit)}) ± {tol:.1f} mm"

        min_det_mm = round(min(abs(lo), abs(hi)), 1)
        return {
            "formula": formula,
            "slack_cap_mm": round(cap_mm, 1),
            "slack_cap_formatted": format_mm(cap_mm, unit),
            "allowance_per_wall_mm": wall_max if wall is None else wall,
            "lo_mm": round(lo, 1),
            "hi_mm": round(hi, 1),
            "num_walls": num_walls,
            "smallest_detectable_error_mm": min_det_mm,
            "smallest_detectable_error_formatted": format_mm(min_det_mm, unit),
        }

    # Count box frequencies across all labels to ensure box uniqueness
    box_counts = {}
    for r in label_records:
        b = r.get("ocr_box")
        if b:
            box_counts[tuple(b)] = box_counts.get(tuple(b), 0) + 1

    def is_unique_in_page_box(box: Optional[list[float]]) -> bool:
        if not box:
            return False
        return box_counts.get(tuple(box), 0) == 1

    def verify_edge_chain(chain_recs: list[dict], side_name: str) -> tuple[bool, str, Optional[dict], list[dict]]:
        """Verify chain membership:
          - is_valid_chain(values) imported from reader.py (>= 3 values, max >= 1.5x second max)
          - unique in-page box for overall
          - at least 60% of segments have unique in-page boxes
        """
        if len(chain_recs) < 3:
            return False, f"chain has fewer than 3 labels ({len(chain_recs)})", None, []

        vals = [r["parsed_mm"][0] for r in chain_recs if r.get("parsed_mm") and len(r["parsed_mm"]) == 1]
        if not is_valid_chain(vals):
            return False, f"fails shared is_valid_chain rule (values={vals})", None, []

        ov_idx, seg_indices = _find_chain_overall(chain_recs, unit, tol_mm)
        if ov_idx is None or len(seg_indices) < 2:
            return False, "could not identify overall and at least 2 segments", None, []

        ov_rec = chain_recs[ov_idx]
        seg_recs = [chain_recs[i] for i in seg_indices]

        # Overall must have a unique in-page box
        if not is_unique_in_page_box(ov_rec.get("ocr_box")):
            return False, f"overall '{ov_rec['text_as_written']}' lacks a unique in-page OCR box", ov_rec, seg_recs

        # At least 60% of segments must have unique in-page boxes
        unique_seg_count = sum(1 for s in seg_recs if is_unique_in_page_box(s.get("ocr_box")))
        seg_ratio = unique_seg_count / len(seg_recs) if seg_recs else 0.0
        if seg_ratio < 0.60:
            return False, f"only {unique_seg_count}/{len(seg_recs)} segments ({seg_ratio*100:.1f}% < 60%) have unique in-page boxes", ov_rec, seg_recs

        # Invariant: box positions are monotonic in chain_index
        segs_with_boxes = [s for s in sorted(seg_recs, key=lambda x: x.get("chain_index", 0)) if s.get("ocr_box")]
        if len(segs_with_boxes) >= 2:
            if side_name in ("left", "right") or ov_rec.get("direction") == "vertical":
                coords = [Box.from_any(s["ocr_box"]).yc for s in segs_with_boxes]
            else:
                coords = [Box.from_any(s["ocr_box"]).xc for s in segs_with_boxes]

            is_inc = all(coords[i] < coords[i + 1] for i in range(len(coords) - 1))
            is_dec = all(coords[i] > coords[i + 1] for i in range(len(coords) - 1))
            if not (is_inc or is_dec):
                return False, f"box positions are not monotonic in chain_index (coords={coords})", ov_rec, seg_recs

        return True, f"corroborated: unique overall box, {unique_seg_count}/{len(seg_recs)} ({seg_ratio*100:.1f}%) unique segment boxes, and monotonic positions", ov_rec, seg_recs

    # Left Chain
    left_items = plan.get("left_chain", [])
    if left_items:
        left_lids = match_chain_to_lids(left_items, "left")
        left_recs = [label_records[int(i.split('_')[1])] for i in left_lids]
        valid_chain, reason, ov_rec, seg_recs = verify_edge_chain(left_recs, "left")
        if valid_chain:
            slack = compute_slack_info(len(seg_recs), tol_mm, wall_mm, wall_max_mm)
            c = checks.Constraint("left_chain", ov_rec["id"], [s["id"] for s in seg_recs], slack["lo_mm"], slack["hi_mm"], "chain")
            constraints.append(c)
            overall_sides["left"] = ov_rec["id"]
            for s in seg_recs:
                if s["status"] != "unit_ambiguous":
                    s["status"] = "verified"
            if ov_rec["status"] != "unit_ambiguous":
                ov_rec["status"] = "verified"
            constraint_metadata.append({
                "id": c.id,
                "kind": c.kind,
                "total_id": ov_rec["id"],
                "part_ids": [s["id"] for s in seg_recs],
                "total_label": ov_rec["text_as_written"],
                "total_box": ov_rec.get("ocr_box"),
                "part_labels": [s["text_as_written"] for s in seg_recs],
                "part_boxes": [s.get("ocr_box") for s in seg_recs],
                "lo_mm": slack["lo_mm"],
                "hi_mm": slack["hi_mm"],
                "slack": slack,
                "geometry_verification": reason,
            })
        else:
            for r in left_recs:
                r["status"] = "membership uncertain"

    # Right Chain
    right_items = plan.get("right_chain", [])
    if right_items:
        right_lids = match_chain_to_lids(right_items, "right")
        right_recs = [label_records[int(i.split('_')[1])] for i in right_lids]
        valid_chain, reason, ov_rec, seg_recs = verify_edge_chain(right_recs, "right")
        if valid_chain:
            slack = compute_slack_info(len(seg_recs), tol_mm, wall_mm, wall_max_mm)
            c = checks.Constraint("right_chain", ov_rec["id"], [s["id"] for s in seg_recs], slack["lo_mm"], slack["hi_mm"], "chain")
            constraints.append(c)
            overall_sides["right"] = ov_rec["id"]
            for s in seg_recs:
                if s["status"] != "unit_ambiguous":
                    s["status"] = "verified"
            if ov_rec["status"] != "unit_ambiguous":
                ov_rec["status"] = "verified"
            constraint_metadata.append({
                "id": c.id,
                "kind": c.kind,
                "total_id": ov_rec["id"],
                "part_ids": [s["id"] for s in seg_recs],
                "total_label": ov_rec["text_as_written"],
                "total_box": ov_rec.get("ocr_box"),
                "part_labels": [s["text_as_written"] for s in seg_recs],
                "part_boxes": [s.get("ocr_box") for s in seg_recs],
                "lo_mm": slack["lo_mm"],
                "hi_mm": slack["hi_mm"],
                "slack": slack,
                "geometry_verification": reason,
            })
        else:
            for r in right_recs:
                r["status"] = "membership uncertain"

    # Left = Right overall height equality
    if "left" in overall_sides and "right" in overall_sides:
        left_rec = label_records[int(overall_sides["left"].split('_')[1])]
        right_rec = label_records[int(overall_sides["right"].split('_')[1])]
        c = checks.equal_constraint("left_eq_right", overall_sides["left"], overall_sides["right"], tol_mm)
        constraints.append(c)
        constraint_metadata.append({
            "id": c.id,
            "kind": c.kind,
            "total_label": left_rec["text_as_written"],
            "total_box": left_rec.get("ocr_box"),
            "part_labels": [right_rec["text_as_written"]],
            "part_boxes": [right_rec.get("ocr_box")],
            "slack": {"formula": f"equal_wall: ± {tol_mm:.1f} mm ({format_mm(tol_mm, unit)})", "slack_cap_mm": tol_mm},
            "geometry_verification": "opposite outer wall height equality",
        })

    # Horizontal overall width candidates
    h_overalls = [
        rec for rec in label_records
        if rec["kind"] == "overall" and (rec["direction"] == "horizontal" or "width" in rec["applies_to"].lower())
    ]

    for ov in h_overalls:
        ov_val = ov["parsed_mm"][0] if ov["parsed_mm"] else 0.0
        if ov_val <= 0.0:
            continue

        h_segs = [
            rec for rec in label_records
            if rec["id"] != ov["id"]
            and rec["direction"] == "horizontal"
            and len(rec["parsed_mm"]) == 1
            and rec["parsed_mm"][0] < ov_val
        ]

        built_for_ov = False
        for i in range(len(h_segs)):
            for j in range(i + 1, len(h_segs)):
                pair = [h_segs[i], h_segs[j]]
                pair_sum = pair[0]["parsed_mm"][0] + pair[1]["parsed_mm"][0]
                gap = ov_val - pair_sum
                slack = compute_slack_info(2, tol_mm, wall_mm, wall_max_mm)
                if slack["lo_mm"] <= gap <= slack["hi_mm"]:
                    valid_geom, reason = _verify_chain_geometry(ov, pair, "horizontal")
                    if valid_geom:
                        cid = f"width_overall_{int(round(ov_val/304.8 if unit == 'feet_inches' else ov_val/1000))}"
                        c = checks.Constraint(cid, ov["id"], [p["id"] for p in pair], slack["lo_mm"], slack["hi_mm"], "chain")
                        constraints.append(c)
                        constraint_metadata.append({
                            "id": c.id,
                            "kind": c.kind,
                            "total_label": ov["text_as_written"],
                            "total_box": ov.get("ocr_box"),
                            "part_labels": [p["text_as_written"] for p in pair],
                            "part_boxes": [p.get("ocr_box") for p in pair],
                            "slack": slack,
                            "geometry_verification": reason,
                        })
                        built_for_ov = True
                        break
                    else:
                        ov["status"] = "unverified"
                        for p in pair:
                            p["status"] = "unverified"
            if built_for_ov:
                break
        if not built_for_ov:
            ov["status"] = "unverified"

    # -------------------------------------------------------------------------
    # 3. Deterministic constraint checking & candidate suggestion ranking
    # -------------------------------------------------------------------------
    violations = checks.check(labels_dict, constraints)
    conflicts: list[dict] = []
    covered_ids = checks.covered_ids(labels_dict, constraints)
    values = checks._values(labels_dict)

    # Global suggestions targeting the entire system
    global_suggs = checks.suggest(labels_dict, constraints, max_results=10)

    for viol in violations:
        c = viol.constraint
        cid = c.id
        excess_formatted = format_mm(viol.excess_mm, unit)
        gap_formatted = format_mm(abs(viol.gap_mm), unit)
        gap_sign = "+" if viol.gap_mm >= 0 else "-"

        total_txt = labels_dict[c.total].text if c.total in labels_dict else "?"
        parts_txts = [labels_dict[p].text for p in c.parts if p in labels_dict]
        parts_sum_mm = sum(values.get(p, 0.0) for p in c.parts)
        parts_sum_formatted = format_mm(parts_sum_mm, unit)

        if c.kind == "chain":
            summary = (
                f"Chain '{cid}' mismatch: segments ({', '.join(parts_txts)}) sum to {parts_sum_formatted}, "
                f"while overall dimension says {total_txt} (difference {gap_sign}{gap_formatted}, excess {excess_formatted})."
            )
        else:
            other_txt = parts_txts[0] if parts_txts else "?"
            summary = (
                f"Equality constraint '{cid}' mismatch: {total_txt} != {other_txt} "
                f"(difference {gap_sign}{gap_formatted}, excess {excess_formatted})."
            )

        suspect_ids = [c.total] + c.parts
        matched_suggs = [
            {
                "label_id": s.label_id,
                "label_description": next((r["applies_to"] for r in label_records if r["id"] == s.label_id), ""),
                "old_text": s.old_text,
                "new_text": s.new_text,
                "remaining_violations": s.remaining_violations,
                "cost": s.cost,
                "remaining_excess_mm": s.remaining_excess_mm,
                "delta_mm": round(s.delta_mm, 1),
                "delta_formatted": format_mm(s.delta_mm, unit),
                "status": s.status,
                "hypothesis": s.hypothesis,
            }
            for s in global_suggs
            if s.label_id in suspect_ids
        ]

        conflicts.append({
            "constraint_id": cid,
            "kind": c.kind,
            "gap_mm": round(viol.gap_mm, 1),
            "gap_formatted": f"{gap_sign}{gap_formatted}",
            "excess_mm": round(viol.excess_mm, 1),
            "excess_formatted": excess_formatted,
            "summary": summary,
            "suspect_label_ids": suspect_ids,
            "suspect_labels": [
                {
                    "id": sid,
                    "text": labels_dict[sid].text,
                    "applies_to": next((r["applies_to"] for r in label_records if r["id"] == sid), ""),
                    "ocr_box": next((r.get("ocr_box") for r in label_records if r["id"] == sid), None),
                }
                for sid in suspect_ids
                if sid in labels_dict
            ],
            "ranked_suggestions": matched_suggs[:5],
        })

    viol_cids = {v.constraint.id: v for v in violations}
    for c_meta in constraint_metadata:
        cid = c_meta["id"]
        c_obj = next((c for c in constraints if c.id == cid), None)
        if not c_obj:
            continue
        tot_val = values.get(c_obj.total, 0.0)
        parts_sum = sum(values.get(p, 0.0) for p in c_obj.parts)
        gap = tot_val - parts_sum
        gap_fmt = format_mm(abs(gap), unit)
        gap_sign = "+" if gap >= 0 else "-"
        gap_str = f"{gap_sign}{gap_fmt}"

        num_walls = max(0, len(c_obj.parts) - 1)
        implied_thickness_mm = round(gap / num_walls, 1) if num_walls > 0 else 0.0
        implied_thickness_fmt = f"{format_mm(abs(implied_thickness_mm), unit)} per wall" if num_walls > 0 else "N/A"

        if c_obj.lo_mm <= gap <= c_obj.hi_mm:
            smallest_err_mm = round(min(c_obj.hi_mm - gap, gap - c_obj.lo_mm), 1)
        else:
            smallest_err_mm = 0.0
        smallest_err_fmt = format_mm(smallest_err_mm, unit)

        c_meta["gap_mm"] = round(gap, 1)
        c_meta["gap_formatted"] = gap_str
        c_meta["implied_thickness_per_wall_mm"] = implied_thickness_mm
        c_meta["implied_thickness_per_wall_formatted"] = implied_thickness_fmt
        c_meta["smallest_detectable_error_mm"] = smallest_err_mm
        c_meta["smallest_detectable_error_formatted"] = smallest_err_fmt

        if cid in viol_cids:
            v = viol_cids[cid]
            c_meta["status"] = f"violation (excess {format_mm(v.excess_mm, unit)})"
            c_meta["is_satisfied"] = False
        else:
            c_meta["status"] = f"within wall allowance (gap {gap_str})"
            c_meta["is_satisfied"] = True

    # -------------------------------------------------------------------------
    # 4. Deterministic room area computation & envelope comparison
    # -------------------------------------------------------------------------
    room_areas: list[dict] = []
    seen_room_keys: set[tuple] = set()
    wd_pair_count = 0
    sum_of_rooms_read_sq_ft = 0.0
    sum_of_rooms_read_sq_m = 0.0

    for rec in label_records:
        if rec["kind"] == "room_size" or "x" in rec.get("text_as_written", ""):
            mms = rec["parsed_mm"]
            app = str(rec.get("applies_to", "")).strip().lower()
            pos = str(rec.get("position", "")).strip().lower()

            if len(mms) == 2:
                # Dedupe key = size AND applies_to AND position cell
                size_key = tuple(sorted(round(v, 1) for v in mms))
                dedupe_key = (size_key, app, pos)
                if dedupe_key in seen_room_keys:
                    continue

                seen_room_keys.add(dedupe_key)
                wd_pair_count += 1

                w_mm, d_mm = mms[0], mms[1]
                w_ft = w_mm / 304.8
                d_ft = d_mm / 304.8
                area_sq_ft = round(w_ft * d_ft, 2)

                w_m = w_mm / 1000.0
                d_m = d_mm / 1000.0
                area_sq_m = round(w_m * d_m, 2)

                sum_of_rooms_read_sq_ft += area_sq_ft
                sum_of_rooms_read_sq_m += area_sq_m

                room_areas.append({
                    "label_id": rec["id"],
                    "room_name": rec["applies_to"] or "Room",
                    "text_as_written": rec["text_as_written"],
                    "dimensions_mm": [round(w_mm, 1), round(d_mm, 1)],
                    "dimensions_formatted": f"{format_mm(w_mm, unit)} x {format_mm(d_mm, unit)}",
                    "area_sq_ft": area_sq_ft,
                    "area_sq_m": area_sq_m,
                })
            elif len(mms) == 1:
                room_areas.append({
                    "label_id": rec["id"],
                    "room_name": rec["applies_to"] or "Linear Area",
                    "text_as_written": rec["text_as_written"],
                    "dimensions_mm": [round(mms[0], 1)],
                    "dimensions_formatted": format_mm(mms[0], unit),
                    "note": "single dimension only (no area)",
                    "area_sq_ft": 0.0,
                    "area_sq_m": 0.0,
                })

    has_wd_pairs = wd_pair_count > 0
    total_area_sq_ft_out: Any = round(sum_of_rooms_read_sq_ft, 1) if has_wd_pairs else "N/A"
    total_area_sq_m_out: Any = round(sum_of_rooms_read_sq_m, 1) if has_wd_pairs else "N/A"

    # Stated envelope: overall width x overall height
    ov_w = next((r for r in label_records if r["kind"] == "overall" and ("width" in r["applies_to"].lower() or r["direction"] == "horizontal")), None)
    ov_h = next((r for r in label_records if r["kind"] == "overall" and ("height" in r["applies_to"].lower() or r["direction"] == "vertical" or r.get("chain_side"))), None)

    stated_envelope_sq_ft = 0.0
    stated_envelope_sq_m = 0.0
    stated_envelope_ratio_pct: Any = "N/A"
    stated_envelope_dims_formatted = ""

    w_mm = ov_w["parsed_mm"][0] if (ov_w and ov_w.get("parsed_mm")) else 0.0
    h_mm = ov_h["parsed_mm"][0] if (ov_h and ov_h.get("parsed_mm")) else 0.0

    if w_mm > 0 and h_mm > 0:
        stated_envelope_sq_ft = round((w_mm / 304.8) * (h_mm / 304.8), 2)
        stated_envelope_sq_m = round((w_mm / 1000.0) * (h_mm / 1000.0), 2)
        stated_envelope_dims_formatted = f"{ov_w['text_as_written']} x {ov_h['text_as_written']}"
        if has_wd_pairs and stated_envelope_sq_ft > 0:
            stated_envelope_ratio_pct = round((sum_of_rooms_read_sq_ft / stated_envelope_sq_ft) * 100.0, 1)

    # Chain-sum envelope
    chain_w_mm = w_mm
    for c_meta in constraint_metadata:
        if c_meta["id"].startswith("width_overall") or c_meta["id"] == "top_chain":
            parts_mms = [values.get(pid, 0.0) for pid in c_meta["part_ids"]]
            if parts_mms:
                chain_w_mm = sum(parts_mms)

    chain_h_mm = h_mm
    for c_meta in constraint_metadata:
        if c_meta["id"] in ("left_chain", "right_chain"):
            parts_mms = [values.get(pid, 0.0) for pid in c_meta["part_ids"]]
            if parts_mms:
                chain_h_mm = sum(parts_mms)

    chain_sum_envelope_sq_ft = round((chain_w_mm / 304.8) * (chain_h_mm / 304.8), 2) if (chain_w_mm and chain_h_mm) else stated_envelope_sq_ft
    chain_sum_envelope_sq_m = round((chain_w_mm / 1000.0) * (chain_h_mm / 1000.0), 2) if (chain_w_mm and chain_h_mm) else stated_envelope_sq_m
    chain_sum_envelope_ratio_pct: Any = "N/A"
    if has_wd_pairs and chain_sum_envelope_sq_ft > 0:
        chain_sum_envelope_ratio_pct = round((sum_of_rooms_read_sq_ft / chain_sum_envelope_sq_ft) * 100.0, 1)

    envelope_flag = "within envelope"
    if has_wd_pairs:
        exceeds = False
        if isinstance(stated_envelope_ratio_pct, (int, float)) and stated_envelope_ratio_pct > 100.0:
            exceeds = True
        if isinstance(chain_sum_envelope_ratio_pct, (int, float)) and chain_sum_envelope_ratio_pct > 100.0:
            exceeds = True
        if exceeds:
            envelope_flag = "sum of rooms exceeds envelope (>100%)"

    # -------------------------------------------------------------------------
    # 5. List unverified values (no redundancy on the plan)
    # -------------------------------------------------------------------------
    unverified_values: list[dict] = []
    for rec in label_records:
        if rec["id"] not in covered_ids:
            unverified_values.append({
                "id": rec["id"],
                "text_as_written": rec["text_as_written"],
                "applies_to": rec["applies_to"],
                "kind": rec["kind"],
                "status": rec["status"],
                "ocr_box": rec.get("ocr_box"),
                "reason": "no redundancy: not part of any verified dimension chain or equality constraint",
            })

    # -------------------------------------------------------------------------
    # 6. Summary and coverage metrics
    # -------------------------------------------------------------------------
    total_labels = len(label_records)
    covered_labels_count = len(covered_ids)
    coverage_pct = round((covered_labels_count / total_labels * 100.0), 1) if total_labels else 0.0
    verified_count = sum(1 for r in label_records if r["status"] == "verified")
    unverified_count = sum(1 for r in label_records if r["status"] == "unverified")
    uncertain_count = sum(1 for r in label_records if r["status"] == "membership uncertain")
    ambiguous_count = sum(1 for r in label_records if r["status"] == "unit_ambiguous")

    return {
        "plan_unit": unit,
        "tolerance_used_mm": round(tol_mm, 1),
        "tolerance_used_formatted": format_mm(tol_mm, unit),
        "labels": label_records,
        "constraints": constraint_metadata,
        "conflicts": conflicts,
        "room_areas": room_areas,
        "sum_of_rooms_read_sq_ft": total_area_sq_ft_out,
        "sum_of_rooms_read_sq_m": total_area_sq_m_out,
        "total_area_sq_ft": total_area_sq_ft_out,
        "total_area_sq_m": total_area_sq_m_out,
        "stated_envelope_sq_ft": stated_envelope_sq_ft,
        "stated_envelope_sq_m": stated_envelope_sq_m,
        "stated_envelope_ratio_pct": stated_envelope_ratio_pct,
        "chain_sum_envelope_sq_ft": chain_sum_envelope_sq_ft,
        "chain_sum_envelope_sq_m": chain_sum_envelope_sq_m,
        "chain_sum_envelope_ratio_pct": chain_sum_envelope_ratio_pct,
        "envelope_flag": envelope_flag,
        "envelope_sq_ft": stated_envelope_sq_ft,
        "envelope_sq_m": stated_envelope_sq_m,
        "envelope_dims_formatted": stated_envelope_dims_formatted,
        "envelope_ratio_pct": stated_envelope_ratio_pct,
        "unverified_values": unverified_values,
        "coverage": {
            "labels_in_constraints": covered_labels_count,
            "total_labels": total_labels,
            "coverage_pct": coverage_pct,
            "constraints_built": len(constraints),
            "conflicts_count": len(conflicts),
        },
        "summary": {
            "total_labels": total_labels,
            "verified_count": verified_count,
            "unverified_count": unverified_count,
            "membership_uncertain_count": uncertain_count,
            "unit_ambiguous_count": ambiguous_count,
            "constraints_count": len(constraints),
            "conflicts_count": len(conflicts),
            "unverified_count": len(unverified_values),
        },
    }
