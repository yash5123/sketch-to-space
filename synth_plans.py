"""Synthetic floor-plan generator with exact ground truth and injected errors.

For every plan it writes three files:
  sketches/synth/<name>.png       the rendered "photo" of a hand-drawn plan
  answers/<name>.json             labels AS WRITTEN on the image (for reader scoring,
                                  same format as the other answer sheets)
  synthetic/<name>_meta.json      the TRUE structure plus the injected errors
                                  (for scoring the solver)

Usage (from the project root, venv active):
    python synth_plans.py --count 10 --seed 7
    python synth_plans.py --count 6 --unit metres --error-rate 1.0
    python synth_plans.py --count 5 --font "C:/Windows/Fonts/Inkfree.ttf"

Honest limit: rendered handwriting-style fonts are CLEANER than real handwriting.
Reader results on these plans must be reported separately from real plans and
must never be presented as proof that real handwriting is read well.
"""

import argparse
import json
import math
import os
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

CANVAS_W, CANVAS_H = 1100, 1500
INK_COLOURS = [(20, 20, 70), (15, 15, 15), (35, 35, 110), (60, 30, 30)]
ROOM_NAMES = [
    "BED ROOM", "MASTER BED ROOM", "KITCHEN", "TOILET", "BATH", "HALL",
    "DRAWING", "DINING", "LIVING", "STORE", "POOJA", "STUDY", "PORCH",
]
HALF_INCH_MM = 12.7  # feet-inch plans are stored in half-inch units

# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

WINDOWS_HANDWRITING = ["Inkfree.ttf", "segoepr.ttf", "segoesc.ttf", "LHANDW.TTF", "comic.ttf"]
FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def find_fonts(extra: list[str]) -> list[str]:
    found = [p for p in extra if os.path.isfile(p)]
    win_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    found += [str(win_dir / n) for n in WINDOWS_HANDWRITING if (win_dir / n).is_file()]
    if not found:
        found = [p for p in FALLBACK_FONTS if os.path.isfile(p)]
        if found:
            print("NOTE: no handwriting font found, using a plain font. "
                  "Pass --font <path to .ttf> for more realistic output.")
    return found


def load_font(path, size: int):
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


# --------------------------------------------------------------------------
# Plan model and label formatting
# --------------------------------------------------------------------------

def format_value(units: int, unit: str, style: str) -> str:
    """Written form of a length. feet_inches: half-inch units. metres: millimetres."""
    if unit == "metres":
        return f"{units / 1000:.3f}"
    half_inches = units
    inches_total, odd = divmod(half_inches, 2)
    feet, inches = divmod(inches_total, 12)
    frac = " 1/2" if odd else ""
    sep = "-" if style == "dash" else ""
    return f"{feet}'{sep}{inches}{frac}\""


def unit_to_mm(units: int, unit: str) -> float:
    return units * HALF_INCH_MM if unit == "feet_inches" else float(units)


def partition(total: int, parts: int, min_part: int, rng: random.Random, grid: int) -> list[int]:
    """Split total into `parts` integers >= min_part that sum to total."""
    for _ in range(500):
        cuts = sorted(rng.sample(range(min_part, total - min_part + 1), parts - 1)) if parts > 1 else []
        cuts = [c if rng.random() > 0.7 else round(c / grid) * grid for c in cuts]
        edges = [0] + cuts + [total]
        sizes = [b - a for a, b in zip(edges, edges[1:])]
        if all(s >= min_part for s in sizes) and sum(sizes) == total:
            return sizes
    even = total // parts
    return [even] * (parts - 1) + [total - even * (parts - 1)]


def build_plan(rng: random.Random, unit: str, chain_segs: tuple[int, int] = (2, 4)) -> dict:
    min_segs, max_segs = chain_segs
    if unit == "feet_inches":
        total_w = 12 * rng.randint(40, 72)    # 20-36 ft in half-inch units (6 in steps)
        total_h = 12 * rng.randint(50, 100) if max_segs <= 4 else 12 * rng.randint(70, 130)   # 25-50 ft (or up to 65 ft)
        min_part, grid = (96, 6) if max_segs <= 4 else (48, 6)                # 4 ft minimum (or 2 ft for 5-7 segs), 3 in grid
    else:
        total_w = rng.randint(6000, 11000)
        total_h = rng.randint(7000, 14000) if max_segs <= 4 else rng.randint(12000, 20000)
        min_part, grid = (1500, 10) if max_segs <= 4 else (1000, 10)
    n_cols = rng.choice([2, 2, 3])
    cols = partition(total_w, n_cols, min_part, rng, grid)
    rows = [partition(total_h, rng.randint(min_segs, max_segs), min_part, rng, grid) for _ in cols]
    names = rng.sample(ROOM_NAMES, k=min(len(ROOM_NAMES), sum(len(r) for r in rows)))
    rooms, idx = [], 0
    for ci, col_rows in enumerate(rows):
        for ri, depth in enumerate(col_rows):
            rooms.append({"name": names[idx % len(names)], "col": ci, "row": ri,
                          "w": cols[ci], "d": depth})
            idx += 1
    return {"unit": unit, "total_w": total_w, "total_h": total_h, "cols": cols, "rows": rows,
            "rooms": rooms}


