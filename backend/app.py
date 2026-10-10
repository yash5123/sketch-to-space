"""FastAPI demo backend for Sketch-to-Space.

Serves the read, verify, correct, audit pipeline from saved results
and synthetic files. Localhost only, CORS off, zero model calls.
"""

import copy
import json
import os
import re
from pathlib import Path
import sys
from typing import Any, Optional

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import io
from PIL import Image, ImageDraw, ImageFont

import checks
import config
from geometry import Box
from parse import format_mm, infer_unit, parse_label
from pipeline import run_plan

ROOT_DIR = BACKEND_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

SKETCHES_DIR = ROOT_DIR / "sketches"
SYNTHETIC_DIR = ROOT_DIR / "synthetic"
RESULTS_DIR = ROOT_DIR / "results"
FRONTEND_DIR = ROOT_DIR / "frontend"


app = FastAPI(title="Sketch-to-Space", docs_url=None, redoc_url=None)

# -----------------------------------------------------------------------------
# Static files and mount points
# -----------------------------------------------------------------------------
# Mount static sketch and synthetic images
app.mount("/static/sketches", StaticFiles(directory=str(SKETCHES_DIR)), name="sketches")
app.mount("/static/synthetic", StaticFiles(directory=str(SYNTHETIC_DIR)), name="synthetic")

# -----------------------------------------------------------------------------
# In-memory working copies (never mutate stored disk files)
# -----------------------------------------------------------------------------
ORIGINAL_PLANS: dict[str, dict] = {}
WORKING_COPIES: dict[str, dict] = {}

SAMPLE_MANIFEST = [
    {
        "id": "synth_008",
        "title": "Synth",
        "kind": "synthetic",
        "description": "Multi-room CAD layout with an injected dimension mismatch.",
        "status_chip": "conflict found",
        "thumbnail_url": "/static/sketches/synth/synth_008.png",
        "file_type": "synth_meta",
        "file_path": str(SYNTHETIC_DIR / "synth_008_meta.json"),
        "image_file": str(SKETCHES_DIR / "synth" / "synth_008.png"),
        "image_url": "/static/sketches/synth/synth_008.png",
    },
    {
        "id": "test6",
        "title": "Real Photo",
        "kind": "real",
        "description": "Hand-drawn studio sketch with isolated room dimensions.",
        "status_chip": "no redundancy",
        "thumbnail_url": "/static/sketches/test6.jpeg",
        "file_type": "result_json",
        "file_path": str(RESULTS_DIR / "test6_result.json"),
        "image_file": str(SKETCHES_DIR / "test6.jpeg"),
        "image_url": "/static/sketches/test6.jpeg",
    },
]

# Additional plans kept in memory for testing/verification
ALL_PLANS_MANIFEST = SAMPLE_MANIFEST + [
    {
        "id": "test4",
        "title": "Test 4 - 30'x50' Residence",
        "kind": "real",
        "description": "Real hand-drawn 2-bedroom plan with surviving left chain mismatch (4'0\" deficit).",
        "status_chip": "conflict found",
        "thumbnail_url": "/static/sketches/test4.jpg",
        "file_type": "result_json",
        "file_path": str(RESULTS_DIR / "test4_result.json"),
        "image_file": str(SKETCHES_DIR / "test4.jpg"),
        "image_url": "/static/sketches/test4.jpg",
    },
    {
        "id": "synth_002",
        "title": "Synth 002 - Metric 12-Room Plan",
        "kind": "synthetic",
        "description": "Clean synthetic layout with perfectly closed chains and zero dimension conflicts.",
        "status_chip": "clean",
        "thumbnail_url": "/static/sketches/synth/synth_002.png",
        "file_type": "synth_meta",
        "file_path": str(SYNTHETIC_DIR / "synth_002_meta.json"),
        "image_file": str(SKETCHES_DIR / "synth" / "synth_002.png"),
        "image_url": "/static/sketches/synth/synth_002.png",
    },
    {
        "id": "test1",
        "title": "Test 1 - 5-Room Residence",
        "kind": "real",
        "description": "Real floor plan with 5 clear room sizes and verified dimensions.",
        "status_chip": "no redundancy",
        "thumbnail_url": "/static/sketches/test1.png",
        "file_type": "result_json",
        "file_path": str(RESULTS_DIR / "test1_result.json"),
        "image_file": str(SKETCHES_DIR / "test1.png"),
        "image_url": "/static/sketches/test1.png",
    },
]


def load_all_manifest_plans():
    for item in ALL_PLANS_MANIFEST:
        pid = item["id"]
        fp = Path(item["file_path"])
        if fp.is_file():
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            ORIGINAL_PLANS[pid] = data
            WORKING_COPIES[pid] = copy.deepcopy(data)


load_all_manifest_plans()


def get_image_dimensions(image_path: str) -> tuple[int, int]:
    try:
        with Image.open(image_path) as im:
            return im.size
    except Exception:
        return 1000, 1000


def get_human_name(lid: str, plan_data: dict) -> str:
    """Map internal IDs (room_1_w, room_1_w_eq_col) to readable names from applies_to or room layout."""
    base_id = lid
    if base_id.endswith("_eq_col"):
        base_id = base_id[:-7]
    elif base_id.endswith("_eq_left"):
        base_id = base_id[:-8]
    elif base_id.endswith("_eq_right"):
        base_id = base_id[:-9]

    # Look up in plan_data["labels"]
    labels = plan_data.get("labels", [])
    if isinstance(labels, list):
        for l in labels:
            if isinstance(l, dict) and l.get("id") == base_id and l.get("applies_to"):
                raw = l["applies_to"]
                parts = raw.split()
                if len(parts) >= 2 and parts[-1].lower() in ("width", "depth", "height"):
                    room_name = " ".join(parts[:-1]).capitalize()
                    dim = parts[-1].lower()
                    return f"{room_name} {dim}"
                return raw.capitalize()
    elif isinstance(labels, dict):
        l = labels.get(base_id)
        if isinstance(l, dict) and l.get("applies_to"):
            raw = l["applies_to"]
            parts = raw.split()
            if len(parts) >= 2 and parts[-1].lower() in ("width", "depth", "height"):
                room_name = " ".join(parts[:-1]).capitalize()
                dim = parts[-1].lower()
                return f"{room_name} {dim}"
            return raw.capitalize()

    # Pattern match room_N_w or room_N_d
    m = re.match(r"room_(\d+)_(w|d)", base_id)
    if m:
        r_idx = int(m.group(1))
        dim = "width" if m.group(2) == "w" else "depth"
        rooms = plan_data.get("plan", {}).get("rooms", [])
        if r_idx < len(rooms):
            return f"{rooms[r_idx]['name'].capitalize()} {dim}"
        return f"Room {r_idx + 1} {dim}"

    if base_id == "top_overall": return "Overall width"
    if base_id == "left_overall": return "Overall height (left)"
    if base_id == "right_overall": return "Overall height (right)"
    if base_id == "left_eq_right": return "Left vs right overall height"
    m_seg = re.match(r"(top|left|right)_seg_(\d+)", base_id)
    if m_seg:
        return f"{m_seg.group(1).capitalize()} segment {int(m_seg.group(2)) + 1}"
    return base_id.replace("_", " ").capitalize()


