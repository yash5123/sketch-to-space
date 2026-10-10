"""Constraint checker: find dimensions that do not add up and suggest fixes.

Pure code, no AI, no sketch-specific values. The reader supplies labels (text as
written) and which labels belong together; this module decides whether they agree.

A constraint says:   value(total) - sum(values(parts))  must lie in [lo_mm, hi_mm].
  chain  : an overall dimension and the segments that make it up
           (segments are often CLEAR sizes, so the overall exceeds their sum by
           roughly one wall thickness per segment -> a slack window).
  equal  : two labels that should be the same (left and right overall height).

Honest limit: an error smaller than the tolerance is invisible by design.
Suggestions are single-label edits ranked by how plausible the edit is
(known handwriting confusions first), and are never applied automatically.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from parse import infer_unit, parse_label

# Digit pairs a reader tends to confuse (symmetric). Override via the `confusions`
# argument; derive your own from real reader mistakes when you have them.
DEFAULT_CONFUSIONS = {
    ("1", "7"), ("7", "1"), ("5", "6"), ("6", "5"), ("3", "8"), ("8", "3"),
    ("0", "6"), ("6", "0"), ("8", "5"), ("5", "8"), ("8", "0"), ("0", "8"),
}

_FEET_PATTERN = re.compile(r"""^(0|[1-9]\d*)'-?(\d{1,2})( 1/2)?"$""")
_METRE_PATTERN = re.compile(r"^(0|[1-9]\d*)\.\d+$")


@dataclass
class Label:
    id: str
    text: str
    unit: str
    mm: Optional[float] = None

    def __post_init__(self):
        if self.mm is None:
            try:
                values = parse_label(self.text, self.unit)
            except Exception:
                values = []
            self.mm = values[0] if len(values) == 1 else None


@dataclass
class Constraint:
    id: str
    total: str
    parts: list[str]
    lo_mm: float
    hi_mm: float
    kind: str = "chain"


@dataclass
class Violation:
    constraint: Constraint
    gap_mm: float        # value(total) - sum(parts)
    excess_mm: float     # distance outside the allowed window


@dataclass
class Suggestion:
    label_id: str
    old_text: str
    new_text: str
    remaining_violations: int
    cost: float
    delta_mm: float
    remaining_excess_mm: float = 0.0
    status: str = "complete"
    hypothesis: str = "candidate_edit"


def chain_constraint(cid, total, parts, tol_mm, wall_mm=None, wall_max_mm=230.0) -> Constraint:
    """overall = sum(parts) + slack. Known wall: slack = n*wall. Unknown: 0..n*wall_max."""
    n = len(parts)
    if wall_mm is not None:
        lo, hi = n * wall_mm - tol_mm, n * wall_mm + tol_mm
    else:
        lo, hi = -tol_mm, n * wall_max_mm + tol_mm
    return Constraint(cid, total, list(parts), lo, hi, "chain")


def equal_constraint(cid, a, b, tol_mm) -> Constraint:
    return Constraint(cid, a, [b], -tol_mm, tol_mm, "equal")


def _values(labels: dict[str, Label]) -> dict[str, float]:
    return {i: l.mm for i, l in labels.items() if l.mm is not None}


def violations(values: dict[str, float], constraints: list[Constraint]) -> list[Violation]:
    out = []
    for c in constraints:
        ids = [c.total] + c.parts
        if any(i not in values for i in ids):
            continue  # cannot be checked: a label is missing or unparseable
        gap = values[c.total] - sum(values[p] for p in c.parts)
        if gap < c.lo_mm:
            out.append(Violation(c, gap, c.lo_mm - gap))
        elif gap > c.hi_mm:
            out.append(Violation(c, gap, gap - c.hi_mm))
    return out


def check(labels: dict[str, Label], constraints: list[Constraint]) -> list[Violation]:
    return violations(_values(labels), constraints)


