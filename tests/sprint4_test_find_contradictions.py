"""
S4-4: find_contradictions — semantic sourcing, keyword detector removed.

Contract tests:
- Top-level keys preserved: entity_id, signals_analyzed, contradictions
- Contradiction entry keys: signal_a, signal_b, type, reason, timestamp_a, timestamp_b
  (plus optional status, confidence, speakers)

New behaviour:
- Pending candidates (metadata.conflict_candidates, status==pending) → status "candidate"
- Confirmed pairs (metadata.conflicts_with) → status "confirmed", deduped
- Classic keyword/sentiment reversal ("on track" → "delayed") → NO contradiction
- _detect_contradictions no longer exists

Window filter: date_from / date_to applied against proposed_at or signal source_timestamp.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

NOW = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
PAST = NOW - timedelta(days=30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_svc(graph_records=None):
    """Build a TemporalQueryService with a mocked graph_store."""
    from app.services.temporal_queries import TemporalQueryService

    mock_sk = MagicMock()
    mock_sk.graph_store = MagicMock()
    mock_sk.graph_store.execute_query = MagicMock(return_value=graph_records or [])
    return TemporalQueryService(mock_sk)


def _make_signal(
    signal_id,
    content="content",
    source_timestamp=None,
    conflict_candidates=None,
    conflicts_with=None,
):
    """Build a minimal mock Signal object."""
    sig = MagicMock()
    sig.id = signal_id
    sig.content = content
    sig.source_timestamp = source_timestamp or NOW.isoformat()
    sig.signal_type = "decision"
    meta = {}
    if conflict_candidates is not None:
        meta["conflict_candidates"] = conflict_candidates
    if conflicts_with is not None:
        meta["conflicts_with"] = conflicts_with
    sig.metadata = meta
    return sig


# ===========================================================================
# Contract: top-level keys
# ===========================================================================


class TestContractTopLevelKeys:
    """The top-level keys of the return dict must never change."""

    @pytest.mark.asyncio
    async def test_top_level_keys_present_empty(self):
        svc = _make_svc(graph_records=[])
        result = await svc.find_contradictions("entity-1")
        assert "entity_id" in result
        assert "signals_analyzed" in result
        assert "contradictions" in result

    @pytest.mark.asyncio
    async def test_entity_id_echoed(self):
        svc = _make_svc(graph_records=[])
        result = await svc.find_contradictions("my-entity-42")
        assert result["entity_id"] == "my-entity-42"

    @pytest.mark.asyncio
    async def test_signals_analyzed_is_int(self):
        svc = _make_svc(graph_records=[
            {"signal_id": "s1", "content": "x", "timestamp": NOW.isoformat(), "type": "decision"},
        ])
        # Patch store to return no metadata (no candidates/confirmed)
        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.return_value = None
            result = await svc.find_contradictions("entity-1")
        assert isinstance(result["signals_analyzed"], int)
        assert result["signals_analyzed"] >= 0


# ===========================================================================
# Contract: contradiction entry keys
# ===========================================================================


class TestContractEntryKeys:
    """Every contradiction entry must have the legacy keys callers rely on."""

    @pytest.mark.asyncio
    async def test_candidate_entry_has_legacy_keys(self):
        """A pending candidate produces an entry with all legacy keys present."""
        svc = _make_svc(graph_records=[
            {
                "signal_id": "s1",
                "content": "We will use PostgreSQL for all storage",
                "timestamp": PAST.isoformat(),
                "type": "decision",
            },
        ])

        candidate = {
            "other_signal_id": "s2",
            "other_content": "We will use MongoDB instead",
            "rationale": "Directly contradicts the PostgreSQL decision",
            "confidence": 0.92,
            "speakers": ["Alice"],
            "status": "pending",
            "proposed_at": NOW.isoformat(),
        }
        signal_s1 = _make_signal("s1", conflict_candidates=[candidate])

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = lambda sid: (
                (signal_s1, MagicMock()) if sid == "s1" else None
            )
            result = await svc.find_contradictions("entity-1")

        assert len(result["contradictions"]) == 1
        entry = result["contradictions"][0]
        # Legacy keys
        assert "signal_a" in entry
        assert "signal_b" in entry
        assert "reason" in entry
        assert "type" in entry
        assert "timestamp_a" in entry
        assert "timestamp_b" in entry
        # New semantic keys
        assert "status" in entry
        assert "confidence" in entry
        assert "speakers" in entry

    @pytest.mark.asyncio
    async def test_confirmed_entry_has_legacy_keys(self):
        """A confirmed conflict pair produces an entry with all legacy keys."""
        svc = _make_svc(graph_records=[
            {
                "signal_id": "s1",
                "content": "Use React for the frontend",
                "timestamp": PAST.isoformat(),
                "type": "decision",
            },
            {
                "signal_id": "s2",
                "content": "Use Vue for the frontend",
                "timestamp": NOW.isoformat(),
                "type": "decision",
            },
        ])

        signal_s1 = _make_signal("s1", conflicts_with=["s2"])
        signal_s2 = _make_signal("s2", conflicts_with=["s1"])

        def _lookup(sid):
            if sid == "s1":
                return (signal_s1, MagicMock())
            if sid == "s2":
                return (signal_s2, MagicMock())
            return None

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = _lookup
            result = await svc.find_contradictions("entity-1")

        assert len(result["contradictions"]) >= 1
        entry = result["contradictions"][0]
        assert "signal_a" in entry
        assert "signal_b" in entry
        assert "reason" in entry
        assert "type" in entry
        assert "timestamp_a" in entry
        assert "timestamp_b" in entry
        assert entry["status"] == "confirmed"


# ===========================================================================
# Semantic sourcing: pending candidates
# ===========================================================================


class TestPendingCandidates:
    """Signals with metadata.conflict_candidates (status==pending) are surfaced."""

    @pytest.mark.asyncio
    async def test_pending_candidate_returned_as_candidate_status(self):
        svc = _make_svc(graph_records=[
            {"signal_id": "s1", "content": "c1", "timestamp": PAST.isoformat(), "type": "decision"},
        ])

        candidate = {
            "other_signal_id": "s2",
            "other_content": "contradictory content",
            "rationale": "Direct semantic contradiction detected by LLM",
            "confidence": 0.88,
            "speakers": ["Bob"],
            "status": "pending",
            "proposed_at": NOW.isoformat(),
        }
        signal_s1 = _make_signal("s1", conflict_candidates=[candidate])

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = lambda sid: (
                (signal_s1, MagicMock()) if sid == "s1" else None
            )
            result = await svc.find_contradictions("entity-1")

        assert len(result["contradictions"]) == 1
        entry = result["contradictions"][0]
        assert entry["signal_a"] == "s1"
        assert entry["signal_b"] == "s2"
        assert entry["status"] == "candidate"
        assert entry["reason"] == "Direct semantic contradiction detected by LLM"
        assert entry["confidence"] == 0.88
        assert entry["speakers"] == ["Bob"]

    @pytest.mark.asyncio
    async def test_non_pending_candidates_skipped(self):
        """Rejected/dismissed candidates are NOT surfaced."""
        svc = _make_svc(graph_records=[
            {"signal_id": "s1", "content": "c1", "timestamp": PAST.isoformat(), "type": "decision"},
        ])

        rejected_candidate = {
            "other_signal_id": "s2",
            "other_content": "contradictory content",
            "rationale": "Not really a conflict",
            "confidence": 0.55,
            "speakers": [],
            "status": "rejected",
            "proposed_at": NOW.isoformat(),
        }
        signal_s1 = _make_signal("s1", conflict_candidates=[rejected_candidate])

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = lambda sid: (
                (signal_s1, MagicMock()) if sid == "s1" else None
            )
            result = await svc.find_contradictions("entity-1")

        assert result["contradictions"] == []

    @pytest.mark.asyncio
    async def test_no_candidates_no_contradictions(self):
        """Signals with no conflict metadata produce no contradictions."""
        svc = _make_svc(graph_records=[
            {"signal_id": "s1", "content": "c1", "timestamp": PAST.isoformat(), "type": "decision"},
        ])
        signal_s1 = _make_signal("s1")  # no conflict metadata

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = lambda sid: (
                (signal_s1, MagicMock()) if sid == "s1" else None
            )
            result = await svc.find_contradictions("entity-1")

        assert result["contradictions"] == []


# ===========================================================================
# Semantic sourcing: confirmed pairs
# ===========================================================================


class TestConfirmedPairs:
    """Signals with metadata.conflicts_with produce confirmed entries, deduped."""

    @pytest.mark.asyncio
    async def test_confirmed_pair_appears_once(self):
        """A (s1, s2) confirmed pair should appear exactly once, not twice."""
        svc = _make_svc(graph_records=[
            {"signal_id": "s1", "content": "Use SQL", "timestamp": PAST.isoformat(), "type": "decision"},
            {"signal_id": "s2", "content": "Use NoSQL", "timestamp": NOW.isoformat(), "type": "decision"},
        ])

        signal_s1 = _make_signal("s1", conflicts_with=["s2"])
        signal_s2 = _make_signal("s2", conflicts_with=["s1"])

        def _lookup(sid):
            if sid == "s1":
                return (signal_s1, MagicMock())
            if sid == "s2":
                return (signal_s2, MagicMock())
            return None

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = _lookup
            result = await svc.find_contradictions("entity-1")

        confirmed = [e for e in result["contradictions"] if e["status"] == "confirmed"]
        assert len(confirmed) == 1

    @pytest.mark.asyncio
    async def test_confirmed_status_on_entry(self):
        svc = _make_svc(graph_records=[
            {"signal_id": "s1", "content": "Use SQL", "timestamp": PAST.isoformat(), "type": "decision"},
            {"signal_id": "s2", "content": "Use NoSQL", "timestamp": NOW.isoformat(), "type": "decision"},
        ])

        signal_s1 = _make_signal("s1", conflicts_with=["s2"])
        signal_s2 = _make_signal("s2", conflicts_with=["s1"])

        def _lookup(sid):
            return {
                "s1": (signal_s1, MagicMock()),
                "s2": (signal_s2, MagicMock()),
            }.get(sid)

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = _lookup
            result = await svc.find_contradictions("entity-1")

        entry = result["contradictions"][0]
        assert entry["status"] == "confirmed"

    @pytest.mark.asyncio
    async def test_confirmed_entry_ids_canonical_order(self):
        """signal_a should be the lexicographically smaller id (canonical dedup)."""
        svc = _make_svc(graph_records=[
            {"signal_id": "aaa", "content": "Use SQL", "timestamp": PAST.isoformat(), "type": "decision"},
            {"signal_id": "zzz", "content": "Use NoSQL", "timestamp": NOW.isoformat(), "type": "decision"},
        ])

        signal_aaa = _make_signal("aaa", conflicts_with=["zzz"])
        signal_zzz = _make_signal("zzz", conflicts_with=["aaa"])

        def _lookup(sid):
            return {
                "aaa": (signal_aaa, MagicMock()),
                "zzz": (signal_zzz, MagicMock()),
            }.get(sid)

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = _lookup
            result = await svc.find_contradictions("entity-1")

        confirmed = [e for e in result["contradictions"] if e["status"] == "confirmed"]
        assert len(confirmed) == 1
        assert confirmed[0]["signal_a"] == "aaa", (
            "signal_a must be the lexicographically smaller id"
        )
        assert confirmed[0]["signal_b"] == "zzz", (
            "signal_b must be the lexicographically larger id"
        )


# ===========================================================================
# Keyword path is gone
# ===========================================================================


class TestKeywordPathGone:
    """Classic sentiment reversal must NOT produce contradictions."""

    @pytest.mark.asyncio
    async def test_on_track_then_delayed_no_contradiction(self):
        """'on track' → 'delayed' keyword reversal must produce zero contradictions."""
        svc = _make_svc(graph_records=[
            {
                "signal_id": "s1",
                "content": "Project Alpha is on track for Q1 delivery",
                "timestamp": PAST.isoformat(),
                "type": "status_update",
            },
            {
                "signal_id": "s2",
                "content": "Project Alpha is delayed, won't ship until Q3",
                "timestamp": NOW.isoformat(),
                "type": "status_update",
            },
        ])

        # Signals have no conflict metadata — keyword path must not activate
        signal_s1 = _make_signal("s1")
        signal_s2 = _make_signal("s2")

        def _lookup(sid):
            return {
                "s1": (signal_s1, MagicMock()),
                "s2": (signal_s2, MagicMock()),
            }.get(sid)

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = _lookup
            result = await svc.find_contradictions("entity-1")

        assert result["contradictions"] == [], (
            "keyword/sentiment reversal must not produce contradictions (keyword path removed)"
        )

    @pytest.mark.asyncio
    async def test_completed_then_cancelled_no_contradiction(self):
        """'completed' → 'cancelled' keyword reversal must produce zero contradictions."""
        svc = _make_svc(graph_records=[
            {
                "signal_id": "s1",
                "content": "Task completed successfully",
                "timestamp": PAST.isoformat(),
                "type": "status_update",
            },
            {
                "signal_id": "s2",
                "content": "Task cancelled due to budget",
                "timestamp": NOW.isoformat(),
                "type": "status_update",
            },
        ])

        signal_s1 = _make_signal("s1")
        signal_s2 = _make_signal("s2")

        def _lookup(sid):
            return {
                "s1": (signal_s1, MagicMock()),
                "s2": (signal_s2, MagicMock()),
            }.get(sid)

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = _lookup
            result = await svc.find_contradictions("entity-1")

        assert result["contradictions"] == []


# ===========================================================================
# _detect_contradictions is gone
# ===========================================================================


class TestDetectContradictionsRemoved:
    """_detect_contradictions must not exist as a module-level name."""

    def test_detect_contradictions_not_importable(self):
        import importlib
        import app.services.temporal_queries as tq
        importlib.reload(tq)
        assert not hasattr(tq, "_detect_contradictions"), (
            "_detect_contradictions must be deleted; keyword detector is removed (S4-4)"
        )


# ===========================================================================
# Window filtering
# ===========================================================================


class TestWindowFiltering:
    """date_from / date_to should be passed to the Cypher query."""

    @pytest.mark.asyncio
    async def test_date_filters_passed_to_query(self):
        svc = _make_svc(graph_records=[])
        result = await svc.find_contradictions(
            "entity-1",
            date_from=PAST,
            date_to=NOW,
        )

        call_args = svc.sk.graph_store.execute_query.call_args
        assert call_args is not None
        _cypher, params = call_args[0]
        assert "date_from" in params
        assert "date_to" in params
        assert result["contradictions"] == []

    @pytest.mark.asyncio
    async def test_no_date_filters_no_params(self):
        svc = _make_svc(graph_records=[])
        await svc.find_contradictions("entity-1")

        call_args = svc.sk.graph_store.execute_query.call_args
        _cypher, params = call_args[0]
        assert "date_from" not in params
        assert "date_to" not in params

    @pytest.mark.asyncio
    async def test_candidate_outside_window_excluded(self):
        """Candidates whose proposed_at is outside the window must be excluded."""
        svc = _make_svc(graph_records=[
            {"signal_id": "s1", "content": "c1", "timestamp": PAST.isoformat(), "type": "decision"},
        ])

        # Candidate proposed well before the window
        old_proposed_at = (PAST - timedelta(days=60)).isoformat()
        candidate = {
            "other_signal_id": "s2",
            "other_content": "contradicts",
            "rationale": "rationale",
            "confidence": 0.9,
            "speakers": [],
            "status": "pending",
            "proposed_at": old_proposed_at,
        }
        signal_s1 = _make_signal("s1", conflict_candidates=[candidate])

        # Window starts at PAST (after the candidate's proposed_at)
        window_start = PAST

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = lambda sid: (
                (signal_s1, MagicMock()) if sid == "s1" else None
            )
            result = await svc.find_contradictions("entity-1", date_from=window_start)

        assert result["contradictions"] == []

    @pytest.mark.asyncio
    async def test_candidate_within_window_included(self):
        """Candidates whose proposed_at is inside the window must be included."""
        svc = _make_svc(graph_records=[
            {"signal_id": "s1", "content": "c1", "timestamp": PAST.isoformat(), "type": "decision"},
        ])

        candidate = {
            "other_signal_id": "s2",
            "other_content": "contradicts",
            "rationale": "semantic contradiction",
            "confidence": 0.9,
            "speakers": [],
            "status": "pending",
            "proposed_at": NOW.isoformat(),
        }
        signal_s1 = _make_signal("s1", conflict_candidates=[candidate])

        # Window entirely covers NOW
        window_start = PAST

        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = lambda sid: (
                (signal_s1, MagicMock()) if sid == "s1" else None
            )
            result = await svc.find_contradictions("entity-1", date_from=window_start)

        assert len(result["contradictions"]) == 1


# ===========================================================================
# REFERENCES_ relationship still used
# ===========================================================================


class TestReferencesRelationship:
    """Cypher must still query REFERENCES_* edges (not :ABOUT)."""

    @pytest.mark.asyncio
    async def test_uses_references_relationship_pattern(self):
        svc = _make_svc(graph_records=[])
        await svc.find_contradictions("entity-1")

        cypher = svc.sk.graph_store.execute_query.call_args[0][0]
        assert "REFERENCES_" in cypher
        assert "ABOUT" not in cypher
        assert "MENTIONS" in cypher
