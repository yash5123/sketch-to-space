"""Multi-pass floor plan reader.

Coordinates five targeted visual passes:
  1. Pass F: Full-page overview (room sizes, overall layout).
  2. Pass E: Edge strips (left and right), rotated and scaled for vertical chains.
             Automatically detects orientation (CW vs CCW) via strict hierarchy:
               a) reject readings with multiple dimensions per item;
               b) prefer reading with more parseable single-value items;
               c) prefer sum-to-overall within 6 inches (1 sub-dimension excluded);
               d) prefer higher agreement with Pass F.
  3. Pass H: Horizontal bands (top 0-35% and bottom 65-100%), scaled 1.5x.
  4. Merge:  Deduplicates by (parsed mm, position cell, direction, applies_to),
             tracks source agreement, and lets strip values override Pass F for vertical edge chains.
"""

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageOps

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Ensure sufficient prediction tokens and context for multi-label JSON
os.environ.setdefault("READER_NUM_PREDICT", "2500")
os.environ.setdefault("READER_NUM_CTX", "4096")

import config
import floorplan_reader_v2
import ollama_client
from parse import format_mm, infer_unit, parse_label, _normalise
from dedupe import band_compatible, collapse_fragments
from ocr_engine import has_dimension_feature

STRIP_PROMPT = (
    "Transcribe every dimension value on this strip in reading order. "
    "If the strip has no dimension text, return an empty list. "
    "One value per list item. Never join values with dashes. "
    "Copy exactly, keep ' and \" marks. Return JSON only."
)

STRIP_SCHEMA = {
    "type": "object",
    "properties": {
        "values": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text_as_written": {"type": "string"},
                    "legibility": {
                        "type": "string",
                        "enum": ["clear", "partly_unclear", "hard_to_read"],
                    },
                },
                "required": ["text_as_written", "legibility"],
            },
        }
    },
    "required": ["values"],
}


# ---------------------------------------------------------------------------
# Image cropping helpers
# ---------------------------------------------------------------------------

def _load_oriented_image(image_path: str) -> Image.Image:
    with Image.open(image_path) as raw:
        img = ImageOps.exif_transpose(raw)
        if img is None:
            img = raw.copy()
        return img.convert("RGB")


def find_content_bbox(
    img: Image.Image,
    border_pct: float = 0.02,
    thresh: float = 25.0,
) -> tuple[int, int, int, int]:
    """Compute the plan content bounding box (threshold non-background pixels, ignore a 2% border)."""
    import numpy as np

    w, h = img.size
    bx = max(1, int(w * border_pct))
    by = max(1, int(h * border_pct))

    gray = np.array(img.convert("L"))
    inner = gray[by : h - by, bx : w - bx]

    corner_h = min(20, inner.shape[0])
    corner_w = min(20, inner.shape[1])
    corners = [
        inner[:corner_h, :corner_w],
        inner[:corner_h, -corner_w:],
        inner[-corner_h:, :corner_w],
        inner[-corner_h:, -corner_w:],
    ]
    bg_est = float(np.median([np.median(c) for c in corners]))

    if bg_est > 128:
        mask = (bg_est - inner) > thresh
    else:
        mask = (inner - bg_est) > thresh

    if not np.any(mask):
        return (0, 0, w, h)

    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)

    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]

    x0 = max(0, int(cmin + bx))
    y0 = max(0, int(rmin + by))
    x1 = min(w, int(cmax + bx + 1))
    y1 = min(h, int(rmax + by + 1))
    return (x0, y0, x1, y1)


def _crop_strip(
    img: Image.Image,
    side: str,
    strip_frac: float = 0.15,
    bbox: Optional[tuple[int, int, int, int]] = None,
) -> Image.Image:
    if bbox is None:
        bbox = find_content_bbox(img)
    x0, y0, x1, y1 = bbox
    box_w = max(1, x1 - x0)
    strip_w = max(1, int(box_w * strip_frac))

    if side == "left":
        box = (x0, y0, min(img.width, x0 + strip_w), y1)
    elif side == "right":
        box = (max(0, x1 - strip_w), y0, x1, y1)
    else:
        raise ValueError(f"Unknown side: {side}")
    return img.crop(box)


def _crop_band(
    img: Image.Image,
    y_start: float,
    y_end: float,
    bbox: Optional[tuple[int, int, int, int]] = None,
) -> Image.Image:
    if bbox is not None:
        x0, y0, x1, y1 = bbox
        box_h = max(1, y1 - y0)
        b_y0 = int(y0 + box_h * y_start)
        b_y1 = int(y0 + box_h * y_end)
        return img.crop((x0, b_y0, x1, b_y1))
    w, h = img.size
    box = (0, int(h * y_start), w, int(h * y_end))
    return img.crop(box)


# ---------------------------------------------------------------------------
# Pass F: Full-Page Call
# ---------------------------------------------------------------------------

