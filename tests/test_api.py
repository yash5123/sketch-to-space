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
