"""Tests for the constraint checker. No model, no images."""
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

import checks
from checks import Label, chain_constraint, equal_constraint, candidate_edits, check, suggest

FT = 304.8


def mk(texts: dict, unit="feet_inches"):
    return {i: Label(i, t, unit) for i, t in texts.items()}


def chain(tol=0.0, wall=0.0):
    return [chain_constraint("c", "all", ["a", "b"], tol, wall)]


def test_consistent_chain_has_no_violation():
    labels = mk({"all": "30'-0\"", "a": "10'-0\"", "b": "20'-0\""})
    assert check(labels, chain()) == []


def test_wrong_segment_is_flagged_and_true_fix_is_offered():
    # One chain alone cannot say WHICH of its labels is wrong, so the true fix
    # must be among the top suggestions, not necessarily first.
    labels = mk({"all": "30'-0\"", "a": "10'-0\"", "b": "25'-0\""})     # 25 should be 20
    assert check(labels, chain())
    top = [(s.label_id, s.new_text, s.remaining_violations) for s in suggest(labels, chain())[:5]]
    assert ("b", "20'-0\"", 0) in top


def test_tolerance_hides_small_errors_by_design():
    labels = mk({"all": "30'-0\"", "a": "10'-0\"", "b": "20'-3\""})     # 3 inches off
    assert check(labels, chain(tol=152.4)) == []
    assert check(labels, chain(tol=0.0))


def test_known_wall_slack_is_not_a_conflict():
    # clear sizes: 9'-6" + 19'-6" + 2 walls of 6" = 30'-0"
    labels = mk({"all": "30'-0\"", "a": "9'-6\"", "b": "19'-6\""})
    assert check(labels, chain(tol=25.4, wall=152.4)) == []
    assert check(labels, chain(tol=25.4, wall=0.0))         # without slack it is flagged


def test_unknown_wall_uses_a_window_not_a_point():
    labels = mk({"all": "30'-0\"", "a": "9'-6\"", "b": "19'-6\""})     # overall - sum = 12 in
    window = [chain_constraint("c", "all", ["a", "b"], 25.4, None, wall_max_mm=230.0)]
    assert check(labels, window) == []


def test_equal_constraint():
    labels = mk({"x": "46'-0\"", "y": "46'-6\""})
    assert check(labels, [equal_constraint("e", "x", "y", 25.4)])
    assert check(labels, [equal_constraint("e", "x", "y", 300)]) == []


def test_second_constraint_disambiguates_which_label_is_wrong():
    # a is wrong: it breaks the chain AND disagrees with its twin in the second constraint
    labels = mk({"all": "30'-0\"", "a": "15'-0\"", "b": "20'-0\"", "a2": "10'-0\""})
    cons = chain() + [equal_constraint("e", "a", "a2", 0)]
    best = suggest(labels, cons)[0]
    assert (best.label_id, best.new_text) == ("a", "10'-0\"")


def test_confusion_edit_is_cheaper_than_arbitrary_edit():
    cost = dict(candidate_edits("5.018", "metres"))
    assert cost["5.015"] == 0.5          # 8 -> 5 is a known confusion
    assert cost["5.010"] == 0.5          # 8 -> 0 is a known confusion
    assert cost["5.017"] == 0.75         # off by one digit
    assert cost["5.012"] == 1.0          # arbitrary digit


@pytest.mark.parametrize("text", ["9'-9\"", "4'1 1/2\"", "46'-0\"", "20'6\""])
def test_edits_never_produce_impossible_labels(text):
    for new, _ in candidate_edits(text, "feet_inches"):
        assert checks._FEET_PATTERN.match(new), new
        assert int(checks._FEET_PATTERN.match(new).group(2)) <= 11, new


def test_fraction_digits_are_not_edited():
    assert not any("1/9" in n or "1/7" in n for n, _ in candidate_edits("4'1 1/2\"", "feet_inches"))


def test_unparseable_label_is_skipped_not_crashing():
    labels = mk({"all": "30'-0\"", "a": "??", "b": "20'-0\""})
    assert check(labels, chain()) == []
    assert "a" not in checks.covered_ids(labels, chain())


def test_reader_plan_adapter_builds_chain_and_checks_it():
    plan = {"plan_unit": "feet_inches",
            "left_chain": [{"text_as_written": "12'-6\"", "kind": "segment"},
                           {"text_as_written": "25'-0\"", "kind": "segment"},
                           {"text_as_written": "36'-0\"", "kind": "overall"}],
            "right_chain": []}
    labels, cons = checks.constraints_from_reader_plan(plan, tol_mm=152.4, wall_mm=None)
    assert len(cons) == 1 and cons[0].total == "left_2"
    assert check(labels, cons)            # 12.5 + 25 = 37.5 ft vs 36 ft overall
