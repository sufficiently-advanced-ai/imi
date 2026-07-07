"""Tests for app/services/decision_view.py (Issue #954 — Task 2).

Covers:
- load_decision_signals: returns only decision-type signals across all meetings
- decision_to_view: shape of the returned dict
- list_decisions: filtering, ordering, counts_by_state, truncation, error paths
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

NOW = datetime(2026, 6, 11, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DECISION_ID_1 = "aaaaaaaa-0001-0001-0001-000000000001"
DECISION_ID_2 = "aaaaaaaa-0002-0002-0002-000000000002"
DECISION_ID_3 = "aaaaaaaa-0003-0003-0003-000000000003"
ACTION_ITEM_ID = "aaaaaaaa-0004-0004-0004-000000000004"

# "recent" timestamp — 5 days old → will be candidate/active, not stale
RECENT_TS = "2026-06-06T12:00:00+00:00"
# "old" timestamp — 120 days old → will be stale
OLD_TS = "2026-02-11T12:00:00+00:00"


def _build_store(tmp_path):
    """Build a SignalStore pre-populated with a mix of signals."""
    from app.models.signal import EntityRef, MeetingSignals, Signal
    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path / "signals")

    owner_ref = EntityRef(id="person-alice", type="person", name="Alice")

    # --- Meeting 1: two decisions + one action_item ---
    # Decision 1: confirmed → active
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
    # Decision 2: pending → candidate (superseded_by set → superseded state)
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
    # Action item — MUST be excluded from decision view
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

    # --- Meeting 2: one old decision (stale) + different client ---
    decision_stale = Signal(
        id=DECISION_ID_3,
        type="decision",
        content="Use Redis for caching.",
        source_meeting_id="bot-2",
        source_meeting_title="Infra Planning",
        source_timestamp=OLD_TS,  # 120 days old → stale
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
# load_decision_signals
# ---------------------------------------------------------------------------


class TestLoadDecisionSignals:
    def test_returns_only_decisions(self, store):
        from app.services.decision_view import load_decision_signals

        sigs = load_decision_signals(store=store)
        assert all(s.type == "decision" for s in sigs)

    def test_excludes_action_items(self, store):
        from app.services.decision_view import load_decision_signals

        sigs = load_decision_signals(store=store)
        ids = [s.id for s in sigs]
        assert ACTION_ITEM_ID not in ids

    def test_returns_all_decisions_across_meetings(self, store):
        from app.services.decision_view import load_decision_signals

        sigs = load_decision_signals(store=store)
        ids = {s.id for s in sigs}
        assert {DECISION_ID_1, DECISION_ID_2, DECISION_ID_3} == ids

    def test_empty_store_returns_empty(self, tmp_path):
        from app.services.decision_view import load_decision_signals
        from app.services.signal_store import SignalStore

        empty_store = SignalStore(signals_dir=tmp_path / "empty")
        assert load_decision_signals(store=empty_store) == []


# ---------------------------------------------------------------------------
# decision_to_view
# ---------------------------------------------------------------------------


class TestDecisionToView:
    def test_shape_has_all_required_keys(self, store):
        from app.services.decision_view import decision_to_view, load_decision_signals

        sigs = load_decision_signals(store=store)
        view = decision_to_view(sigs[0], now=NOW)

        expected_keys = {
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
        assert expected_keys == set(view.keys())

    def test_active_decision_state(self, store):
        from app.models.signal import Signal
        from app.services.decision_view import decision_to_view

        sig = Signal(
            id=DECISION_ID_1,
            type="decision",
            content="Active decision",
            source_meeting_id="bot-x",
            source_timestamp=RECENT_TS,
            review_status="confirmed",
            provenance_status="user_confirmed",
            can_use_as_instruction=True,
        )
        view = decision_to_view(sig, now=NOW)
        assert view["state"] == "active"
        assert view["state_reason"] == "review_status=confirmed"

    def test_superseded_decision(self, store):
        from app.models.signal import Signal
        from app.services.decision_view import decision_to_view

        sig = Signal(
            id=DECISION_ID_2,
            type="decision",
            content="Old decision",
            source_meeting_id="bot-x",
            source_timestamp=RECENT_TS,
            superseded_by=DECISION_ID_1,
            provenance_status="generated",
        )
        view = decision_to_view(sig, now=NOW)
        assert view["state"] == "superseded"
        assert view["superseded_by"] == DECISION_ID_1

    def test_owner_fields_populated(self, store):
        from app.models.signal import EntityRef, Signal
        from app.services.decision_view import decision_to_view

        sig = Signal(
            id=DECISION_ID_1,
            type="decision",
            content="Owned decision",
            source_meeting_id="bot-x",
            source_timestamp=RECENT_TS,
            owner=EntityRef(id="person-alice", type="person", name="Alice"),
            review_status="confirmed",
            provenance_status="user_confirmed",
            can_use_as_instruction=True,
        )
        view = decision_to_view(sig, now=NOW)
        assert view["owner"] == "Alice"
        assert view["owner_id"] == "person-alice"

    def test_no_owner_fields_none(self, store):
        from app.models.signal import Signal
        from app.services.decision_view import decision_to_view

        sig = Signal(
            id=DECISION_ID_1,
            type="decision",
            content="No owner",
            source_meeting_id="bot-x",
            source_timestamp=RECENT_TS,
            provenance_status="generated",
        )
        view = decision_to_view(sig, now=NOW)
        assert view["owner"] is None
        assert view["owner_id"] is None

    def test_age_days_computed(self, store):
        from app.models.signal import Signal
        from app.services.decision_view import decision_to_view

        sig = Signal(
            id=DECISION_ID_1,
            type="decision",
            content="Aged decision",
            source_meeting_id="bot-x",
            source_timestamp=RECENT_TS,  # 5 days before NOW
            provenance_status="generated",
        )
        view = decision_to_view(sig, now=NOW)
        # NOW=2026-06-11T00:00Z, RECENT_TS=2026-06-06T12:00Z → 4 days 12h → .days = 4
        assert view["age_days"] == 4


# ---------------------------------------------------------------------------
# list_decisions
# ---------------------------------------------------------------------------


class TestListDecisions:
    def test_returns_only_decisions_newest_first(self, store):
        """All returned entries are decisions, ordered newest source_timestamp first."""
        from app.services.decision_view import list_decisions

        result = list_decisions(store=store, now=NOW)
        decisions = result["decisions"]

        # Only decisions (action_item excluded)
        assert all(d["source_meeting_id"] in {"bot-1", "bot-2"} for d in decisions)
        assert len(decisions) == 3

        # Ordered newest source_timestamp first (RECENT_TS > OLD_TS)
        timestamps = [d["source_timestamp"] for d in decisions]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_total_and_counts_match(self, store):
        from app.services.decision_view import list_decisions

        result = list_decisions(store=store, now=NOW)
        assert result["total"] == 3

        counts = result["counts_by_state"]
        # States we expect: active (1), superseded (1), stale (1)
        assert counts.get("active", 0) == 1
        assert counts.get("superseded", 0) == 1
        assert counts.get("stale", 0) == 1

    def test_filter_by_state_active(self, store):
        from app.services.decision_view import list_decisions

        result = list_decisions(state="active", store=store, now=NOW)
        assert result["total"] == 1
        assert all(d["state"] == "active" for d in result["decisions"])

    def test_filter_by_state_superseded(self, store):
        from app.services.decision_view import list_decisions

        result = list_decisions(state="superseded", store=store, now=NOW)
        assert result["total"] == 1
        assert result["decisions"][0]["id"] == DECISION_ID_2

    def test_filter_by_owner_id(self, store):
        from app.services.decision_view import list_decisions

        result = list_decisions(owner_id="person-alice", store=store, now=NOW)
        # decision_active has owner, decision_stale has owner; decision_superseded has no owner
        assert result["total"] == 2

    def test_filter_by_client_id(self, store):
        from app.services.decision_view import list_decisions

        result = list_decisions(client_id="client-acme", store=store, now=NOW)
        assert result["total"] == 2
        assert all(d["client_id"] == "client-acme" for d in result["decisions"])

    def test_filter_by_client_id_beta(self, store):
        from app.services.decision_view import list_decisions

        result = list_decisions(client_id="client-beta", store=store, now=NOW)
        assert result["total"] == 1

    def test_filter_by_date_from(self, store):
        from app.services.decision_view import list_decisions

        # Only decisions on or after 2026-06-01 — the stale OLD_TS is before this
        result = list_decisions(
            date_from="2026-06-01T00:00:00+00:00", store=store, now=NOW
        )
        assert result["total"] == 2

    def test_filter_by_date_to(self, store):
        from app.services.decision_view import list_decisions

        # Only decisions on or before 2026-03-01 — only the stale one qualifies
        result = list_decisions(
            date_to="2026-03-01T00:00:00+00:00", store=store, now=NOW
        )
        assert result["total"] == 1

    def test_max_results_truncates_list_not_counts(self, store):
        from app.services.decision_view import list_decisions

        result = list_decisions(max_results=1, store=store, now=NOW)
        assert len(result["decisions"]) == 1
        # total and counts_by_state reflect ALL matches, not just the page
        assert result["total"] == 3
        counts_sum = sum(result["counts_by_state"].values())
        assert counts_sum == 3

    def test_invalid_state_raises_value_error(self, store):
        from app.services.decision_view import list_decisions

        with pytest.raises(ValueError, match="bogus"):
            list_decisions(state="bogus", store=store, now=NOW)

    def test_reserved_state_accepted_returns_empty(self, store):
        """Reserved states (zombie, temporary, conflicting) are valid members of
        DECISION_STATES but never emitted by compute_decision_state in Sprint 1,
        so filtering by them returns an empty result without raising."""
        from app.services.decision_view import list_decisions

        result = list_decisions(state="zombie", store=store, now=NOW)
        assert result["decisions"] == []
        assert result["total"] == 0

    def test_non_decision_signals_never_appear(self, store):
        from app.services.decision_view import list_decisions

        result = list_decisions(store=store, now=NOW)
        ids = [d["id"] for d in result["decisions"]]
        assert ACTION_ITEM_ID not in ids

    def test_empty_store_returns_empty_result(self, tmp_path):
        from app.services.decision_view import list_decisions
        from app.services.signal_store import SignalStore

        empty_store = SignalStore(signals_dir=tmp_path / "empty")
        result = list_decisions(store=empty_store, now=NOW)
        assert result["decisions"] == []
        assert result["total"] == 0
        assert result["counts_by_state"] == {}

    def test_combined_filters_and_semantics(self, store):
        """Test that multiple filters apply AND semantics.

        Fixture has:
        - decision_active (id=1): client="client-acme", owner="person-alice"
        - decision_superseded (id=2): client="client-acme", owner=None
        - decision_stale (id=3): client="client-beta", owner="person-alice"

        Filter by client_id="client-acme" AND owner_id="person-alice"
        should return only decision_active (id=1).
        """
        from app.services.decision_view import list_decisions

        result = list_decisions(
            client_id="client-acme",
            owner_id="person-alice",
            store=store,
            now=NOW,
        )
        assert result["total"] == 1
        assert len(result["decisions"]) == 1
        assert result["decisions"][0]["id"] == DECISION_ID_1
        assert result["decisions"][0]["client_id"] == "client-acme"
        assert result["decisions"][0]["owner_id"] == "person-alice"


# ---------------------------------------------------------------------------
# get_decision
# ---------------------------------------------------------------------------


class TestGetDecision:
    def test_unknown_id_returns_none(self, store):
        """Unknown decision id returns None."""
        from app.services.decision_view import get_decision

        result = get_decision("nope", store=store)
        assert result is None

    def test_supersession_chain_lineage(self, tmp_path):
        """Three-decision chain A→B→C: get_decision(B) returns [A(pred), B(self), C(succ)]."""
        from app.models.signal import MeetingSignals, Signal
        from app.services.decision_view import get_decision
        from app.services.signal_store import SignalStore

        ID_A = "cccccccc-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        ID_B = "cccccccc-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        ID_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"

        store = SignalStore(signals_dir=tmp_path / "signals")

        sig_a = Signal(
            id=ID_A,
            type="decision",
            content="Decision A",
            source_meeting_id="bot-chain",
            source_timestamp="2026-01-01T10:00:00+00:00",
            provenance_status="superseded",
            review_status="merged",
            can_use_as_instruction=False,
            superseded_by=ID_B,
        )
        sig_b = Signal(
            id=ID_B,
            type="decision",
            content="Decision B",
            source_meeting_id="bot-chain",
            source_timestamp="2026-02-01T10:00:00+00:00",
            provenance_status="superseded",
            review_status="merged",
            can_use_as_instruction=False,
            superseded_by=ID_C,
        )
        sig_c = Signal(
            id=ID_C,
            type="decision",
            content="Decision C",
            source_meeting_id="bot-chain",
            source_timestamp="2026-03-01T10:00:00+00:00",
            provenance_status="generated",
            review_status="pending",
        )
        store.save(
            MeetingSignals(
                meeting_id="m-chain",
                bot_id="bot-chain",
                signals=[sig_a, sig_b, sig_c],
            )
        )

        result = get_decision(ID_B, store=store, now=NOW)
        assert result is not None

        lineage = result["lineage"]
        assert len(lineage) == 3

        # Predecessor first, then self, then successor
        assert lineage[0]["id"] == ID_A
        assert lineage[0]["relation"] == "predecessor"
        assert lineage[1]["id"] == ID_B
        assert lineage[1]["relation"] == "self"
        assert lineage[2]["id"] == ID_C
        assert lineage[2]["relation"] == "successor"

        # Each entry has the required fields
        for entry in lineage:
            assert {"id", "content", "state", "source_timestamp", "relation"} <= set(
                entry.keys()
            )

    def test_audit_history_round_trip(self, tmp_path):
        """get_decision returns audit_history rows after a real governance round-trip."""
        from app.models.signal import MeetingSignals, Signal
        from app.services.decision_view import get_decision
        from app.services.signal_audit import SignalAuditStore, review_with_audit
        from app.services.signal_store import SignalStore

        AUDIT_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"

        store = SignalStore(signals_dir=tmp_path / "signals")
        audit_store = SignalAuditStore(
            audit_dir=tmp_path / "audit",
            repo_root=tmp_path,
        )

        sig = Signal(
            id=AUDIT_ID,
            type="decision",
            content="Decision to be confirmed",
            source_meeting_id="bot-audit",
            source_timestamp=RECENT_TS,
            provenance_status="generated",
            review_status="pending",
        )
        store.save(
            MeetingSignals(
                meeting_id="m-audit",
                bot_id="bot-audit",
                signals=[sig],
            )
        )

        # Perform a real governance round-trip
        new_sig, record = review_with_audit(sig, "confirm", actor="test-user")
        audit_store.append(record)

        result = get_decision(AUDIT_ID, store=store, audit_store=audit_store, now=NOW)
        assert result is not None

        audit_history = result["audit_history"]
        assert len(audit_history) == 1

        row = audit_history[0]
        assert row["action"] == "confirm"
        assert row["gate_response"] == "allow"
        assert row["actor"] == "test-user"
        assert "reasoning" in row
        assert "created_at" in row

    def test_cycle_guard_does_not_hang(self, tmp_path):
        """Cycle A.superseded_by=B, B.superseded_by=A returns without hanging."""
        from app.models.signal import MeetingSignals, Signal
        from app.services.decision_view import get_decision
        from app.services.signal_store import SignalStore

        ID_A = "eeeeeeee-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        ID_B = "eeeeeeee-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

        store = SignalStore(signals_dir=tmp_path / "signals")

        sig_a = Signal(
            id=ID_A,
            type="decision",
            content="Cycle A",
            source_meeting_id="bot-cycle",
            source_timestamp="2026-01-01T10:00:00+00:00",
            provenance_status="superseded",
            review_status="merged",
            can_use_as_instruction=False,
            superseded_by=ID_B,
        )
        sig_b = Signal(
            id=ID_B,
            type="decision",
            content="Cycle B",
            source_meeting_id="bot-cycle",
            source_timestamp="2026-02-01T10:00:00+00:00",
            provenance_status="superseded",
            review_status="merged",
            can_use_as_instruction=False,
            superseded_by=ID_A,
        )
        store.save(
            MeetingSignals(
                meeting_id="m-cycle",
                bot_id="bot-cycle",
                signals=[sig_a, sig_b],
            )
        )

        # Must return without hanging or raising; lineage contains both signals
        result = get_decision(ID_A, store=store, now=NOW)
        assert result is not None

        lineage_ids = {entry["id"] for entry in result["lineage"]}
        assert ID_A in lineage_ids
        assert ID_B in lineage_ids

    def test_governance_ladder_instruction(self, tmp_path):
        """Confirmed + instruction-grade decision → position='instruction'."""
        from app.models.signal import MeetingSignals, Signal
        from app.services.decision_view import get_decision
        from app.services.signal_store import SignalStore

        ID = "ffffffff-1111-1111-1111-111111111111"
        store = SignalStore(signals_dir=tmp_path / "signals")

        sig = Signal(
            id=ID,
            type="decision",
            content="Instruction-grade decision",
            source_meeting_id="bot-gov",
            source_timestamp=RECENT_TS,
            provenance_status="user_confirmed",
            review_status="confirmed",
            can_use_as_evidence=True,
            can_use_as_instruction=True,
        )
        store.save(
            MeetingSignals(
                meeting_id="m-gov",
                bot_id="bot-gov",
                signals=[sig],
            )
        )

        result = get_decision(ID, store=store, now=NOW)
        assert result is not None

        ladder = result["governance_ladder"]
        assert ladder["position"] == "instruction"
        assert ladder["can_use_as_evidence"] is True
        assert ladder["can_use_as_instruction"] is True

    def test_governance_ladder_evidence_only(self, tmp_path):
        """evidence_only review → position='evidence'."""
        from app.models.signal import MeetingSignals, Signal
        from app.services.decision_view import get_decision
        from app.services.signal_store import SignalStore

        ID = "ffffffff-2222-2222-2222-222222222222"
        store = SignalStore(signals_dir=tmp_path / "signals")

        sig = Signal(
            id=ID,
            type="decision",
            content="Evidence-only decision",
            source_meeting_id="bot-gov",
            source_timestamp=RECENT_TS,
            provenance_status="generated",
            review_status="evidence_only",
            can_use_as_evidence=True,
            can_use_as_instruction=False,
        )
        store.save(
            MeetingSignals(
                meeting_id="m-gov2",
                bot_id="bot-gov",
                signals=[sig],
            )
        )

        result = get_decision(ID, store=store, now=NOW)
        assert result is not None

        ladder = result["governance_ladder"]
        assert ladder["position"] == "evidence"
        assert ladder["can_use_as_evidence"] is True
        assert ladder["can_use_as_instruction"] is False

    def test_governance_ladder_blocked(self, tmp_path):
        """Rejected decision → position='blocked'."""
        from app.models.signal import MeetingSignals, Signal
        from app.services.decision_view import get_decision
        from app.services.signal_store import SignalStore

        ID = "ffffffff-3333-3333-3333-333333333333"
        store = SignalStore(signals_dir=tmp_path / "signals")

        sig = Signal(
            id=ID,
            type="decision",
            content="Rejected decision",
            source_meeting_id="bot-gov",
            source_timestamp=RECENT_TS,
            provenance_status="generated",
            review_status="rejected",
            can_use_as_evidence=False,
            can_use_as_instruction=False,
        )
        store.save(
            MeetingSignals(
                meeting_id="m-gov3",
                bot_id="bot-gov",
                signals=[sig],
            )
        )

        result = get_decision(ID, store=store, now=NOW)
        assert result is not None

        ladder = result["governance_ladder"]
        assert ladder["position"] == "blocked"
        assert ladder["can_use_as_evidence"] is False
        assert ladder["can_use_as_instruction"] is False


# ---------------------------------------------------------------------------
# compute_decision_stats
# ---------------------------------------------------------------------------


class TestComputeDecisionStats:
    def test_stats_headline_exact(self, store):
        """Verify exact headline string with fixture contents: 2 meetings, 3 decisions, 1 stale, 1 superseded."""
        from app.services.decision_view import compute_decision_stats

        stats = compute_decision_stats(store=store, now=NOW)
        assert (
            stats["headline"] == "Across 2 meetings: 3 decisions, 1 stale, 1 superseded"
        )

    def test_stats_meetings_count(self, store):
        """Count of distinct meetings (via load_all)."""
        from app.services.decision_view import compute_decision_stats

        stats = compute_decision_stats(store=store, now=NOW)
        assert stats["meetings"] == 2

    def test_stats_decisions_count(self, store):
        """Count of decision-type signals."""
        from app.services.decision_view import compute_decision_stats

        stats = compute_decision_stats(store=store, now=NOW)
        assert stats["decisions"] == 3

    def test_stats_counts_by_state(self, store):
        """Count of decisions per emitted state."""
        from app.services.decision_view import compute_decision_stats

        stats = compute_decision_stats(store=store, now=NOW)
        counts = stats["counts_by_state"]
        # Fixture: active=1, superseded=1, stale=1
        assert counts["active"] == 1
        assert counts["superseded"] == 1
        assert counts["stale"] == 1
        assert sum(counts.values()) == 3

    def test_stats_stale_count(self, store):
        """Count of decisions in stale state."""
        from app.services.decision_view import compute_decision_stats

        stats = compute_decision_stats(store=store, now=NOW)
        assert stats["stale"] == 1

    def test_stats_superseded_count(self, store):
        """Count of decisions in superseded state."""
        from app.services.decision_view import compute_decision_stats

        stats = compute_decision_stats(store=store, now=NOW)
        assert stats["superseded"] == 1

    def test_empty_store_headline(self, tmp_path):
        """Empty store yields all-zero headline."""
        from app.services.decision_view import compute_decision_stats
        from app.services.signal_store import SignalStore

        empty_store = SignalStore(signals_dir=tmp_path / "empty")
        stats = compute_decision_stats(store=empty_store, now=NOW)
        assert (
            stats["headline"] == "Across 0 meetings: 0 decisions, 0 stale, 0 superseded"
        )

    def test_empty_store_all_fields(self, tmp_path):
        """Empty store returns all expected fields as zero."""
        from app.services.decision_view import compute_decision_stats
        from app.services.signal_store import SignalStore

        empty_store = SignalStore(signals_dir=tmp_path / "empty")
        stats = compute_decision_stats(store=empty_store, now=NOW)
        assert stats["meetings"] == 0
        assert stats["decisions"] == 0
        assert stats["counts_by_state"] == {}
        assert stats["stale"] == 0
        assert stats["superseded"] == 0