def run_pass_full(image_path: str) -> dict:
    result = ollama_client.chat(
        floorplan_reader_v2.USER_PROMPT,
        image_path=image_path,
        json_schema=floorplan_reader_v2.PLAN_SCHEMA,
        system=floorplan_reader_v2.SYSTEM_PROMPT,
    )
    try:
        return json.loads(result.text)
    except json.JSONDecodeError:
        text = result.text.strip()
        for fix in (text + '"]}]}', text + '"}]}', text + '}]}', text + '}'):
            try:
                return json.loads(fix)
            except json.JSONDecodeError:
                pass
        raise


# ---------------------------------------------------------------------------
# Pass E: Edge Strips (with automatic orientation selection)
# ---------------------------------------------------------------------------

def _eval_chain_sum(mms: list[float], tolerance_mm: float = config.CHAIN_TOLERANCE_MM) -> tuple[bool, float, float]:
    """Check if non-overall values sum to the largest value within tolerance (6 inches),
    allowing at most one nested sub-dimension to be excluded.
    Returns (matches, sum_used, overall).
    """
    if len(mms) < 2:
        return False, 0.0, 0.0

    sorted_mms = sorted(mms)
    overall = sorted_mms[-1]
    segments = sorted_mms[:-1]
    total_seg = sum(segments)

    # 1. Direct sum
    if abs(total_seg - overall) <= tolerance_mm:
        return True, total_seg, overall

    # 2. Sum with exactly one nested sub-dimension excluded
    for seg in segments:
        sub_sum = total_seg - seg
        if abs(sub_sum - overall) <= tolerance_mm:
            return True, sub_sum, overall

    return False, total_seg, overall


def _has_multiple_dims(text: str) -> bool:
    """Check if a string contains multiple joined dimensions."""
    norm = _normalise(text)
    if re.search(r'"\s*-\s*\d', norm):
        return True
    if re.search(r'(?<=")\s*-\s*(?=\d)', norm):
        return True
    if norm.count("'") > 1 or norm.count('"') > 1:
        return True
    return False


def _split_fallback_items(raw_items: list[dict], unit: str) -> list[dict]:
    """Fallback split for items joined with dashes."""
    split_items = []
    for it in raw_items:
        text = it.get("text_as_written", "")
        if re.search(r'(?<=")\s*-\s*(?=\d)', text):
            parts = re.split(r'(?<=")\s*-\s*(?=\d)', text)
            for p in parts:
                p_clean = p.strip()
                if p_clean:
                    new_it = dict(it)
                    new_it["text_as_written"] = p_clean
                    new_it["legibility"] = "partly_unclear"
                    new_it["source"] = "split_fallback"
                    try:
                        new_it["mm"] = parse_label(p_clean, unit)
                    except Exception:
                        new_it["mm"] = []
                    split_items.append(new_it)
        else:
            new_it = dict(it)
            try:
                new_it["mm"] = parse_label(text, unit)
            except Exception:
                new_it["mm"] = []
            split_items.append(new_it)
    return split_items


def run_pass_strip(
    img: Image.Image,
    side: str,
    unit: str,
    pass_f_items: list[dict],
    strip_frac: float = 0.15,
    scale: float = 2.0,
    bbox: Optional[tuple[int, int, int, int]] = None,
) -> tuple[list[dict], str, dict, dict]:
    """Crop left or right strip, run both CW and CCW rotations, and select the best orientation.

    Selection hierarchy (applied in order):
      a) reject a reading if any single item holds more than one dimension;
      b) prefer the reading with more parseable single-value items;
      c) prefer sum-to-overall within 6 inches (1 sub-dimension excluded);
      d) prefer higher agreement with Pass F.
    """
    strip = _crop_strip(img, side, strip_frac, bbox=bbox)
    os.makedirs("results", exist_ok=True)

    # Physical rotation:
    # cw: -90 degrees in PIL (90 clockwise)
    # ccw: 90 degrees in PIL (90 counter-clockwise)
    rotations = {
        "cw": strip.rotate(-90, expand=True),
        "ccw": strip.rotate(90, expand=True),
    }

    candidates: dict[str, dict] = {}
    raw_unmerged: dict[str, list[dict]] = {}

    for rot_name, rot_img in rotations.items():
        if scale != 1.0:
            new_w = round(rot_img.width * scale)
            new_h = round(rot_img.height * scale)
            rot_img = rot_img.resize((new_w, new_h), Image.LANCZOS)

        tmp_path = f"results/strip_{side}_{rot_name}.png"
        rot_img.save(tmp_path)

        res = ollama_client.chat(
            STRIP_PROMPT,
            image_path=tmp_path,
            json_schema=STRIP_SCHEMA,
            system="You read architectural floor plan dimension strips. Transcribe exactly as written.",
        )
        try:
            data = json.loads(res.text)
            raw_items = data.get("values", [])
        except json.JSONDecodeError:
            raw_items = []

        raw_unmerged[rot_name] = list(raw_items)

    chosen_items, chosen_rot, scores = select_strip_orientation(
        raw_unmerged.get("cw", []),
        raw_unmerged.get("ccw", []),
        side,
        unit,
        pass_f_items,
    )
    return chosen_items, chosen_rot, scores, raw_unmerged


