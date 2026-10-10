"""Integration and unit tests for FastAPI demo backend.

Tests samples, plan retrieval, suggestion confirmation, reset,
upload error handling, SVG generation, and box sanity.
"""

from pathlib import Path
import sys
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

from backend.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_samples_manifest(client):
    """GET /api/samples returns manifest with exactly the 2 demo plans: synth_008 and test6."""
    resp = client.get("/api/samples")
    assert resp.status_code == 200
    samples = resp.json()
    assert isinstance(samples, list)
    assert len(samples) == 2

    ids = [s["id"] for s in samples]
    assert "synth_008" in ids
    assert "test6" in ids

    for s in samples:
        assert "id" in s
        assert "title" in s
        assert "kind" in s
        assert "description" in s
        assert "status_chip" in s
        assert "thumbnail_url" in s
        assert "image_url" in s


def test_get_plan_test4(client):
    """GET /api/plan/test4 returns verified plan with coverage, conflicts, and areas."""
    resp = client.get("/api/plan/test4")
    assert resp.status_code == 200
    data = resp.json()

    assert data["id"] == "test4"
    assert "coverage" in data
    assert "conflicts" in data
    assert "areas" in data
    assert "labels" in data
    assert len(data["conflicts"]) == 1

    cf = data["conflicts"][0]
    assert "sentence" in cf
    assert "ranked_suggestions" in cf
    assert len(cf["ranked_suggestions"]) > 0


def test_get_plan_synth_008(client):
    """GET /api/plan/synth_008 returns synthetic plan with 1 room conflict."""
    resp = client.get("/api/plan/synth_008")
    assert resp.status_code == 200
    data = resp.json()

    assert data["id"] == "synth_008"
    assert len(data["conflicts"]) == 1
    sug = data["conflicts"][0]["ranked_suggestions"][0]
    assert "new_text" in sug


def test_confirm_flow_and_reset_test4(client):
    """POST /api/confirm resolves test4 conflict and POST /api/reset restores it."""
    # Ensure fresh baseline
    client.post("/api/reset", json={"name": "test4"})

    plan_resp = client.get("/api/plan/test4")
    data = plan_resp.json()
    assert len(data["conflicts"]) == 1
    sug = data["conflicts"][0]["ranked_suggestions"][0]

    # Confirm suggestion
    conf_resp = client.post(
        "/api/confirm",
        json={"name": "test4", "label_id": sug["label_id"], "new_text": sug["new_text"]},
    )
    assert conf_resp.status_code == 200
    conf_data = conf_resp.json()
    assert conf_data["status"] == "success"
    assert conf_data["before_conflicts"] == 1
    assert conf_data["after_conflicts"] == 0

    # Verify plan has 0 conflicts
    updated_plan = client.get("/api/plan/test4").json()
    assert len(updated_plan["conflicts"]) == 0

    # Reset
    reset_resp = client.post("/api/reset", json={"name": "test4"})
    assert reset_resp.status_code == 200
    assert reset_resp.json()["status"] == "reset"

    # Verify conflict is restored
    restored_plan = client.get("/api/plan/test4").json()
    assert len(restored_plan["conflicts"]) == 1


def test_upload_returns_501(client):
    """POST /api/upload returns HTTP 501 Not Implemented."""
    resp = client.post("/api/upload")
    assert resp.status_code == 501
    assert "Live reading is not wired" in resp.json()["detail"]


def test_corrected_svg_rendering(client):
    """GET /api/corrected/{name} returns valid SVG for synthetic and real plans."""
    for name in ["synth_008", "test4"]:
        resp = client.get(f"/api/corrected/{name}")
        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]
        text = resp.text
        assert "<svg" in text
        assert "</svg>" in text
        # Drawn on white surface background
        assert "background:#FFFFFF" in text


def test_box_sanity(client):
    """Box sanity check: every box_ok label is inside image dimensions and not shared."""
    for plan_id in ["test4", "synth_008"]:
        resp = client.get(f"/api/plan/{plan_id}")
        assert resp.status_code == 200
        labels = resp.json()["labels"]
        boxes_seen = set()

        for lbl in labels:
            if lbl.get("box_ok") and lbl.get("box"):
                box = tuple(lbl["box"])
                assert len(box) == 4
                x0, y0, x1, y1 = box
                assert x0 >= 0 and y0 >= 0
                assert x1 > x0 and y1 > y0
                # Unique box per label
                assert box not in boxes_seen
                boxes_seen.add(box)


def test_preview_stores_nothing(client):
    """POST /api/preview validates, checks copy, and stores nothing."""
    # Invalid preview
    inv_resp = client.post("/api/preview", json={"name": "synth_008", "label_id": "room_1_w", "new_text": "bad_dim"})
    assert inv_resp.status_code == 200
    assert inv_resp.json()["valid"] is False
    assert "Invalid" in inv_resp.json()["error"] or "Parse" in inv_resp.json()["error"]

    # Valid preview resolving conflict
    val_resp = client.post("/api/preview", json={"name": "synth_008", "label_id": "room_1_w", "new_text": "4.729"})
    assert val_resp.status_code == 200
    data = val_resp.json()
    assert data["valid"] is True
    assert data["conflicts_remaining"] == 0
    assert data["resolved"] is True
    assert "changed_areas" in data
    assert data["sum_of_rooms_ratio"] is not None

    # Verify original in-memory copy was NOT modified (stores nothing)
    plan_resp = client.get("/api/plan/synth_008")
    assert len(plan_resp.json()["conflicts"]) == 1
    # Check that room_1_w is still 4.129
    r1 = next(l for l in plan_resp.json()["labels"] if l["id"] == "room_1_w")
    assert r1["text"] == "4.129"


def test_confirm_revert_changelog(client):
    """Confirm records history, changelog exports text, and revert restores value."""
    # Confirm edit
    c_resp = client.post("/api/confirm", json={"name": "synth_008", "label_id": "room_1_w", "new_text": "4.729", "source": "typed"})
    assert c_resp.status_code == 200
    assert len(c_resp.json()["remaining_conflicts"]) == 0
    assert len(c_resp.json()["change_history"]) == 1

    # Changelog
    cl_resp = client.get("/api/changelog/synth_008")
    assert cl_resp.status_code == 200
    assert "4.129 -> 4.729" in cl_resp.text
    assert "Source: typed" in cl_resp.text

    # Revert
    rev_resp = client.post("/api/revert", json={"name": "synth_008", "label_id": "room_1_w"})
    assert rev_resp.status_code == 200
    assert rev_resp.json()["status"] == "reverted"
    assert len(rev_resp.json()["plan"]["conflicts"]) == 1

    # Reset
    reset_resp = client.post("/api/reset", json={"name": "synth_008"})
    assert reset_resp.status_code == 200


def test_replaced_image_endpoint(client):
    """GET /api/replaced/{name} returns valid image bytes."""
    for name in ["synth_008", "test6"]:
        resp = client.get(f"/api/replaced/{name}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/")
        assert len(resp.content) > 100


def test_report_html_endpoint(client):
    """GET /api/report/{name} returns printable self-contained HTML audit report."""
    for name in ["synth_008", "test6"]:
        resp = client.get(f"/api/report/{name}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        html = resp.text
        assert "<!DOCTYPE html>" in html
        assert "<html" in html
        assert "@media print" in html
        assert "Plan Verification" in html