# -----------------------------------------------------------------------------
# Plan Execution & Standardization
# -----------------------------------------------------------------------------
def process_real_plan(plan_dict: dict, manifest_item: dict) -> dict:
    """Run verification pipeline on a real plan and enforce box sanity."""
    res = run_plan(plan_dict)
    img_w, img_h = get_image_dimensions(manifest_item["image_file"])

    # Count box occurrences across all labels for uniqueness
    box_occurrences = {}
    for r in res["labels"]:
        b = r.get("ocr_box")
        if b:
            key = tuple(b)
            box_occurrences[key] = box_occurrences.get(key, 0) + 1

    # Conflict suspect IDs
    conflict_suspect_ids = set()
    for cf in res.get("conflicts", []):
        for sid in cf.get("suspect_label_ids", []):
            conflict_suspect_ids.add(sid)

    labels_out = []
    for r in res["labels"]:
        lid = r["id"]
        raw_box = r.get("ocr_box")
        if manifest_item.get("id") == "test6":
            raw_box = None
        status = r.get("status", "unverified")
        if lid in conflict_suspect_ids and status not in ("unit_ambiguous", "membership uncertain"):
            status = "conflict_suspect"

        box_ok = False
        box_coords = None
        if raw_box and len(raw_box) == 4:
            x0, y0, x1, y1 = raw_box[0], raw_box[1], raw_box[2], raw_box[3]
            inside = (0 <= x0 <= img_w) and (0 <= x1 <= img_w) and (0 <= y0 <= img_h) and (0 <= y1 <= img_h)
            is_unique = (box_occurrences.get(tuple(raw_box), 0) == 1)
            not_uncertain = (r.get("status") != "membership uncertain")
            if inside and is_unique and not_uncertain and (x1 > x0) and (y1 > y0):
                box_ok = True
                box_coords = [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]

        labels_out.append({
            "id": lid,
            "name": get_human_name(lid, plan_dict),
            "text": r.get("text_as_written", ""),
            "mm": r.get("parsed_mm", []),
            "status": status,
            "support": r.get("support", 1),
            "sources": r.get("sources", []),
            "box": box_coords,
            "box_ok": box_ok,
            "applies_to": r.get("applies_to", ""),
            "direction": r.get("direction", "none"),
            "kind": r.get("kind", "segment"),
        })

    # Constraints standardization
    constraints_out = []
    for c_meta in res.get("constraints", []):
        constraints_out.append({
            "id": c_meta.get("id", ""),
            "sentence": f"{c_meta.get('total_label')} vs {', '.join(c_meta.get('part_labels', []))}",
            "gap": c_meta.get("gap_mm", 0.0),
            "gap_formatted": c_meta.get("gap_formatted", ""),
            "slack_formula": c_meta.get("slack", {}).get("formula", ""),
            "slack_cap_formatted": c_meta.get("slack", {}).get("slack_cap_formatted", ""),
            "implied_thickness_per_wall": c_meta.get("implied_thickness_per_wall_formatted", "N/A"),
            "smallest_detectable_error": c_meta.get("smallest_detectable_error_formatted", "N/A"),
            "status": c_meta.get("status", ""),
            "is_satisfied": c_meta.get("is_satisfied", False),
        })

    # Conflicts standardization
    conflicts_out = []
    for cf in res.get("conflicts", []):
        suggs_out = []
        for s in cf.get("ranked_suggestions", []):
            s_why = s.get("why") or s.get("details", "") or s.get("hypothesis", "")
            suggs_out.append({
                "label_id": s.get("label_id"),
                "label_name": get_human_name(s.get("label_id", ""), plan_dict),
                "old_text": s.get("old_text"),
                "new_text": s.get("new_text"),
                "cost": s.get("cost", 1.0),
                "support": s.get("support", 1),
                "ocr_verified": s.get("ocr_verified", False),
                "tag": s.get("status", "complete"),
                "why": s_why,
                "note": s.get("details", "") or s.get("hypothesis", ""),
                "delta_formatted": s.get("delta_formatted", ""),
            })

        conflicts_out.append({
            "constraint_id": cf.get("constraint_id"),
            "sentence": cf.get("summary", ""),
            "suspect_label_ids": cf.get("suspect_label_ids", []),
            "gap_formatted": cf.get("gap_formatted", ""),
            "excess_formatted": cf.get("excess_formatted", ""),
            "ranked_suggestions": suggs_out,
        })

    # Unit ambiguous list with dual reading explanations
    unit_ambig_out = []
    for l in labels_out:
        if l["status"] == "unit_ambiguous":
            txt = l["text"]
            # Detect 7'x4" or similar asymmetric feet-inch notation
            m = re.search(r"(\d+)['′]\s*[xX×]\s*(\d+)[\"″]", txt)
            if m:
                f_val, i_val = m.group(1), m.group(2)
                r1_text = f"{f_val}'-0\"x{i_val}'-0\""
                r2_text = f"{f_val}'-0\"x0'-{i_val}\""
                desc = f"{txt} may mean {f_val} feet by {i_val} feet, or {f_val} feet by {i_val} inches. Please confirm:"
            elif re.match(r"^\d+$", txt.strip()):
                val = txt.strip()
                r1_text = f"{val}'-0\""
                r2_text = f"{val}\""
                desc = f"Bare number '{val}' has no unit marking. Confirm if feet or inches:"
            else:
                r1_text = f"{txt}'"
                r2_text = f"{txt}\""
                desc = f"Unit marking in '{txt}' is ambiguous. Please confirm interpretation:"

            unit_ambig_out.append({
                "id": l["id"],
                "text": txt,
                "applies_to": l.get("applies_to", ""),
                "prompt": desc,
                "reading_1": r1_text,
                "reading_1_label": f"Interpret as {r1_text}",
                "reading_2": r2_text,
                "reading_2_label": f"Interpret as {r2_text}",
            })

    # Unverified list
    unverified_out = []
    for l in labels_out:
        if l["status"] == "unverified":
            unverified_out.append({
                "id": l["id"],
                "text": l["text"],
                "applies_to": l.get("applies_to", ""),
                "reason": "no redundancy to check against",
            })
        elif l["status"] == "membership uncertain":
            unverified_out.append({
                "id": l["id"],
                "text": l["text"],
                "applies_to": l.get("applies_to", ""),
                "reason": "chain box coverage below 60% rule",
            })

    # Areas
    areas_out = {
        "sum_of_rooms_read_sq_ft": res.get("sum_of_rooms_read_sq_ft", "N/A"),
        "sum_of_rooms_read_sq_m": res.get("sum_of_rooms_read_sq_m", "N/A"),
        "stated_envelope_sq_ft": res.get("stated_envelope_sq_ft", "N/A"),
        "stated_envelope_sq_m": res.get("stated_envelope_sq_m", "N/A"),
        "stated_envelope_dims": res.get("envelope_dims_formatted", "N/A"),
        "chain_sum_envelope_sq_ft": res.get("chain_sum_envelope_sq_ft", "N/A"),
        "chain_sum_envelope_sq_m": res.get("chain_sum_envelope_sq_m", "N/A"),
        "stated_envelope_ratio_pct": res.get("stated_envelope_ratio_pct", "N/A"),
        "chain_sum_envelope_ratio_pct": res.get("chain_sum_envelope_ratio_pct", "N/A"),
        "envelope_flag": res.get("envelope_flag", "within envelope"),
        "rooms": res.get("room_areas", []),
    }

    # Coverage
    coverage_out = {
        "labels_in_constraints": res.get("coverage", {}).get("labels_in_constraints", 0),
        "total_labels": len(labels_out),
        "constraints_built": len(constraints_out),
        "conflicts": len(conflicts_out),
    }

    return {
        "id": manifest_item["id"],
        "title": manifest_item["title"],
        "kind": manifest_item["kind"],
        "image_url": manifest_item["image_url"],
        "unit": res.get("plan_unit", "feet_inches"),
        "labels": labels_out,
        "constraints": constraints_out,
        "conflicts": conflicts_out,
        "areas": areas_out,
        "coverage": coverage_out,
        "unverified": unverified_out,
        "unit_ambiguous": unit_ambig_out,
    }


