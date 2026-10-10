"""PaddleOCR engine wrapper for architectural floor plans.

Provides fast, physical text bounding-box detection and character transcription
to cross-check Vision-LLM outputs and suppress hallucinated extras.
"""

import os
import sys
import warnings
import logging
import contextlib
from pathlib import Path
from typing import Any, Optional

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

# Disable oneDNN/MKLDNN on CPU to prevent Paddle 3.3.x PIR executor attribute bug
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
os.environ["GLOG_minloglevel"] = "3"
os.environ["PADDLE_LOG_LEVEL"] = "3"
os.environ["PADDLEX_LOG_LEVEL"] = "ERROR"

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


@contextlib.contextmanager
def _silence_native():
    """Silence all C/C++ native stdout/stderr during Paddle model loading."""
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        saved_stdout_fd = os.dup(1)
        saved_stderr_fd = os.dup(2)
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        try:
            yield
        finally:
            os.dup2(saved_stdout_fd, 1)
            os.dup2(saved_stderr_fd, 2)
            os.close(saved_stdout_fd)
            os.close(saved_stderr_fd)
            os.close(devnull_fd)
    except Exception:
        yield


_OCR_INSTANCE = None


def get_ocr_instance():
    """Lazily initialize and cache the PaddleOCR singleton instance silently."""
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        with _silence_native():
            from paddleocr import PaddleOCR
            # PP-OCRv4 / PP-OCRv6 default pipeline for English text
            _OCR_INSTANCE = PaddleOCR(lang="en")
    return _OCR_INSTANCE


def extract_text_boxes(image_path: str) -> list[dict]:
    """Run PaddleOCR on an image and return structured text boxes.

    Returns:
        List of detections, each containing:
            - text (str): recognized text
            - confidence (float): OCR confidence (0.0 to 1.0)
            - box (list[float]): bounding box [xmin, ymin, xmax, ymax] in pixels
            - poly (list[list[float]]): 4-corner polygon coordinates
    """
    ocr = get_ocr_instance()
    with _silence_native():
        results = ocr.predict(str(image_path))
    if not results:
        return []

    page = results[0]
    rec_texts = page.get("rec_texts", [])
    rec_scores = page.get("rec_scores", [])
    rec_boxes = page.get("rec_boxes", [])
    dt_polys = page.get("dt_polys", [])

    detections = []
    for idx, (txt, score) in enumerate(zip(rec_texts, rec_scores)):
        poly = dt_polys[idx] if idx < len(dt_polys) else []
        box = rec_boxes[idx] if idx < len(rec_boxes) else []

        if hasattr(poly, "tolist"):
            poly = poly.tolist()
        elif isinstance(poly, (list, tuple)):
            poly = [p.tolist() if hasattr(p, "tolist") else [float(v) for v in p] if isinstance(p, (list, tuple)) else float(p) for p in poly]

        if hasattr(box, "tolist"):
            box = box.tolist()
        clean_box = [float(v) for v in box]

        # Clean string formatting
        clean_text = txt.strip()
        if not clean_text:
            continue

        detections.append({
            "text": clean_text,
            "confidence": float(score),
            "box": clean_box,
            "poly": poly,
        })

    return detections


