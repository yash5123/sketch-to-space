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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image

import checks
import config
from geometry import Box
from parse import format_mm, infer_unit, parse_label
from pipeline import run_plan

ROOT_DIR = BACKEND_DIR.parent
SKETCHES_DIR = ROOT_DIR / "sketches"
RESULTS_DIR = ROOT_DIR / "results"
SYNTHETIC_DIR = ROOT_DIR / "synthetic"
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
        "title": "Synth 008 - Metric 6-Room Layout",
        "kind": "synthetic",
        "description": "Synthetic architectural layout with injected room width mismatch (4.129 m vs 4.729 m).",
        "status_chip": "conflict found",
        "thumbnail_url": "/static/sketches/synth/synth_008.png",
        "file_type": "synth_meta",
        "file_path": str(SYNTHETIC_DIR / "synth_008_meta.json"),
        "image_file": str(SKETCHES_DIR / "synth" / "synth_008.png"),
        "image_url": "/static/sketches/synth/synth_008.png",
    },
    {
        "id": "test6",
        "title": "Test 6 - 2-Room Studio",
        "kind": "real",
        "description": "Real sketch with unit-ambiguous notation (7'x4\" and 7'x5\" bathroom labels).",
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
            suggs_out.append({
                "label_id": s.get("label_id"),
                "old_text": s.get("old_text"),
                "new_text": s.get("new_text"),
                "cost": s.get("cost", 1.0),
                "support": s.get("support", 1),
                "ocr_verified": s.get("ocr_verified", False),
                "tag": s.get("status", "complete"),
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
        label_records.append({
            "id": lid,
            "text": txt,
            "mm": mms,
            "status": "verified",
            "support": 1,
            "sources": ["synthetic"],
            "box": None,
            "box_ok": False,
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
        c_suggs = [
            {
                "label_id": s.label_id,
                "old_text": s.old_text,
                "new_text": s.new_text,
                "cost": s.cost,
                "support": s.support,
                "ocr_verified": s.ocr_verified,
                "tag": s.status,
                "note": s.details or s.hypothesis,
                "delta_formatted": format_mm(s.delta_mm, unit),
            }
            for s in suggs
            if s.label_id in suspects
        ]
        conflicts_out.append({
            "constraint_id": c.id,
            "sentence": f"Mismatch in {c.id}: {labels_dict[c.total].text} vs {', '.join(labels_dict[p].text for p in c.parts)} (difference {format_mm(abs(v.gap_mm), unit)})",
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


class ConfirmRequest(BaseModel):
    name: str
    label_id: str
    new_text: str


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

    after_plan = get_plan_data(name)
    after_conflicts = len(after_plan["conflicts"])

    return {
        "status": "success",
        "before_conflicts": before_conflicts,
        "after_conflicts": after_conflicts,
        "changed_labels": [{"id": req.label_id, "old_text": old_text, "new_text": req.new_text}],
        "remaining_conflicts": after_plan["conflicts"],
        "plan": after_plan,
    }


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
    }


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

    # ViewBox coordinate system
    vb_w, vb_h = 960, 840
    margin_l, margin_t = 120, 110
    grid_w = vb_w - margin_l - 120
    grid_h = vb_h - margin_t - 110

    scale_x = grid_w / max(1, total_w)
    scale_y = grid_h / max(1, total_h)

    # Check for conflicts on rooms
    conflict_labels = {cf["suspect_label_ids"][0] for cf in plan_data.get("conflicts", []) if cf.get("suspect_label_ids")}

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w} {vb_h}" width="100%" height="100%" style="background:#FFFFFF; font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif;">',
        '<defs>',
        '  <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">',
        '    <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#EFEBE3" stroke-width="1"/>',
        '  </pattern>',
        '</defs>',
        f'<rect width="{vb_w}" height="{vb_h}" fill="url(#grid)" />',
        # Title header
        f'<text x="{margin_l}" y="48" fill="#1F4E5F" font-size="16" font-weight="700" letter-spacing="0.5">ARCHITECTURAL CAD SCHEMATIC - {plan_data["title"].upper()}</text>',
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

        if is_changed:
            border_col = "#2F7D4F"
            fill_col = "#E3F1E8"
            dim_fill = "#E3F1E8"
            dim_text_col = "#266741"
            badge_tag = ' <tspan fill="#266741" font-weight="700">[VERIFIED]</tspan>'
        elif is_conflict:
            border_col = "#B3261E"
            fill_col = "#FBE4E1"
            dim_fill = "#FBE4E1"
            dim_text_col = "#B3261E"
            badge_tag = ' <tspan fill="#B3261E" font-weight="700">[MISMATCH]</tspan>'
        else:
            border_col = "#1F2A37"
            fill_col = "#FFFFFF"
            dim_fill = "#EFEBE3"
            dim_text_col = "#1F2A37"
            badge_tag = ''

        # Room rectangle
        svg_parts.append(f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{rw:.1f}" height="{rh:.1f}" fill="{fill_col}" stroke="{border_col}" stroke-width="2" rx="3"/>')

        # Room text
        cx = rx + rw / 2.0
        cy = ry + rh / 2.0
        room_name = r["name"]
        w_txt = f"{r['w']/1000.0:.3f} m" if unit == "metres" else f"{r['w']/24.0:.1f}'"
        d_txt = f"{r['d']/1000.0:.3f} m" if unit == "metres" else f"{r['d']/24.0:.1f}'"
        dim_str = f"{w_txt} x {d_txt}"

        svg_parts.append(f'<text x="{cx:.1f}" y="{cy - 10:.1f}" fill="#1F2A37" font-size="13" font-weight="600" text-anchor="middle">{room_name}{badge_tag}</text>')
        # Dimension pill badge on surface-alt
        pill_w = max(110, len(dim_str) * 8 + 16)
        svg_parts.append(f'<rect x="{cx - pill_w/2:.1f}" y="{cy + 2:.1f}" width="{pill_w}" height="22" rx="4" fill="{dim_fill}" stroke="{border_col}" stroke-width="1"/>')
        svg_parts.append(f'<text x="{cx:.1f}" y="{cy + 17:.1f}" fill="{dim_text_col}" font-size="12" font-family="monospace" font-weight="600" text-anchor="middle">{dim_str}</text>')

    # Outer dimensions
    # Top overall
    top_y = margin_t - 28
    svg_parts.append(f'<line x1="{margin_l}" y1="{top_y}" x2="{margin_l + grid_w}" y2="{top_y}" stroke="#1F2A37" stroke-width="1.5"/>')
    svg_parts.append(f'<line x1="{margin_l}" y1="{top_y-6}" x2="{margin_l}" y2="{top_y+6}" stroke="#1F2A37" stroke-width="1.5"/>')
    svg_parts.append(f'<line x1="{margin_l + grid_w}" y1="{top_y-6}" x2="{margin_l + grid_w}" y2="{top_y+6}" stroke="#1F2A37" stroke-width="1.5"/>')
    tot_w_txt = f"{total_w/1000.0:.3f} m" if unit == "metres" else f"{total_w/24.0:.1f}'"
    svg_parts.append(f'<rect x="{margin_l + grid_w/2 - 40:.1f}" y="{top_y - 20}" width="80" height="18" rx="3" fill="#EFEBE3" stroke="#E2DCCF"/>')
    svg_parts.append(f'<text x="{margin_l + grid_w/2}" y="{top_y - 7}" fill="#1F2A37" font-size="12" font-family="monospace" font-weight="600" text-anchor="middle">{tot_w_txt}</text>')

    # Left overall
    left_x = margin_l - 28
    svg_parts.append(f'<line x1="{left_x}" y1="{margin_t}" x2="{left_x}" y2="{margin_t + grid_h}" stroke="#1F2A37" stroke-width="1.5"/>')
    svg_parts.append(f'<line x1="{left_x-6}" y1="{margin_t}" x2="{left_x+6}" y2="{margin_t}" stroke="#1F2A37" stroke-width="1.5"/>')
    svg_parts.append(f'<line x1="{left_x-6}" y1="{margin_t + grid_h}" x2="{left_x+6}" y2="{margin_t + grid_h}" stroke="#1F2A37" stroke-width="1.5"/>')
    tot_h_txt = f"{total_h/1000.0:.3f} m" if unit == "metres" else f"{total_h/24.0:.1f}'"
    svg_parts.append(f'<text x="{left_x - 12}" y="{margin_t + grid_h/2}" fill="#1F2A37" font-size="12" font-family="monospace" font-weight="600" text-anchor="middle" transform="rotate(-90, {left_x - 12}, {margin_t + grid_h/2})">{tot_h_txt}</text>')

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
        # Prominent caption banner
        f'<rect x="20" y="16" width="{vb_w - 40}" height="42" fill="#EFEBE3" rx="6" stroke="#E2DCCF" stroke-width="1.5"/>',
        f'<text x="{vb_w/2}" y="42" fill="#1F2A37" font-size="13" font-weight="600" text-anchor="middle">Schematic only. Layout and positions are not read from the photo.</text>',
    ]

    if not rooms:
        svg_parts.append(f'<text x="{vb_w/2}" y="{vb_h/2}" fill="#5B6675" font-size="15" text-anchor="middle">No room dimensions available to render schematic.</text>')
        svg_parts.append('</svg>')
        return "\n".join(svg_parts)

    # Lay out room boxes in proportional rows
    padding = 24
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

        # Proportional box width
        box_w = max(180, min(260, int(150 + sq_ft * 0.45)))
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
            dim_bg = "#E3F1E8"
            dim_tc = "#266741"
            badge_text = ' <tspan fill="#266741" font-size="11" font-weight="700">[CORRECTED]</tspan>'
        elif is_ambig:
            border_c = "#C2570C"
            bg_c = "#FDE9D9"
            dim_bg = "#FDE9D9"
            dim_tc = "#A14506"
            badge_text = ' <tspan fill="#A14506" font-size="11" font-weight="700">[AMBIGUOUS UNIT]</tspan>'
        else:
            border_c = "#E2DCCF"
            bg_c = "#FFFFFF"
            dim_bg = "#EFEBE3"
            dim_tc = "#1F2A37"
            badge_text = ''

        svg_parts.append(f'<g transform="translate({curr_x}, {curr_y})">')
        svg_parts.append(f'  <rect width="{box_w}" height="{box_h}" fill="{bg_c}" stroke="{border_c}" stroke-width="1.5" rx="6"/>')
        svg_parts.append(f'  <text x="14" y="24" fill="#1F2A37" font-size="13" font-weight="600">{r_name}{badge_text}</text>')
        # Dimension pill
        pill_w = min(box_w - 28, max(80, len(dim_str) * 8 + 14))
        svg_parts.append(f'  <rect x="14" y="32" width="{pill_w}" height="20" rx="3" fill="{dim_bg}" stroke="{border_c}" stroke-width="1"/>')
        svg_parts.append(f'  <text x="{14 + pill_w/2}" y="46" fill="{dim_tc}" font-size="12" font-family="monospace" font-weight="600" text-anchor="middle">{dim_str}</text>')
        if sq_ft > 0:
            svg_parts.append(f'  <text x="14" y="72" fill="#5B6675" font-size="11">{sq_ft:.1f} sq ft ({sq_m:.1f} sq m)</text>')
        svg_parts.append('</g>')

        curr_x += box_w + 16

    # Envelope summary badge at bottom
    env_ft = plan_data.get("areas", {}).get("stated_envelope_sq_ft", "N/A")
    sum_ft = plan_data.get("areas", {}).get("sum_of_rooms_read_sq_ft", "N/A")
    flag = plan_data.get("areas", {}).get("envelope_flag", "")
    badge_fill = "#B3261E" if ">100%" in flag else "#2F7D4F"

    svg_parts.append(f'<rect x="40" y="{vb_h - 45}" width="{vb_w - 80}" height="32" fill="#EFEBE3" rx="4" stroke="#E2DCCF"/>')
    svg_parts.append(f'<text x="56" y="{vb_h - 24}" fill="#1F2A37" font-size="12">Total Rooms Area: <tspan font-family="monospace" font-weight="600" fill="#1F2A37">{sum_ft} sq ft</tspan> | Stated Envelope: <tspan font-family="monospace" font-weight="600" fill="#1F2A37">{env_ft} sq ft</tspan> | Status: <tspan fill="{badge_fill}" font-weight="700">{flag}</tspan></text>')

    svg_parts.append('</svg>')
    return "\n".join(svg_parts)


# -----------------------------------------------------------------------------
# Frontend Static Routing
# -----------------------------------------------------------------------------
# Mount frontend folder at root for static assets and index.html
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
