"""Unit tests for the PaddleOCR wrapper engine."""

import os
import sys
from pathlib import Path

# Add backend to sys.path
root_dir = Path(__file__).resolve().parent.parent
backend_dir = root_dir / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from ocr_engine import find_matching_ocr_boxes


def test_find_matching_ocr_boxes_exact_string():
    detections = [
        {"text": "14'-6\"x11'-0\"", "confidence": 0.95, "box": [100, 200, 120, 300]},
        {"text": "TOILET", "confidence": 0.99, "box": [50, 50, 70, 100]},
    ]
    matches = find_matching_ocr_boxes("14'-6\"x11'-0\"", detections, "feet_inches")
    assert len(matches) == 1
    assert matches[0]["text"] == "14'-6\"x11'-0\""


def test_find_matching_ocr_boxes_numerical_tolerance():
    # Model returns 14'-6" but OCR read 14'-6"
    detections = [
        {"text": "14'-6\"", "confidence": 0.92, "box": [10, 10, 30, 50]},
        {"text": "50'-0\"", "confidence": 0.95, "box": [200, 200, 220, 250]},
    ]
    matches = find_matching_ocr_boxes("14'-6\"", detections, "feet_inches")
    assert len(matches) == 1
    assert matches[0]["text"] == "14'-6\""


def test_find_matching_ocr_boxes_reversed_pair():
    detections = [
        {"text": "11'-0\"x14'-6\"", "confidence": 0.91, "box": [100, 100, 130, 200]}
    ]
    matches = find_matching_ocr_boxes("14'-6\"x11'-0\"", detections, "feet_inches")
    assert len(matches) == 1