def find_matching_ocr_boxes(
    pred_text: str,
    ocr_detections: list[dict],
    unit: str = "feet_inches",
) -> list[dict]:
    """Find all PaddleOCR text detections that numerically or textually match pred_text."""
    from parse import parse_label, _normalise

    matches = []
    try:
        pred_mms = parse_label(pred_text, unit)
    except Exception:
        pred_mms = []

    pred_norm = _normalise(pred_text).replace("-", "").replace(" ", "")

    for det in ocr_detections:
        raw_det = det["text"]
        det_norm = _normalise(raw_det).replace("-", "").replace(" ", "")

        # 1. String match
        if pred_norm and pred_norm == det_norm:
            matches.append(det)
            continue

        # 2. Numerical mm match
        try:
            det_mms = parse_label(raw_det, unit)
        except Exception:
            det_mms = []

        if pred_mms and det_mms:
            # 1D match
            if len(pred_mms) == 1 and len(det_mms) == 1:
                if abs(pred_mms[0] - det_mms[0]) <= 2.0:
                    matches.append(det)
            # 2D pair match
            elif len(pred_mms) == 2 and len(det_mms) == 2:
                if (abs(pred_mms[0] - det_mms[0]) <= 2.0 and abs(pred_mms[1] - det_mms[1]) <= 2.0) or \
                   (abs(pred_mms[0] - det_mms[1]) <= 2.0 and abs(pred_mms[1] - det_mms[0]) <= 2.0):
                    matches.append(det)

    return matches


def has_dimension_feature(text: str) -> bool:
    """Check if text contains numeric dimension features (decimals, imperial marks, multipliers, or 3+ digit numbers)."""
    import re
    digits_in_txt = re.findall(r"\d+", text)
    return bool(
        re.search(r"\d+\.\d+", text)
        or re.search(r"['\"′″'']", text)
        or re.search(r"\d+\s*[xX×]\s*\d+", text)
        or (digits_in_txt and any(len(d_str) >= 3 for d_str in digits_in_txt))
    )


def filter_dimension_detections(detections: list[dict]) -> list[dict]:
    """Filter detections down to items containing actual numeric dimensions."""
    import re
    from parse import parse_label

    clean_dims = []
    for d in detections:
        txt = d["text"].strip()
        if not any(c.isdigit() for c in txt):
            continue
        # Filter isolated single digits (e.g. standalone "4" or "9")
        if txt.isdigit() and len(txt) == 1:
            continue

        # Normalize metric dash-as-dot if applicable (e.g. 1-231 -> 1.231)
        norm_txt = txt
        if re.search(r"\b\d+-\d{2,3}\b", norm_txt):
            norm_txt = re.sub(r"\b(\d+)-(\d{2,3})\b", r"\1.\2", norm_txt)

        # Filter strings that have letters and only a single isolated digit without decimals, imperial marks, or multipliers
        # e.g. "Bebeoon4" (Bedroom 4), "V9" (grid V9), "D1", "W2"
        has_letters = bool(re.search(r"[a-zA-Z]", norm_txt))
        if has_letters and not has_dimension_feature(norm_txt):
            continue

        # Check parseability
        mms_m, mms_imp = [], []
        try:
            mms_m = parse_label(norm_txt, "metres")
        except Exception:
            pass
        try:
            mms_imp = parse_label(norm_txt, "feet_inches")
        except Exception:
            pass

        if mms_m or mms_imp:
            if len(mms_m) == 2 or len(mms_imp) == 2:
                dim_type = "2D Room"
            elif len(mms_m) == 1 or len(mms_imp) == 1:
                dim_type = "1D Dimension"
            else:
                dim_type = "Dimension"

            clean_dims.append({
                "text": norm_txt,
                "raw_text": txt,
                "confidence": d["confidence"],
                "box": d["box"],
                "poly": d.get("poly", []),
                "type": dim_type,
                "mm": mms_m or mms_imp,
            })
    return clean_dims