def select_strip_orientation(
    raw_cw: list[dict],
    raw_ccw: list[dict],
    side: str,
    unit: str,
    pass_f_items: list[dict],
) -> tuple[list[dict], str, dict]:
    """Select the best orientation for a dimension strip (CW vs CCW) via strict hierarchy:
      a) reject a reading if any single item holds more than one dimension;
      b) prefer the reading with more parseable single-value items;
      c) then the existing sum-to-overall rule (allow excluding one nested value);
      d) then agreement with Pass F.
    """
    rotations = {"cw": raw_cw, "ccw": raw_ccw}
    candidates: dict[str, dict] = {}

    for rot_name, raw_items in rotations.items():
        has_multi = any(_has_multiple_dims(it.get("text_as_written", "")) for it in raw_items)

        # Parse valid single-value items
        valid_single_items = []
        parsed_mms = []
        for it in raw_items:
            raw_text = it.get("text_as_written", "")
            if not _has_multiple_dims(raw_text):
                try:
                    mms = parse_label(raw_text, unit)
                    if mms and all(floorplan_reader_v2.MIN_MM <= v <= floorplan_reader_v2.MAX_MM for v in mms):
                        parsed_mms.extend(mms)
                        valid_single_items.append({**it, "mm": mms})
                except Exception:
                    pass

        # Check c: sum to overall
        sum_ok, sum_val, overall_val = _eval_chain_sum(parsed_mms)

        # Check d: agreement with Pass F
        pass_f_mms = [v for it in pass_f_items for v in it.get("mm", [])]
        agree_count = sum(
            1 for m in parsed_mms
            if any(abs(m - f_val) <= 1.0 for f_val in pass_f_mms)
        )

        candidates[rot_name] = {
            "raw_items": raw_items,
            "has_multi": has_multi,
            "items": valid_single_items,
            "single_val_count": len(valid_single_items),
            "parsed_mms": parsed_mms,
            "sum_ok": sum_ok,
            "sum_val": sum_val,
            "overall_val": overall_val,
            "agree_count": agree_count,
        }

    cw_c = candidates["cw"]
    ccw_c = candidates["ccw"]

    scores = {
        "cw": {
            "has_multi": cw_c["has_multi"],
            "single_val_count": cw_c["single_val_count"],
            "sum_ok": cw_c["sum_ok"],
            "agree_count": cw_c["agree_count"],
        },
        "ccw": {
            "has_multi": ccw_c["has_multi"],
            "single_val_count": ccw_c["single_val_count"],
            "sum_ok": ccw_c["sum_ok"],
            "agree_count": ccw_c["agree_count"],
        },
    }

    # Step a: reject if any single item holds more than one dimension
    cw_valid_a = not cw_c["has_multi"] and cw_c["single_val_count"] > 0
    ccw_valid_a = not ccw_c["has_multi"] and ccw_c["single_val_count"] > 0

    chosen_rot: str
    if cw_valid_a and not ccw_valid_a:
        chosen_rot = "cw"
    elif ccw_valid_a and not cw_valid_a:
        chosen_rot = "ccw"
    elif cw_valid_a and ccw_valid_a:
        # Step b: prefer reading with more parseable single-value items
        if cw_c["single_val_count"] != ccw_c["single_val_count"]:
            chosen_rot = "cw" if cw_c["single_val_count"] > ccw_c["single_val_count"] else "ccw"
        # Step c: sum to overall
        elif cw_c["sum_ok"] and not ccw_c["sum_ok"]:
            chosen_rot = "cw"
        elif ccw_c["sum_ok"] and not cw_c["sum_ok"]:
            chosen_rot = "ccw"
        # Step d: agreement with Pass F
        elif cw_c["agree_count"] != ccw_c["agree_count"]:
            chosen_rot = "cw" if cw_c["agree_count"] > ccw_c["agree_count"] else "ccw"
        else:
            chosen_rot = "ccw" if side == "left" else "cw"
    else:
        chosen_rot = "uncertain"

    # Log orientation scoring
    print(f"  [{side.upper()} Strip Orientation Scoring]")
    print(f"    CW : single={cw_c['single_val_count']}, multi_rejected={cw_c['has_multi']}, sum_ok={cw_c['sum_ok']}, agree_F={cw_c['agree_count']}")
    print(f"    CCW: single={ccw_c['single_val_count']}, multi_rejected={ccw_c['has_multi']}, sum_ok={ccw_c['sum_ok']}, agree_F={ccw_c['agree_count']}")
    print(f"    => Chosen: {chosen_rot}")

    if chosen_rot in ("cw", "ccw"):
        chosen_cand = candidates[chosen_rot]
        # If chosen reading had multiple dimensions (fallback needed), apply split fallback
        if chosen_cand["has_multi"]:
            chosen_items = _split_fallback_items(chosen_cand["raw_items"], unit)
        else:
            chosen_items = list(chosen_cand["items"])

        # Reading order after cw is bottom-to-top: reverse so chain index runs top-to-bottom
        if chosen_rot == "cw":
            chosen_items.reverse()

        chain_mms = []
        for it in chosen_items:
            mms = it.get("mm")
            if not mms:
                try:
                    mms = parse_label(it.get("text_as_written", ""), unit)
                except Exception:
                    mms = []
                it["mm"] = mms
            if mms and len(mms) == 1:
                chain_mms.append(mms[0])

        sum_ok, _, overall_val = _eval_chain_sum(chain_mms) if len(chain_mms) >= 2 else (False, 0.0, 0.0)

        overall_marked = False
        for idx, it in enumerate(chosen_items):
            it["chain_index"] = idx
            it["chain_side"] = side
            it["direction"] = "vertical"
            it["position"] = "middle-left" if side == "left" else "middle-right"
            it["applies_to"] = f"{side} vertical chain #{idx+1}"
            it_mm = it.get("mm")
            if (
                sum_ok
                and not overall_marked
                and it_mm
                and len(it_mm) == 1
                and abs(it_mm[0] - overall_val) <= 1.0
            ):
                it["kind"] = "overall"
                overall_marked = True
            else:
                it["kind"] = "segment"

        return chosen_items, chosen_rot, scores

    # Uncertain fallback: no valid chain detected
    return [], "uncertain", scores


