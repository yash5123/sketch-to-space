"""Dimension text -> millimetre conversion (pure code, no AI).

This module handles:
  - Normalising Unicode quotes and symbols
  - Parsing feet-inches and metric labels into millimetre values
  - Inferring the unit system from a list of label texts
  - Formatting millimetre values back to human-readable strings
"""

import re
from typing import Optional

UNITS = ["feet_inches", "metres", "centimetres", "millimetres", "unclear"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MM_PER_FOOT = 304.8
MM_PER_INCH = 25.4
_METRIC_FACTOR = {"metres": 1000.0, "centimetres": 10.0, "millimetres": 1.0}
_SUFFIX_FACTOR = {"mm": 1.0, "cm": 10.0, "m": 1000.0}

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Splits "10'x12'" or "1.098 x 2.069" on the x separator.
_SPLIT = re.compile(r"(?<=[\d'\"])\s*[xX\u00d7]\s*(?=\d)")

# Feet-inches patterns, tried left to right:
#   1) feet'[-]inches[half]["?]   — e.g. 8'7", 8'7 1/2", 8'-7", 8'7 (missing ")
#   2) feet'                      — e.g. 10'
#   3) inches[half]" or half"     — e.g. 7", 4 1/2", 1/2"
_FEET_INCH = re.compile(
    r"(?P<feet>\d+)\s*(?:'\s*-?|-)\s*(?P<inches>\d+)(?P<half>\s*(?:1/2|\u00bd))?\s*\"?"
    r"|"
    r"(?P<feet_only>\d+)\s*'"
    r"|"
    r"(?:(?P<inch_only>\d+)(?P<half_only>\s*(?:1/2|\u00bd))?|(?P<half_pure>1/2|\u00bd))\s*\""
)

_HALF_REPAIR = re.compile(r"(\d+)\s*'\s*-?\s*(\d+)\s*/\s*[28](?=\s*\"|\s*[xX]|\s*$)")
_DASH_X_REPAIR = re.compile(r"(?<!\d)(\d+)\s*'\s*[xX\u00d7]\s*(\d{1,2}(?:\s*(?:1/2|\u00bd))?)\s*\"(?!\s*')")

# Metric number with optional suffix: 6.034, 150cm, 3.051m
_NUMBER = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|m)?(?![a-zA-Z0-9])")


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Replace Unicode quotes and symbols with ASCII equivalents."""
    text = text.replace("\u2032", "'").replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u2033", '"').replace("\u201d", '"').replace("\u201c", '"')
    text = text.replace("`", "'")
    text = text.replace("\u00d7", "x").replace("''", '"')
    return text.strip()


# ---------------------------------------------------------------------------
# Unit inference
# ---------------------------------------------------------------------------

def infer_unit(texts: list[str]) -> str:
    """Guess the unit system from a list of raw label texts.

    Heuristic (applied in order):
      1. Any label has ' or " marks -> feet_inches.
      2. An explicit suffix (m, cm, mm) is present -> that unit.
      3. Values have decimals and fall in 0.05-50 -> metres.
      4. Otherwise -> unclear.
    """
    normalised = [_normalise(t) for t in texts if t.strip()]
    if not normalised:
        return "unclear"

    # Step 1: foot / inch marks
    if any("'" in t or '"' in t for t in normalised):
        return "feet_inches"

    # Step 2: explicit metric suffixes
    joined = " ".join(normalised)
    if re.search(r"\d\s*mm\b", joined):
        return "millimetres"
    if re.search(r"\d\s*cm\b", joined):
        return "centimetres"
    if re.search(r"\d\s*m\b", joined):
        return "metres"

    # Step 3: decimal numbers in a plausible range for metres
    numbers: list[float] = []
    for t in normalised:
        for part in _SPLIT.split(t):
            for m in re.finditer(r"\d+(?:\.\d+)?", part):
                try:
                    numbers.append(float(m.group()))
                except ValueError:
                    pass
    if not numbers:
        return "unclear"

    has_decimals = any("." in t for t in normalised)
    if has_decimals and all(0.05 <= n <= 50 for n in numbers):
        return "metres"

    return "unclear"


# ---------------------------------------------------------------------------
# Parsing: text -> millimetres
# ---------------------------------------------------------------------------

def parse_label(text: str, unit: str) -> list[float]:
    """Convert a transcribed dimension label into millimetre values.

    Examples (feet-inches):
        "10'x12'"      -> [3048.0, 3657.6]
        "8'7 1/2\""   -> [2628.9]
        "8'7"          -> [2616.2]   (no trailing ", still works)
        "4 1/2\""     -> [114.3]

    Examples (metric, unit='metres'):
        "6.034"         -> [6034.0]
        "1.098 x 2.069" -> [1098.0, 2069.0]

    If *unit* is ``'unclear'``, ``infer_unit`` is tried on the single label.
    Raises ``ValueError`` when the unit truly cannot be determined.
    """
    if unit not in UNITS:
        raise ValueError(f"unknown unit: {unit!r}")
    if unit == "unclear":
        inferred = infer_unit([text])
        if inferred == "unclear":
            raise ValueError(
                f"unit is 'unclear' and cannot be inferred from '{text}'"
            )
        unit = inferred

    cleaned = _normalise(text)
    if unit == "feet_inches":
        cleaned = _HALF_REPAIR.sub(r"\1'\2 1/2", cleaned)
        cleaned = _DASH_X_REPAIR.sub(r"\1'-\2\"", cleaned)

    values: list[float] = []
    for part in _SPLIT.split(cleaned):
        if unit == "feet_inches":
            found = False
            for match in _FEET_INCH.finditer(part):
                if match.group("feet") is not None:
                    # Pattern 1: feet'[-]inches (trailing " optional)
                    feet = int(match.group("feet"))
                    inches = int(match.group("inches"))
                    half = 0.5 if match.group("half") else 0.0
                elif match.group("feet_only") is not None:
                    # Pattern 2: feet only
                    feet = int(match.group("feet_only"))
                    inches = 0
                    half = 0.0
                elif match.group("inch_only") is not None or match.group("half_pure") is not None:
                    # Pattern 3: inches only (trailing " required)
                    feet = 0
                    inches = int(match.group("inch_only")) if match.group("inch_only") else 0
                    half = 0.5 if (match.group("half_only") or match.group("half_pure")) else 0.0
                else:
                    continue
                values.append(round(feet * MM_PER_FOOT + (inches + half) * MM_PER_INCH, 1))
                found = True
            if not found:
                # Bare number like "12" in "10x12": treat as feet.
                bare = re.search(r"\d+(?:\.\d+)?", part)
                if bare:
                    values.append(round(float(bare.group()) * MM_PER_FOOT, 1))
        else:
            clean_part = part
            if unit in ("metres", "centimetres"):
                clean_part = re.sub(r"\b(\d+)-(\d{2,3})\b", r"\1.\2", clean_part)
            for match in _NUMBER.finditer(clean_part):
                factor = _SUFFIX_FACTOR.get(
                    match.group("unit") or "", _METRIC_FACTOR[unit]
                )
                values.append(round(float(match.group("num")) * factor, 1))
    return values


# ---------------------------------------------------------------------------
# Formatting: millimetres -> readable text
# ---------------------------------------------------------------------------

def format_mm(value: float, unit: str) -> str:
    """Readable form of a millimetre value in the plan's own unit."""
    if unit == "feet_inches":
        total_inches = round(value / MM_PER_INCH * 2) / 2
        feet = int(total_inches // 12)
        rest = total_inches - feet * 12
        whole = int(rest)
        half = " 1/2" if rest - whole >= 0.5 else ""
        return f"{feet}'{whole}{half}\""
    if unit == "metres":
        return f"{value / 1000:.3f} m"
    if unit == "centimetres":
        return f"{value / 10:.1f} cm"
    return f"{value:.0f} mm"