def visualize_detections(
    image_path: str,
    detections: list[dict],
    save_path: Optional[str] = None,
) -> str:
    """Draw bounding boxes and detected numbers on the image and save."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    for idx, d in enumerate(detections, 1):
        box = d.get("box", [])
        if len(box) != 4:
            continue
        xmin, ymin, xmax, ymax = [int(v) for v in box]
        txt = d.get("text", "")
        conf = d.get("confidence", 1.0) * 100

        # Draw green bounding box for dimensions
        draw.rectangle([xmin, ymin, xmax, ymax], outline=(0, 200, 50), width=3)

        # Text banner
        label = f"#{idx}: {txt} ({conf:.0f}%)"
        # Draw background pill for text readability
        draw.rectangle([xmin, max(0, ymin - 18), min(img.width, xmin + len(label) * 8), ymin], fill=(0, 160, 40))
        draw.text((xmin + 2, max(0, ymin - 16)), label, fill=(255, 255, 255))

    if not save_path:
        stem = Path(image_path).stem
        os.makedirs("results", exist_ok=True)
        save_path = f"results/{stem}_detected.png"

    img.save(save_path)
    return save_path


def resolve_image_path(raw_path: str) -> str:
    """Find the image even if extension differs (e.g. .webp vs .png) or omitted."""
    p = Path(raw_path)
    if p.is_file():
        return str(p)
    candidates = [p, Path("sketches") / p.name, Path("sketches") / p.stem]
    for c in candidates:
        if c.is_file():
            return str(c)
        parent = c.parent if str(c.parent) != "." else Path("sketches")
        stem = c.stem
        for ext in [".webp", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"]:
            trial = parent / f"{stem}{ext}"
            if trial.is_file():
                return str(trial)
            trial_root = Path("sketches") / f"{stem}{ext}"
            if trial_root.is_file():
                return str(trial_root)
    return str(raw_path)


if __name__ == "__main__":
    import argparse
    import time

    parser = argparse.ArgumentParser(description="PaddleOCR architectural dimension extractor.")
    parser.add_argument("image", help="Path to floor plan image")
    parser.add_argument("--all", action="store_true", help="Show all detected text boxes including non-numbers")
    parser.add_argument("--visualize", "-v", action="store_true", help="Draw bounding boxes on image and save to results/")
    parser.add_argument("--raw", "-r", action="store_true", help="Print only raw numbers / dimensions")
    args = parser.parse_args()

    img_file = resolve_image_path(args.image)
    if not os.path.exists(img_file):
        print(f"Error: File '{args.image}' not found.")
        sys.exit(1)

    t0 = time.time()
    all_dets = extract_text_boxes(img_file)
    elapsed = time.time() - t0

    if args.all:
        display_items = all_dets
        title = f"ALL OCR TEXT DETECTIONS ({len(all_dets)} total)"
    else:
        display_items = filter_dimension_detections(all_dets)
        title = f"RAW DETECTED DIMENSIONS / NUMBERS ({len(display_items)} values)"

    if args.raw:
        raw_values = [d["text"] for d in display_items]
        print(f"\nRaw detected numbers ({len(raw_values)}):")
        print(", ".join(raw_values))
        for v in raw_values:
            print(f"  - {v}")
    else:
        print(f"\n{'='*82}")
        print(f"IMAGE: {img_file} | {title} | Time: {elapsed:.2f}s")
        print(f"{'='*82}")
        print(f"{'#':<4} {'DETECTED NUMBER / TEXT':<28} {'TYPE':<14} {'CONF':<8} {'BOUNDING BOX [xmin, ymin, xmax, ymax]'}")
        print(f"{'-'*82}")

        for idx, d in enumerate(display_items, 1):
            txt = d["text"]
            conf = f"{d['confidence']*100:.1f}%"
            dtype = d.get("type", "Text")
            b = [int(v) for v in d["box"]]
            print(f"{idx:<4} {txt:<28} {dtype:<14} {conf:<8} {b}")

        print(f"{'='*82}")
        raw_list = [d["text"] for d in display_items]
        print(f"Raw Numbers Extracted: {', '.join(raw_list)}")
        print(f"Total: {len(display_items)} numbers detected across the floor plan.")

        if args.visualize:
            vis_path = visualize_detections(img_file, display_items)
            print(f"[Visualized] Bounding boxes saved to: {vis_path}")
        else:
            print("Tip: Add --visualize (or -v) to generate an annotated image with boxes drawn.")
        print(f"{'='*82}\n")
