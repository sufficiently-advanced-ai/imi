"""Tests for /api/supersession endpoints (R2.4 + R4.4).

Covers:
  GET  /api/supersession/candidates         — lists pending only
  POST /api/supersession/candidates/confirm — calls update_signal correctly, flips to confirmed
  POST /api/supersession/candidates/dismiss — flips to dismissed, update_signal NOT called
  404  for unknown signal ids
  CRITICAL: listing/dismissal never calls update_signal / apply governance
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.signal_store import SignalStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = "2026-06-01T00:00:00+00:00"
_PROPOSED_AT = "2026-06-10T00:00:00+00:00"


def _make_entity(eid: str) -> EntityRef:
    return EntityRef(
        id=eid, type="project", name=eid.split("-", 1)[-1].replace("-", " ").title()
    )


def _make_decision(
    *,
    signal_id: str | None = None,
    content: str = "A decision",
    meeting_id: str = "bot-1",
    entities: list[EntityRef] | None = None,
    candidates: list[dict] | None = None,
) -> Signal:
    metadata: dict = {}
    if candidates is not None:
        metadata["supersession_candidates"] = candidates

    return Signal(
        id=signal_id or str(uuid.uuid4()),
        type="decision",
        content=content,
        source_meeting_id=meeting_id,
        source_timestamp=_TS,
        entities=entities or [],
        metadata=metadata,
    )


def _candidate(old_id: str, status: str = "pending") -> dict:
    return {
        "old_signal_id": old_id,
        "old_content": "Old decision content",
        "matched_entities": ["project-kb-llm"],
        "reason": "Shared entities: Kb Llm",
        "confidence": 1.0,
        "status": status,
        "proposed_at": _PROPOSED_AT,
    }


def _build_populated_store(tmp_path):
    """Store with:
    - new_decision (bot-new) — has 1 pending candidate pointing at old_decision
    - confirmed_decision (bot-conf) — has 1 confirmed candidate
    - dismissed_decision (bot-dis) — has 1 dismissed candidate
    - old_decision (bot-old) — the target of new_decision's candidate
    """
    store = SignalStore(signals_dir=tmp_path / "signals")

    old_id = str(uuid.uuid4())
    new_id = str(uuid.uuid4())
    conf_id = str(uuid.uuid4())
    dis_id = str(uuid.uuid4())

    old_decision = _make_decision(
        signal_id=old_id, content="Old decision", meeting_id="bot-old"
    )
    new_decision = _make_decision(
        signal_id=new_id,
        content="New decision",
        meeting_id="bot-new",
        candidates=[_candidate(old_id, status="pending")],
    )
    conf_decision = _make_decision(
        signal_id=conf_id,
        content="Confirmed decision",
        meeting_id="bot-conf",
        candidates=[_candidate(old_id, status="confirmed")],
    )
    dis_decision = _make_decision(
        signal_id=dis_id,
        content="Dismissed decision",
        meeting_id="bot-dis",
        candidates=[_candidate(old_id, status="dismissed")],
    )

    store.save(
        MeetingSignals(meeting_id="m-old", bot_id="bot-old", signals=[old_decision])
    )
    store.save(
        MeetingSignals(meeting_id="m-new", bot_id="bot-new", signals=[new_decision])
    )
    store.save(
        MeetingSignals(meeting_id="m-conf", bot_id="bot-conf", signals=[conf_decision])
    )
    store.save(
        MeetingSignals(meeting_id="m-dis", bot_id="bot-dis", signals=[dis_decision])
    )

    return store, {
        "old_id": old_id,
        "new_id": new_id,
        "conf_id": conf_id,
        "dis_id": dis_id,
    }


def _make_client(store, monkeypatch):
    """Build a TestClient for the supersession router using the given store."""
    import app.routes.supersession as sup_route

    monkeypatch.setattr(sup_route, "signal_store", store)

    test_app = FastAPI()
    test_app.include_router(sup_route.router)
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# GET /api/supersession/candidates
# ---------------------------------------------------------------------------


class TestListCandidates:
    def test_returns_200(self, tmp_path, monkeypatch):
        store, _ = _build_populated_store(tmp_path)
        tc = _make_client(store, monkeypatch)
        resp = tc.get("/api/supersession/candidates")
        assert resp.status_code == 200

    def test_only_pending_returned(self, tmp_path, monkeypatch):
        """confirmed and dismissed candidates are NOT included."""
        store, ids = _build_populated_store(tmp_path)
        tc = _make_client(store, monkeypatch)
        resp = tc.get("/api/supersession/candidates")
        body = resp.json()
        # Only the pending candidate from new_decision
        assert len(body) == 1
        assert body[0]["new_signal_id"] == ids["new_id"]

    def test_candidate_has_required_keys(self, tmp_path, monkeypatch):
        store, _ = _build_populated_store(tmp_path)
        tc = _make_client(store, monkeypatch)
        resp = tc.get("/api/supersession/candidates")
        item = resp.json()[0]
        required = {
            "new_signal_id",
            "new_content",
            "old_signal_id",
            "old_content",
            "matched_entities",
            "reason",
            "confidence",
            "proposed_at",
        }
        assert required <= set(item.keys())

    def test_list_never_calls_update_signal(self, tmp_path, monkeypatch):
        """CRITICAL: GET /candidates must never call update_signal."""
        store, _ = _build_populated_store(tmp_path)
        import app.routes.supersession as sup_route

        monkeypatch.setattr(sup_route, "signal_store", store)

        update_signal_mock = AsyncMock()
        monkeypatch.setattr(sup_route, "update_signal", update_signal_mock)

        test_app = FastAPI()
        test_app.include_router(sup_route.router)
        tc = TestClient(test_app)

        tc.get("/api/supersession/candidates")
        update_signal_mock.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/supersession/candidates/confirm
# ---------------------------------------------------------------------------


class TestConfirmCandidate:
    def test_confirm_returns_success(self, tmp_path, monkeypatch):
        store, ids = _build_populated_store(tmp_path)
        import app.routes.supersession as sup_route

        monkeypatch.setattr(sup_route, "signal_store", store)
        monkeypatch.setattr(
            sup_route,
            "update_signal",
            AsyncMock(return_value={"success": True, "review_applied": True}),
        )

        test_app = FastAPI()
        test_app.include_router(sup_route.router)
        tc = TestClient(test_app)

        resp = tc.post(
            "/api/supersession/candidates/confirm",
            json={
                "new_signal_id": ids["new_id"],
                "old_signal_id": ids["old_id"],
                "actor": "alice",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["confirmed"] is True

    def test_confirm_calls_update_signal_with_correct_args(self, tmp_path, monkeypatch):
        """confirm must call update_signal(old_id, review_action='supersede',
        superseded_by=new_id, actor=actor)."""
        store, ids = _build_populated_store(tmp_path)
        import app.routes.supersession as sup_route

        monkeypatch.setattr(sup_route, "signal_store", store)

        mock_update = AsyncMock(return_value={"success": True, "review_applied": True})
        monkeypatch.setattr(sup_route, "update_signal", mock_update)

        test_app = FastAPI()
        test_app.include_router(sup_route.router)
        tc = TestClient(test_app)

        tc.post(
            "/api/supersession/candidates/confirm",
            json={
                "new_signal_id": ids["new_id"],
                "old_signal_id": ids["old_id"],
                "actor": "alice",
            },
        )

        mock_update.assert_awaited_once_with(
            ids["old_id"],
            review_action="supersede",
            superseded_by=ids["new_id"],
            actor="alice",
        )

    def test_confirm_flips_candidate_status_to_confirmed(self, tmp_path, monkeypatch):
        """After confirm, the candidate status in new_signal.metadata is 'confirmed'."""
        store, ids = _build_populated_store(tmp_path)
        import app.routes.supersession as sup_route

        monkeypatch.setattr(sup_route, "signal_store", store)
        monkeypatch.setattr(
            sup_route,
            "update_signal",
            AsyncMock(return_value={"success": True, "review_applied": True}),
        )

        test_app = FastAPI()
        test_app.include_router(sup_route.router)
        tc = TestClient(test_app)

        tc.post(
            "/api/supersession/candidates/confirm",
            json={"new_signal_id": ids["new_id"], "old_signal_id": ids["old_id"]},
        )

        # Reload and check
        updated = store.find_signal_by_id(ids["new_id"])
        assert updated is not None
        new_sig, _ = updated
        candidates = new_sig.metadata.get("supersession_candidates", [])
        matching = [c for c in candidates if c["old_signal_id"] == ids["old_id"]]
        assert len(matching) == 1
        assert matching[0]["status"] == "confirmed"

    def test_confirm_unknown_new_id_returns_404(self, tmp_path, monkeypatch):
        store, ids = _build_populated_store(tmp_path)
        tc = _make_client(store, monkeypatch)
        resp = tc.post(
            "/api/supersession/candidates/confirm",
            json={"new_signal_id": "unknown-id", "old_signal_id": ids["old_id"]},
        )
        assert resp.status_code == 404

    def test_confirm_unknown_old_id_returns_404(self, tmp_path, monkeypatch):
        store, ids = _build_populated_store(tmp_path)
        tc = _make_client(store, monkeypatch)
        resp = tc.post(
            "/api/supersession/candidates/confirm",
            json={"new_signal_id": ids["new_id"], "old_signal_id": "unknown-id"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/supersession/candidates/dismiss
# ---------------------------------------------------------------------------


class TestDismissCandidate:
    def test_dismiss_returns_success(self, tmp_path, monkeypatch):
        store, ids = _build_populated_store(tmp_path)
        tc = _make_client(store, monkeypatch)
        resp = tc.post(
            "/api/supersession/candidates/dismiss",
            json={"new_signal_id": ids["new_id"], "old_signal_id": ids["old_id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["dismissed"] is True

    def test_dismiss_flips_status_to_dismissed(self, tmp_path, monkeypatch):
        store, ids = _build_populated_store(tmp_path)
        tc = _make_client(store, monkeypatch)

        tc.post(
            "/api/supersession/candidates/dismiss",
            json={"new_signal_id": ids["new_id"], "old_signal_id": ids["old_id"]},
        )

        updated = store.find_signal_by_id(ids["new_id"])
        new_sig, _ = updated
        candidates = new_sig.metadata.get("supersession_candidates", [])
        matching = [c for c in candidates if c["old_signal_id"] == ids["old_id"]]
        assert matching[0]["status"] == "dismissed"

    def test_dismiss_never_calls_update_signal(self, tmp_path, monkeypatch):
        """CRITICAL: dismiss must NOT call update_signal / apply_review."""
        store, ids = _build_populated_store(tmp_path)
        import app.routes.supersession as sup_route

        monkeypatch.setattr(sup_route, "signal_store", store)

        update_mock = AsyncMock()
        monkeypatch.setattr(sup_route, "update_signal", update_mock)

        test_app = FastAPI()
        test_app.include_router(sup_route.router)
        tc = TestClient(test_app)

        tc.post(
            "/api/supersession/candidates/dismiss",
            json={"new_signal_id": ids["new_id"], "old_signal_id": ids["old_id"]},
        )

        update_mock.assert_not_called()

    def test_dismiss_unknown_new_id_returns_404(self, tmp_path, monkeypatch):
        store, ids = _build_populated_store(tmp_path)
        tc = _make_client(store, monkeypatch)
        resp = tc.post(
            "/api/supersession/candidates/dismiss",
            json={"new_signal_id": "unknown-id", "old_signal_id": ids["old_id"]},
        )
        assert resp.status_code == 404

    def test_dismiss_unknown_old_id_returns_404(self, tmp_path, monkeypatch):
        store, ids = _build_populated_store(tmp_path)
        tc = _make_client(store, monkeypatch)
        resp = tc.post(
            "/api/supersession/candidates/dismiss",
            json={"new_signal_id": ids["new_id"], "old_signal_id": "unknown-id"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /candidates after confirm/dismiss shows none
# ---------------------------------------------------------------------------


class TestCandidateLifecycle:
    def test_confirmed_candidate_no_longer_listed(self, tmp_path, monkeypatch):
        store, ids = _build_populated_store(tmp_path)
        import app.routes.supersession as sup_route

        monkeypatch.setattr(sup_route, "signal_store", store)
        monkeypatch.setattr(
            sup_route,
            "update_signal",
            AsyncMock(return_value={"success": True, "review_applied": True}),
        )

        test_app = FastAPI()
        test_app.include_router(sup_route.router)
        tc = TestClient(test_app)

        # Confirm the pending candidate
        tc.post(
            "/api/supersession/candidates/confirm",
            json={"new_signal_id": ids["new_id"], "old_signal_id": ids["old_id"]},
        )

        # Now GET should return no pending for new_id
        resp = tc.get("/api/supersession/candidates")
        body = resp.json()
        pending = [item for item in body if item["new_signal_id"] == ids["new_id"]]
        assert len(pending) == 0

    def test_dismissed_candidate_no_longer_listed(self, tmp_path, monkeypatch):
        store, ids = _build_populated_store(tmp_path)
        tc = _make_client(store, monkeypatch)

        # Dismiss the pending candidate
        tc.post(
            "/api/supersession/candidates/dismiss",
            json={"new_signal_id": ids["new_id"], "old_signal_id": ids["old_id"]},
        )

        # Now GET should return no pending for new_id
        resp = tc.get("/api/supersession/candidates")
        body = resp.json()
        pending = [item for item in body if item["new_signal_id"] == ids["new_id"]]
        assert len(pending) == 0


# ---------------------------------------------------------------------------
# Multi-candidate flattening
# ---------------------------------------------------------------------------


class TestMultiCandidateFlattening:
    """A signal with 2 pending candidates must expand to 2 rows in GET /candidates."""

    def test_two_candidates_yield_two_rows(self, tmp_path, monkeypatch):
        """New signal carrying 2 pending candidates → exactly 2 rows from GET /candidates,
        each with a distinct old_signal_id."""
        store = SignalStore(signals_dir=tmp_path / "signals")

        old_id_1 = str(uuid.uuid4())
        old_id_2 = str(uuid.uuid4())
        new_id = str(uuid.uuid4())

        # The target signals (so the store has them, though the endpoint only
        # reads candidates from the new signal's metadata)
        old_sig_1 = _make_decision(
            signal_id=old_id_1, content="Old decision 1", meeting_id="bot-old-1"
        )
        old_sig_2 = _make_decision(
            signal_id=old_id_2, content="Old decision 2", meeting_id="bot-old-2"
        )

        # New signal with 2 pending candidates
        new_sig = _make_decision(
            signal_id=new_id,
            content="New decision superseding two",
            meeting_id="bot-multi",
            candidates=[
                _candidate(old_id_1, status="pending"),
                _candidate(old_id_2, status="pending"),
            ],
        )

        store.save(
            MeetingSignals(
                meeting_id="m-old-1", bot_id="bot-old-1", signals=[old_sig_1]
            )
        )
        store.save(
            MeetingSignals(
                meeting_id="m-old-2", bot_id="bot-old-2", signals=[old_sig_2]
            )
        )
        store.save(
            MeetingSignals(meeting_id="m-multi", bot_id="bot-multi", signals=[new_sig])
        )

        tc = _make_client(store, monkeypatch)
        resp = tc.get("/api/supersession/candidates")
        assert resp.status_code == 200

        body = resp.json()
        # Filter to rows from our new signal
        rows = [item for item in body if item["new_signal_id"] == new_id]
        assert (
            len(rows) == 2
        ), f"Expected 2 candidate rows for new_signal_id={new_id}, got {len(rows)}: {rows}"

        old_ids_returned = {row["old_signal_id"] for row in rows}
        assert (
            old_ids_returned == {old_id_1, old_id_2}
        ), f"Expected both old_signal_ids {{{old_id_1}, {old_id_2}}} but got {old_ids_returned}"


# ---------------------------------------------------------------------------
# Unlinked-pair 404
# ---------------------------------------------------------------------------


class TestUnlinkedPair404:
    """POST /confirm with old_signal_id that EXISTS in the store but is NOT in
    the new signal's candidate list must return 404 and never call update_signal."""

    def test_confirm_unlinked_old_id_returns_404(self, tmp_path, monkeypatch):
        """old_signal_id in store but not in new_signal's candidate list → 404."""
        store = SignalStore(signals_dir=tmp_path / "signals")

        linked_old_id = str(uuid.uuid4())
        unlinked_old_id = str(uuid.uuid4())
        new_id = str(uuid.uuid4())

        # The "linked" old decision is referenced in new signal's candidates
        linked_old = _make_decision(
            signal_id=linked_old_id, content="Linked old", meeting_id="bot-linked-old"
        )
        # The "unlinked" old decision exists in the store but has no entry in new_sig's candidates
        unlinked_old = _make_decision(
            signal_id=unlinked_old_id,
            content="Unlinked old",
            meeting_id="bot-unlinked-old",
        )

        new_sig = _make_decision(
            signal_id=new_id,
            content="New decision",
            meeting_id="bot-new-unlinked",
            candidates=[_candidate(linked_old_id, status="pending")],
        )

        store.save(
            MeetingSignals(
                meeting_id="m-linked-old", bot_id="bot-linked-old", signals=[linked_old]
            )
        )
        store.save(
            MeetingSignals(
                meeting_id="m-unlinked-old",
                bot_id="bot-unlinked-old",
                signals=[unlinked_old],
            )
        )
        store.save(
            MeetingSignals(
                meeting_id="m-new-unlinked",
                bot_id="bot-new-unlinked",
                signals=[new_sig],
            )
        )

        import app.routes.supersession as sup_route

        monkeypatch.setattr(sup_route, "signal_store", store)
        update_mock = AsyncMock()
        monkeypatch.setattr(sup_route, "update_signal", update_mock)

        test_app = FastAPI()
        test_app.include_router(sup_route.router)
        tc = TestClient(test_app)

        # POST confirm with the unlinked old_signal_id — it exists in the store
        # but is NOT in new_sig's candidate list
        resp = tc.post(
            "/api/supersession/candidates/confirm",
            json={"new_signal_id": new_id, "old_signal_id": unlinked_old_id},
        )
        assert (
            resp.status_code == 404
        ), f"Expected 404 for unlinked pair, got {resp.status_code}: {resp.text}"
        update_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Fix C: 409 for non-pending candidates