# ---------------------------------------------------------------------------
# Pass H: Horizontal Bands
# ---------------------------------------------------------------------------

def run_pass_band(
    img: Image.Image,
    band_name: str,
    y_start: float,
    y_end: float,
    scale: float = 1.5,
    bbox: Optional[tuple[int, int, int, int]] = None,
) -> list[dict]:
    band = _crop_band(img, y_start, y_end, bbox=bbox)
    if scale != 1.0:
        new_w = round(band.width * scale)
        new_h = round(band.height * scale)
        band = band.resize((new_w, new_h), Image.LANCZOS)

    tmp_path = f"results/band_{band_name}.png"
    band.save(tmp_path)

    res = ollama_client.chat(
        floorplan_reader_v2.USER_PROMPT,
        image_path=tmp_path,
        json_schema=floorplan_reader_v2.PLAN_SCHEMA,
        system=floorplan_reader_v2.SYSTEM_PROMPT,
    )
    try:
        data = json.loads(res.text)
        return data.get("dimensions", [])
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------------------
# Merge: Deduplicate by (parsed mm, position cell, direction, kind)
# ---------------------------------------------------------------------------

def _is_seen_near_edge(
    strip_item: dict,
    side: str,
    other_items: list[dict],
    tol_mm: float = 1.0,
) -> bool:
    """Check if Pass F or horizontal bands saw this dimension value near the side edge."""
    strip_mms = strip_item.get("mm", [])
    if not strip_mms:
        return False
    strip_val = strip_mms[0]
    for it in other_items:
        pos = it.get("position", "").lower()
        if side in pos:
            for m in it.get("mm", []):
                if abs(m - strip_val) <= tol_mm:
                    return True
    return False


def _make_key(item: dict) -> tuple:
    mm_list = item.get("mm", [])
    mm_key = tuple(round(v, 0) for v in mm_list) if mm_list else (item.get("text_as_written", ""),)
    pos_cell = item.get("position", "center")
    direction = item.get("direction", "none")
    kind = item.get("kind", "segment")
    if "chain_index" in item:
        return (mm_key, pos_cell, direction, kind, item["chain_index"])
    return (mm_key, pos_cell, direction, kind)