def build_labels(plan: dict, style: str) -> list[dict]:
    """Every written number on the plan, with its true text."""
    unit = plan["unit"]
    labels = []

    def add(label_id, role, value, applies_to):
        labels.append({"id": label_id, "role": role, "value": value, "applies_to": applies_to,
                       "truth_text": format_value(value, unit, style),
                       "written_text": format_value(value, unit, style)})

    for i, w in enumerate(plan["cols"]):
        add(f"top_seg_{i}", "top_segment", w, f"top chain segment {i + 1}")
    add("top_overall", "top_overall", plan["total_w"], "overall width")
    for j, d in enumerate(plan["rows"][0]):
        add(f"left_seg_{j}", "left_segment", d, f"left chain segment {j + 1}")
    add("left_overall", "left_overall", plan["total_h"], "overall height (left)")
    for j, d in enumerate(plan["rows"][-1]):
        add(f"right_seg_{j}", "right_segment", d, f"right chain segment {j + 1}")
    add("right_overall", "right_overall", plan["total_h"], "overall height (right)")
    for k, room in enumerate(plan["rooms"]):
        add(f"room_{k}_w", "room_w", room["w"], f"{room['name']} width")
        add(f"room_{k}_d", "room_d", room["d"], f"{room['name']} depth")
    return labels


# --------------------------------------------------------------------------
# Error injection
# --------------------------------------------------------------------------

CONFUSIONS = {"1": "7", "7": "1", "5": "6", "6": "5", "3": "8", "8": "3", "0": "6"}


def corrupt_text(text: str, unit: str, rng: random.Random) -> str:
    """Return a plausible misreading of a label (always different from the input)."""
    for _ in range(40):
        mode = rng.choice(["digit", "confuse", "half"])
        chars = list(text)
        positions = [i for i, c in enumerate(chars) if c.isdigit()]
        if not positions:
            continue
        if mode == "half" and unit == "feet_inches":
            new = text.replace(" 1/2", "") if " 1/2" in text else text[:-1] + ' 1/2"'
        elif mode == "confuse":
            candidates = [i for i in positions if chars[i] in CONFUSIONS]
            if not candidates:
                continue
            i = rng.choice(candidates)
            chars[i] = CONFUSIONS[chars[i]]
            new = "".join(chars)
        else:
            i = rng.choice(positions)
            chars[i] = str((int(chars[i]) + rng.randint(1, 9)) % 10)
            new = "".join(chars)
        if new != text:
            return new
    return text


def inject_errors(labels: list[dict], count: int, unit: str, rng: random.Random) -> list[dict]:
    chosen = rng.sample(labels, k=min(count, len(labels)))
    errors = []
    for lab in chosen:
        written = corrupt_text(lab["truth_text"], unit, rng)
        if written != lab["truth_text"]:
            lab["written_text"] = written
            errors.append({"id": lab["id"], "role": lab["role"],
                           "truth_text": lab["truth_text"], "written_text": written})
    return errors


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def wobble_line(draw, p0, p1, width, ink, rng, jitter=1.6, step=45):
    n = max(2, int(math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / step))
    pts = []
    for i in range(n + 1):
        t = i / n
        pts.append((p0[0] + (p1[0] - p0[0]) * t + rng.uniform(-jitter, jitter),
                    p0[1] + (p1[1] - p0[1]) * t + rng.uniform(-jitter, jitter)))
    draw.line(pts, fill=ink, width=width, joint="curve")


def arrow(draw, p0, p1, ink, rng):
    wobble_line(draw, p0, p1, 2, ink, rng, jitter=0.8)
    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    for tip, sign in ((p1, 1), (p0, -1)):
        a = ang if sign == 1 else ang + math.pi
        left = (tip[0] - 14 * math.cos(a - 0.4), tip[1] - 14 * math.sin(a - 0.4))
        right = (tip[0] - 14 * math.cos(a + 0.4), tip[1] - 14 * math.sin(a + 0.4))
        draw.polygon([tip, left, right], fill=ink)


