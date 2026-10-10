"""Unit tests for dimension parsing, unit inference, and formatting."""

import sys
from pathlib import Path

import pytest

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from parse import (  # noqa: E402
    MM_PER_FOOT,
    MM_PER_INCH,
    UNITS,
    _normalise,
    format_mm,
    infer_unit,
    parse_label,
)
import floorplan_reader_v2 as reader  # noqa: E402


class TestNormalise:
    def test_unicode_single_quotes(self):
        assert _normalise("10\u2032") == "10'"
        assert _normalise("10\u2019") == "10'"
        assert _normalise("10\u2018") == "10'"
        assert _normalise("10`") == "10'"

    def test_unicode_double_quotes(self):
        assert _normalise('7\u2033') == '7"'
        assert _normalise('7\u201d') == '7"'
        assert _normalise('7\u201c') == '7"'
        assert _normalise("7''") == '7"'

    def test_multiplication_sign(self):
        assert _normalise("10\u00d712") == "10x12"

    def test_whitespace_stripping(self):
        assert _normalise("  8'7\"  ") == "8'7\""


class TestInferUnit:
    def test_feet_inch_marks(self):
        assert infer_unit(["10'x12'"]) == "feet_inches"
        assert infer_unit(['8"']) == "feet_inches"
        assert infer_unit(["8'7\""]) == "feet_inches"

    def test_explicit_suffixes(self):
        assert infer_unit(["1500mm"]) == "millimetres"
        assert infer_unit(["150cm"]) == "centimetres"
        assert infer_unit(["6.034m"]) == "metres"

    def test_decimal_metric_range(self):
        # test3 labels
        labels = ["6.680", "3.210", "1.935", "2.965", "2.702", "5.018", "2.418"]
        assert infer_unit(labels) == "metres"

    def test_room_size_decimal_metres(self):
        assert infer_unit(["1.098 x 2.069"]) == "metres"

    def test_ambiguous_or_unclear(self):
        assert infer_unit([]) == "unclear"
        assert infer_unit(["   "]) == "unclear"
        assert infer_unit(["100", "200"]) == "unclear"


class TestParseLabelFeetInches:
    def test_standard_feet_and_inches(self):
        assert parse_label("8'7\"", "feet_inches") == [2616.2]

    def test_feet_and_inches_with_half(self):
        assert parse_label("8'7 1/2\"", "feet_inches") == [2628.9]

    def test_feet_and_inches_with_unicode_half(self):
        assert parse_label("8'7½\"", "feet_inches") == [2628.9]

    def test_missing_trailing_quote(self):
        # "8'7" without trailing " should still parse as 8 feet 7 inches
        assert parse_label("8'7", "feet_inches") == [2616.2]
        assert parse_label("8'7 1/2", "feet_inches") == [2628.9]

    def test_hyphenated_feet_inches(self):
        assert parse_label("8'-7\"", "feet_inches") == [2616.2]
        assert parse_label("8'-7 1/2\"", "feet_inches") == [2628.9]

    def test_feet_only(self):
        assert parse_label("10'", "feet_inches") == [3048.0]

    def test_inches_only(self):
        assert parse_label('7"', "feet_inches") == [177.8]
        assert parse_label('9"', "feet_inches") == [228.6]

    def test_inches_with_half(self):
        assert parse_label('4 1/2"', "feet_inches") == [114.3]
        assert parse_label('4½"', "feet_inches") == [114.3]

    def test_pure_fraction_inches(self):
        assert parse_label('1/2"', "feet_inches") == [12.7]

    def test_room_size_feet_inches(self):
        assert parse_label("10'x12'", "feet_inches") == [3048.0, 3657.6]

    def test_bare_room_size(self):
        assert parse_label("10x12", "feet_inches") == [3048.0, 3657.6]


class TestParseLabelMetric:
    def test_decimal_metres(self):
        assert parse_label("6.034", "metres") == [6034.0]

    def test_room_size_metres(self):
        assert parse_label("1.098 x 2.069", "metres") == [1098.0, 2069.0]

    def test_explicit_suffixes(self):
        assert parse_label("3.051m", "metres") == [3051.0]
        assert parse_label("150cm", "metres") == [1500.0]
        assert parse_label("230mm", "metres") == [230.0]

    def test_centimetres_unit(self):
        assert parse_label("150", "centimetres") == [1500.0]

    def test_millimetres_unit(self):
        assert parse_label("2300", "millimetres") == [2300.0]


class TestParseLabelUnclearFallback:
    def test_unclear_unit_infers_metres(self):
        # The key bug fix: model output unit is unclear, but value is 6.680
        assert parse_label("6.680", "unclear") == [6680.0]

    def test_unclear_unit_infers_feet_inches(self):
        assert parse_label("10'x12'", "unclear") == [3048.0, 3657.6]

    def test_unclear_cannot_be_inferred_raises(self):
        with pytest.raises(ValueError, match="cannot be inferred"):
            parse_label("100", "unclear")

    def test_invalid_unit_raises(self):
        with pytest.raises(ValueError, match="unknown unit"):
            parse_label("100", "lightyears")


class TestFormatMm:
    def test_format_feet_inches(self):
        assert format_mm(2628.9, "feet_inches") == '8\'7 1/2"'
        assert format_mm(2616.2, "feet_inches") == '8\'7"'
        assert format_mm(3048.0, "feet_inches") == '10\'0"'

    def test_format_metres(self):
        assert format_mm(6034.0, "metres") == "6.034 m"

    def test_format_centimetres(self):
        assert format_mm(1500.0, "centimetres") == "150.0 cm"

    def test_format_millimetres(self):
        assert format_mm(230.0, "millimetres") == "230 mm"


class TestFloorplanReaderIntegration:
    def test_plan_values_with_unclear_unit_infers_metres(self):
        plan = {
            "plan_unit": "unclear",
            "unit_evidence": "Numbers with decimals, no explicit unit.",
            "dimensions": [
                {
                    "text_as_written": "6.680",
                    "applies_to": "overall width",
                    "kind": "overall",
                    "direction": "horizontal",
                    "position": "top-left",
                    "legibility": "clear",
                },
                {
                    "text_as_written": "3.210",
                    "applies_to": "right section width",
                    "kind": "segment",
                    "direction": "horizontal",
                    "position": "top-right",
                    "legibility": "clear",
                },
            ],
        }
        items = reader.plan_values(plan)
        assert len(items) == 2
        assert items[0]["mm"] == [6680.0]
        assert items[0]["error"] == ""
        assert items[1]["mm"] == [3210.0]
        assert items[1]["error"] == ""

    def test_validate_with_unclear_unit(self):
        plan = {
            "plan_unit": "unclear",
            "unit_evidence": "Numbers with decimals, no explicit unit.",
            "dimensions": [
                {
                    "text_as_written": "6.680",
                    "applies_to": "overall width",
                    "kind": "overall",
                    "direction": "horizontal",
                    "position": "top-left",
                    "legibility": "clear",
                }
            ],
        }
        warnings = reader.validate(plan)
        assert any("inferred 'metres'" in w for w in warnings)
