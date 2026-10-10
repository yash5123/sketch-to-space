"""Generalised evaluation harness for floor plan reader runs across multiple images.

Usage:
  python eval_runs.py --images test1 test2 test3 test4 --reader multipass --replay
  python eval_runs.py --images test4 --reader multipass --runs 3
  python eval_runs.py --images test4 --reader fullpage
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, "backend")

from parse import parse_label

def get_expected_items(image_name: str) -> list[dict]:
    """Dynamically load expected items for an image from answers/."""
    return load_expected_items(image_name)[1]


def __getattr__(name: str) -> Any:
    """Backward compatibility: dynamically load expected items without hardcoding."""
    if name in ("EXPECTED_ITEMS_TEST4", "EXPECTED_ITEMS"):
        return load_expected_items("test4")[1]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")



def find_image_file(image_name: str) -> Optional[str]:
    """Find image file corresponding to image_name in sketches/."""
    for ext in (".jpg", ".png", ".webp", ".jpeg", ".JPG", ".PNG"):
        candidate = f"sketches/{image_name}{ext}"
        if os.path.exists(candidate):
            return candidate
    return None


def load_expected_items(image_name: str) -> tuple[Optional[str], list[dict], str]:
    """Load expected items and default plan unit from answers/<name>_expected.json or answers/<name>.json."""
    candidates = [
        f"answers/{image_name}_expected.json",
        f"answers/{image_name}.json",
    ]
    found_path = None
    for cand in candidates:
        if os.path.exists(cand):
            found_path = cand
            break

    if not found_path:
        return None, [], "feet_inches"

    with open(found_path, encoding="utf-8") as f:
        data = json.load(f)

    unit = data.get("unit", "feet_inches")
    items = []

    # 1. Format with room_labels and dimension_lines (like test4_expected.json)
    if "room_labels" in data or "dimension_lines" in data:
        for r in data.get("room_labels", []):
            vals = r.get("values", [])
            items.append({
                "name": r.get("name", " ".join(vals)),
                "values": vals,
                "category": "room",
            })
        dim_lines = data.get("dimension_lines", {})
        for v in dim_lines.get("left_chain", []):
            items.append({"name": f"left chain {v}", "values": [v], "category": "left_chain"})
        for v in dim_lines.get("right_chain", []):
            items.append({"name": f"right chain {v}", "values": [v], "category": "right_chain"})
        for v in dim_lines.get("other", []):
            items.append({"name": f"other dim {v}", "values": [v], "category": "other_dim"})

    # 2. Format with a list of dimensions (like test1.json, test2.json, test3.json)
    elif "dimensions" in data:
        for d in data.get("dimensions", []):
            name = d.get("applies_to", "") or d.get("name", "")
            vals = d.get("values", [])
            cat = d.get("category")
            if not cat:
                nl = name.lower()
                if "left" in nl and "chain" in nl:
                    cat = "left_chain"
                elif "right" in nl and "chain" in nl:
                    cat = "right_chain"
                elif len(vals) >= 2:
                    cat = "room"
                else:
                    cat = "other_dim"
            items.append({
                "name": name or f"dim {' '.join(str(v) for v in vals)}",
                "values": vals,
                "category": cat,
            })

    return found_path, items, unit


def categorize_item(dim: dict) -> str:
    """Categorize an output dimension for scoped multiset evaluation."""
    srcs = dim.get("sources", [])
    side = dim.get("chain_side")
    pos = dim.get("position", "").lower()
    direction = dim.get("direction", "")
    kind = dim.get("kind", "")

    # 1. Left chain: from E_left or vertical Pass F items positioned on left side
    if "E_left" in srcs or side == "left":
        return "left_chain"
    if ("F" in srcs or not srcs) and direction == "vertical" and "left" in pos and kind != "room_size":
        return "left_chain"

    # 2. Right chain: from E_right or vertical Pass F items positioned on right side
    if "E_right" in srcs or side == "right":
        return "right_chain"
    if ("F" in srcs or not srcs) and direction == "vertical" and "right" in pos and kind != "room_size":
        return "right_chain"

    # 3. Room labels: kind room_size
    if kind == "room_size":
        return "room"

    # 4. Other dimensions
    return "other_dim"


def load_run_dimensions(json_path: str) -> tuple[str, list[dict]]:
    """Return plan_unit and raw dimensions list for a saved run."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    unit = data.get("plan_unit", "feet_inches")
    return unit, data.get("dimensions", [])


