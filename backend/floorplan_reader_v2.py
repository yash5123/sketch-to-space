"""General plan reader: works for feet-inches and metric plans, any layout.

Division of labour
  Model : transcribes every dimension label exactly as written, says what it
          belongs to and roughly where it sits on the page.
  Code  : converts the transcribed text into numbers (millimetres) and checks
          them. The model never does unit conversion, because that is where it
          made mistakes before (3' returned as 3 inches).

Nothing in here is specific to one image.
"""

import re
from typing import Any

try:
    from parse import (
        UNITS,
        MM_PER_FOOT,
        MM_PER_INCH,
        _normalise,
        format_mm,
        infer_unit,
        parse_label,
    )
except ImportError:
    from backend.parse import (
        UNITS,
        MM_PER_FOOT,
        MM_PER_INCH,
        _normalise,
        format_mm,
        infer_unit,
        parse_label,
    )

POSITIONS = [
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
]

# --------------------------------------------------------------------------
# JSON schema: the fixed "form" the model must fill in (passed to Ollama)
# --------------------------------------------------------------------------

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plan_unit": {"type": "string", "enum": UNITS},
        "unit_evidence": {"type": "string"},
        "dimensions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text_as_written": {"type": "string"},
                    "applies_to": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["overall", "room_size", "segment", "wall_thickness", "other"],
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["horizontal", "vertical", "none"],
                    },
                    "position": {"type": "string", "enum": POSITIONS},
                    "legibility": {
                        "type": "string",
                        "enum": ["clear", "partly_unclear", "hard_to_read"],
                    },
                },
                "required": [
                    "text_as_written", "applies_to", "kind",
                    "direction", "position", "legibility",
                ],
            },
        },
    },
    "required": ["plan_unit", "unit_evidence", "dimensions"],
}

# --------------------------------------------------------------------------
# Prompt: the instructions the model reads (no image-specific words)
# --------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You read hand-drawn or printed architectural floor plans and transcribe "
    "only the measurements written on them. You never invent or convert values."
)

USER_PROMPT = """Transcribe EVERY written measurement on this floor plan.

Include dimension labels next to arrows, sizes written inside rooms (such as
10'x12' or 1.098 x 2.069), gaps and passages, and wall-thickness notes.

Rules:
1. text_as_written: copy the label EXACTLY as written, keeping every mark
   (' " . x /). Do not convert units, round, add digits or drop decimal points.
2. plan_unit: the unit system of the plan. Use feet_inches if you see ' or "
   marks. If a unit such as m, cm or mm is written, use it. If numbers have
   decimals and no unit mark (e.g. 6.680, 1.098 x 2.069), choose metres.
   Only use unclear if you genuinely cannot tell. In unit_evidence, say in a
   few words what you saw.
3. applies_to: the room or part the label belongs to. For a label on an arrow
   (dimension line), describe what the arrow spans, for example
   "left wall to dividing wall of the upper room".
4. direction: horizontal or vertical for labels on an arrow, otherwise none.
5. position: where the label sits on the page, as a 3x3 grid cell.
6. kind: overall (whole-plan width or height), room_size (a size written
   inside a room; may be one value, e.g. 6'-0" WIDE, or a width x depth pair),
   segment (one part of a wall or a single dimension line), wall_thickness, or
   other. Never add a second value that is not written inside the same label.
7. Do NOT list room names, door or window symbols, single letters such as D,
   W or N, scale text, titles or page numbers.
8. If a digit is overwritten, crossed out or unclear, give your best reading
   and set legibility to partly_unclear or hard_to_read. Never guess silently.
9. Return JSON only."""


# --------------------------------------------------------------------------
# Plan values helper
# --------------------------------------------------------------------------

def plan_values(plan: dict) -> list[dict]:
    """Attach parsed millimetre values to every dimension item.

    If plan_unit is 'unclear', attempts to infer the unit from all dimension texts.
    Items that cannot be parsed get an empty list and an 'error' note.
    """
    unit = plan.get("plan_unit", "unclear")
    dimensions = plan.get("dimensions", [])
    if unit == "unclear":
        all_texts = [item.get("text_as_written", "") for item in dimensions]
        inferred = infer_unit(all_texts)
        if inferred != "unclear":
            unit = inferred

    out = []
    for item in dimensions:
        entry = dict(item)
        try:
            entry["mm"] = parse_label(item.get("text_as_written", ""), unit)
            entry["error"] = ""
        except ValueError as exc:
            entry["mm"] = []
            entry["error"] = str(exc)
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# Sanity checks (plain code)
# --------------------------------------------------------------------------

MIN_MM = 50.0        # smaller than a thin partition wall
MAX_MM = 60_000.0    # larger than a very big house plan


def validate(plan: dict) -> list[str]:
    """Return warnings for values that look wrong or inconsistent."""
    warnings: list[str] = []
    unit = plan.get("plan_unit", "unclear")
    dimensions = plan.get("dimensions", [])

    if unit == "unclear":
        all_texts = [item.get("text_as_written", "") for item in dimensions]
        inferred = infer_unit(all_texts)
        if inferred != "unclear":
            warnings.append(f"plan unit was unclear; inferred '{inferred}' from labels")
            unit = inferred
        else:
            warnings.append("plan unit is unclear: values cannot be converted, ask the user")

    for index, item in enumerate(plan_values(plan), start=1):
        text = item.get("text_as_written", "?")
        name = f"#{index} '{text}' ({item.get('applies_to', '?')})"

        if item["error"]:
            warnings.append(f"{name}: {item['error']}")
            continue
        if not item["mm"]:
            warnings.append(f"{name}: no number found in the label")
            continue

        for value in item["mm"]:
            if not MIN_MM <= value <= MAX_MM:
                warnings.append(f"{name}: {format_mm(value, unit)} is outside the plausible range")

        if item.get("kind") == "room_size":
            has_single_ok = len(item["mm"]) == 1 and bool(re.search(r"\b(wide|deep)\b", text, re.IGNORECASE))
            if not (len(item["mm"]) == 2 or has_single_ok):
                warnings.append(f"{name}: kind 'room_size' should hold 2 value(s), found {len(item['mm'])}")
        elif item.get("kind") != "other":
            if len(item["mm"]) != 1:
                warnings.append(f"{name}: kind '{item.get('kind')}' should hold 1 value(s), found {len(item['mm'])}")

        has_marks = "'" in _normalise(text) or '"' in _normalise(text)
        if unit != "feet_inches" and has_marks:
            warnings.append(f"{name}: has feet/inch marks but the plan unit is {unit}")
        if unit == "feet_inches" and re.search(r"\d\.\d{2,}", text):
            warnings.append(f"{name}: looks metric but the plan unit is feet_inches")

        if item.get("legibility") != "clear":
            warnings.append(f"{name}: marked {item.get('legibility')}, check by eye")
    return warnings
