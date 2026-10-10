"""Score the constraint checker on synthetic plans (needs synthetic/*_meta.json).

    python synth_plans.py --count 60 --seed 11 --error-rate 0.7 --max-errors 1
    python solver_eval.py
    python solver_eval.py --constraints chains --tol-mm 152.4
    python solver_eval.py --constraints full --tol-mm 1

--constraints chains : only the top/left/right chains + left=right overall
                       (what the reader can produce today).
--constraints full   : also room-to-column links from the synthetic ground truth.
                       This is an ORACLE: it assumes perfect topology, so report it
                       as an upper bound, never as an end-to-end result.

Honest limits, printed with the results:
  * synthetic plans are cleaner than real ones;
  * if a plan has no wall_mm in its metadata, chains sum exactly and the false-alarm
    rate is optimistic (real plans carry 1-2 ft of wall slack).
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

import checks  # noqa: E402
from parse import parse_label  # noqa: E402

SIZE_CLASSES = [("under 6 in (152 mm)", 0, 152.4), ("6 in to 3 ft", 152.4, 914.4), ("over 3 ft", 914.4, 1e12)]


def build(meta: dict, mode: str, tol_mm: float, wall_max_mm: float):
    unit, plan = meta["unit"], meta["plan"]
    labels = {l["id"]: checks.Label(l["id"], l["written_text"], unit) for l in meta["labels"]}
    wall = meta.get("wall_mm")
    ch = lambda cid, total, parts: checks.chain_constraint(cid, total, parts, tol_mm, wall, wall_max_mm)
    cons = [
        ch("top", "top_overall", [f"top_seg_{i}" for i in range(len(plan["cols"]))]),
        ch("left", "left_overall", [f"left_seg_{j}" for j in range(len(plan["rows"][0]))]),
        ch("right", "right_overall", [f"right_seg_{j}" for j in range(len(plan["rows"][-1]))]),
        checks.equal_constraint("left_eq_right", "left_overall", "right_overall", tol_mm),
    ]
    if mode == "full":
        rooms = plan["rooms"]
        n_cols = len(plan["cols"])
        for ci in range(n_cols):
            col_rooms = [k for k, r in enumerate(rooms) if r["col"] == ci]
            cons.append(ch(f"col{ci}_sum", "left_overall", [f"room_{k}_d" for k in col_rooms]))
        for k, r in enumerate(rooms):
            cons.append(checks.equal_constraint(f"room{k}_w", f"room_{k}_w", f"top_seg_{r['col']}", tol_mm))
            if r["col"] == 0:
                cons.append(checks.equal_constraint(f"room{k}_d_left", f"room_{k}_d", f"left_seg_{r['row']}", tol_mm))
            if r["col"] == n_cols - 1:
                cons.append(checks.equal_constraint(f"room{k}_d_right", f"room_{k}_d", f"right_seg_{r['row']}", tol_mm))
    return labels, cons


def size_class(mm: float) -> str:
    for name, lo, hi in SIZE_CLASSES:
        if lo <= mm < hi:
            return name
    return SIZE_CLASSES[-1][0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meta-dir", default="synthetic")
    ap.add_argument("--constraints", choices=["chains", "full"], default="chains")
    ap.add_argument("--tol-mm", type=float, default=152.4)
    ap.add_argument("--wall-max-mm", type=float, default=0.0,
                    help="allowed extra slack per segment when a plan has no wall_mm (default 0)")
    args = ap.parse_args()

    files = sorted(Path(args.meta_dir).glob("*_meta.json"))
    if not files:
        print(f"No *_meta.json in {args.meta_dir}. Run synth_plans.py first.")
        return 1

    n_plans = n_err_plans = n_clean = false_alarms = 0
    n_errors = covered = 0
    by_size = defaultdict(lambda: [0, 0, 0])   # class -> [errors covered, flagged, total errors]
    single = top1 = top3 = located = 0
    plan_missed = 0
    has_wall = 0

    ge5_cov = ge5_fl = ge5_tot = 0
    lt5_cov = lt5_fl = lt5_tot = 0

    for f in files:
        meta = json.loads(f.read_text(encoding="utf-8"))
        labels, cons = build(meta, args.constraints, args.tol_mm, args.wall_max_mm)
        has_wall += meta.get("wall_mm") is not None
        viol = checks.check(labels, cons)
        flagged_ids = {i for v in viol for i in [v.constraint.total] + v.constraint.parts}
        covered_set = checks.covered_ids(labels, cons)
        errors = meta["injected_errors"]
        n_plans += 1
        if not errors:
            n_clean += 1
            false_alarms += bool(viol)
            continue
        n_err_plans += 1
        plan_missed += not viol
        for e in errors:
            n_errors += 1
            truth = parse_label(e["truth_text"], meta["unit"])[0]
            written = parse_label(e["written_text"], meta["unit"])[0]
            cls = size_class(abs(written - truth))
            by_size[cls][2] += 1

            # Check segment count of constraints covering this error
            matching_chains = [c for c in cons if c.kind == "chain" and e["id"] in [c.total] + c.parts]
            max_segs = max((len(c.parts) for c in matching_chains), default=0)
            if max_segs >= 5:
                ge5_tot += 1
            elif matching_chains:
                lt5_tot += 1

            if e["id"] in covered_set:
                covered += 1
                by_size[cls][0] += 1
                is_flagged = e["id"] in flagged_ids
                if is_flagged:
                    by_size[cls][1] += 1

                if max_segs >= 5:
                    ge5_cov += 1
                    if is_flagged:
                        ge5_fl += 1
                elif matching_chains:
                    lt5_cov += 1
                    if is_flagged:
                        lt5_fl += 1

        if len(errors) == 1 and viol:
            single += 1
            e = errors[0]
            sugg = checks.suggest(labels, cons, max_results=10)
            ranks = [i for i, s in enumerate(sugg, 1) if s.label_id == e["id"] and s.new_text == e["truth_text"]]
            located += any(s.label_id == e["id"] for s in sugg[:3])
            if ranks:
                top1 += ranks[0] == 1
                top3 += ranks[0] <= 3

    pct = lambda a, b: f"{a}/{b} ({100 * a / b:.0f}%)" if b else f"{a}/{b}"
    total_flagged = sum(by_size[name][1] for name, _, _ in SIZE_CLASSES)
    print(f"Solver evaluation on {n_plans} synthetic plans  |  constraints: {args.constraints}  |  tolerance: {args.tol_mm:g} mm")
    print(f"  plans with errors: {n_err_plans}   error-free plans: {n_clean}   injected errors: {n_errors}")
    print(f"\nOverall chains-mode catch rate (flagged of all injected): {pct(total_flagged, n_errors)}")
    print(f"Errors the constraints can see at all (covered): {pct(covered, n_errors)}")
    print(f"Detection rate among covered errors: {pct(total_flagged, covered)}")
    print("\nDetection, among errors the constraints can see:")
    for name, _, _ in SIZE_CLASSES:
        c, fl, tot = by_size[name]
        print(f"  error size {name:<22} flagged {pct(fl, c)}   (of {tot} injected)")

    print(f"\nRecall by chain segment count (covered errors flagged):")
    print(f"  chains with 5 or more segments: {pct(ge5_fl, ge5_cov)}")
    print(f"  chains with < 5 segments      : {pct(lt5_fl, lt5_cov)}")

    print(f"\nPlans with an error where nothing was flagged: {pct(plan_missed, n_err_plans)}")
    print(f"False alarms on error-free plans: {pct(false_alarms, n_clean)}")
    print(f"\nCorrection, single-error plans that raised a flag: {single}")
    print(f"  right label in top 3 suggestions : {pct(located, single)}")
    print(f"  exact true value ranked 1st      : {pct(top1, single)}")
    print(f"  exact true value in top 3        : {pct(top3, single)}")
    print("\nNotes:")
    if not has_wall:
        print("  * plans have no wall slack, so false alarms are optimistic (real plans: 1-2 ft slack).")
    if args.constraints == "full":
        print("  * 'full' uses ground-truth topology: an upper bound, not an end-to-end result.")
    print("  * synthetic text is cleaner than real handwriting; do not merge with real-plan numbers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