def merge_passes(
    pass_f_plan: dict,
    strip_left_items: list[dict],
    strip_right_items: list[dict],
    band_top_items: list[dict],
    band_bottom_items: list[dict],
    ocr_detections: Optional[list[dict]] = None,
) -> dict:
    unit = pass_f_plan.get("plan_unit", "feet_inches")
    f_items = floorplan_reader_v2.plan_values(pass_f_plan)

    ocr_dets = list(ocr_detections or [])
    for idx, d in enumerate(ocr_dets):
        d["idx"] = idx
        if "mms" not in d:
            try:
                d["mms"] = parse_label(d["text"], unit)
            except Exception:
                d["mms"] = []

    claimed_dets: set[int] = set()

    def match_item_to_ocr(item: dict) -> Optional[dict]:
        if not ocr_dets:
            return None
        txt = item.get("text_as_written", "")
        from ocr_engine import find_matching_ocr_boxes
        # First try unclaimed boxes
        avail = [d for d in ocr_dets if d["idx"] not in claimed_dets]
        m = find_matching_ocr_boxes(txt, avail, unit)
        if m:
            m.sort(key=lambda x: x["confidence"], reverse=True)
            return m[0]
        # Fallback to all boxes
        all_m = find_matching_ocr_boxes(txt, ocr_dets, unit)
        if all_m:
            all_m.sort(key=lambda x: x["confidence"], reverse=True)
            return all_m[0]
        return None

    registry: list[dict] = []

    # 1. Add Strip items (Left and Right) with edge verification
    other_edge_items = f_items + band_top_items + band_bottom_items

    for it in strip_left_items:
        it = dict(it)
        if not _is_seen_near_edge(it, "left", other_edge_items):
            it["legibility"] = "partly_unclear"
            it["source"] = "strip_only"
            src = "strip_only"
        else:
            src = "E_left"
        it["sources"] = [src]
        det = match_item_to_ocr(it)
        if det:
            it["ocr_verified"] = True
            it["ocr_box"] = det["box"]
            it["ocr_confidence"] = det["confidence"]
            claimed_dets.add(det["idx"])
        else:
            it["ocr_verified"] = False
        registry.append(it)

    for it in strip_right_items:
        it = dict(it)
        if not _is_seen_near_edge(it, "right", other_edge_items):
            it["legibility"] = "partly_unclear"
            it["source"] = "strip_only"
            src = "strip_only"
        else:
            src = "E_right"
        it["sources"] = [src]
        det = match_item_to_ocr(it)
        if det:
            it["ocr_verified"] = True
            it["ocr_box"] = det["box"]
            it["ocr_confidence"] = det["confidence"]
            claimed_dets.add(det["idx"])
        else:
            it["ocr_verified"] = False
        registry.append(it)

    # 2. Add Pass F items
    for it in f_items:
        it = dict(it)
        txt = it.get("text_as_written", "")
        mms = it.get("mm", [])
        if not mms:
            try:
                mms = parse_label(txt, unit)
            except Exception:
                mms = []
            it["mm"] = mms

        # Check if already captured by chain item
        matched_chain = False
        if it.get("direction") == "vertical" and ("left" in it.get("position", "") or "right" in it.get("position", "")):
            for reg in registry:
                if reg.get("direction") == "vertical" and reg.get("mm") == mms:
                    if "F" not in reg["sources"]:
                        reg["sources"].append("F")
                    reg["agree"] = True
                    matched_chain = True
                    break
        if matched_chain:
            continue

        det = match_item_to_ocr(it)
        it["sources"] = ["F"]
        if det:
            it["ocr_verified"] = True
            it["ocr_box"] = det["box"]
            it["ocr_confidence"] = det["confidence"]
            claimed_dets.add(det["idx"])
            # If OCR detected a complete 2D pair but Pass F only had 1D, rescue the 2D pair!
            if len(mms) == 1 and len(det.get("mms", [])) == 2:
                it["text_as_written"] = det["text"]
                it["mm"] = det["mms"]
                it["kind"] = "room_size"
        else:
            it["ocr_verified"] = False
        registry.append(it)

    # 3. Add Band items (H_top and H_bottom)
    for band_it, band_src in [(it, "H_top") for it in band_top_items] + [(it, "H_bottom") for it in band_bottom_items]:
        band_it = dict(band_it)
        txt = band_it.get("text_as_written", "")
        mms = band_it.get("mm", [])
        if not mms:
            try:
                mms = parse_label(txt, unit)
            except Exception:
                mms = []
            band_it["mm"] = mms

        # Skip non-measurement text without numbers (e.g. "ENTRY")
        if not mms:
            continue

        matched_reg = None
        for reg in registry:
            if reg.get("mm") == mms and band_compatible(reg, band_src):
                # Vertical edge chain item: band passes should never duplicate chain items
                if reg.get("direction") == "vertical" and "chain_side" in reg:
                    matched_reg = reg
                    break
                # Room size: only merge if same applies_to or both generic bedrooms
                if reg.get("kind") == "room_size" and band_it.get("kind") == "room_size":
                    reg_app = reg.get("applies_to", "").lower()
                    band_app = band_it.get("applies_to", "").lower()
                    if reg_app == band_app or (reg_app and reg_app in band_app) or (band_app and band_app in reg_app):
                        matched_reg = reg
                        break
                # Non-room segment with same mm:
                if reg.get("kind") == "segment" or band_it.get("kind") == "segment":
                    matched_reg = reg
                    break
                # Non-room item with same applies_to
                if reg.get("applies_to") and reg.get("applies_to") == band_it.get("applies_to"):
                    matched_reg = reg
                    break

        if matched_reg:
            if band_src not in matched_reg["sources"]:
                matched_reg["sources"].append(band_src)
            matched_reg["agree"] = True
        else:
            det = match_item_to_ocr(band_it)
            band_it["sources"] = [band_src]
            if det:
                band_it["ocr_verified"] = True
                band_it["ocr_box"] = det["box"]
                band_it["ocr_confidence"] = det["confidence"]
                claimed_dets.add(det["idx"])
            else:
                band_it["ocr_verified"] = False
            registry.append(band_it)

    # 4. OCR Rescue for any high-confidence 2D room labels not yet represented
    if ocr_dets:
        for d in ocr_dets:
            txt = d.get("text", "")
            if not any(c.isdigit() for c in txt):
                continue
            if bool(re.search(r"[a-zA-Z]", txt)) and not has_dimension_feature(txt):
                continue
            if unit == "feet_inches" and not any(c in txt for c in ("'", '"', "′", "″", "’", "”")):
                continue
            if unit in ("metres", "centimetres") and "." not in txt and "m" not in txt.lower():
                continue
            if len(d.get("mms", [])) == 2 and d.get("confidence", 0) >= config.OCR_RESCUE_CONFIDENCE:
                # Check if this exact 2D dimension pair is represented in registry
                found_count = 0
                for reg in registry:
                    if reg.get("kind") == "room_size":
                        reg_mms = reg.get("mm", [])
                        if len(reg_mms) == 2:
                            if (abs(reg_mms[0] - d["mms"][0]) <= 2.0 and abs(reg_mms[1] - d["mms"][1]) <= 2.0) or \
                               (abs(reg_mms[0] - d["mms"][1]) <= 2.0 and abs(reg_mms[1] - d["mms"][0]) <= 2.0):
                                found_count += 1
                if found_count == 0:
                    d_box = d["box"]
                    d_cx = (d_box[0] + d_box[2]) / 2
                    d_cy = (d_box[1] + d_box[3]) / 2
                    best_name = "room"
                    min_dist = float("inf")
                    for td in ocr_dets:
                        if len(td.get("mms", [])) == 0 and len(td.get("text", "")) > 2 and not td.get("text", "").isdigit():
                            t_cx = (td["box"][0] + td["box"][2]) / 2
                            t_cy = (td["box"][1] + td["box"][3]) / 2
                            dy = d_cy - t_cy
                            dx = abs(d_cx - t_cx)
                            if -30 < dy < 150 and dx < 150:
                                dist = math.hypot(dx, dy)
                                if dist < min_dist:
                                    min_dist = dist
                                    best_name = td["text"]
                    print(f"  Rescued room from OCR: {best_name} {d['text']} (conf={d['confidence']:.2f})")
                    registry.append({
                        "applies_to": best_name,
                        "text_as_written": f"{best_name} {d['text']}" if best_name not in d["text"] else d["text"],
                        "mm": d["mms"],
                        "kind": "room_size",
                        "sources": ["OCR"],
                        "ocr_verified": True,
                        "ocr_box": d["box"],
                        "ocr_confidence": d["confidence"],
                        "direction": "none",
                    })

    registry = collapse_fragments(registry)

    # 5. Compute support and status per label
    for it in registry:
        it["support"] = len(set(it.get("sources", [])))
        it["status"] = "verified" if (it.get("ocr_verified") or it["support"] > 1) else "unverified"

    # 6. Filter extras: never discard Pass F items, treat OCR as soft signal
    final_dims = []
    for it in registry:
        # 1. Pass F items are foundational and always kept
        if "F" in it.get("sources", []):
            final_dims.append(it)
        # 2. Rooms with numeric mm always kept
        elif it.get("kind") == "room_size" and it.get("mm"):
            final_dims.append(it)
        # 3. OCR-rescued items kept
        elif "OCR" in it.get("sources", []):
            final_dims.append(it)
        # 4. Corroborated items (support >= 2 or OCR verified) kept
        elif it.get("ocr_verified") or it.get("support", 1) >= 2:
            final_dims.append(it)
        # 5. Chain items kept if corroborated (not strip_only) or when no OCR/fallback
        elif "chain_index" in it and (it.get("source") != "strip_only" or not ocr_dets):
            final_dims.append(it)
        # 6. Fallback when OCR is not active
        elif not ocr_dets:
            final_dims.append(it)
        # 7. Otherwise suppress uncorroborated single-source fragment
        else:
            pass

    left_chain = sorted(
        [it for it in final_dims if it.get("chain_side") == "left" and "chain_index" in it],
        key=lambda x: x["chain_index"],
    )
    right_chain = sorted(
        [it for it in final_dims if it.get("chain_side") == "right" and "chain_index" in it],
        key=lambda x: x["chain_index"],
    )

    passes_list = ["F", "E_left", "E_right", "H_top", "H_bottom"]
    if ocr_dets:
        passes_list.append("OCR")

    return {
        "plan_unit": unit,
        "unit_evidence": pass_f_plan.get("unit_evidence", ""),
        "dimensions": final_dims,
        "left_chain": left_chain,
        "right_chain": right_chain,
        "passes": passes_list,
    }