def covered_ids(labels: dict[str, Label], constraints: list[Constraint]) -> set[str]:
    """Labels that appear in at least one checkable constraint (others are 'unverified')."""
    values = _values(labels)
    covered = set()
    for c in constraints:
        ids = [c.total] + c.parts
        if all(i in values for i in ids):
            covered.update(ids)
    return covered


# ---------------------------------------------------------------------------
# Candidate edits
# ---------------------------------------------------------------------------

def _valid(text: str, unit: str, strict: bool) -> bool:
    if strict:
        if unit == "feet_inches":
            m = _FEET_PATTERN.match(text)
            return bool(m) and int(m.group(2)) <= 11
        return bool(_METRE_PATTERN.match(text))
    try:
        return len(parse_label(text, unit)) == 1
    except Exception:
        return False


def candidate_edits(text: str, unit: str, confusions=DEFAULT_CONFUSIONS) -> list[tuple[str, float]]:
    """Plausible corrections of one written label with a plausibility cost (lower = likelier)."""
    strict = bool(_FEET_PATTERN.match(text) if unit == "feet_inches" else _METRE_PATTERN.match(text))
    frac = text.find(" 1/2")
    protected = set(range(frac, frac + 4)) if frac >= 0 else set()
    digit_positions = [i for i, ch in enumerate(text) if ch.isdigit() and i not in protected]

    found: dict[str, float] = {}

    def add(new: str, cost: float):
        if new != text and _valid(new, unit, strict):
            found[new] = min(cost, found.get(new, 99.0))

    for i in digit_positions:
        old = text[i]
        for d in "0123456789":
            if d == old:
                continue
            if (old, d) in confusions:
                cost = 0.5
            elif abs(int(d) - int(old)) == 1:
                cost = 0.75
            else:
                cost = 1.0
            add(text[:i] + d + text[i + 1:], cost)
    for a, b in zip(digit_positions, digit_positions[1:]):
        if b == a + 1 and text[a] != text[b]:
            add(text[:a] + text[b] + text[a] + text[b + 1:], 1.0)
    if unit == "feet_inches" and strict:
        add(text.replace(" 1/2", "") if frac >= 0 else text[:-1] + ' 1/2"', 0.5)
    return sorted(found.items(), key=lambda kv: kv[1])


