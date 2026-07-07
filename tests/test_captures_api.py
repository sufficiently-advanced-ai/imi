"""Tests for the /api/captures REST surface (Phase 1 of the OB1 absorption).

The routes are thin delegates to ``capture_service`` (the governance
chokepoint) — these tests cover the route contract: payload mapping, action
whitelist, filter passthrough, and error translation. The capture flow itself
is covered by test_capture_service.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.memory_capture import capture_memory


@pytest.fixture
def client(monkeypatch):
    import app.routes.captures as captures_route

    calls = {"create": [], "review": []}

    async def fake_capture_and_persist(content, source="manual", source_id=None, **kw):
        calls["create"].append({"content": content, "source": source, **kw})
        return {
            "success": True,
            "id": "cap-1",
            "deduped": False,
            "enrichment": {"type": "observation"},
            "vector_indexed": True,
            "committed": True,
            "capture": {"id": "cap-1", "content": content},
        }

    async def fake_review_capture(capture_id, action, **kw):
        calls["review"].append({"capture_id": capture_id, "action": action, **kw})
        if capture_id == "missing-id":
            return {"success": False, "error": f"Capture '{capture_id}' not found"}
        if capture_id == "bad-transition":
            return {"success": False, "error": "supersede requires a superseded_by"}
        return {
            "success": True,
            "review_applied": True,
            "audit_row_id": "audit-1",
            "gate_response": "allow",
            "committed": True,
            "capture": {"id": capture_id},
        }

    monkeypatch.setattr(
        captures_route, "capture_and_persist", fake_capture_and_persist
    )
    monkeypatch.setattr(captures_route, "review_capture", fake_review_capture)

    stored = [
        capture_memory("Alpha thought.", source="manual"),
        capture_memory("Beta thought.", source="web"),
    ]

    class FakeStore:
        def _matches(self, *, review_status=None, source=None):
            return [
                m
                for m in stored
                if (source is None or m.source == source)
                and (review_status is None or m.review_status == review_status)
            ]

        def list(self, *, review_status=None, source=None, limit=50):
            return self._matches(review_status=review_status, source=source)[:limit]

        def count(self, *, review_status=None, source=None):
            return len(self._matches(review_status=review_status, source=source))

        def get(self, memory_id):
            for m in stored:
                if m.id == memory_id:
                    return m
            return None

    monkeypatch.setattr(captures_route, "CaptureStore", FakeStore)

    test_app = FastAPI()
    test_app.include_router(captures_route.router)
    return TestClient(test_app), calls, stored


class TestCreateCapture:
    def test_post_delegates_and_returns_result(self, client):
        c, calls, _ = client
        resp = c.post(
            "/api/captures",
            json={"content": "A new thought.", "source": "manual", "actor": "scott"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "cap-1"
        assert body["deduped"] is False
        assert calls["create"][0]["content"] == "A new thought."
        assert calls["create"][0]["actor"] == "scott"

    def test_post_empty_content_rejected(self, client):
        c, calls, _ = client
        resp = c.post("/api/captures", json={"content": ""})
        assert resp.status_code == 422
        assert not calls["create"]

    def test_post_governance_fields_not_accepted(self, client):
        """ADR-002: provenance/authority are server-injected, never client params."""
        c, calls, _ = client
        resp = c.post(
            "/api/captures",
            json={"content": "sneaky", "can_use_as_instruction": True},
        )
        # extra fields are ignored by the schema and never reach the service
        assert resp.status_code == 200
        assert "can_use_as_instruction" not in calls["create"][0]


class TestListAndDetail:
    def test_list_returns_all(self, client):
        c, _, stored = client
        resp = c.get("/api/captures")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert {r["id"] for r in body["captures"]} == {m.id for m in stored}

    def test_list_filters_by_source(self, client):
        c, _, _ = client
        body = c.get("/api/captures", params={"source": "web"}).json()
        assert body["total"] == 1
        assert body["captures"][0]["source"] == "web"

    def test_total_reports_full_match_count_not_page_size(self, client):
        c, _, _ = client
        body = c.get("/api/captures", params={"limit": 1}).json()
        assert len(body["captures"]) == 1
        assert body["total"] == 2  # pre-truncation count

    def test_detail_returns_record(self, client):
        c, _, stored = client
        resp = c.get(f"/api/captures/{stored[0].id}")
        assert resp.status_code == 200
        assert resp.json()["content"] == "Alpha thought."

    def test_detail_unknown_404(self, client):
        c, _, _ = client
        assert c.get("/api/captures/nope").status_code == 404


class TestReviewCapture:
    def test_review_confirm_delegates(self, client):
        c, calls, _ = client
        resp = c.post(
            "/api/captures/cap-9/review", json={"action": "confirm", "actor": "scott"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["review_applied"] is True
        assert body["gate_response"] == "allow"
        assert calls["review"][0]["capture_id"] == "cap-9"
        assert calls["review"][0]["actor"] == "scott"

    def test_review_supersede_passes_successor(self, client):
        c, calls, _ = client
        resp = c.post(
            "/api/captures/cap-9/review",
            json={"action": "supersede", "superseded_by": "cap-10"},
        )
        assert resp.status_code == 200
        assert calls["review"][0]["superseded_by"] == "cap-10"

    def test_review_unknown_action_422(self, client):
        c, calls, _ = client
        resp = c.post("/api/captures/cap-9/review", json={"action": "bless"})
        assert resp.status_code == 422
        assert not calls["review"]

    def test_review_supersede_without_successor_422(self, client):
        c, calls, _ = client
        resp = c.post("/api/captures/cap-9/review", json={"action": "supersede"})
        assert resp.status_code == 422
        assert not calls["review"]

    def test_review_missing_id_404(self, client):
        c, _, _ = client
        resp = c.post("/api/captures/missing-id/review", json={"action": "confirm"})
        assert resp.status_code == 404

    def test_review_invalid_transition_400(self, client):
        c, _, _ = client
        resp = c.post(
            "/api/captures/bad-transition/review",
            json={"action": "supersede", "superseded_by": "cap-10"},
        )
        assert resp.status_code == 400