# ---------------------------------------------------------------------------
# Main Multi-Pass Analysis Entrypoint
# ---------------------------------------------------------------------------

def read_plan(image_path: str, save_path: str = None) -> dict:
    """Analyze a floor plan using multi-pass extraction."""
    img = _load_oriented_image(image_path)
    content_bbox = find_content_bbox(img)

    # Fast physical text detection via PaddleOCR
    print("Running PaddleOCR physical text detection & anchoring...")
    try:
        from ocr_engine import extract_text_boxes
        ocr_detections = extract_text_boxes(image_path)
        print(f"  PaddleOCR detected {len(ocr_detections)} physical text boxes")
    except Exception as e:
        print(f"  Warning: PaddleOCR failed ({e}), continuing with vision passes only.")
        ocr_detections = []

    # Pass F: Full-Page
    print("Running Pass F (Full Page)...")
    pass_f_plan = run_pass_full(image_path)
    unit = pass_f_plan.get("plan_unit", "feet_inches")
    f_items = floorplan_reader_v2.plan_values(pass_f_plan)

    def _needs_strip_pass(side: str) -> bool:
        """Run strip pass only if vertical edge chain is present and its sum cannot be checked."""
        side_items = [
            d for d in f_items
            if d.get("direction") == "vertical" and side in d.get("position", "").lower()
        ]
        if not side_items:
            return False
        # If Pass F already has multiple segments and an overall that checks out, strip is not needed
        mms = [d["mm"][0] for d in side_items if d.get("mm")]
        if len(mms) >= 3:
            overall = max(mms)
            segments = [m for m in mms if m != overall]
            if abs(sum(segments) - overall) <= config.CHAIN_TOLERANCE_MM:
                return False  # Sum verified from Pass F
        return True

    need_left = _needs_strip_pass("left")
    need_right = _needs_strip_pass("right")

    if need_left:
        print("Running Pass E (Left Strip with automatic rotation)...")
        strip_left_items, rot_left, scores_left, raw_left = run_pass_strip(
            img, "left", unit, f_items, bbox=content_bbox
        )
    else:
        print("Left edge chain absent or already verified in Pass F. Skipping left strip.")
        strip_left_items, rot_left, scores_left, raw_left = [], "skipped", {}, {}

    if need_right:
        print("Running Pass E (Right Strip with automatic rotation)...")
        strip_right_items, rot_right, scores_right, raw_right = run_pass_strip(
            img, "right", unit, f_items, bbox=content_bbox
        )
    else:
        print("Right edge chain absent or already verified in Pass F. Skipping right strip.")
        strip_right_items, rot_right, scores_right, raw_right = [], "skipped", {}, {}

    # Pass H: Horizontal Bands
    print("Running Pass H (Horizontal Bands: top 0-35% and bottom 65-100%)...")
    band_top_items = run_pass_band(img, "top", 0.0, 0.35, scale=1.5, bbox=content_bbox)
    print(f"  Top band transcribed {len(band_top_items)} items")

    band_bottom_items = run_pass_band(img, "bottom", 0.65, 1.0, scale=1.5, bbox=content_bbox)
    print(f"  Bottom band transcribed {len(band_bottom_items)} items")

    # Save raw unmerged passes
    if save_path:
        raw_path = save_path.replace(".json", "_raw.json")
        raw_output = {
            "F": pass_f_plan,
            "E_left_cw": raw_left.get("cw", []),
            "E_left_ccw": raw_left.get("ccw", []),
            "E_right_cw": raw_right.get("cw", []),
            "E_right_ccw": raw_right.get("ccw", []),
            "H_top": band_top_items,
            "H_bottom": band_bottom_items,
            "ocr_detections": ocr_detections,
            "orientation_scores": {"left": scores_left, "right": scores_right},
            "chosen_orientation": {"left": rot_left, "right": rot_right},
        }
        with open(raw_path, "w", encoding="utf-8") as f_raw:
            json.dump(raw_output, f_raw, indent=2)
        print(f"Saved unmerged raw pass data to: {raw_path}")

    # Merge
    print("Merging all passes with refined deduplication and support scoring...")
    merged = merge_passes(
        pass_f_plan,
        strip_left_items,
        strip_right_items,
        band_top_items,
        band_bottom_items,
        ocr_detections=ocr_detections,
    )
    return merged