def put_text(canvas, centre, text, font, ink, angle, rng):
    box = font.getbbox(text)
    layer = Image.new("RGBA", (box[2] - box[0] + 24, box[3] - box[1] + 24), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((12 - box[0], 12 - box[1]), text, font=font, fill=ink + (255,))
    angle += rng.uniform(-2.5, 2.5)
    layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    x = int(centre[0] - layer.width / 2 + rng.uniform(-3, 3))
    y = int(centre[1] - layer.height / 2 + rng.uniform(-3, 3))
    canvas.paste(layer, (x, y), layer)


def render(plan: dict, labels: list[dict], fonts: list[str], rng: random.Random) -> Image.Image:
    unit = plan["unit"]
    font_path = rng.choice(fonts) if fonts else None
    ink = rng.choice(INK_COLOURS)
    num_font = load_font(font_path, rng.randint(30, 38))
    name_font = load_font(font_path, rng.randint(24, 30))

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (244, 242, 236, 255))
    draw = ImageDraw.Draw(canvas)

    margin_l, margin_r, margin_t, margin_b = 200, 200, 190, 110
    total_w_mm = unit_to_mm(plan["total_w"], unit)
    total_h_mm = unit_to_mm(plan["total_h"], unit)
    scale = min((CANVAS_W - margin_l - margin_r) / total_w_mm,
                (CANVAS_H - margin_t - margin_b) / total_h_mm)
    x0, y0 = margin_l, margin_t
    x1, y1 = x0 + total_w_mm * scale, y0 + total_h_mm * scale

    def px(units):  # length in plan units -> pixels
        return unit_to_mm(units, unit) * scale

    text = {lab["id"]: lab["written_text"] for lab in labels}

    # Walls
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        wobble_line(draw, a, b, 5, ink, rng)
    col_x = [x0]
    for w in plan["cols"]:
        col_x.append(col_x[-1] + px(w))
    for cx in col_x[1:-1]:
        wobble_line(draw, (cx, y0), (cx, y1), 4, ink, rng)
    for ci, col_rows in enumerate(plan["rows"]):
        y = y0
        for depth in col_rows[:-1]:
            y += px(depth)
            wobble_line(draw, (col_x[ci], y), (col_x[ci + 1], y), 4, ink, rng)

    # Room names and sizes
    for k, room in enumerate(plan["rooms"]):
        cx = (col_x[room["col"]] + col_x[room["col"] + 1]) / 2
        top = y0 + sum(plan["rows"][room["col"]][:room["row"]]) * 0 + sum(
            px(d) for d in plan["rows"][room["col"]][:room["row"]])
        cy = top + px(room["d"]) / 2
        put_text(canvas, (cx, cy - 20), room["name"], name_font, ink, 0, rng)
        size = f"{text[f'room_{k}_w']} x {text[f'room_{k}_d']}"
        put_text(canvas, (cx, cy + 22), size, name_font, ink, 0, rng)

    def hline(y, xa, xb, label_id):
        arrow(draw, (xa, y), (xb, y), ink, rng)
        for xx in (xa, xb):
            wobble_line(draw, (xx, y - 10), (xx, y + 10), 2, ink, rng, jitter=0.5)
        put_text(canvas, ((xa + xb) / 2, y - 24), text[label_id], num_font, ink, 0, rng)

    def vline(x, ya, yb, label_id, side):
        arrow(draw, (x, ya), (x, yb), ink, rng)
        for yy in (ya, yb):
            wobble_line(draw, (x - 10, yy), (x + 10, yy), 2, ink, rng, jitter=0.5)
        put_text(canvas, (x + side * 26, (ya + yb) / 2), text[label_id], num_font, ink, 90, rng)

    # Top chain and overall width
    for i in range(len(plan["cols"])):
        hline(y0 - 50, col_x[i], col_x[i + 1], f"top_seg_{i}")
    hline(y0 - 115, x0, x1, "top_overall")

    # Left chain and overall height
    y = y0
    for j, depth in enumerate(plan["rows"][0]):
        vline(x0 - 50, y, y + px(depth), f"left_seg_{j}", -1)
        y += px(depth)
    vline(x0 - 115, y0, y1, "left_overall", -1)

    # Right chain and overall height
    y = y0
    for j, depth in enumerate(plan["rows"][-1]):
        vline(x1 + 50, y, y + px(depth), f"right_seg_{j}", 1)
        y += px(depth)
    vline(x1 + 115, y0, y1, "right_overall", 1)

    return canvas.convert("RGB")