# ---------------------------------------------------------------------------


class TestNonPending409:
    """Confirm and dismiss on already-actioned candidates must return 409."""

    def test_confirm_on_confirmed_returns_409(self, tmp_path, monkeypatch):
        """Confirming an already-confirmed candidate → 409, update_signal not called."""
        store, ids = _build_populated_store(tmp_path)
        import app.routes.supersession as sup_route

        monkeypatch.setattr(sup_route, "signal_store", store)
        update_mock = AsyncMock()
        monkeypatch.setattr(sup_route, "update_signal", update_mock)

        test_app = FastAPI()
        test_app.include_router(sup_route.router)
        tc = TestClient(test_app)

        # conf_id has a confirmed candidate pointing at old_id
        resp = tc.post(
            "/api/supersession/candidates/confirm",
            json={"new_signal_id": ids["conf_id"], "old_signal_id": ids["old_id"]},
        )
        assert (
            resp.status_code == 409
        ), f"Expected 409 for confirmed candidate, got {resp.status_code}: {resp.text}"
        update_mock.assert_not_called()

    def test_dismiss_on_confirmed_returns_409(self, tmp_path, monkeypatch):
        """Dismissing a confirmed candidate → 409."""
        store, ids = _build_populated_store(tmp_path)
        tc = _make_client(store, monkeypatch)

        resp = tc.post(
            "/api/supersession/candidates/dismiss",
            json={"new_signal_id": ids["conf_id"], "old_signal_id": ids["old_id"]},
        )
        assert (
            resp.status_code == 409
        ), f"Expected 409 for confirmed candidate, got {resp.status_code}: {resp.text}"