# ---------------------------------------------------------------------------
# Chain Report Helper
# ---------------------------------------------------------------------------

def chain_report(plan: dict) -> dict:
    """Print left and right chain sums vs overall, flagging mismatches."""
    unit = plan.get("unit") or plan.get("plan_unit") or "feet_inches"

    def extract_chain_data(chain_name: str, side_key: str):
        if "dimension_lines" in plan and chain_name in plan["dimension_lines"]:
            raw_labels = plan["dimension_lines"][chain_name]
            mms = [parse_label(x, unit)[0] for x in raw_labels if parse_label(x, unit)]
            labels = list(raw_labels)
        else:
            raw_items = plan.get(f"{side_key}_chain", [])
            items = [
                d for d in raw_items
                if d.get("source") != "strip_only" and "strip_only" not in d.get("sources", [])
            ]
            if not items:
                items = [
                    d for d in plan.get("dimensions", [])
                    if (d.get("chain_side") == side_key or f"{side_key} chain" in d.get("applies_to", "").lower())
                    and d.get("source") != "strip_only" and "strip_only" not in d.get("sources", [])
                ]
            mms = [d["mm"][0] for d in items if d.get("mm")]
            labels = [d.get("text_as_written", "") for d in items]

        if not mms:
            return None

        overall_idx = max(range(len(mms)), key=lambda i: mms[i])
        overall_mm = mms[overall_idx]
        overall_label = labels[overall_idx]

        segment_mms = [mms[i] for i in range(len(mms)) if i != overall_idx]
        segment_labels = [labels[i] for i in range(len(labels)) if i != overall_idx]
        sum_mm = sum(segment_mms)
        diff_mm = sum_mm - overall_mm
        match = abs(diff_mm) <= config.CHAIN_TOLERANCE_MM

        return {
            "segments": segment_labels,
            "segment_mms": segment_mms,
            "sum_mm": sum_mm,
            "sum_formatted": format_mm(sum_mm, unit),
            "overall_mm": overall_mm,
            "overall_formatted": overall_label,
            "diff_mm": diff_mm,
            "diff_formatted": format_mm(abs(diff_mm), unit),
            "match": match,
        }

    left = extract_chain_data("left_chain", "left")
    right = extract_chain_data("right_chain", "right")

    print("\n" + "=" * 60)
    print("VERTICAL DIMENSION CHAIN REPORT")
    print("=" * 60)

    for side, data in [("Left Chain", left), ("Right Chain", right)]:
        print(f"\n[{side}]")
        if not data:
            print("  No chain data found.")
            continue

        print(f"  Segments ({len(data['segments'])}): {', '.join(data['segments'])}")
        print(f"  Sum of segments : {data['sum_formatted']} ({data['sum_mm']:.1f} mm)")
        print(f"  Overall dimension: {data['overall_formatted']} ({data['overall_mm']:.1f} mm)")
        diff_sign = "+" if data["diff_mm"] > 0 else "-"
        status = "MATCH (within 6 in)" if data["match"] else "MISMATCH"
        print(f"  Difference       : {diff_sign}{data['diff_formatted']} ({data['diff_mm']:+.1f} mm) -> [{status}]")

    print("=" * 60 + "\n")
    return {"left": left, "right": right}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Multi-pass floor plan reader.")
    parser.add_argument("image", help="Path to floor plan image")
    parser.add_argument("--save", help="Path to save output JSON")
    parser.add_argument("--fullpage", action="store_true", help="Run Pass F (full page) only")
    args = parser.parse_args()

    from ocr_engine import resolve_image_path
    args.image = resolve_image_path(args.image)
    if not os.path.exists(args.image):
        print(f"Error: Image '{args.image}' not found.")
        return 1

    if args.fullpage:
        print("Running Pass F (Full Page) only...")
        plan = run_pass_full(args.image)
        items = floorplan_reader_v2.plan_values(plan)
        print(f"\nFull-page extraction completed: {len(items)} labels returned.")
        if args.save:
            with open(args.save, "w", encoding="utf-8") as f:
                json.dump(plan, f, indent=2)
            print(f"Saved full-page result to {args.save}")
        return 0

    plan = read_plan(args.image, save_path=args.save)
    items = plan.get("dimensions", [])
    print(f"\n{'='*82}")
    print(f"MULTI-PASS EXTRACTION RESULTS ({len(items)} total merged dimensions/rooms)")
    print(f"{'='*82}")
    print(f"{'#':<4} {'LABEL / MEASUREMENT':<26} {'APPLIES TO':<24} {'OCR VERIFIED':<14} {'SOURCES'}")
    print(f"{'-'*82}")
    for idx, it in enumerate(items, 1):
        txt = it.get("text_as_written", "")
        app = it.get("applies_to", "") or it.get("kind", "")
        ocr_v = "YES" if it.get("ocr_verified") else "Vision Only"
        srcs = ",".join(it.get("sources", []))
        print(f"{idx:<4} {txt:<26} {app:<24} {ocr_v:<14} {srcs}")
    print(f"{'='*82}\n")

    chain_report(plan)

    img_name = Path(args.image).stem
    expected_path = f"answers/{img_name}_expected.json"
    if not os.path.exists(expected_path):
        expected_path = f"answers/{img_name}.json"
    if os.path.exists(expected_path):
        with open(expected_path, encoding="utf-8") as f:
            exp_data = json.load(f)
        if "dimension_lines" in exp_data:
            print(f"Running chain report on ground truth: {expected_path}")
            chain_report(exp_data)

    save_dest = args.save or f"results/{img_name}_result.json"
    os.makedirs(os.path.dirname(save_dest) or ".", exist_ok=True)
    with open(save_dest, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
    print(f"Saved merged result to: {save_dest}")
    return 0



if __name__ == "__main__":
    sys.exit(main())