def process_synthetic_plan(meta: dict, manifest_item: dict) -> dict:
    """Build labels, constraints and run checks directly from synthetic metadata."""
    unit = meta["unit"]
    plan = meta["plan"]
    raw_labels = meta["labels"]
    injected_errors = meta.get("injected_errors", [])
    tol_mm = 152.4

    labels_dict = {}
    label_records = []
    for l in raw_labels:
        lid = l["id"]
        txt = l["written_text"]
        try:
            mms = parse_label(txt, unit)
        except Exception:
            mms = [l.get("value", 0.0)]
        labels_dict[lid] = checks.Label(lid, txt, unit, mm=mms[0] if mms else l.get("value", 0.0))
        raw_box = l.get("box")
        box_ok = False
        box_coords = None
        img_w, img_h = 1100, 1089
        if raw_box and len(raw_box) == 4:
            x0, y0, x1, y1 = raw_box[0], raw_box[1], raw_box[2], raw_box[3]
            inside = (0 <= x0 <= img_w) and (0 <= x1 <= img_w) and (0 <= y0 <= img_h) and (0 <= y1 <= img_h)
            if inside and (x1 > x0) and (y1 > y0):
                box_ok = True
                box_coords = [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]

        label_records.append({
            "id": lid,
            "name": get_human_name(lid, meta),
            "text": txt,
            "mm": mms,
            "status": "verified",
            "support": 1,
            "sources": ["synthetic"],
            "box": box_coords,
            "box_ok": box_ok,
            "applies_to": l.get("applies_to", ""),
            "role": l.get("role", ""),
        })


    # Constraints construction for synthetic plan
    constraints = [
        checks.chain_constraint("top_chain", "top_overall", [f"top_seg_{i}" for i in range(len(plan["cols"]))], tol_mm),
        checks.chain_constraint("left_chain", "left_overall", [f"left_seg_{j}" for j in range(len(plan["rows"][0]))], tol_mm),
        checks.chain_constraint("right_chain", "right_overall", [f"right_seg_{j}" for j in range(len(plan["rows"][-1]))], tol_mm),
        checks.equal_constraint("left_eq_right", "left_overall", "right_overall", tol_mm),
    ]

    # Room-to-boundary alignments
    rooms = plan["rooms"]
    n_cols = len(plan["cols"])
    for k, r in enumerate(rooms):
        # Room width matches its column segment
        constraints.append(checks.equal_constraint(f"room_{k}_w_eq_col", f"room_{k}_w", f"top_seg_{r['col']}", tol_mm))
        if r["col"] == 0:
            constraints.append(checks.equal_constraint(f"room_{k}_d_eq_left", f"room_{k}_d", f"left_seg_{r['row']}", tol_mm))
        if r["col"] == n_cols - 1:
            constraints.append(checks.equal_constraint(f"room_{k}_d_eq_right", f"room_{k}_d", f"right_seg_{r['row']}", tol_mm))

    violations = checks.check(labels_dict, constraints)
    suggs = checks.suggest(labels_dict, constraints, max_results=10)
    covered_ids = checks.covered_ids(labels_dict, constraints)

    # Flag conflict suspects
    conflict_suspect_ids = set()
    for v in violations:
        c = v.constraint
        conflict_suspect_ids.add(c.total)
        for p in c.parts:
            conflict_suspect_ids.add(p)

    for lr in label_records:
        if lr["id"] in conflict_suspect_ids:
            lr["status"] = "conflict_suspect"
        elif lr["id"] in covered_ids:
            lr["status"] = "verified"
        else:
            lr["status"] = "unverified"

    # Constraints format
    constraints_out = []
    viol_cids = {v.constraint.id: v for v in violations}
    for c in constraints:
        tot_txt = labels_dict[c.total].text if c.total in labels_dict else "?"
        parts_txts = [labels_dict[p].text for p in c.parts if p in labels_dict]
        tot_val = labels_dict[c.total].mm or 0.0
        parts_val = sum(labels_dict[p].mm or 0.0 for p in c.parts)
        gap = tot_val - parts_val
        gap_sign = "+" if gap >= 0 else "-"
        gap_fmt = f"{gap_sign}{format_mm(abs(gap), unit)}"

        num_walls = max(0, len(c.parts) - 1)
        slack_formula = f"equal_wall: ± {tol_mm:.1f} mm ({format_mm(tol_mm, unit)})" if c.kind == "equal" else f"chain: ± {tol_mm:.1f} mm ({format_mm(tol_mm, unit)})"

        if c.id in viol_cids:
            v = viol_cids[c.id]
            st = f"violation (excess {format_mm(v.excess_mm, unit)})"
            sat = False
        else:
            st = f"within wall allowance (gap {gap_fmt})"
            sat = True

        sentence = f"{c.id}: {tot_txt} vs {', '.join(parts_txts)}"
        constraints_out.append({
            "id": c.id,
            "sentence": sentence,
            "gap": round(gap, 1),
            "gap_formatted": gap_fmt,
            "slack_formula": slack_formula,
            "slack_cap_formatted": format_mm(tol_mm, unit),
            "implied_thickness_per_wall": "N/A" if num_walls == 0 else f"{format_mm(abs(gap / num_walls), unit)} per wall",
            "smallest_detectable_error": format_mm(tol_mm, unit),
            "status": st,
            "is_satisfied": sat,
        })

    # Conflicts format
    conflicts_out = []
    for v in violations:
        c = v.constraint
        suspects = [c.total] + c.parts
        c_suggs = []
        for s in suggs:
            if s.label_id in suspects:
                s_why = s.why or s.details
                if not s_why or s_why == "candidate_edit":
                    if s.status == "exact":
                        s_why = "matches the other rooms in this column"
                    elif s.status == "within tolerance":
                        s_why = "passes only within wall allowance"
                    else:
                        s_why = "partially satisfies constraints"

                c_suggs.append({
                    "label_id": s.label_id,
                    "label_name": get_human_name(s.label_id, meta),
                    "old_text": s.old_text,
                    "new_text": s.new_text,
                    "cost": s.cost,
                    "support": s.support,
                    "ocr_verified": s.ocr_verified,
                    "tag": s.status,
                    "why": s_why,
                    "note": s_why,
                    "delta_formatted": format_mm(s.delta_mm, unit),
                })

        # Plain-language conflict sentence
        if "eq_col" in c.id:
            m = re.match(r"room_(\d+)_w", c.total)
            if m:
                r_idx = int(m.group(1))
                r_name = get_human_name(c.total, meta)
                r_val = labels_dict[c.total].text
                col_idx = rooms[r_idx]["col"]
                other_rooms = [rooms[other]["name"].capitalize() for other, rm in enumerate(rooms) if rm["col"] == col_idx and other != r_idx]
                partner_val = labels_dict[f"top_seg_{col_idx}"].text
                sentence = f"{r_name} reads {r_val} m, but {' and '.join(other_rooms)} in the same column read {partner_val} m."
            else:
                sentence = f"{get_human_name(c.total, meta)} reads {labels_dict[c.total].text}, but column partners read {', '.join(labels_dict[p].text for p in c.parts)}."
        elif c.kind == "chain":
            tot_name = get_human_name(c.total, meta)
            parts_sum = format_mm(sum(labels_dict[p].mm or 0.0 for p in c.parts), unit)
            sentence = f"{tot_name} reads {labels_dict[c.total].text}, but chain segments sum to {parts_sum} (difference {format_mm(abs(v.gap_mm), unit)})."
        else:
            tot_name = get_human_name(c.total, meta)
            part_name = get_human_name(c.parts[0], meta) if c.parts else "?"
            sentence = f"{tot_name} reads {labels_dict[c.total].text}, but {part_name} reads {labels_dict[c.parts[0]].text}."

        conflicts_out.append({
            "constraint_id": c.id,
            "sentence": sentence,
            "suspect_label_ids": suspects,
            "gap_formatted": format_mm(abs(v.gap_mm), unit),
            "excess_formatted": format_mm(v.excess_mm, unit),
            "ranked_suggestions": c_suggs[:5],
        })

    # Calculate room areas from grid
    rooms_out = []
    sum_sq_m = 0.0
    sum_sq_ft = 0.0
    for k, r in enumerate(rooms):
        w_txt = labels_dict.get(f"room_{k}_w", checks.Label("", "0", unit)).text
        d_txt = labels_dict.get(f"room_{k}_d", checks.Label("", "0", unit)).text
        w_mms = parse_label(w_txt, unit)
        d_mms = parse_label(d_txt, unit)
        w_mm = w_mms[0] if w_mms else 0.0
        d_mm = d_mms[0] if d_mms else 0.0

        area_sq_m = round((w_mm / 1000.0) * (d_mm / 1000.0), 2)
        area_sq_ft = round((w_mm / 304.8) * (d_mm / 304.8), 2)
        sum_sq_m += area_sq_m
        sum_sq_ft += area_sq_ft

        rooms_out.append({
            "label_id": f"room_{k}",
            "room_name": r["name"],
            "text_as_written": f"{w_txt} x {d_txt}",
            "dimensions_formatted": f"{format_mm(w_mm, unit)} x {format_mm(d_mm, unit)}",
            "area_sq_ft": area_sq_ft,
            "area_sq_m": area_sq_m,
        })

    tot_w_mm = labels_dict["top_overall"].mm or 0.0
    tot_h_mm = labels_dict["left_overall"].mm or 0.0
    env_sq_m = round((tot_w_mm / 1000.0) * (tot_h_mm / 1000.0), 2)
    env_sq_ft = round((tot_w_mm / 304.8) * (tot_h_mm / 304.8), 2)
    stated_ratio = round((sum_sq_ft / env_sq_ft * 100.0), 1) if env_sq_ft > 0 else 100.0

    areas_out = {
        "sum_of_rooms_read_sq_ft": round(sum_sq_ft, 1),
        "sum_of_rooms_read_sq_m": round(sum_sq_m, 1),
        "stated_envelope_sq_ft": env_sq_ft,
        "stated_envelope_sq_m": env_sq_m,
        "stated_envelope_dims": f"{labels_dict['top_overall'].text} x {labels_dict['left_overall'].text}",
        "chain_sum_envelope_sq_ft": env_sq_ft,
        "chain_sum_envelope_sq_m": env_sq_m,
        "stated_envelope_ratio_pct": stated_ratio,
        "chain_sum_envelope_ratio_pct": stated_ratio,
        "envelope_flag": "sum of rooms exceeds envelope (>100%)" if stated_ratio > 100.0 else "within envelope",
        "rooms": rooms_out,
    }

    coverage_out = {
        "labels_in_constraints": len(covered_ids),
        "total_labels": len(label_records),
        "constraints_built": len(constraints_out),
        "conflicts": len(conflicts_out),
    }

    unverified_out = [
        {"id": lr["id"], "text": lr["text"], "applies_to": lr["applies_to"], "reason": "no redundancy to check against"}
        for lr in label_records if lr["status"] == "unverified"
    ]

    return {
        "id": manifest_item["id"],
        "title": manifest_item["title"],
        "kind": manifest_item["kind"],
        "image_url": manifest_item["image_url"],
        "unit": unit,
        "labels": label_records,
        "constraints": constraints_out,
        "conflicts": conflicts_out,
        "areas": areas_out,
        "coverage": coverage_out,
        "unverified": unverified_out,
        "unit_ambiguous": [],
    }