def evaluate_run_categorized(
    dimensions: list[dict],
    unit: str,
    expected_items: list[dict],
) -> tuple[list[bool], dict]:
    """Evaluate run using two-pass category-aware multiset matching.

    Pass 1: Match within identical category (e.g. left_chain to left_chain, room to room).
    Pass 2: Cross-category fallback for remaining unmatched items (e.g. arrow to segment).
    """
    # Parse expected items
    exp_parsed = []
    for idx, exp in enumerate(expected_items):
        raw_vals = exp.get("values", [])
        cat = exp.get("category", "other_dim")
        # Handle alternatives like [['3.784', '3.704']]
        if len(raw_vals) == 1 and isinstance(raw_vals[0], list):
            alts = []
            for a in raw_vals[0]:
                try:
                    alts.append(parse_label(str(a), unit))
                except Exception:
                    pass
            exp_parsed.append({
                "idx": idx,
                "name": exp.get("name", ""),
                "type": "alt",
                "alts": alts,
                "raw": raw_vals,
                "category": cat,
                "matched": False,
                "matched_with": None,
            })
        elif len(raw_vals) == 1:
            try:
                mms = parse_label(str(raw_vals[0]), unit)
            except Exception:
                mms = []
            exp_parsed.append({
                "idx": idx,
                "name": exp.get("name", ""),
                "type": "single",
                "mms": mms,
                "raw": raw_vals,
                "category": cat,
                "matched": False,
                "matched_with": None,
            })
        else:
            mms = []
            for v in raw_vals:
                try:
                    mms.extend(parse_label(str(v), unit))
                except Exception:
                    pass
            exp_parsed.append({
                "idx": idx,
                "name": exp.get("name", ""),
                "type": "pair",
                "mms": mms,
                "raw": raw_vals,
                "category": cat,
                "matched": False,
                "matched_with": None,
            })

    # Parse predicted dimensions
    pred_parsed = []
    for d in dimensions:
        txt = d.get("text_as_written", "")
        try:
            mms = parse_label(txt, unit)
        except Exception:
            mms = []
        pred_parsed.append({
            "text": txt,
            "mms": mms,
            "category": categorize_item(d),
            "legibility": d.get("legibility", "clear"),
            "support": d.get("support", 1),
            "status": d.get("status", "unverified"),
            "matched": False,
        })

    def try_match(e: dict, p: dict) -> bool:
        if p["matched"]:
            return False
        if e["type"] == "pair":
            if len(e["mms"]) == 2 and len(p["mms"]) == 2:
                # Room dimensions can be written in either order (WxH or HxW)
                if (abs(e["mms"][0] - p["mms"][0]) <= 2.0 and abs(e["mms"][1] - p["mms"][1]) <= 2.0) or \
                   (abs(e["mms"][0] - p["mms"][1]) <= 2.0 and abs(e["mms"][1] - p["mms"][0]) <= 2.0):
                    return True
        elif e["type"] == "single":
            if len(e["mms"]) == 1 and len(p["mms"]) == 1:
                if abs(e["mms"][0] - p["mms"][0]) <= 2.0:
                    return True
        elif e["type"] == "alt":
            if len(p["mms"]) == 1:
                for alt_mms in e["alts"]:
                    if len(alt_mms) == 1 and abs(alt_mms[0] - p["mms"][0]) <= 2.0:
                        return True
        return False

    # PASS 1: Match within same category (pairs first, then single/alt)
    for e in exp_parsed:
        if e["type"] == "pair" and not e["matched"]:
            for p in pred_parsed:
                if p["category"] == e["category"] and try_match(e, p):
                    e["matched"] = True
                    p["matched"] = True
                    e["matched_with"] = p["text"]
                    break

    for e in exp_parsed:
        if e["type"] != "pair" and not e["matched"]:
            for p in pred_parsed:
                if p["category"] == e["category"] and try_match(e, p):
                    e["matched"] = True
                    p["matched"] = True
                    e["matched_with"] = p["text"]
                    break

    # PASS 2: Cross-category fallback for any still unmatched expected items
    for e in exp_parsed:
        if e["type"] == "pair" and not e["matched"]:
            for p in pred_parsed:
                if try_match(e, p):
                    e["matched"] = True
                    p["matched"] = True
                    e["matched_with"] = p["text"]
                    break

    for e in exp_parsed:
        if e["type"] != "pair" and not e["matched"]:
            for p in pred_parsed:
                if try_match(e, p):
                    e["matched"] = True
                    p["matched"] = True
                    e["matched_with"] = p["text"]
                    break

    hits_mask = [e["matched"] for e in exp_parsed]

    # Calculate metrics per category
    all_categories = list(dict.fromkeys(
        [e["category"] for e in exp_parsed] + [p["category"] for p in pred_parsed]
    ))
    cat_metrics = {}

    for cat in all_categories:
        c_exps = [e for e in exp_parsed if e["category"] == cat]
        c_hits = sum(1 for e in c_exps if e["matched"])
        c_exp_count = len(c_exps)

        c_preds = [p for p in pred_parsed if p["category"] == cat]
        c_extras = [p for p in c_preds if not p["matched"]]
        c_extras_count = len(c_extras)
        c_clear_extras = sum(1 for p in c_extras if p["legibility"] == "clear")

        c_tot_pred = c_hits + c_extras_count
        c_prec = (c_hits / c_tot_pred * 100) if c_tot_pred > 0 else 0.0
        c_rate = (c_hits / c_exp_count * 100) if c_exp_count > 0 else 0.0

        cat_metrics[cat] = {
            "hits": c_hits,
            "expected": c_exp_count,
            "hit_rate": c_rate,
            "extras": c_extras_count,
            "clear_extras": c_clear_extras,
            "precision": c_prec,
            "total_pred": c_tot_pred,
            "extras_list": [p["text"] for p in c_extras],
        }

    # Total aggregate metrics
    t_hits = sum(1 for e in exp_parsed if e["matched"])
    t_exp = len(exp_parsed)
    t_extras_list = [p for p in pred_parsed if not p["matched"]]
    t_extras = len(t_extras_list)
    t_clear_extras = sum(1 for p in t_extras_list if p["legibility"] == "clear")
    t_pred = len(pred_parsed)
    t_prec = (t_hits / t_pred * 100) if t_pred > 0 else 0.0
    t_rate = (t_hits / t_exp * 100) if t_exp > 0 else 0.0

    cat_metrics["total"] = {
        "hits": t_hits,
        "expected": t_exp,
        "hit_rate": t_rate,
        "extras": t_extras,
        "clear_extras": t_clear_extras,
        "precision": t_prec,
        "total_pred": t_pred,
    }

    # Store detailed matching audit for reports
    cat_metrics["_details"] = {
        "hits": [
            {"name": e["name"], "raw": e["raw"], "matched_with": e["matched_with"], "category": e["category"]}
            for e in exp_parsed if e["matched"]
        ],
        "misses": [
            {"name": e["name"], "raw": e["raw"], "category": e["category"]}
            for e in exp_parsed if not e["matched"]
        ],
        "extras": [
            {"text": p["text"], "category": p["category"], "support": p["support"], "status": p["status"]}
            for p in t_extras_list
        ],
    }

    return hits_mask, cat_metrics


