"""Tests for Sprint 4 Task S4-3: conflicting state, conflicts API, CONFLICTS_WITH edge.

Covers:
  GET  /api/conflicts/candidates         — lists pending conflict candidates only
  POST /api/conflicts/candidates/confirm — flips both signals' conflicts_with + writes edge
  POST /api/conflicts/candidates/dismiss — flips status only, no edge write

Safety invariants (mirrors supersession tests):
  - list/dismiss never write CONFLICTS_WITH edges
  - detection metadata can't auto-confirm
  - 409 for non-pending candidates
  - 404 for unknown signal IDs and unlinked pairs
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.signal import MeetingSignals, Signal
from app.services.signal_store import SignalStore

# ---------------------------------------------------------------------------
# Test data constants
# ---------------------------------------------------------------------------

_TS = "2026-06-01T00:00:00+00:00"
_PROPOSED_AT = "2026-06-10T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(
    *,
    signal_id: str | None = None,
    content: str = "A decision",
    meeting_id: str = "bot-1",
    metadata: dict | None = None,
) -> Signal:
    return Signal(
        id=signal_id or str(uuid.uuid4()),
        type="decision",
        content=content,
        source_meeting_id=meeting_id,
        source_timestamp=_TS,
        metadata=metadata or {},
    )


def _conflict_candidate(other_id: str, other_content: str = "Other decision", status: str = "pending") -> dict:
    return {
        "other_signal_id": other_id,
        "other_content": other_content,
        "rationale": "Both decisions address authentication policy with opposing approaches",
        "confidence": 0.85,
        "speakers": ["Alice", "Bob"],
        "status": status,
        "proposed_at": _PROPOSED_AT,
    }


def _build_store(tmp_path, signals_by_meeting: dict[str, list[Signal]]) -> SignalStore:
    """Populate a SignalStore from {meeting_id: [signal, ...]}."""
    store = SignalStore(signals_dir=tmp_path / "signals")
    for meeting_id, signals in signals_by_meeting.items():
        ms = MeetingSignals(meeting_id=f"meet-{meeting_id}", bot_id=meeting_id, signals=signals)
        store.save(ms)
    return store


def _app_with_store(store: SignalStore) -> FastAPI:
    """Build a minimal FastAPI app with the conflicts router wired to a custom store."""
    from app.routes.conflicts import router

    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# GET /api/conflicts/candidates
# ---------------------------------------------------------------------------


class TestListCandidates:
    """GET /api/conflicts/candidates lists pending conflict candidates only."""

    def test_empty_store_returns_empty_list(self, tmp_path):
        store = _build_store(tmp_path, {})
        app = _app_with_store(store)
        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(app)
            resp = client.get("/api/conflicts/candidates")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_pending_candidate_appears_in_list(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())

        sig_a = _make_decision(
            signal_id=sig_a_id,
            content="Use SSO for all auth",
            meeting_id="bot-1",
            metadata={
                "conflict_candidates": [_conflict_candidate(sig_b_id, "Use per-service auth")]
            },
        )
        sig_b = _make_decision(signal_id=sig_b_id, content="Use per-service auth", meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})
        app = _app_with_store(store)

        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(app)
            resp = client.get("/api/conflicts/candidates")

        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        item = items[0]
        assert item["signal_id"] == sig_a_id
        assert item["other_signal_id"] == sig_b_id
        assert item["signal_content"] == "Use SSO for all auth"
        assert item["other_content"] == "Use per-service auth"
        assert "rationale" in item
        assert "confidence" in item
        assert "proposed_at" in item

    def test_confirmed_candidate_excluded_from_list(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())

        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={
                "conflict_candidates": [
                    _conflict_candidate(sig_b_id, status="confirmed")
                ]
            },
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(_app_with_store(store))
            resp = client.get("/api/conflicts/candidates")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_dismissed_candidate_excluded_from_list(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())

        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={
                "conflict_candidates": [
                    _conflict_candidate(sig_b_id, status="dismissed")
                ]
            },
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(_app_with_store(store))
            resp = client.get("/api/conflicts/candidates")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_never_writes_graph_edge(self, tmp_path):
        """Safety: GET /candidates must never call write_conflicts_with_edge."""
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts._write_edge_best_effort") as mock_edge,
        ):
            client = TestClient(_app_with_store(store))
            client.get("/api/conflicts/candidates")

        mock_edge.assert_not_called()

    def test_speakers_field_present(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(_app_with_store(store))
            resp = client.get("/api/conflicts/candidates")

        item = resp.json()[0]
        assert "speakers" in item
        assert item["speakers"] == ["Alice", "Bob"]


# ---------------------------------------------------------------------------
# POST /api/conflicts/candidates/confirm
# ---------------------------------------------------------------------------


class TestConfirmCandidate:
    """POST /api/conflicts/candidates/confirm."""

    def test_404_unknown_signal_a(self, tmp_path):
        store = _build_store(tmp_path, {})
        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": "no-such-id", "other_signal_id": "also-no"},
            )
        assert resp.status_code == 404

    def test_404_unknown_signal_b(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        # sig_b is NOT in the store
        store = _build_store(tmp_path, {"bot-1": [sig_a]})

        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )
        assert resp.status_code == 404

    def test_404_no_candidate_pair(self, tmp_path):
        """Signal exists but has no conflict_candidates entry pointing at other."""
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(signal_id=sig_a_id, meeting_id="bot-1", metadata={})
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )
        assert resp.status_code == 404

    def test_409_already_confirmed(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id, status="confirmed")]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )
        assert resp.status_code == 409

    def test_409_already_dismissed(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id, status="dismissed")]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )
        assert resp.status_code == 409

    def test_confirm_success_response_shape(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            content="Use SSO",
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id, "Use per-service")]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, content="Use per-service", meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts._write_edge_best_effort", new_callable=AsyncMock),
            patch("app.routes.conflicts.git_ops") as mock_git,
        ):
            mock_git.commit_and_push = AsyncMock()
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id, "actor": "alice"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["confirmed"] is True
        assert data["signal_id"] == sig_a_id
        assert data["other_signal_id"] == sig_b_id
        assert "committed" in data

    def test_confirm_flips_candidate_status_to_confirmed(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts._write_edge_best_effort", new_callable=AsyncMock),
            patch("app.routes.conflicts.git_ops") as mock_git,
        ):
            mock_git.commit_and_push = AsyncMock()
            client = TestClient(_app_with_store(store))
            client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )

        # Reload from disk and verify
        updated_a, _ = store.find_signal_by_id(sig_a_id)
        cands = updated_a.metadata.get("conflict_candidates", [])
        assert len(cands) == 1
        assert cands[0]["status"] == "confirmed"

    def test_confirm_appends_conflicts_with_to_both_signals(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts._write_edge_best_effort", new_callable=AsyncMock),
            patch("app.routes.conflicts.git_ops") as mock_git,
        ):
            mock_git.commit_and_push = AsyncMock()
            client = TestClient(_app_with_store(store))
            client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )

        updated_a, _ = store.find_signal_by_id(sig_a_id)
        updated_b, _ = store.find_signal_by_id(sig_b_id)

        assert sig_b_id in updated_a.metadata.get("conflicts_with", [])
        assert sig_a_id in updated_b.metadata.get("conflicts_with", [])

    def test_confirm_dedupes_conflicts_with(self, tmp_path):
        """Calling confirm twice should not duplicate conflicts_with entries."""
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        # Pre-populate conflicts_with so it's already there
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={
                "conflict_candidates": [_conflict_candidate(sig_b_id)],
                "conflicts_with": [sig_b_id],  # already present
            },
        )
        sig_b = _make_decision(
            signal_id=sig_b_id,
            meeting_id="bot-2",
            metadata={"conflicts_with": [sig_a_id]},
        )
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts._write_edge_best_effort", new_callable=AsyncMock),
            patch("app.routes.conflicts.git_ops") as mock_git,
        ):
            mock_git.commit_and_push = AsyncMock()
            client = TestClient(_app_with_store(store))
            client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )

        updated_a, _ = store.find_signal_by_id(sig_a_id)
        assert updated_a.metadata["conflicts_with"].count(sig_b_id) == 1

    def test_confirm_writes_graph_edge(self, tmp_path):
        """confirm must call _write_edge_best_effort with both signal IDs."""
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        mock_edge = AsyncMock()
        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts._write_edge_best_effort", mock_edge),
            patch("app.routes.conflicts.git_ops") as mock_git,
        ):
            mock_git.commit_and_push = AsyncMock()
            client = TestClient(_app_with_store(store))
            client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id, "actor": "alice"},
            )

        mock_edge.assert_called_once()
        call_kwargs = mock_edge.call_args
        assert sig_a_id in str(call_kwargs)
        assert sig_b_id in str(call_kwargs)

    def test_confirm_same_meeting_file_deduped_paths(self, tmp_path):
        """When both signals are in the same meeting file, commit_and_push gets one path."""
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            content="Use SSO",
            meeting_id="bot-same",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(
            signal_id=sig_b_id,
            content="Use per-service",
            meeting_id="bot-same",  # same meeting file
        )
        store = _build_store(tmp_path, {"bot-same": [sig_a, sig_b]})

        committed_paths = []

        async def _fake_commit(paths, msg):
            committed_paths.extend(paths)

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts._write_edge_best_effort", new_callable=AsyncMock),
            patch("app.routes.conflicts.git_ops") as mock_git,
        ):
            mock_git.commit_and_push = _fake_commit
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )

        assert resp.status_code == 200
        # Paths should be deduplicated — only one path for the same file
        assert len(set(committed_paths)) == 1
        assert "bot-same" in committed_paths[0]

    def test_confirm_git_failure_returns_committed_false(self, tmp_path):
        """Git failure is best-effort: mutation persists, committed=False in response."""
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts._write_edge_best_effort", new_callable=AsyncMock),
            patch("app.routes.conflicts.git_ops") as mock_git,
        ):
            mock_git.commit_and_push = AsyncMock(side_effect=RuntimeError("git boom"))
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )

        assert resp.status_code == 200
        assert resp.json()["committed"] is False
        # But mutation is persisted
        updated_a, _ = store.find_signal_by_id(sig_a_id)
        assert sig_b_id in updated_a.metadata.get("conflicts_with", [])

    def test_confirm_does_not_call_update_signal_governance(self, tmp_path):
        """Confirm must NOT go through apply_review / governance axis (PRD R3.5)."""
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts._write_edge_best_effort", new_callable=AsyncMock),
            patch("app.routes.conflicts.git_ops") as mock_git,
            patch("app.routes.conflicts.update_signal", new_callable=AsyncMock) as mock_update,
        ):
            mock_git.commit_and_push = AsyncMock()
            client = TestClient(_app_with_store(store))
            client.post(
                "/api/conflicts/candidates/confirm",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )

        mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/conflicts/candidates/dismiss
# ---------------------------------------------------------------------------


class TestDismissCandidate:
    """POST /api/conflicts/candidates/dismiss."""

    def test_404_unknown_signal(self, tmp_path):
        store = _build_store(tmp_path, {})
        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/dismiss",
                json={"signal_id": "no-such-id", "other_signal_id": "also-no"},
            )
        assert resp.status_code == 404

    def test_404_no_candidate_pair(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(signal_id=sig_a_id, meeting_id="bot-1", metadata={})
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/dismiss",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )
        assert resp.status_code == 404

    def test_409_already_confirmed(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id, status="confirmed")]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with patch("app.routes.conflicts.signal_store", store):
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/dismiss",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )
        assert resp.status_code == 409

    def test_dismiss_success_response(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts.git_ops") as mock_git,
        ):
            mock_git.commit_and_push = AsyncMock()
            client = TestClient(_app_with_store(store))
            resp = client.post(
                "/api/conflicts/candidates/dismiss",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["dismissed"] is True
        assert data["signal_id"] == sig_a_id
        assert data["other_signal_id"] == sig_b_id

    def test_dismiss_flips_status_to_dismissed(self, tmp_path):
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts.git_ops") as mock_git,
        ):
            mock_git.commit_and_push = AsyncMock()
            client = TestClient(_app_with_store(store))
            client.post(
                "/api/conflicts/candidates/dismiss",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )

        updated_a, _ = store.find_signal_by_id(sig_a_id)
        cands = updated_a.metadata.get("conflict_candidates", [])
        assert cands[0]["status"] == "dismissed"

    def test_dismiss_does_not_touch_other_signal(self, tmp_path):
        """Dismiss only touches signal_a, not signal_b."""
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2", metadata={"untouched": True})
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts.git_ops") as mock_git,
        ):
            mock_git.commit_and_push = AsyncMock()
            client = TestClient(_app_with_store(store))
            client.post(
                "/api/conflicts/candidates/dismiss",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )

        updated_b, _ = store.find_signal_by_id(sig_b_id)
        # sig_b's metadata should remain unchanged (no conflicts_with added)
        assert updated_b.metadata.get("untouched") is True
        assert "conflicts_with" not in updated_b.metadata

    def test_dismiss_never_writes_graph_edge(self, tmp_path):
        """Safety: dismiss must never call _write_edge_best_effort."""
        sig_a_id = str(uuid.uuid4())
        sig_b_id = str(uuid.uuid4())
        sig_a = _make_decision(
            signal_id=sig_a_id,
            meeting_id="bot-1",
            metadata={"conflict_candidates": [_conflict_candidate(sig_b_id)]},
        )
        sig_b = _make_decision(signal_id=sig_b_id, meeting_id="bot-2")
        store = _build_store(tmp_path, {"bot-1": [sig_a], "bot-2": [sig_b]})

        with (
            patch("app.routes.conflicts.signal_store", store),
            patch("app.routes.conflicts._write_edge_best_effort") as mock_edge,
            patch("app.routes.conflicts.git_ops") as mock_git,
        ):
            mock_git.commit_and_push = AsyncMock()
            client = TestClient(_app_with_store(store))
            client.post(
                "/api/conflicts/candidates/dismiss",
                json={"signal_id": sig_a_id, "other_signal_id": sig_b_id},
            )

        mock_edge.assert_not_called()


# ---------------------------------------------------------------------------
# write_conflicts_with_edge — unit tests
# ---------------------------------------------------------------------------


class TestWriteConflictsWithEdge:
    """Unit tests for SignalGraphWriter.write_conflicts_with_edge."""

    class _FakeClient:
        def __init__(self, *, return_row: bool = True, raise_exc=None):
            self.writes: list[tuple[str, dict]] = []
            self._return_row = return_row
            self._raise_exc = raise_exc

        async def execute_write(self, query: str, params: dict):
            self.writes.append((query, params))
            if self._raise_exc is not None:
                raise self._raise_exc
            if self._return_row:
                return [{"id": "x"}]
            return []

    @pytest.mark.asyncio
    async def test_cypher_contains_merge_and_conflicts_with(self):
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = self._FakeClient()
        writer = SignalGraphWriter(client)
        await writer.write_conflicts_with_edge(
            "sig-aaa",
            "sig-bbb",
            confirmed_at="2026-06-11T10:00:00+00:00",
        )
        assert client.writes, "No execute_write calls"
        query, _ = client.writes[0]
        assert "MERGE" in query
        assert "CONFLICTS_WITH" in query

    @pytest.mark.asyncio
    async def test_params_include_all_required_keys(self):
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = self._FakeClient()
        writer = SignalGraphWriter(client)
        await writer.write_conflicts_with_edge(
            "sig-aaa",
            "sig-bbb",
            confirmed_at="2026-06-11T10:00:00+00:00",
            actor="alice",
            tenant_id="tenant-t1",
        )
        _, params = client.writes[0]
        assert params.get("confirmed_at") == "2026-06-11T10:00:00+00:00"
        assert params.get("actor") == "alice"
        assert params.get("tenant_id") == "tenant-t1"

    @pytest.mark.asyncio
    async def test_canonical_direction_sorted_ids(self):
        """Edge direction is canonical (sorted): (min_id)-[:CONFLICTS_WITH]->(max_id)."""
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        # Feed IDs in reverse lexicographic order
        id_high = "zzz-signal"
        id_low = "aaa-signal"

        client = self._FakeClient()
        writer = SignalGraphWriter(client)
        await writer.write_conflicts_with_edge(
            id_high,  # a > b lexicographically
            id_low,
            confirmed_at="2026-06-11T10:00:00+00:00",
        )
        _, params = client.writes[0]
        # The low ID should be a_id, the high should be b_id
        assert params["a_id"] == id_low
        assert params["b_id"] == id_high

    @pytest.mark.asyncio
    async def test_canonical_direction_consistent_regardless_of_call_order(self):
        """Calling (a, b) and (b, a) should produce the same canonical params."""
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        id_a = "aaa-signal"
        id_b = "zzz-signal"

        client1 = self._FakeClient()
        client2 = self._FakeClient()
        writer1 = SignalGraphWriter(client1)
        writer2 = SignalGraphWriter(client2)

        await writer1.write_conflicts_with_edge(id_a, id_b, confirmed_at="2026-06-11T10:00:00+00:00")
        await writer2.write_conflicts_with_edge(id_b, id_a, confirmed_at="2026-06-11T10:00:00+00:00")

        _, params1 = client1.writes[0]
        _, params2 = client2.writes[0]
        assert params1["a_id"] == params2["a_id"]
        assert params1["b_id"] == params2["b_id"]

    @pytest.mark.asyncio
    async def test_returns_true_when_row_returned(self):
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = self._FakeClient(return_row=True)
        writer = SignalGraphWriter(client)
        result = await writer.write_conflicts_with_edge(
            "sig-aaa", "sig-bbb", confirmed_at="2026-06-11T10:00:00+00:00"
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_row_returned(self):
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = self._FakeClient(return_row=False)
        writer = SignalGraphWriter(client)
        result = await writer.write_conflicts_with_edge(
            "sig-aaa", "sig-bbb", confirmed_at="2026-06-11T10:00:00+00:00"
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_never_raises_on_exception(self):
        """Best-effort: exceptions are swallowed, returns False."""
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = self._FakeClient(raise_exc=RuntimeError("neo4j exploded"))
        writer = SignalGraphWriter(client)
        result = await writer.write_conflicts_with_edge(
            "sig-aaa", "sig-bbb", confirmed_at="2026-06-11T10:00:00+00:00"
        )
        assert result is False


# ---------------------------------------------------------------------------
# EMITTED_STATES completeness check
# ---------------------------------------------------------------------------


def test_emitted_states_now_includes_conflicting():
    """After Sprint 4, conflicting must be in EMITTED_STATES."""
    from app.services.decision_states import EMITTED_STATES

    assert "conflicting" in EMITTED_STATES, (
        "conflicting must be in EMITTED_STATES after Sprint 4 implementation"
    )