def get_plan_data(name: str) -> dict:
    if name not in WORKING_COPIES:
        raise HTTPException(status_code=404, detail=f"Plan '{name}' not found")
    manifest = next((m for m in ALL_PLANS_MANIFEST if m["id"] == name), None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Plan '{name}' manifest entry missing")

    copy_data = WORKING_COPIES[name]
    if manifest["kind"] == "synthetic":
        return process_synthetic_plan(copy_data, manifest)
    else:
        return process_real_plan(copy_data, manifest)


# -----------------------------------------------------------------------------
# API Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/samples")
def get_samples():
    """Return available sample plans."""
    return [
        {
            "id": s["id"],
            "title": s["title"],
            "kind": s["kind"],
            "description": s["description"],
            "status_chip": s["status_chip"],
            "thumbnail_url": s["thumbnail_url"],
            "image_url": s["image_url"],
        }
        for s in SAMPLE_MANIFEST
    ]


@app.get("/api/plan/{name}")
def get_plan(name: str):
    """Return complete standardized plan verification structure."""
    return get_plan_data(name)


class PreviewRequest(BaseModel):
    name: str
    label_id: str
    new_text: str


@app.post("/api/preview")
def preview_label_edit(req: PreviewRequest):
    """Validate and preview an edit on a COPY without storing anything."""
    name = req.name
    if name not in WORKING_COPIES:
        raise HTTPException(status_code=404, detail=f"Plan '{name}' not found")

    manifest = next((m for m in ALL_PLANS_MANIFEST if m["id"] == name), None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Manifest for '{name}' not found")

    working = WORKING_COPIES[name]
    is_synth = (manifest["kind"] == "synthetic")
    unit = working.get("unit" if is_synth else "plan_unit", "metres" if is_synth else "feet_inches")

    before_plan = get_plan_data(name)

    # 1. Validate text with plan unit parser
    try:
        parsed = parse_label(req.new_text, unit)
        if not parsed or len(parsed) != 1 or parsed[0] <= 0:
            return {
                "valid": False,
                "error": f"Invalid {unit.replace('_', ' ')} value: '{req.new_text}'",
                "conflicts_remaining": len(before_plan["conflicts"]),
                "constraint_statuses": [c["status"] for c in before_plan["constraints"]],
                "changed_areas": {},
                "sum_of_rooms_ratio": before_plan["areas"].get("stated_envelope_ratio_pct"),
                "resolved": False,
            }
    except Exception as e:
        return {
            "valid": False,
            "error": f"Parse error: {str(e)}",
            "conflicts_remaining": len(before_plan["conflicts"]),
            "constraint_statuses": [c["status"] for c in before_plan["constraints"]],
            "changed_areas": {},
            "sum_of_rooms_ratio": before_plan["areas"].get("stated_envelope_ratio_pct"),
            "resolved": False,
        }

    # 2. Re-run verification on a deep COPY (stores nothing)
    copy_working = copy.deepcopy(working)
    new_val = parsed[0]

    if is_synth:
        for l in copy_working.get("labels", []):
            if l["id"] == req.label_id:
                l["written_text"] = req.new_text
                break
        plan = copy_working.get("plan", {})
        for k, r in enumerate(plan.get("rooms", [])):
            if req.label_id == f"room_{k}_w":
                r["w"] = int(new_val) if unit == "metres" else int(new_val / 12.7)
            elif req.label_id == f"room_{k}_d":
                r["d"] = int(new_val) if unit == "metres" else int(new_val / 12.7)
        copy_plan_data = process_synthetic_plan(copy_working, manifest)
    else:
        old_text = ""
        for d in copy_working.get("dimensions", []):
            if d.get("id") == req.label_id:
                old_text = d.get("text_as_written", "")
                d["text_as_written"] = req.new_text
                d["mm"] = parsed
                break
        for side in ("left_chain", "right_chain"):
            for it in copy_working.get(side, []):
                if it.get("text_as_written") == old_text or it.get("id") == req.label_id:
                    it["text_as_written"] = req.new_text
                    it["mm"] = parsed
        copy_plan_data = process_real_plan(copy_working, manifest)

    conflicts_remaining = len(copy_plan_data.get("conflicts", []))
    constraint_statuses = [
        {"id": c["id"], "sentence": c["sentence"], "status": c["status"], "is_satisfied": c["is_satisfied"]}
        for c in copy_plan_data.get("constraints", [])
    ]
    changed_areas = {
        "before_sum_sq_m": before_plan["areas"].get("sum_of_rooms_read_sq_m"),
        "after_sum_sq_m": copy_plan_data["areas"].get("sum_of_rooms_read_sq_m"),
        "before_sum_sq_ft": before_plan["areas"].get("sum_of_rooms_read_sq_ft"),
        "after_sum_sq_ft": copy_plan_data["areas"].get("sum_of_rooms_read_sq_ft"),
        "envelope_sq_m": copy_plan_data["areas"].get("stated_envelope_sq_m"),
        "envelope_sq_ft": copy_plan_data["areas"].get("stated_envelope_sq_ft"),
        "rooms": copy_plan_data["areas"].get("rooms", []),
    }
    sum_ratio = copy_plan_data["areas"].get("stated_envelope_ratio_pct")

    return {
        "valid": True,
        "error": None,
        "conflicts_remaining": conflicts_remaining,
        "remaining_conflicts": copy_plan_data.get("conflicts", []),
        "constraint_statuses": constraint_statuses,
        "changed_areas": changed_areas,
        "sum_of_rooms_ratio": sum_ratio,
        "resolved": (conflicts_remaining == 0),
    }


class ConfirmRequest(BaseModel):
    name: str
    label_id: str
    new_text: str
    source: Optional[str] = "suggestion"


@app.post("/api/confirm")
def confirm_label_edit(req: ConfirmRequest):
    """Apply an edit to the in-memory working copy and re-verify."""
    name = req.name
    if name not in WORKING_COPIES:
        raise HTTPException(status_code=404, detail=f"Plan '{name}' not found")

    manifest = next((m for m in ALL_PLANS_MANIFEST if m["id"] == name), None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Manifest for '{name}' not found")

    before_plan = get_plan_data(name)
    before_conflicts = len(before_plan["conflicts"])

    working = WORKING_COPIES[name]
    old_text = ""

    if manifest["kind"] == "synthetic":
        # Synthetic labels update
        for l in working.get("labels", []):
            if l["id"] == req.label_id:
                old_text = l.get("written_text", "")
                l["written_text"] = req.new_text
                break
        # Also update underlying room model if applicable
        plan = working.get("plan", {})
        unit = working.get("unit", "metres")
        parsed = parse_label(req.new_text, unit)
        new_val = parsed[0] if parsed else 0.0
        for k, r in enumerate(plan.get("rooms", [])):
            if req.label_id == f"room_{k}_w":
                r["w"] = int(new_val) if unit == "metres" else int(new_val / 12.7)
            elif req.label_id == f"room_{k}_d":
                r["d"] = int(new_val) if unit == "metres" else int(new_val / 12.7)
    else:
        # Real dimensions update
        unit = working.get("plan_unit", "feet_inches")
        parsed = parse_label(req.new_text, unit)
        # Update raw dimensions
        for d in working.get("dimensions", []):
            if d.get("id") == req.label_id or (req.label_id.startswith("dim_") and d.get("text_as_written") == before_plan["labels"][int(req.label_id.split('_')[1])]["text"]):
                old_text = d.get("text_as_written", "")
                d["text_as_written"] = req.new_text
                d["mm"] = parsed
                break
        # Update chain arrays if present
        for side in ("left_chain", "right_chain"):
            for it in working.get(side, []):
                if it.get("text_as_written") == old_text:
                    it["text_as_written"] = req.new_text
                    it["mm"] = parsed

    working.setdefault("changed_label_ids", set()).add(req.label_id)

    # Record in history
    change_history = working.setdefault("change_history", [])
    change_history.append({
        "label_id": req.label_id,
        "label_name": get_human_name(req.label_id, working),
        "old_text": old_text,
        "new_text": req.new_text,
        "source": req.source or "suggestion",
    })

    after_plan = get_plan_data(name)
    after_conflicts = len(after_plan["conflicts"])

    return {
        "status": "success",
        "before_conflicts": before_conflicts,
        "after_conflicts": after_conflicts,
        "changed_labels": [{"id": req.label_id, "old_text": old_text, "new_text": req.new_text}],
        "remaining_conflicts": after_plan["conflicts"],
        "plan": after_plan,
        "change_history": change_history,
    }


class RevertRequest(BaseModel):
    name: str
    label_id: str


@app.post("/api/revert")
def revert_label_edit(req: RevertRequest):
    """Revert a single label back to its original loaded value."""
    name = req.name
    if name not in WORKING_COPIES or name not in ORIGINAL_PLANS:
        raise HTTPException(status_code=404, detail=f"Plan '{name}' not found")

    manifest = next((m for m in ALL_PLANS_MANIFEST if m["id"] == name), None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Manifest for '{name}' not found")

    orig = ORIGINAL_PLANS[name]
    working = WORKING_COPIES[name]
    lid = req.label_id

    if manifest["kind"] == "synthetic":
        orig_lbl = next((l for l in orig.get("labels", []) if l["id"] == lid), None)
        if orig_lbl:
            orig_text = orig_lbl.get("written_text", "")
            for l in working.get("labels", []):
                if l["id"] == lid:
                    l["written_text"] = orig_text
                    break
            unit = orig.get("unit", "metres")
            parsed = parse_label(orig_text, unit)
            orig_val = parsed[0] if parsed else 0.0
            plan = working.get("plan", {})
            for k, r in enumerate(plan.get("rooms", [])):
                if lid == f"room_{k}_w":
                    r["w"] = int(orig_val) if unit == "metres" else int(orig_val / 12.7)
                elif lid == f"room_{k}_d":
                    r["d"] = int(orig_val) if unit == "metres" else int(orig_val / 12.7)
    else:
        orig_dim = next((d for d in orig.get("dimensions", []) if d.get("id") == lid), None)
        if orig_dim:
            orig_text = orig_dim.get("text_as_written", "")
            for d in working.get("dimensions", []):
                if d.get("id") == lid:
                    d["text_as_written"] = orig_text
                    d["mm"] = orig_dim.get("mm", [])
                    break
            for side in ("left_chain", "right_chain"):
                for it in working.get(side, []):
                    if it.get("id") == lid:
                        it["text_as_written"] = orig_text
                        it["mm"] = orig_dim.get("mm", [])

    if "changed_label_ids" in working and lid in working["changed_label_ids"]:
        working["changed_label_ids"].discard(lid)

    history = working.get("change_history", [])
    working["change_history"] = [h for h in history if h["label_id"] != lid]

    after_plan = get_plan_data(name)
    return {
        "status": "reverted",
        "label_id": lid,
        "plan": after_plan,
        "change_history": working["change_history"],
    }


@app.get("/api/changelog/{name}")
def get_changelog(name: str):
    """Return plain-text changelog for download."""
    if name not in WORKING_COPIES:
        raise HTTPException(status_code=404, detail=f"Plan '{name}' not found")
    working = WORKING_COPIES[name]
    history = working.get("change_history", [])
    lines = [
        f"Sketch-to-Space Change Log",
        f"Plan: {name}",
        f"Total Modifications: {len(history)}",
        "----------------------------------------",
    ]
    if not history:
        lines.append("No modifications made to original plan.")
    else:
        for idx, item in enumerate(history, 1):
            lines.append(f"{idx}. {item.get('label_name', item['label_id'])} ({item['label_id']}):")
            lines.append(f"   {item['old_text']} -> {item['new_text']} [Source: {item.get('source', 'user')}]")
    content = "\n".join(lines)
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{name}_changelog.txt"'}
    )


class ResetRequest(BaseModel):
    name: str


@app.post("/api/reset")
def reset_plan(req: ResetRequest):
    """Restore original working copy from stored data."""
    name = req.name
    if name not in ORIGINAL_PLANS:
        raise HTTPException(status_code=404, detail=f"Original plan '{name}' not found")
    WORKING_COPIES[name] = copy.deepcopy(ORIGINAL_PLANS[name])
    return {
        "status": "reset",
        "name": name,
        "plan": get_plan_data(name),
        "change_history": [],
    }



@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Return empty 204 to prevent 404 in browser console."""
    return Response(status_code=204)


@app.post("/api/upload")
def upload_plan():
    """Live reading endpoint intentionally disabled in demo build."""
    raise HTTPException(status_code=501, detail="Live reading is not wired in this build. Pick a sample plan.")


# -----------------------------------------------------------------------------
# SVG Generation Endpoint (/api/corrected/{name})
# -----------------------------------------------------------------------------
@app.get("/api/corrected/{name}")
def get_corrected_svg(name: str):
    """Render crisp engineering SVG of corrected layout."""
    plan_data = get_plan_data(name)
    manifest = next((m for m in ALL_PLANS_MANIFEST if m["id"] == name), None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Plan '{name}' not found")

    is_synth = (manifest["kind"] == "synthetic")

    if is_synth:
        svg_content = _render_synthetic_svg(name, plan_data)
    else:
        svg_content = _render_real_schematic_svg(name, plan_data)

    return Response(content=svg_content, media_type="image/svg+xml")


def _render_synthetic_svg(name: str, plan_data: dict) -> str:
    """Render synthetic floor plan directly from grid coordinates in light palette."""
    working = WORKING_COPIES[name]
    plan = working["plan"]
    cols = plan["cols"]
    rows = plan["rows"]
    rooms = plan["rooms"]
    unit = plan_data["unit"]
    changed_ids = working.get("changed_label_ids", set())

    total_w = plan["total_w"]
    total_h = plan["total_h"]

    vb_w, vb_h = 1000, 860
    margin_l, margin_t = 140, 130
    grid_w = vb_w - margin_l - 120
    grid_h = vb_h - margin_t - 130

    scale_x = grid_w / max(1, total_w)
    scale_y = grid_h / max(1, total_h)

    conflict_labels = {cf["suspect_label_ids"][0] for cf in plan_data.get("conflicts", []) if cf.get("suspect_label_ids")}

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w} {vb_h}" width="100%" height="100%" style="background:#FFFFFF; font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;">',
        '<defs>',
        '  <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">',
        '    <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#EFEBE3" stroke-width="1"/>',
        '  </pattern>',
        '  <pattern id="redHatch" width="8" height="8" patternTransform="rotate(45 0 0)" patternUnits="userSpaceOnUse">',
        '    <line x1="0" y1="0" x2="0" y2="8" stroke="#B3261E" stroke-width="2" />',
        '  </pattern>',
        '  <marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">',
        '    <path d="M 0 2 L 8 5 L 0 8 z" fill="#1F2A37" />',
        '  </marker>',
        '</defs>',
        f'<rect width="{vb_w}" height="{vb_h}" fill="url(#grid)" />',
        f'<text x="{margin_l}" y="42" fill="#1F4E5F" font-size="16" font-weight="700" letter-spacing="0.5">ARCHITECTURAL CAD SCHEMATIC - {plan_data["title"].upper()}</text>',
    ]

    # Compute column X offsets
    col_x_offsets = [0]
    for w in cols[:-1]:
        col_x_offsets.append(col_x_offsets[-1] + w)

    # Compute row Y offsets per column
    col_y_offsets = []
    for c_idx, r_list in enumerate(rows):
        y_offs = [0]
        for d in r_list[:-1]:
            y_offs.append(y_offs[-1] + d)
        col_y_offsets.append(y_offs)

    # Double-line outer envelope wall
    svg_parts.append(f'<rect x="{margin_l - 4}" y="{margin_t - 4}" width="{grid_w + 8}" height="{grid_h + 8}" fill="none" stroke="#1F2A37" stroke-width="2"/>')
    svg_parts.append(f'<rect x="{margin_l}" y="{margin_t}" width="{grid_w}" height="{grid_h}" fill="none" stroke="#1F2A37" stroke-width="1.5"/>')

    # Draw rooms
    for k, r in enumerate(rooms):
        ci = r["col"]
        ri = r["row"]
        rx = margin_l + col_x_offsets[ci] * scale_x
        ry = margin_t + col_y_offsets[ci][ri] * scale_y
        rw = r["w"] * scale_x
        rh = r["d"] * scale_y

        is_conflict = (f"room_{k}_w" in conflict_labels or f"room_{k}_d" in conflict_labels)
        is_changed = (f"room_{k}_w" in changed_ids or f"room_{k}_d" in changed_ids)

        w_lbl = next((l for l in working.get("labels", []) if l.get("id") == f"room_{k}_w"), None)
        d_lbl = next((l for l in working.get("labels", []) if l.get("id") == f"room_{k}_d"), None)
        w_text_cur = w_lbl.get("written_text", "") if w_lbl else f"{r['w']/1000.0:.3f}"
        d_text_cur = d_lbl.get("written_text", "") if d_lbl else f"{r['d']/1000.0:.3f}"

        try:
            cur_w_mm = parse_label(w_text_cur, unit)[0]
            cur_d_mm = parse_label(d_text_cur, unit)[0]
        except Exception:
            cur_w_mm, cur_d_mm = r["w"], r["d"]
        area_sq_m = round((cur_w_mm / 1000.0) * (cur_d_mm / 1000.0), 2)

        # Before-accept conflict state: draw conflicting room at its READ dimensions, hatch the gap in red
        if is_conflict and not is_changed:
            rw_read = cur_w_mm * scale_x
            gap_w = max(4.0, rw - rw_read)
            gap_mm = r["w"] - cur_w_mm

            # Read room rect
            svg_parts.append(f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{rw_read:.1f}" height="{rh:.1f}" fill="#FBE4E1" stroke="#B3261E" stroke-width="2" rx="2"/>')
            # Hatched gap
            svg_parts.append(f'<rect x="{rx + rw_read:.1f}" y="{ry:.1f}" width="{gap_w:.1f}" height="{rh:.1f}" fill="url(#redHatch)" stroke="#B3261E" stroke-width="1.5" stroke-dasharray="3 3"/>')
            # Label on the gap
            svg_parts.append(f'<text x="{rx + rw_read + gap_w/2:.1f}" y="{ry + rh/2:.1f}" fill="#B3261E" font-size="12" font-weight="700" text-anchor="middle" transform="rotate(-90, {rx + rw_read + gap_w/2:.1f}, {ry + rh/2:.1f})">gap {format_mm(abs(gap_mm), unit)}</text>')

            cx = rx + rw_read / 2.0
            cy = ry + rh / 2.0
            svg_parts.append(f'<text x="{cx:.1f}" y="{cy - 16:.1f}" fill="#1F2A37" font-size="13" font-weight="700" text-anchor="middle">{r["name"]}</text>')
            svg_parts.append(f'<rect x="{cx - 55:.1f}" y="{cy - 4:.1f}" width="110" height="20" rx="3" fill="#FBE4E1" stroke="#B3261E"/>')
            svg_parts.append(f'<text x="{cx:.1f}" y="{cy + 10:.1f}" fill="#B3261E" font-size="12" font-family="monospace" font-weight="700" text-anchor="middle">{w_text_cur} x {d_text_cur} m</text>')
            svg_parts.append(f'<text x="{cx:.1f}" y="{cy + 28:.1f}" fill="#B3261E" font-size="12" font-weight="700" text-anchor="middle">needs your decision</text>')
            svg_parts.append(f'<text x="{cx:.1f}" y="{cy + 44:.1f}" fill="#5B6675" font-size="12" text-anchor="middle">{area_sq_m:.2f} sq m</text>')

        elif is_changed:
            # Corrected state: hatch disappears, old value struck through in grey, new in green
            svg_parts.append(f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{rw:.1f}" height="{rh:.1f}" fill="#E3F1E8" stroke="#2F7D4F" stroke-width="2" rx="2"/>')
            cx = rx + rw / 2.0
            cy = ry + rh / 2.0
            svg_parts.append(f'<text x="{cx:.1f}" y="{cy - 16:.1f}" fill="#1F2A37" font-size="13" font-weight="700" text-anchor="middle">{r["name"]}</text>')
            orig_l = next((l for l in ORIGINAL_PLANS[name].get("labels", []) if l.get("id") == f"room_{k}_w"), None)
            old_w_txt = orig_l.get("written_text", "") if orig_l else ""
            svg_parts.append(f'<rect x="{cx - 75:.1f}" y="{cy - 4:.1f}" width="150" height="22" rx="3" fill="#FFFFFF" stroke="#2F7D4F"/>')
            svg_parts.append(f'<text x="{cx:.1f}" y="{cy + 11:.1f}" text-anchor="middle" font-size="12" font-family="monospace">')
            if old_w_txt and old_w_txt != w_text_cur:
                svg_parts.append(f'  <tspan fill="#8C939D" text-decoration="line-through">{old_w_txt} m</tspan>')
            svg_parts.append(f'  <tspan fill="#266741" font-weight="700"> {w_text_cur} x {d_text_cur} m</tspan>')
            svg_parts.append(f'</text>')
            svg_parts.append(f'<text x="{cx:.1f}" y="{cy + 28:.1f}" fill="#266741" font-size="12" font-weight="700" text-anchor="middle">corrected</text>')
            svg_parts.append(f'<text x="{cx:.1f}" y="{cy + 44:.1f}" fill="#5B6675" font-size="12" text-anchor="middle">{area_sq_m:.2f} sq m</text>')

        else:
            svg_parts.append(f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{rw:.1f}" height="{rh:.1f}" fill="#FFFFFF" stroke="#1F2A37" stroke-width="1.5" rx="2"/>')
            cx = rx + rw / 2.0
            cy = ry + rh / 2.0
            svg_parts.append(f'<text x="{cx:.1f}" y="{cy - 12:.1f}" fill="#1F2A37" font-size="13" font-weight="700" text-anchor="middle">{r["name"]}</text>')
            svg_parts.append(f'<rect x="{cx - 55:.1f}" y="{cy - 2:.1f}" width="110" height="20" rx="3" fill="#EFEBE3" stroke="#E2DCCF"/>')
            svg_parts.append(f'<text x="{cx:.1f}" y="{cy + 12:.1f}" fill="#1F2A37" font-size="12" font-family="monospace" font-weight="600" text-anchor="middle">{w_text_cur} x {d_text_cur} m</text>')
            svg_parts.append(f'<text x="{cx:.1f}" y="{cy + 30:.1f}" fill="#5B6675" font-size="12" text-anchor="middle">{area_sq_m:.2f} sq m</text>')

    # Dimension chains with arrowheads
    # 1. Top chain segments (column widths)
    top_seg_y = margin_t - 24
    for i, col_w in enumerate(cols):
        sx0 = margin_l + col_x_offsets[i] * scale_x
        sx1 = sx0 + col_w * scale_x
        svg_parts.append(f'<line x1="{sx0+4}" y1="{top_seg_y}" x2="{sx1-4}" y2="{top_seg_y}" stroke="#1F2A37" stroke-width="1.5" marker-start="url(#arrow)" marker-end="url(#arrow)"/>')
        svg_parts.append(f'<line x1="{sx0}" y1="{top_seg_y-5}" x2="{sx0}" y2="{top_seg_y+5}" stroke="#1F2A37" stroke-width="1"/>')
        svg_parts.append(f'<line x1="{sx1}" y1="{top_seg_y-5}" x2="{sx1}" y2="{top_seg_y+5}" stroke="#1F2A37" stroke-width="1"/>')
        svg_parts.append(f'<text x="{(sx0 + sx1)/2}" y="{top_seg_y - 8}" fill="#1F2A37" font-size="12" font-family="monospace" font-weight="600" text-anchor="middle">{format_mm(col_w, unit)}</text>')

    # 2. Top overall width
    top_ov_y = margin_t - 64
    svg_parts.append(f'<line x1="{margin_l+4}" y1="{top_ov_y}" x2="{margin_l + grid_w - 4}" y2="{top_ov_y}" stroke="#1F2A37" stroke-width="1.5" marker-start="url(#arrow)" marker-end="url(#arrow)"/>')
    svg_parts.append(f'<line x1="{margin_l}" y1="{top_ov_y-5}" x2="{margin_l}" y2="{top_ov_y+5}" stroke="#1F2A37" stroke-width="1"/>')
    svg_parts.append(f'<line x1="{margin_l + grid_w}" y1="{top_ov_y-5}" x2="{margin_l + grid_w}" y2="{top_ov_y+5}" stroke="#1F2A37" stroke-width="1"/>')
    svg_parts.append(f'<rect x="{margin_l + grid_w/2 - 40:.1f}" y="{top_ov_y - 20}" width="80" height="18" rx="3" fill="#EFEBE3" stroke="#E2DCCF"/>')
    svg_parts.append(f'<text x="{margin_l + grid_w/2}" y="{top_ov_y - 7}" fill="#1F2A37" font-size="12" font-family="monospace" font-weight="700" text-anchor="middle">{format_mm(total_w, unit)}</text>')

    # 3. Left chain segments (row depths)
    left_seg_x = margin_l - 24
    curr_y = margin_t
    for j, depth in enumerate(rows[0]):
        sy0 = curr_y
        sy1 = curr_y + depth * scale_y
        svg_parts.append(f'<line x1="{left_seg_x}" y1="{sy0+4}" x2="{left_seg_x}" y2="{sy1-4}" stroke="#1F2A37" stroke-width="1.5" marker-start="url(#arrow)" marker-end="url(#arrow)"/>')
        svg_parts.append(f'<line x1="{left_seg_x-5}" y1="{sy0}" x2="{left_seg_x+5}" y2="{sy0}" stroke="#1F2A37" stroke-width="1"/>')
        svg_parts.append(f'<line x1="{left_seg_x-5}" y1="{sy1}" x2="{left_seg_x+5}" y2="{sy1}" stroke="#1F2A37" stroke-width="1"/>')
        svg_parts.append(f'<text x="{left_seg_x - 8}" y="{(sy0 + sy1)/2 + 4}" fill="#1F2A37" font-size="12" font-family="monospace" font-weight="600" text-anchor="end">{format_mm(depth, unit)}</text>')
        curr_y = sy1

    # 4. Left overall height
    left_ov_x = margin_l - 68
    svg_parts.append(f'<line x1="{left_ov_x}" y1="{margin_t+4}" x2="{left_ov_x}" y2="{margin_t + grid_h - 4}" stroke="#1F2A37" stroke-width="1.5" marker-start="url(#arrow)" marker-end="url(#arrow)"/>')
    svg_parts.append(f'<line x1="{left_ov_x-5}" y1="{margin_t}" x2="{left_ov_x+5}" y2="{margin_t}" stroke="#1F2A37" stroke-width="1"/>')
    svg_parts.append(f'<line x1="{left_ov_x-5}" y1="{margin_t + grid_h}" x2="{left_ov_x+5}" y2="{margin_t + grid_h}" stroke="#1F2A37" stroke-width="1"/>')
    svg_parts.append(f'<text x="{left_ov_x - 10}" y="{margin_t + grid_h/2 + 4}" fill="#1F2A37" font-size="12" font-family="monospace" font-weight="700" text-anchor="middle" transform="rotate(-90, {left_ov_x - 10}, {margin_t + grid_h/2 + 4})">{format_mm(total_h, unit)}</text>')

    # Legend at bottom left
    leg_y = vb_h - 38
    svg_parts.append(f'<g transform="translate({margin_l}, {leg_y})">')
    svg_parts.append('  <rect x="0" y="0" width="16" height="16" fill="#FFFFFF" stroke="#1F2A37" stroke-width="1.5" rx="2"/>')
    svg_parts.append('  <text x="22" y="13" fill="#1F2A37" font-size="12">Verified</text>')
    svg_parts.append('  <rect x="90" y="0" width="16" height="16" fill="#FBE4E1" stroke="#B3261E" stroke-width="1.5" rx="2"/>')
    svg_parts.append('  <text x="112" y="13" fill="#B3261E" font-size="12" font-weight="600">Needs decision</text>')
    svg_parts.append('  <rect x="220" y="0" width="16" height="16" fill="#E3F1E8" stroke="#2F7D4F" stroke-width="1.5" rx="2"/>')
    svg_parts.append('  <text x="242" y="13" fill="#266741" font-size="12" font-weight="600">Corrected</text>')
    svg_parts.append('</g>')

    # Scale bar at bottom right
    scale_bar_mm = 2000.0
    scale_bar_px = scale_bar_mm * scale_x
    sb_x = vb_w - 120 - scale_bar_px
    sb_y = vb_h - 38
    svg_parts.append(f'<g transform="translate({sb_x:.1f}, {sb_y})">')
    svg_parts.append(f'  <line x1="0" y1="8" x2="{scale_bar_px:.1f}" y2="8" stroke="#1F2A37" stroke-width="2"/>')
    svg_parts.append('  <line x1="0" y1="2" x2="0" y2="14" stroke="#1F2A37" stroke-width="2"/>')
    svg_parts.append(f'  <line x1="{scale_bar_px:.1f}" y1="2" x2="{scale_bar_px:.1f}" y2="14" stroke="#1F2A37" stroke-width="2"/>')
    svg_parts.append(f'  <text x="{scale_bar_px/2:.1f}" y="-2" fill="#1F2A37" font-size="12" font-weight="600" text-anchor="middle">2.0 m scale bar</text>')
    svg_parts.append('</g>')

    svg_parts.append('</svg>')
    return "\n".join(svg_parts)


def _render_real_schematic_svg(name: str, plan_data: dict) -> str:
    """Render room-box schematic for real hand-drawn plans in light palette."""
    rooms = plan_data.get("areas", {}).get("rooms", [])
    vb_w, vb_h = 960, 680
    working = WORKING_COPIES.get(name, {})
    changed_ids = working.get("changed_label_ids", set())
    unit_ambig_ids = {u["id"] for u in plan_data.get("unit_ambiguous", [])}

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w} {vb_h}" width="100%" height="100%" style="background:#FFFFFF; font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;">',
        # Prominent caption banner - always visible
        f'<rect x="20" y="16" width="{vb_w - 40}" height="42" fill="#EFEBE3" rx="6" stroke="#E2DCCF" stroke-width="1.5"/>',
        f'<text x="{vb_w/2}" y="42" fill="#1F2A37" font-size="13" font-weight="600" text-anchor="middle">Schematic only. Layout and positions are not read from the photo.</text>',
    ]

    if not rooms:
        svg_parts.append(f'<text x="{vb_w/2}" y="{vb_h/2}" fill="#5B6675" font-size="15" text-anchor="middle">No room dimensions available to render schematic.</text>')
        svg_parts.append('</svg>')
        return "\n".join(svg_parts)

    start_x = 40
    start_y = 80
    max_row_w = vb_w - 80

    curr_x = start_x
    curr_y = start_y
    row_height = 110

    for rm in rooms:
        lid = rm.get("label_id", "")
        r_name = rm.get("room_name", "Room")
        dim_str = rm.get("dimensions_formatted", rm.get("text_as_written", ""))
        sq_ft = rm.get("area_sq_ft", 0.0)
        sq_m = rm.get("area_sq_m", 0.0)

        box_w = max(190, min(270, int(160 + sq_ft * 0.45)))
        box_h = 95

        if curr_x + box_w > max_row_w:
            curr_x = start_x
            curr_y += row_height + 15

        if curr_y + box_h > vb_h - 45:
            break

        is_changed = (lid in changed_ids)
        is_ambig = (lid in unit_ambig_ids)

        if is_changed:
            border_c = "#2F7D4F"
            bg_c = "#E3F1E8"
            dim_bg = "#FFFFFF"
            dim_tc = "#266741"
            badge_text = ' <tspan fill="#266741" font-size="12" font-weight="700">[CORRECTED]</tspan>'
        elif is_ambig:
            border_c = "#C2570C"
            bg_c = "#FDE9D9"
            dim_bg = "#FFFFFF"
            dim_tc = "#A14506"
            badge_text = ' <tspan fill="#A14506" font-size="12" font-weight="700">[AMBIGUOUS UNIT]</tspan>'
        else:
            border_c = "#E2DCCF"
            bg_c = "#FFFFFF"
            dim_bg = "#EFEBE3"
            dim_tc = "#1F2A37"
            badge_text = ''

        svg_parts.append(f'<g transform="translate({curr_x}, {curr_y})">')
        svg_parts.append(f'  <rect width="{box_w}" height="{box_h}" fill="{bg_c}" stroke="{border_c}" stroke-width="1.5" rx="6"/>')
        svg_parts.append(f'  <text x="14" y="24" fill="#1F2A37" font-size="13" font-weight="700">{r_name}{badge_text}</text>')
        pill_w = min(box_w - 28, max(85, len(dim_str) * 8 + 16))
        svg_parts.append(f'  <rect x="14" y="32" width="{pill_w}" height="22" rx="3" fill="{dim_bg}" stroke="{border_c}" stroke-width="1"/>')
        svg_parts.append(f'  <text x="{14 + pill_w/2}" y="47" fill="{dim_tc}" font-size="12" font-family="monospace" font-weight="600" text-anchor="middle">{dim_str}</text>')
        if sq_ft > 0:
            svg_parts.append(f'  <text x="14" y="74" fill="#5B6675" font-size="12">{sq_ft:.1f} sq ft ({sq_m:.1f} sq m)</text>')
        svg_parts.append('</g>')

        curr_x += box_w + 16

    env_ft = plan_data.get("areas", {}).get("stated_envelope_sq_ft", "N/A")
    sum_ft = plan_data.get("areas", {}).get("sum_of_rooms_read_sq_ft", "N/A")
    flag = plan_data.get("areas", {}).get("envelope_flag", "")
    badge_fill = "#B3261E" if ">100%" in flag else "#2F7D4F"

    svg_parts.append(f'<rect x="40" y="{vb_h - 45}" width="{vb_w - 80}" height="32" fill="#EFEBE3" rx="4" stroke="#E2DCCF"/>')
    svg_parts.append(f'<text x="56" y="{vb_h - 24}" fill="#1F2A37" font-size="12">Total Rooms Area: <tspan font-family="monospace" font-weight="600" fill="#1F2A37">{sum_ft} sq ft</tspan> | Stated Envelope: <tspan font-family="monospace" font-weight="600" fill="#1F2A37">{env_ft} sq ft</tspan> | Status: <tspan fill="{badge_fill}" font-weight="700">{flag}</tspan></text>')

    svg_parts.append('</svg>')
    return "\n".join(svg_parts)


# -----------------------------------------------------------------------------
# Replaced Drawing Endpoints (PART 5)
# -----------------------------------------------------------------------------
@app.get("/api/replaced/{name}")
def get_replaced_image(name: str):
    """Return replaced drawing: re-rendered synthetic or painted real box."""
    if name not in WORKING_COPIES:
        raise HTTPException(status_code=404, detail=f"Plan '{name}' not found")
    manifest = next((m for m in ALL_PLANS_MANIFEST if m["id"] == name), None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Manifest for '{name}' not found")

    working = WORKING_COPIES[name]
    is_synth = (manifest["kind"] == "synthetic")

    if is_synth:
        import synth_plans, random
        fonts = synth_plans.find_fonts([])
        rng = random.Random(42)
        im = synth_plans.render(working["plan"], working["labels"], fonts, rng)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    else:
        plan_data = get_plan_data(name)
        img_path = Path(manifest["image_file"])
        if not img_path.is_file():
            raise HTTPException(status_code=404, detail="Original image not found")
        im = Image.open(img_path).convert("RGBA")
        draw = ImageDraw.Draw(im)

        changed_ids = working.get("changed_label_ids", set())
        labels_by_id = {l["id"]: l for l in plan_data.get("labels", [])}

        for cid in changed_ids:
            lbl = labels_by_id.get(cid)
            if not lbl or not lbl.get("box_ok") or not lbl.get("box"):
                continue
            x0, y0, x1, y1 = lbl["box"]
            pad = 2
            bx0 = max(0, int(x0 - pad))
            by0 = max(0, int(y0 - pad))
            bx1 = min(im.width, int(x1 + pad))
            by1 = min(im.height, int(y1 + pad))

            draw.rectangle([bx0, by0, bx1, by1], fill=(247, 245, 238, 255), outline=(47, 125, 79, 255), width=2)
            draw.text((bx0 + 4, by0 + 2), lbl.get("text", ""), fill=(38, 103, 65, 255))
            draw.text((bx0 + 4, max(by0, by1 - 12)), "corrected", fill=(47, 125, 79, 255))

        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=90)
        return Response(content=buf.getvalue(), media_type="image/jpeg")


@app.get("/api/replaced/{name}/download")
def download_replaced_image(name: str):
    """Download replaced drawing image."""
    resp = get_replaced_image(name)
    ext = "png" if resp.media_type == "image/png" else "jpg"
    return Response(
        content=resp.body,
        media_type=resp.media_type,
        headers={"Content-Disposition": f'attachment; filename="{name}_replaced.{ext}"'}
    )


# -----------------------------------------------------------------------------
# Report Export Endpoint (PART 7)
# -----------------------------------------------------------------------------
@app.get("/api/report/{name}")
def get_report(name: str):
    """Generate a self-contained printable HTML report with print styles."""
    if name not in WORKING_COPIES:
        raise HTTPException(status_code=404, detail=f"Plan '{name}' not found")
    manifest = next((m for m in ALL_PLANS_MANIFEST if m["id"] == name), None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Manifest for '{name}' not found")

    plan_data = get_plan_data(name)
    working = WORKING_COPIES[name]
    history = working.get("change_history", [])

    conflicts_html = ""
    if plan_data.get("conflicts"):
        for cf in plan_data["conflicts"]:
            conflicts_html += f"<li><strong>{cf.get('sentence', '')}</strong> (Gap: {cf.get('gap_formatted', '')}, Excess: {cf.get('excess_formatted', '')})</li>"
    else:
        conflicts_html = "<li>No active conflicts. All constraints satisfied within tolerance.</li>"

    changes_html = ""
    if history:
        for h in history:
            changes_html += f"<tr><td>{h.get('label_name', h['label_id'])}</td><td><code>{h['old_text']}</code></td><td><code>{h['new_text']}</code></td><td>{h.get('source', 'user')}</td></tr>"
    else:
        changes_html = "<tr><td colspan='4'>No replacements made to original values.</td></tr>"

    rooms_html = ""
    for r in plan_data.get("areas", {}).get("rooms", []):
        rooms_html += f"<tr><td>{r.get('room_name')}</td><td>{r.get('dimensions_formatted')}</td><td>{r.get('area_sq_ft', '-')}</td><td>{r.get('area_sq_m', '-')}</td></tr>"

    cov = plan_data.get("coverage", {})
    cov_text = f"{cov.get('labels_in_constraints', 0)} of {cov.get('total_labels', 0)} values verified"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Verification Report - {plan_data['title']}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif; margin: 40px; color: #1F2A37; background: #FFFFFF; line-height: 1.5; }}
    h1, h2, h3 {{ color: #1F4E5F; margin-top: 24px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
    th, td {{ border: 1px solid #E2DCCF; padding: 8px 12px; text-align: left; font-size: 13px; }}
    th {{ background: #EFEBE3; font-weight: 600; }}
    .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; background: #EFEBE3; }}
    .badge-ok {{ background: #E3F1E8; color: #266741; }}
    .card {{ border: 1px solid #E2DCCF; border-radius: 6px; padding: 16px; margin: 16px 0; background: #FAF9F5; }}
    .note {{ background: #FFFBEB; border-left: 4px solid #F59E0B; padding: 12px; margin: 16px 0; font-size: 12px; }}
    @media print {{
      body {{ margin: 10mm; font-size: 12pt; }}
      .no-print {{ display: none; }}
      @page {{ margin: 12mm; }}
    }}
  </style>
</head>
<body>
  <div class="no-print" style="margin-bottom: 20px;">
    <button onclick="window.print()" style="padding: 8px 16px; background: #1F4E5F; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-weight: 600;">Print or Save as PDF</button>
  </div>
  <h1>Plan Verification &amp; Audit Report</h1>
  <p><strong>Plan:</strong> {plan_data['title']} ({plan_data['kind']}) | <strong>Unit:</strong> {plan_data['unit']}</p>

  <div class="card">
    <h3>Coverage &amp; Status</h3>
    <p>Coverage: <span class="badge badge-ok">{cov_text}</span></p>
    <p>Active conflicts: <strong>{cov.get('conflicts', 0)}</strong></p>
  </div>

  <h2>Discrepancies &amp; Constraints</h2>
  <ul>{conflicts_html}</ul>

  <h2>Accepted Replacements</h2>
  <table>
    <thead><tr><th>Label</th><th>Original</th><th>Confirmed</th><th>Source</th></tr></thead>
    <tbody>{changes_html}</tbody>
  </table>

  <h2>Room Areas &amp; Boundary Envelopes</h2>
  <table>
    <thead><tr><th>Room</th><th>Dimensions</th><th>Area (sq ft)</th><th>Area (sq m)</th></tr></thead>
    <tbody>{rooms_html}</tbody>
  </table>
  <p><strong>Sum of rooms:</strong> {plan_data.get('areas', {}).get('sum_of_rooms_read_sq_ft', '-')} sq ft ({plan_data.get('areas', {}).get('sum_of_rooms_read_sq_m', '-')} sq m) | <strong>Stated envelope:</strong> {plan_data.get('areas', {}).get('stated_envelope_sq_ft', '-')} sq ft</p>

  <h2>Hard Limitations</h2>
  <div class="note">
    Labels with no redundancy cannot be checked. Errors smaller than the wall allowance are not flagged. Two errors that cancel out are not detected. Real plans are read by a small local model, so unverified values need a human look.
  </div>
</body>
</html>"""
    return Response(content=html, media_type="text/html")



# -----------------------------------------------------------------------------
# Frontend Static Routing
# -----------------------------------------------------------------------------
@app.get("/app")
@app.get("/workspace")
def get_app_page():
    return FileResponse(FRONTEND_DIR / "app.html")


# Mount frontend folder at root for static assets and index.html
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