def suggest(labels: dict[str, Label], constraints: list[Constraint], max_results: int = 10,
            confusions=DEFAULT_CONFUSIONS) -> list[Suggestion]:
    """Single-label edits that reduce the number of violated constraints, best first."""
    from parse import format_mm
    values = _values(labels)
    base = violations(values, constraints)
    if not base:
        return []
    base_excess = sum(v.excess_mm for v in base)
    suspects = {i for v in base for i in [v.constraint.total] + v.constraint.parts}
    results: list[Suggestion] = []
    seen: set[tuple[str, str]] = set()

    # 1. Multi-chain consensus hypothesis ("overall is wrong"):
    # When two or more independent chains agree on the same sum within tolerance,
    # the hypothesis "overall is wrong" ranks first with cost 0.25.
    chain_cons = [c for c in constraints if c.kind == "chain"]
    chain_sums: dict[str, float] = {}
    for c in chain_cons:
        if all(p in values for p in c.parts):
            chain_sums[c.id] = sum(values[p] for p in c.parts)

    for i in range(len(chain_cons)):
        for j in range(i + 1, len(chain_cons)):
            c1, c2 = chain_cons[i], chain_cons[j]
            if c1.id in chain_sums and c2.id in chain_sums:
                s1, s2 = chain_sums[c1.id], chain_sums[c2.id]
                if abs(s1 - s2) <= 152.4:
                    # Consensus trial updates both overalls together if linked
                    trial = dict(values)
                    trial[c1.total] = s1
                    trial[c2.total] = s2
                    trial_viols = violations(trial, constraints)
                    rem_viols = len(trial_viols)
                    rem_excess = sum(v.excess_mm for v in trial_viols)

                    for c_target, s_val in [(c1, s1), (c2, s2)]:
                        t_lbl = labels.get(c_target.total)
                        if t_lbl and t_lbl.mm is not None:
                            new_text = format_mm(s_val, t_lbl.unit)
                            if new_text != t_lbl.text:
                                key = (c_target.total, new_text)
                                if key not in seen:
                                    seen.add(key)
                                    status = "complete" if rem_viols == 0 else "partial"
                                    results.append(Suggestion(
                                        c_target.total, t_lbl.text, new_text,
                                        rem_viols, 0.25, abs(s_val - t_lbl.mm),
                                        remaining_excess_mm=round(rem_excess, 1),
                                        status=status,
                                        hypothesis="overall_is_wrong"
                                    ))

    # 2. Candidate digit/formatting edits
    for label_id in suspects:
        label = labels.get(label_id)
        if label is None or label.mm is None:
            continue
        for new_text, cost in candidate_edits(label.text, label.unit, confusions):
            try:
                new_values = parse_label(new_text, label.unit)
            except Exception:
                continue
            if len(new_values) != 1:
                continue
            key = (label_id, new_text)
            if key in seen:
                continue
            trial = dict(values)
            trial[label_id] = new_values[0]
            trial_viols = violations(trial, constraints)
            rem_viols = len(trial_viols)
            rem_excess = sum(v.excess_mm for v in trial_viols)

            if rem_viols < len(base) or rem_excess < base_excess - 50.0:
                seen.add(key)
                status = "complete" if rem_viols == 0 else "partial"
                results.append(Suggestion(
                    label_id, label.text, new_text, rem_viols, cost,
                    abs(new_values[0] - label.mm),
                    remaining_excess_mm=round(rem_excess, 1),
                    status=status,
                    hypothesis="digit_confusion"
                ))

    # Deduplicate tied edits & sort
    results.sort(key=lambda s: (s.remaining_violations, s.cost, s.remaining_excess_mm, s.delta_mm, s.label_id))

    deduped_results: list[Suggestion] = []
    seen_digit_edits = set()
    for s in results:
        if s.hypothesis == "overall_is_wrong":
            deduped_results.append(s)
            continue
        sig = (s.old_text, s.new_text, s.cost, s.remaining_violations, round(s.remaining_excess_mm, 1))
        if sig in seen_digit_edits:
            continue
        seen_digit_edits.add(sig)
        deduped_results.append(s)

    return deduped_results[:max_results]


# ---------------------------------------------------------------------------
# Adapter for the multi-pass reader's output
# ---------------------------------------------------------------------------

def constraints_from_reader_plan(plan: dict, tol_mm: float, wall_mm=None, wall_max_mm=230.0):
    """Build labels and constraints from reader.merge_passes output (left/right chains)."""
    chains = {s: plan.get(f"{s}_chain", []) for s in ("left", "right")}
    unit = plan.get("plan_unit", "unclear")
    texts = [it.get("text_as_written", "") for c in chains.values() for it in c]
    if unit not in ("feet_inches", "metres", "centimetres", "millimetres"):
        unit = infer_unit(texts)
    labels: dict[str, Label] = {}
    constraints: list[Constraint] = []
    overall_ids = {}
    for side, items in chains.items():
        ids, overall = [], None
        for idx, it in enumerate(items):
            lid = f"{side}_{idx}"
            labels[lid] = Label(lid, it.get("text_as_written", ""), unit)
            if it.get("kind") == "overall" and overall is None:
                overall = lid
            else:
                ids.append(lid)
        if overall and ids:
            constraints.append(chain_constraint(f"{side}_chain", overall, ids, tol_mm, wall_mm, wall_max_mm))
            overall_ids[side] = overall
    if len(overall_ids) == 2:
        constraints.append(equal_constraint("left_eq_right", overall_ids["left"], overall_ids["right"], tol_mm))
    return labels, constraints
