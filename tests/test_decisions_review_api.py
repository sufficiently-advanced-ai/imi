"""Tests for POST /api/decisions/{id}/review (unreviewed-decisions spec).

The route is a thin delegate to ``chat_tools.update_signal`` (the governance
chokepoint composing apply_review + audit + persistence) — these tests cover
the route contract: action whitelist, payload mapping, and error translation.
The governance transitions themselves are covered by the signal_governance and
audited-review test suites.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

DECISION_ID = "aaaaaaaa-0001-0001-0001-000000000001"


@pytest.fixture
def client(monkeypatch):
    import app.routes.decisions as decisions_route

    calls = []

    async def fake_update_signal(signal_id, **kwargs):
        calls.append({"signal_id": signal_id, **kwargs})
        if signal_id == "invalid-transition-id":
            return {"success": False, "error": "Unknown review action: confirm"}
        return {"success": True, "signal": {"id": signal_id}}

    monkeypatch.setattr(decisions_route, "update_signal", fake_update_signal)

    def fake_get_decision(decision_id, **kwargs):
        # missing-id simulates both unknown ids and non-decision signals —
        # get_decision only resolves decision-type signals.
        if decision_id == "missing-id":
            return None
        return {"id": decision_id, "state": "active"}

    monkeypatch.setattr(decisions_route, "get_decision", fake_get_decision)

    test_app = FastAPI()
    test_app.include_router(decisions_route.router)
    return TestClient(test_app), calls


class TestReviewDecision:
    def test_confirm_returns_200_and_delegates(self, client):
        c, calls = client
        resp = c.post(
            f"/api/decisions/{DECISION_ID}/review",
            json={"action": "confirm", "actor": "scott"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reviewed"] is True
        assert body["decision_id"] == DECISION_ID
        assert body["action"] == "confirm"
        assert body["new_state"] == "active"
        assert calls[0]["signal_id"] == DECISION_ID
        assert calls[0]["review_action"] == "confirm"
        assert calls[0]["actor"] == "scott"

    def test_reject_and_evidence_only_accepted(self, client):
        c, _ = client
        for action in ("reject", "evidence_only"):
            resp = c.post(
                f"/api/decisions/{DECISION_ID}/review", json={"action": action}
            )
            assert resp.status_code == 200, action

    def test_unknown_or_non_decision_id_404_without_mutation(self, client):
        c, calls = client
        resp = c.post(
            "/api/decisions/missing-id/review", json={"action": "confirm"}
        )
        assert resp.status_code == 404
        # Pre-check must reject BEFORE the governance mutation runs
        assert calls == []

    def test_validation_failure_maps_to_400(self, client):
        c, _ = client
        resp = c.post(
            "/api/decisions/invalid-transition-id/review",
            json={"action": "confirm"},
        )
        assert resp.status_code == 400

    def test_supersede_and_bogus_actions_422(self, client):
        c, _ = client
        for action in ("supersede", "dispute", "bogus"):
            resp = c.post(
                f"/api/decisions/{DECISION_ID}/review", json={"action": action}
            )
            assert resp.status_code == 422, action
