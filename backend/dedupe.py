"""Generic de-duplication helpers for the multi-pass merge.

No room names, no sketch-specific values. Identity comes from geometry and
from the numbers themselves:

  * collapse_fragments : one reading is a PARTIAL read of another
                         (12'-6" vs 10' x 12'-6") -> keep the complete one.
  * band_compatible    : a horizontal band can only corroborate labels that sit
                         in that band (top band vs a label F placed at the bottom).
"""

import re

_STOP = {"room", "the", "and", "of", "a", "no", "unnamed", "next", "to"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]{3,}", (text or "").lower()) if t not in _STOP}


def _row(position: str | None) -> str | None:
    if not position:
        return None
    first = position.lower().split("-")[0]
    return {"top": "top", "middle": "middle", "center": "middle", "bottom": "bottom"}.get(first)


def band_compatible(reg_item: dict, band_source: str) -> bool:
    """False only when the band and the item are in OPPOSITE rows of the page.

    Adjacent rows are allowed because the 3x3 position the model reports for a
    label is coarse, and the bands overlap the middle row slightly.
    """
    row = _row(reg_item.get("position"))
    if row is None:
        return True
    if band_source == "H_top":
        return row != "bottom"
    if band_source == "H_bottom":
        return row != "top"
    return True


def _is_proper_fragment(small: list[float], big: list[float], tol: float = 2.0) -> bool:
    """small is made of some (not all) of big's values, e.g. [3810] within [3048, 3810]."""
    if not small or len(small) >= len(big):
        return False
    remaining = list(big)
    for value in small:
        hit = next((b for b in remaining if abs(b - value) <= tol), None)
        if hit is None:
            return False
        remaining.remove(hit)
    return True


def _related(a: dict, b: dict) -> bool:
    """Evidence that two readings are about the same physical label."""
    box_a, box_b = a.get("ocr_box"), b.get("ocr_box")
    if box_a is not None and box_a == box_b:
        return True
    return bool(_tokens(a.get("applies_to")) & _tokens(b.get("applies_to")))


def collapse_fragments(registry: list[dict]) -> list[dict]:
    """Merge partial readings into the complete reading of the same label.

    A fragment is only merged when exactly ONE complete candidate is related to
    it. If several candidates fit, nothing is merged and the fragment is marked
    status 'possible_fragment' so it is visible instead of silently guessed.
    Equal readings are left alone: two rooms may legitimately share a size.
    """
    drop: set[int] = set()
    for i, small in enumerate(registry):
        candidates = [
            j for j, big in enumerate(registry)
            if j != i and j not in drop
            and _is_proper_fragment(small.get("mm", []), big.get("mm", []))
            and _related(small, big)
        ]
        if len(candidates) == 1:
            big = registry[candidates[0]]
            big["sources"] = sorted(set(big.get("sources", [])) | set(small.get("sources", [])))
            big["merged_from"] = big.get("merged_from", []) + [small.get("text_as_written", "")]
            if not big.get("applies_to") and small.get("applies_to"):
                big["applies_to"] = small["applies_to"]
            drop.add(i)
        elif len(candidates) > 1:
            small["status_note"] = "possible_fragment"
    return [item for k, item in enumerate(registry) if k not in drop]
