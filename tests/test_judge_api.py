"""Tests for the /api/judge REST surface (Phase 4 of the OB1 absorption).

Route contracts only (judge logic covered by test_judge_service.py):
payload validation, delegation, and error translation. URL style follows imi
convention (/api/*, versioning rides in schema_version — deliberate deviation
from OB1's /v1/ paths).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    import app.routes.judge as judge_route

    calls = {"recall": [], "decide": []}

    async def fake_judge_recall(request, **kw):
        calls["recall"].append(request)
        return {
            "schema_version": "imi.judge.recall_response.v1",
            "recall_request_id": "req-1",
            "memories": [],
            "policy_hits": [
                {
                    "record_id": "dec-1",
                    "record_kind": "decision",
                    "content": "Never email the full client list.",
                    "required_behavior": "block",
                }
            ],
            "warnings": [],
        }

    async def fake_judge_decide(request, **kw):
        calls["decide"].append(request)
        return {
            "success": True,
            "decision_id": "jd-1",
            "replayed": False,
            "memory_written": [],
            "memory_write_rejected": [],
        }

    async def fake_get_decision(decision_id, **kw):
        if decision_id == "missing":
            return None
        return {"decision_id": decision_id, "decision": "block"}

    async def fake_list_decisions(**kw):
        calls.setdefault("list", []).append(kw)
        return [{"decision_id": "jd-1", "decision": "block"}]

    monkeypatch.setattr(judge_route, "judge_recall", fake_judge_recall)
    monkeypatch.setattr(judge_route, "judge_decide", fake_judge_decide)
    monkeypatch.setattr(judge_route, "get_judge_decision", fake_get_decision)
    monkeypatch.setattr(judge_route, "list_judge_decisions", fake_list_decisions)

    test_app = FastAPI()
    test_app.include_router(judge_route.router)
    return TestClient(test_app), calls


class TestJudgeRecall:
    def test_recall_delegates(self, client):
        c, calls = client
        resp = c.post(
            "/api/judge/recall",
            json={
                "query": "can I email the client list",
                "action_type": "external_side_effect",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["policy_hits"][0]["required_behavior"] == "block"
        assert calls["recall"][0].action_type == "external_side_effect"

    def test_recall_invalid_action_type_422(self, client):
        c, calls = client
        resp = c.post(
            "/api/judge/recall", json={"query": "x", "action_type": "yolo"}
        )
        assert resp.status_code == 422
        assert not calls["recall"]


class TestJudgeDecisions:
    def test_decide_delegates(self, client):
        c, calls = client
        resp = c.post(
            "/api/judge/decisions",
            json={
                "action_id": "act-1",
                "risk_class": "external_side_effect",
                "decision": "block",
                "reasoning_summary": "Constraint forbids this.",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["decision_id"] == "jd-1"
        assert calls["decide"][0].decision == "block"

    def test_decide_rejects_unknown_decision(self, client):
        c, _ = client
        resp = c.post(
            "/api/judge/decisions",
            json={
                "action_id": "act-1",
                "risk_class": "read_only",
                "decision": "maybe",
                "reasoning_summary": "?",
            },
        )
        assert resp.status_code == 422

    def test_get_decision(self, client):
        c, _ = client
        assert c.get("/api/judge/decisions/jd-1").status_code == 200
        assert c.get("/api/judge/decisions/missing").status_code == 404

    def test_list_decisions_with_filters(self, client):
        c, calls = client
        resp = c.get("/api/judge/decisions", params={"decision": "block"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert calls["list"][0]["decision"] == "block"