def photo_effects(img: Image.Image, rng: random.Random) -> Image.Image:
    arr = np.asarray(img).astype(np.float32)
    h, w = arr.shape[:2]
    # soft lighting gradient + shadow
    yy, xx = np.mgrid[0:h, 0:w]
    gradient = 1.0 - 0.10 * (xx / w) * rng.uniform(0, 1) - 0.08 * (yy / h) * rng.uniform(0, 1)
    arr *= gradient[..., None]
    arr += np.random.default_rng(rng.randint(0, 10**6)).normal(0, rng.uniform(2, 6), arr.shape)
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    out = out.rotate(rng.uniform(-1.5, 1.5), resample=Image.BICUBIC, fillcolor=(236, 234, 228))
    return out.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.1)))


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def answer_sheet(name: str, plan: dict, labels: list[dict]) -> dict:
    by_id = {lab["id"]: lab for lab in labels}
    dims = []
    for lab in labels:
        if lab["role"] in ("room_w", "room_d"):
            continue
        dims.append({"applies_to": lab["applies_to"], "values": [lab["written_text"]]})
    for k, room in enumerate(plan["rooms"]):
        dims.append({"applies_to": room["name"],
                     "values": [by_id[f"room_{k}_w"]["written_text"], by_id[f"room_{k}_d"]["written_text"]]})
    return {"plan": name, "unit": plan["unit"],
            "note": "SYNTHETIC. Labels exactly as written on the image (including injected errors).",
            "dimensions": dims}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic floor-plan sketches.")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--unit", choices=["feet_inches", "metres", "mixed"], default="mixed")
    parser.add_argument("--error-rate", type=float, default=0.5,
                        help="probability a plan gets injected errors (default 0.5)")
    parser.add_argument("--max-errors", type=int, default=2)
    parser.add_argument("--chain-segments", default="2-4",
                        help="range of segments per chain, e.g. '5-7' or '2-4' (default 2-4)")
    parser.add_argument("--font", action="append", default=[], help="path to a .ttf (repeatable)")
    parser.add_argument("--prefix", default="synth")
    parser.add_argument("--meta-dir", default="synthetic", help="directory to write metadata JSON")
    parser.add_argument("--skip-images", action="store_true", help="skip rendering PNG images")
    args = parser.parse_args()

    # Parse chain segments range
    try:
        parts = [int(p.strip()) for p in args.chain_segments.split("-")]
        chain_segs = (parts[0], parts[1]) if len(parts) == 2 else (parts[0], parts[0])
    except Exception:
        chain_segs = (2, 4)

    fonts = find_fonts(args.font)
    print(f"Fonts used: {fonts or 'PIL default'} | Chain segments range: {chain_segs}")
    for d in ("sketches/synth", "answers/synth", args.meta_dir):
        os.makedirs(d, exist_ok=True)

    for n in range(1, args.count + 1):
        rng = random.Random(args.seed * 1000 + n)
        unit = args.unit if args.unit != "mixed" else ("feet_inches" if n % 2 else "metres")
        style = rng.choice(["dash", "plain"])
        name = f"{args.prefix}_{n:03d}"

        plan = build_plan(rng, unit, chain_segs=chain_segs)
        labels = build_labels(plan, style)
        errors = []
        if rng.random() < args.error_rate:
            errors = inject_errors(labels, rng.randint(1, args.max_errors), unit, rng)

        if not args.skip_images:
            image = photo_effects(render(plan, labels, fonts, rng), rng)
            image.save(f"sketches/synth/{name}.png")
        with open(f"answers/synth/{name}.json", "w", encoding="utf-8") as fh:
            json.dump(answer_sheet(name, plan, labels), fh, indent=2)
        meta = {"name": name, "unit": unit, "style": style, "plan": plan,
                "labels": labels, "injected_errors": errors}
        with open(f"{args.meta_dir}/{name}_meta.json", "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)

        err_txt = ", ".join(f"{e['truth_text']} -> {e['written_text']}" for e in errors) or "none"
        print(f"{name}: {unit:11} {len(plan['rooms'])} rooms | injected errors: {err_txt}")
    print(f"\nDone. Images in sketches/synth, answer sheets in answers/synth/, truth in {args.meta_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
