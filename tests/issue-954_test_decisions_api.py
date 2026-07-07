"""Tests for app/routes/decisions.py (Issue #954 — Task 5).

Covers:
- GET /api/decisions         → 200, list + total + counts_by_state
- GET /api/decisions?state=  → filters; bogus state → 400
- GET /api/decisions?limit=  → truncates list, counts/total unaffected
- GET /api/decisions/stats   → 200 with headline (declared before /{id})
- GET /api/decisions/constitution → 404 placeholder
- GET /api/decisions/{id}    → 200 with lineage, audit_history, governance_ladder
- GET /api/decisions/bogus-id → 404
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

NOW = datetime(2026, 6, 11, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Shared fixture data (mirrors issue-954_test_decision_view.py)
# ---------------------------------------------------------------------------

DECISION_ID_1 = "aaaaaaaa-0001-0001-0001-000000000001"
DECISION_ID_2 = "aaaaaaaa-0002-0002-0002-000000000002"
DECISION_ID_3 = "aaaaaaaa-0003-0003-0003-000000000003"
ACTION_ITEM_ID = "aaaaaaaa-0004-0004-0004-000000000004"

RECENT_TS = "2026-06-06T12:00:00+00:00"
OLD_TS = "2026-02-11T12:00:00+00:00"


def _build_store(tmp_path):
    """Pre-populated SignalStore mirroring the decision_view test fixture."""
    from app.models.signal import EntityRef, MeetingSignals, Signal
    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path / "signals")

    owner_ref = EntityRef(id="person-alice", type="person", name="Alice")

    decision_active = Signal(
        id=DECISION_ID_1,
        type="decision",
        content="We will use PostgreSQL.",
        source_meeting_id="bot-1",
        source_meeting_title="Architecture Meeting",
        source_timestamp=RECENT_TS,
        review_status="confirmed",
        provenance_status="user_confirmed",
        can_use_as_instruction=True,
        owner=owner_ref,
        client_id="client-acme",
        tenant_id="tenant-1",
    )
    decision_superseded = Signal(
        id=DECISION_ID_2,
        type="decision",
        content="We will use MySQL.",
        source_meeting_id="bot-1",
        source_meeting_title="Architecture Meeting",
        source_timestamp=RECENT_TS,
        review_status="pending",
        provenance_status="generated",
        superseded_by=DECISION_ID_1,
        client_id="client-acme",
        tenant_id="tenant-1",
    )
    action_item = Signal(
        id=ACTION_ITEM_ID,
        type="action_item",
        content="Write the migration script.",
        source_meeting_id="bot-1",
        source_meeting_title="Architecture Meeting",
        source_timestamp=RECENT_TS,
        review_status="pending",
        provenance_status="generated",
        owner=owner_ref,
        client_id="client-acme",
        tenant_id="tenant-1",
    )

    store.save(
        MeetingSignals(
            meeting_id="m1",
            bot_id="bot-1",
            meeting_title="Architecture Meeting",
            signals=[decision_active, decision_superseded, action_item],
        )
    )

    decision_stale = Signal(
        id=DECISION_ID_3,
        type="decision",
        content="Use Redis for caching.",
        source_meeting_id="bot-2",
        source_meeting_title="Infra Planning",
        source_timestamp=OLD_TS,
        review_status="pending",
        provenance_status="generated",
        owner=owner_ref,
        client_id="client-beta",
        tenant_id="tenant-2",
    )

    store.save(
        MeetingSignals(
            meeting_id="m2",
            bot_id="bot-2",
            meeting_title="Infra Planning",
            signals=[decision_stale],
        )
    )

    return store


@pytest.fixture
def store(tmp_path):
    return _build_store(tmp_path)


# ---------------------------------------------------------------------------
# Test client fixture — minimal app with decisions router
# ---------------------------------------------------------------------------


@pytest.fixture
def client(store, monkeypatch):
    import app.routes.decisions as decisions_route
    from app.services.decision_view import (
        compute_decision_stats,
        get_decision,
        list_decisions,
    )

    # Monkeypatch the module-level references so route handlers use the tmp store
    monkeypatch.setattr(
        decisions_route,
        "list_decisions",
        partial(list_decisions, store=store),
    )
    monkeypatch.setattr(
        decisions_route,
        "compute_decision_stats",
        partial(compute_decision_stats, store=store),
    )
    monkeypatch.setattr(
        decisions_route,
        "get_decision",
        partial(get_decision, store=store),
    )

    test_app = FastAPI()
    test_app.include_router(decisions_route.router)
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# GET /api/decisions  (list)
# ---------------------------------------------------------------------------


class TestDecisionsList:
    def test_200_with_decisions_and_pagination_keys(self, client):
        resp = client.get("/api/decisions")
        assert resp.status_code == 200
        body = resp.json()
        assert "decisions" in body
        assert "total" in body
        assert "counts_by_state" in body

    def test_returns_all_decisions(self, client):
        resp = client.get("/api/decisions")
        body = resp.json()
        assert body["total"] == 3
        assert len(body["decisions"]) == 3

    def test_decisions_have_required_keys(self, client):
        resp = client.get("/api/decisions")
        decision = resp.json()["decisions"][0]
        required = {
            "id",
            "content",
            "state",
            "state_reason",
            "review_status",
            "provenance_status",
            "can_use_as_evidence",
            "can_use_as_instruction",
            "owner",
            "owner_id",
            "client_id",
            "source_meeting_id",
            "source_meeting_title",
            "source_timestamp",
            "superseded_by",
            "age_days",
            "tenant_id",
            "metadata",
        }
        assert required <= set(decision.keys())

    def test_state_filter_active(self, client):
        resp = client.get("/api/decisions?state=active")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert all(d["state"] == "active" for d in body["decisions"])

    def test_state_filter_bogus_returns_400(self, client):
        resp = client.get("/api/decisions?state=bogus")
        assert resp.status_code == 400
        assert "bogus" in resp.json()["detail"]

    def test_limit_truncates_list_not_counts(self, client):
        resp = client.get("/api/decisions?limit=1")
        assert resp.status_code == 200
        body = resp.json()
        # Only 1 decision in the list
        assert len(body["decisions"]) == 1
        # But total and counts still reflect all 3
        assert body["total"] == 3
        counts_sum = sum(body["counts_by_state"].values())
        assert counts_sum == 3

    def test_counts_by_state_correctness(self, client):
        resp = client.get("/api/decisions")
        counts = resp.json()["counts_by_state"]
        assert counts.get("active", 0) == 1
        assert counts.get("superseded", 0) == 1
        assert counts.get("stale", 0) == 1

    def test_action_items_excluded(self, client):
        resp = client.get("/api/decisions")
        ids = [d["id"] for d in resp.json()["decisions"]]
        assert ACTION_ITEM_ID not in ids


# ---------------------------------------------------------------------------
# GET /api/decisions/stats  (must be registered BEFORE /{decision_id})
# ---------------------------------------------------------------------------


class TestDecisionsStats:
    def test_200_with_headline(self, client):
        resp = client.get("/api/decisions/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert "headline" in body
        assert isinstance(body["headline"], str)
        assert len(body["headline"]) > 0

    def test_stats_keys_present(self, client):
        resp = client.get("/api/decisions/stats")
        body = resp.json()
        for key in (
            "meetings",
            "decisions",
            "counts_by_state",
            "stale",
            "superseded",
            "headline",
        ):
            assert key in body, f"Missing key: {key}"

    def test_stats_values_match_fixture(self, client):
        resp = client.get("/api/decisions/stats")
        body = resp.json()
        assert body["meetings"] == 2
        assert body["decisions"] == 3
        assert body["stale"] == 1
        assert body["superseded"] == 1

    def test_not_captured_by_decision_id_route(self, client):
        """GET /api/decisions/stats must return 200, not attempt id lookup for 'stats'."""
        resp = client.get("/api/decisions/stats")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/decisions/constitution  (404 before any export)
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_repo_client(client, tmp_path, monkeypatch):
    """Client whose git_ops points at an empty repo (no constitution exported).

    The route reads {git_ops.repo_path}/constitution/constitution.md via the
    tenant-scoped proxy; without this isolation the test depends on whether
    the real dev repo has ever exported a constitution.
    """
    import app.routes.decisions as decisions_route

    fake_git_ops = type("FakeGitOps", (), {"repo_path": str(tmp_path)})()
    monkeypatch.setattr(decisions_route, "git_ops", fake_git_ops)
    return client


class TestDecisionsConstitutionNotExported:
    def test_constitution_returns_404(self, empty_repo_client):
        resp = empty_repo_client.get("/api/decisions/constitution")
        assert resp.status_code == 404

    def test_constitution_detail_message(self, empty_repo_client):
        resp = empty_repo_client.get("/api/decisions/constitution")
        detail = resp.json().get("detail", "")
        assert "Constitution" in detail or "constitution" in detail

    def test_not_captured_by_decision_id_route(self, empty_repo_client):
        """GET /api/decisions/constitution must hit the constitution route, not id lookup."""
        resp = empty_repo_client.get("/api/decisions/constitution")
        # A decision lookup for id='constitution' would also 404, so distinguish
        # by the constitution-specific detail text.
        assert resp.status_code == 404
        detail = resp.json().get("detail", "")
        assert "Constitution" in detail or "constitution" in detail.lower()


# ---------------------------------------------------------------------------
# GET /api/decisions/{decision_id}  (detail)
# ---------------------------------------------------------------------------


class TestDecisionsDetail:
    def test_known_id_returns_200_with_enriched_keys(self, client):
        resp = client.get(f"/api/decisions/{DECISION_ID_1}")
        assert resp.status_code == 200
        body = resp.json()
        assert "lineage" in body
        assert "audit_history" in body
        assert "governance_ladder" in body

    def test_lineage_is_list(self, client):
        resp = client.get(f"/api/decisions/{DECISION_ID_1}")
        lineage = resp.json()["lineage"]
        assert isinstance(lineage, list)

    def test_governance_ladder_has_position(self, client):
        resp = client.get(f"/api/decisions/{DECISION_ID_1}")
        ladder = resp.json()["governance_ladder"]
        assert "position" in ladder
        assert ladder["position"] in ("instruction", "evidence", "blocked")

    def test_audit_history_is_list(self, client):
        resp = client.get(f"/api/decisions/{DECISION_ID_1}")
        audit = resp.json()["audit_history"]
        assert isinstance(audit, list)

    def test_unknown_id_returns_404(self, client):
        resp = client.get("/api/decisions/does-not-exist-xyz")
        assert resp.status_code == 404

    def test_detail_contains_base_decision_keys(self, client):
        resp = client.get(f"/api/decisions/{DECISION_ID_1}")
        body = resp.json()
        for key in ("id", "content", "state", "source_meeting_id"):
            assert key in body, f"Missing base key: {key}"

    def test_active_decision_correct_state(self, client):
        resp = client.get(f"/api/decisions/{DECISION_ID_1}")
        assert resp.json()["state"] == "active"

    def test_superseded_decision_state(self, client):
        resp = client.get(f"/api/decisions/{DECISION_ID_2}")
        assert resp.json()["state"] == "superseded"
