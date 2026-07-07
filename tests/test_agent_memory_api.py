"""Tests for /api/agent-memory and the cross-kind /api/memories review surface.

Route contracts only (services covered by test_memory_writeback.py):
payload mapping, unsafe → 422, filter passthrough, kind resolution in the
unified review queue, error translation.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.agent_memory import AgentMemory
from app.services.memory_capture import capture_memory


def _mem(**overrides) -> AgentMemory:
    fields: dict[str, object] = dict(memory_type="lesson", content="Batch calls.")
    fields.update(overrides)
    return AgentMemory(**fields)  # type: ignore[arg-type]


@pytest.fixture
def client(monkeypatch):
    import app.routes.agent_memory as am_route
    import app.routes.memories_review as review_route

    calls = {
        "writeback": [],
        "review_capture": [],
        "review_memory": [],
        "recall": [],
        "usage": [],
    }

    pending_mem = _mem()
    confirmed_mem = _mem(
        review_status="confirmed",
        provenance_status="user_confirmed",
        can_use_as_instruction=True,
        created_at="2026-07-04T09:00:00+00:00",
    )
    pending_capture = capture_memory("A pending capture.", source="manual")

    async def fake_writeback(request, **kw):
        calls["writeback"].append(request)
        if request.memory_payload.outputs and "password" in request.memory_payload.outputs[0]:
            return {
                "success": False,
                "rejected": [{"reason": "credential_like_string", "memory_type": "output"}],
            }
        return {
            "success": True,
            "created": [{"id": "mem-1", "memory_type": "lesson"}],
            "replayed": False,
            "committed": True,
            "schema_version": "imi.memory.writeback.v1",
        }

    async def fake_review_agent_memory(memory_id, action, **kw):
        calls["review_memory"].append({"memory_id": memory_id, "action": action, **kw})
        return {"success": True, "review_applied": True, "gate_response": "allow"}

    async def fake_review_capture(capture_id, action, **kw):
        calls["review_capture"].append({"capture_id": capture_id, "action": action, **kw})
        return {"success": True, "review_applied": True, "gate_response": "allow"}

    class FakeMemoryStore:
        def _matches(self, *, memory_type=None, review_status=None, **kw):
            records = [pending_mem, confirmed_mem]
            if review_status:
                records = [m for m in records if m.review_status == review_status]
            if memory_type:
                records = [m for m in records if m.memory_type == memory_type]
            return records

        def list(self, *, memory_type=None, review_status=None, runtime_name=None,
                 task_id_prefix=None, limit=50):
            return self._matches(
                memory_type=memory_type, review_status=review_status
            )[:limit]

        def count(self, *, memory_type=None, review_status=None, runtime_name=None,
                  task_id_prefix=None):
            return len(
                self._matches(memory_type=memory_type, review_status=review_status)
            )

        def get(self, memory_id):
            for m in (pending_mem, confirmed_mem):
                if m.id == memory_id:
                    return m
            return None

    class FakeCaptureStore:
        def list(self, *, review_status=None, source=None, limit=50):
            if review_status in (None, "pending"):
                return [pending_capture]
            return []

        def get(self, memory_id):
            return pending_capture if memory_id == pending_capture.id else None

    async def fake_recall(request, **kw):
        calls["recall"].append(request)
        return {
            "request_id": "req-1",
            "schema_version": "imi.memory.recall_response.v1",
            "memories": [{"record_id": "cap-1", "record_kind": "capture"}],
            "warnings": [],
        }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def commit(self):
            pass

    async def fake_apply_usage(session, request_id, used_memory_ids=None, ignored=None):
        calls["usage"].append(
            {"request_id": request_id, "used": used_memory_ids, "ignored": ignored}
        )
        return 0 if request_id == "missing-req" else 2

    async def fake_get_trace(session, request_id):
        if request_id == "missing-req":
            return None
        return {"request_id": request_id, "query": "q", "items": []}

    monkeypatch.setattr(am_route, "recall", fake_recall)
    monkeypatch.setattr(am_route, "_session_factory", lambda: FakeSession)
    monkeypatch.setattr(am_route, "apply_usage", fake_apply_usage)
    monkeypatch.setattr(am_route, "get_trace_with_items", fake_get_trace)
    monkeypatch.setattr(am_route, "writeback", fake_writeback)
    monkeypatch.setattr(am_route, "AgentMemoryStore", FakeMemoryStore)
    monkeypatch.setattr(review_route, "AgentMemoryStore", FakeMemoryStore)
    monkeypatch.setattr(review_route, "CaptureStore", FakeCaptureStore)
    monkeypatch.setattr(review_route, "review_agent_memory", fake_review_agent_memory)
    monkeypatch.setattr(review_route, "review_capture", fake_review_capture)

    test_app = FastAPI()
    test_app.include_router(am_route.router)
    test_app.include_router(review_route.router)
    return TestClient(test_app), calls, {
        "pending_mem": pending_mem,
        "confirmed_mem": confirmed_mem,
        "pending_capture": pending_capture,
    }


class TestWriteback:
    def test_writeback_delegates_and_returns_created(self, client):
        c, calls, _ = client
        resp = c.post(
            "/api/agent-memory/writeback",
            json={
                "memory_payload": {"lessons": ["Batch calls."]},
                "task_id": "task-1",
                "idempotency_key": "task-1-run-1",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["created"][0]["memory_type"] == "lesson"
        assert calls["writeback"][0].task_id == "task-1"

    def test_unsafe_writeback_422_with_reasons(self, client):
        c, _, _ = client
        resp = c.post(
            "/api/agent-memory/writeback",
            json={"memory_payload": {"outputs": ["password: supersecret123x"]}},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["rejected"][0]["reason"] == "credential_like_string"

    def test_writeback_rejects_confirmed_provenance(self, client):
        """ADR-002: the clamp is a schema-level 422, not a service concern."""
        c, calls, _ = client
        resp = c.post(
            "/api/agent-memory/writeback",
            json={
                "memory_payload": {"lessons": ["x"]},
                "provenance": {"default_status": "user_confirmed"},
            },
        )
        assert resp.status_code == 422
        assert not calls["writeback"]


class TestMemoriesListing:
    def test_list_memories(self, client):
        c, _, records = client
        body = c.get("/api/agent-memory/memories").json()
        assert body["total"] == 2
        assert {m["id"] for m in body["memories"]} == {
            records["pending_mem"].id,
            records["confirmed_mem"].id,
        }

    def test_detail_404(self, client):
        c, _, _ = client
        assert c.get("/api/agent-memory/memories/nope").status_code == 404

    def test_detail_returns_memory(self, client):
        c, _, records = client
        resp = c.get(f"/api/agent-memory/memories/{records['pending_mem'].id}")
        assert resp.status_code == 200
        assert resp.json()["memory_type"] == "lesson"


class TestRecallEndpoints:
    def test_recall_delegates(self, client):
        c, calls, _ = client
        resp = c.post(
            "/api/agent-memory/recall",
            json={"query": "what framework", "authority": "instruction"},
        )
        assert resp.status_code == 200
        assert resp.json()["request_id"] == "req-1"
        assert calls["recall"][0].authority == "instruction"

    def test_recall_empty_query_422(self, client):
        c, calls, _ = client
        assert (
            c.post("/api/agent-memory/recall", json={"query": "  "}).status_code
            == 422
        )
        assert not calls["recall"]

    def test_usage_delegates(self, client):
        c, calls, _ = client
        resp = c.post(
            "/api/agent-memory/recall/req-1/usage",
            json={
                "used_memory_ids": ["cap-1"],
                "ignored": [{"memory_id": "sig-1", "reason": "off-topic"}],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2
        assert calls["usage"][0]["request_id"] == "req-1"

    def test_usage_unknown_trace_404(self, client):
        c, _, _ = client
        resp = c.post(
            "/api/agent-memory/recall/missing-req/usage",
            json={"used_memory_ids": ["x"]},
        )
        assert resp.status_code == 404

    def test_trace_fetch(self, client):
        c, _, _ = client
        resp = c.get("/api/agent-memory/recall-traces/req-1")
        assert resp.status_code == 200
        assert resp.json()["request_id"] == "req-1"

    def test_trace_unknown_404(self, client):
        c, _, _ = client
        assert (
            c.get("/api/agent-memory/recall-traces/missing-req").status_code == 404
        )


class TestUnifiedReviewQueue:
    def test_queue_spans_captures_and_agent_memories(self, client):
        c, _, records = client
        body = c.get("/api/memories/review").json()
        kinds = {item["record_kind"] for item in body["items"]}
        assert kinds == {"capture", "agent_memory"}
        ids = {item["id"] for item in body["items"]}
        assert records["pending_capture"].id in ids
        assert records["pending_mem"].id in ids
        # only pending records appear
        assert records["confirmed_mem"].id not in ids

    def test_queue_kind_filter(self, client):
        c, _, _ = client
        body = c.get("/api/memories/review", params={"kind": "capture"}).json()
        assert {item["record_kind"] for item in body["items"]} == {"capture"}

    def test_review_resolves_capture_kind(self, client):
        c, calls, records = client
        resp = c.post(
            f"/api/memories/{records['pending_capture'].id}/review",
            json={"action": "confirm", "actor": "scott"},
        )
        assert resp.status_code == 200
        assert calls["review_capture"][0]["capture_id"] == records["pending_capture"].id
        assert not calls["review_memory"]

    def test_review_resolves_agent_memory_kind(self, client):
        c, calls, records = client
        resp = c.post(
            f"/api/memories/{records['pending_mem'].id}/review",
            json={"action": "evidence_only"},
        )
        assert resp.status_code == 200
        assert calls["review_memory"][0]["memory_id"] == records["pending_mem"].id
        assert not calls["review_capture"]

    def test_review_unknown_id_404(self, client):
        c, _, _ = client
        resp = c.post("/api/memories/nope/review", json={"action": "confirm"})
        assert resp.status_code == 404