def evaluate_single_image(
    image_name: str,
    reader: str,
    runs: int,
    replay: bool,
    python_exe: str,
    verbose: bool = False,
) -> Optional[dict]:
    """Run/replay evaluation on a single image across specified runs."""
    image_file = find_image_file(image_name)
    if not image_file:
        print(f"Error: image not found in sketches/ for {image_name}")
        return None

    ans_path, expected_items, expected_unit = load_expected_items(image_name)
    if not ans_path or not expected_items:
        print(f"Warning: No ground truth found for {image_name} in answers/. Skipping evaluation.")
        return None

    print(f"\n{'='*95}")
    print(f"EVALUATING IMAGE: {image_name.upper()} ({image_file}) | Ground Truth: {ans_path} ({len(expected_items)} labels, unit={expected_unit})")
    print(f"{'='*95}")

    prefix = "run" if reader == "fullpage" else "multipass_run"
    run_files = [f"results/{image_name}_{prefix}{i}.json" for i in range(1, runs + 1)]

    # Replay mode
    if replay:
        if reader == "multipass":
            from reader import select_strip_orientation, merge_passes, floorplan_reader_v2
            print(f"--- REPLAY MODE: Re-merging from saved raw files for {image_name} ---")
            for run_idx, run_file in enumerate(run_files, 1):
                raw_file = run_file.replace(".json", "_raw.json")
                if not os.path.exists(raw_file):
                    if os.path.exists(run_file):
                        print(f"Loaded existing run result: {run_file}")
                    else:
                        print(f"Warning: Raw file not found: {raw_file}. Cannot replay run {run_idx}.")
                    continue
                with open(raw_file, encoding="utf-8") as f_raw:
                    raw = json.load(f_raw)
                unit = raw["F"].get("plan_unit", expected_unit)
                f_items = floorplan_reader_v2.plan_values(raw["F"])

                left_items, left_rot, _ = select_strip_orientation(
                    raw.get("E_left_cw", []), raw.get("E_left_ccw", []), "left", unit, f_items
                )
                right_items, right_rot, _ = select_strip_orientation(
                    raw.get("E_right_cw", []), raw.get("E_right_ccw", []), "right", unit, f_items
                )
                ocr_dets = raw.get("ocr_detections")
                if ocr_dets is None and image_file:
                    try:
                        import ocr_engine as ocr
                        ocr_dets = ocr.extract_text_boxes(image_file)
                    except Exception:
                        ocr_dets = []
                merged = merge_passes(
                    raw["F"], left_items, right_items, raw.get("H_top", []), raw.get("H_bottom", []),
                    ocr_detections=ocr_dets,
                )
                with open(run_file, "w", encoding="utf-8") as f_out:
                    json.dump(merged, f_out, indent=2)
                print(f"Re-merged Run {run_idx}: {run_file} (left={left_rot}, right={right_rot})")
        else:
            print(f"--- REPLAY MODE: Evaluating existing saved fullpage files for {image_name} ---")
    else:
        for run_idx, run_file in enumerate(run_files, 1):
            if not os.path.exists(run_file):
                print(f"\n--- Running {reader.upper()} on {image_file} (Run {run_idx}/{runs}) ---")
                if reader == "fullpage":
                    cmd = [python_exe, "backend/reader.py", image_file, "--fullpage", "--save", run_file]
                else:
                    cmd = [python_exe, "backend/reader.py", image_file, "--save", run_file]
                subprocess.run(cmd, check=True)

            else:
                print(f"Loaded existing result for Run {run_idx}: {run_file}")

    run_results = []
    run_metrics = []
    available_runs = 0
    for f in run_files:
        if not os.path.exists(f):
            continue
        available_runs += 1
        unit, dims = load_run_dimensions(f)
        hits, metrics = evaluate_run_categorized(dims, unit, expected_items)
        run_results.append(hits)
        run_metrics.append(metrics)

    if not run_results:
        print(f"No run results available to evaluate for {image_name}.")
        return None

    # Print Per-Run Detailed Metrics
    print(f"\n--- Per-Run Breakdown [{image_name.upper()}] ---")
    for run_idx, metrics in enumerate(run_metrics, 1):
        tot = metrics["total"]
        print(f"\n[RUN {run_idx}] Total Hits: {tot['hits']}/{tot['expected']} ({tot['hit_rate']:.1f}%) | "
              f"Precision: {tot['precision']:.1f}% ({tot['hits']}/{tot['total_pred']}) | Extras: {tot['extras']}")

        # Category Breakdown Table
        print(f"  {'CATEGORY':<16} {'EXPECTED':<10} {'HITS':<10} {'HIT RATE':<12} {'EXTRAS':<10} {'PRECISION':<10}")
        print(f"  {'-'*68}")
        for cat, data in metrics.items():
            if cat in ("total", "_details"):
                continue
            if data["expected"] > 0 or data["extras"] > 0:
                print(f"  {cat:<16} {data['expected']:<10} {data['hits']:<10} {data['hit_rate']:>6.1f}%     {data['extras']:<10} {data['precision']:>6.1f}%")

        details = metrics.get("_details", {})
        misses = details.get("misses", [])
        if misses:
            print(f"  Missed ({len(misses)}):")
            for m in misses:
                print(f"    - [MISS] {m['name']} (expected {m['raw']})")

        if verbose:
            hits = details.get("hits", [])
            print(f"  Matched ({len(hits)}):")
            for h in hits:
                print(f"    + [HIT]  {h['name']} -> matched '{h['matched_with']}'")
            extras = details.get("extras", [])
            if extras:
                print(f"  Sample Extras (first 5 of {len(extras)}):")
                for ex in extras[:5]:
                    print(f"    * [EXTRA] '{ex['text']}' cat={ex['category']} status={ex['status']}")

    # Consistency Analysis across runs
    hit_counts = [0] * len(expected_items)
    for mask in run_results:
        for i, val in enumerate(mask):
            if val:
                hit_counts[i] += 1

    cons_all = sum(1 for c in hit_counts if c == available_runs)
    cons_inter = sum(1 for c in hit_counts if 0 < c < available_runs)
    cons_none = sum(1 for c in hit_counts if c == 0)

    total_expected = len(expected_items)
    avg_hits = sum(m["total"]["hits"] for m in run_metrics) / available_runs
    avg_pred = sum(m["total"]["total_pred"] for m in run_metrics) / available_runs
    avg_hit_rate = (avg_hits / total_expected * 100) if total_expected > 0 else 0.0
    avg_precision = (avg_hits / avg_pred * 100) if avg_pred > 0 else 0.0

    print(f"\n--- Multi-Run Summary for {image_name.upper()} ({available_runs} runs) ---")
    print(f"  Average Hit Rate : {avg_hit_rate:.1f}% ({avg_hits:.1f}/{total_expected})")
    print(f"  Average Precision: {avg_precision:.1f}%")
    print(f"  Consistency      : {cons_all}/{total_expected} hit in all runs ({cons_all/total_expected*100:.1f}%), "
          f"{cons_inter} intermittent, {cons_none} persistent misses")

    return {
        "image": image_name,
        "unit": expected_unit,
        "runs": available_runs,
        "total_expected": total_expected,
        "avg_hits": avg_hits,
        "avg_hit_rate": avg_hit_rate,
        "avg_precision": avg_precision,
        "cons_all": cons_all,
        "cons_none": cons_none,
        "metrics": run_metrics,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate runs across multiple images.")
    parser.add_argument(
        "--images",
        nargs="+",
        default=["test4"],
        help="One or more image names in sketches/ (default: test4)",
    )
    parser.add_argument(
        "--reader",
        choices=["fullpage", "multipass"],
        default="multipass",
        help="Reader to evaluate (default: multipass)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of runs per image (default: 3)",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Re-merge / evaluate existing saved files, no model calls",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed hit list and extra item list",
    )
    args = parser.parse_args()

    os.makedirs("results", exist_ok=True)
    python_exe = sys.executable

    # Allow comma-separated strings in images argument
    image_names = []
    for item in args.images:
        for split_item in item.split(","):
            cleaned = split_item.strip()
            if cleaned:
                image_names.append(cleaned)

    image_summaries = []
    for img_name in image_names:
        summary = evaluate_single_image(
            image_name=img_name,
            reader=args.reader,
            runs=args.runs,
            replay=args.replay,
            python_exe=python_exe,
            verbose=args.verbose,
        )
        if summary:
            image_summaries.append(summary)

    if len(image_summaries) > 1:
        print(f"\n{'='*95}")
        print("CROSS-IMAGE EVALUATION SUMMARY")
        print(f"{'='*95}")
        print(f"{'IMAGE':<12} {'UNIT':<14} {'GT LABELS':<12} {'RUNS':<6} {'AVG HIT RATE':<15} {'AVG PRECISION':<15} {'CONSISTENT (N/N)'}")
        print(f"{'-'*95}")
        for s in image_summaries:
            cons_str = f"{s['cons_all']}/{s['total_expected']} ({s['cons_all']/s['total_expected']*100:.1f}%)"
            print(f"{s['image']:<12} {s['unit']:<14} {s['total_expected']:<12} {s['runs']:<6} {s['avg_hit_rate']:>6.1f}%          {s['avg_precision']:>6.1f}%          {cons_str}")
        print(f"{'='*95}\n")


if __name__ == "__main__":
    main()
