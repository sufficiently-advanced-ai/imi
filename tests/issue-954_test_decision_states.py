"""Tests for the decision lifecycle state computation (Issue #954).

Tests the pure decision state machine: candidate → active → stale/superseded,
with rejection and manual stale marking. Covers the temporal lifecycle orthogonal
to the governance axis (provenance_status, review_status).

See docs/prd/decision-state-and-world-model-prd.md and the temporal-lifecycle
comment in app/models/signal.py (~line 66).
"""

from datetime import UTC, datetime

from app.models.signal import Signal
from app.services.decision_states import (
    DECISION_STATES,
    EMITTED_STATES,
    STALE_AGE_DAYS,
    compute_decision_state,
    decision_age_days,
)

NOW = datetime(2026, 6, 11, tzinfo=UTC)


def _decision(**overrides) -> Signal:
    """Construct a minimal valid decision Signal with optional field overrides."""
    fields: dict[str, object] = dict(
        id="sig-1",
        type="decision",
        content="Ship it.",
        source_meeting_id="bot-123",
        source_timestamp="2026-06-05T10:00:00+00:00",
    )
    fields.update(overrides)
    return Signal(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Core lifecycle: candidate → active → stale/superseded/rejected
# ---------------------------------------------------------------------------


def test_pending_recent_is_candidate():
    """A pending decision with no special flags is a candidate."""
    state, _ = compute_decision_state(_decision(), now=NOW)
    assert state == "candidate"


def test_confirmed_recent_is_active():
    """A confirmed + user_confirmed decision is active."""
    sig = _decision(review_status="confirmed", provenance_status="user_confirmed")
    state, reason = compute_decision_state(sig, now=NOW)
    assert state == "active" and "confirmed" in reason


def test_superseded_by_wins_over_confirmed():
    """Superseded takes precedence over confirmed state."""
    sig = _decision(
        review_status="confirmed",
        provenance_status="user_confirmed",
        superseded_by="sig-2",
    )
    assert compute_decision_state(sig, now=NOW)[0] == "superseded"


def test_manual_stale_flag():
    """review_status=stale immediately marks decision as stale."""
    assert (
        compute_decision_state(_decision(review_status="stale"), now=NOW)[0] == "stale"
    )


def test_age_based_staleness_on_confirmed():
    """A confirmed decision older than STALE_AGE_DAYS is stale."""
    sig = _decision(
        review_status="confirmed",
        provenance_status="user_confirmed",
        source_timestamp="2025-12-01T10:00:00+00:00",  # ~192 days old
    )
    state, reason = compute_decision_state(sig, now=NOW)
    assert state == "stale" and "90" in reason


def test_age_exactly_90_days_is_not_stale():
    """Age == STALE_AGE_DAYS (90 days) is NOT stale (strict >)."""
    # Exactly 90 days before NOW
    sig = _decision(
        review_status="confirmed",
        provenance_status="user_confirmed",
        source_timestamp="2026-03-13T10:00:00+00:00",
    )
    state, reason = compute_decision_state(sig, now=NOW)
    assert (
        state == "active"
    ), f"Expected active at boundary (90d), got {state}: {reason}"


def test_rejected_state():
    """A rejected decision is in rejected state."""
    assert (
        compute_decision_state(_decision(review_status="rejected"), now=NOW)[0]
        == "rejected"
    )


# ---------------------------------------------------------------------------
# Precedence and reserved states
# ---------------------------------------------------------------------------


def test_superseded_beats_rejected():
    """Superseded takes precedence over rejected."""
    sig = _decision(review_status="rejected", superseded_by="sig-2")
    assert compute_decision_state(sig, now=NOW)[0] == "superseded"


def test_rejected_beats_stale():
    """Rejected takes precedence over age-based stale."""
    sig = _decision(
        review_status="rejected",
        source_timestamp="2025-12-01T10:00:00+00:00",
    )
    assert compute_decision_state(sig, now=NOW)[0] == "rejected"


# ---------------------------------------------------------------------------
# Conflicting state (Sprint 4, R3.5)
# ---------------------------------------------------------------------------


def test_conflicting_state_emitted_when_conflicts_with_nonempty():
    """A signal with non-empty metadata.conflicts_with is 'conflicting'."""
    sig = _decision(
        review_status="confirmed",
        metadata={"conflicts_with": ["sig-other-1", "sig-other-2"]},
    )
    state, reason = compute_decision_state(sig, now=NOW)
    assert state == "conflicting"
    assert "2" in reason  # "conflicts with 2 decision(s)"
    assert "sig-othe" in reason  # short IDs (8 chars) present


def test_conflicting_state_with_single_conflict():
    """A signal with one conflict in conflicts_with is 'conflicting'."""
    sig = _decision(metadata={"conflicts_with": ["sig-abc12345"]})
    state, reason = compute_decision_state(sig, now=NOW)
    assert state == "conflicting"
    assert "1" in reason


def test_empty_conflicts_with_falls_through():
    """Empty conflicts_with list does NOT trigger conflicting state."""
    sig = _decision(metadata={"conflicts_with": []})
    state, _ = compute_decision_state(sig, now=NOW)
    assert state != "conflicting"


def test_none_conflicts_with_falls_through():
    """metadata without conflicts_with key does not trigger conflicting state."""
    sig = _decision(metadata={})
    state, _ = compute_decision_state(sig, now=NOW)
    assert state != "conflicting"


def test_superseded_beats_conflicting():
    """Superseded (highest precedence) beats conflicting."""
    sig = _decision(
        superseded_by="sig-2",
        metadata={"conflicts_with": ["sig-other"]},
    )
    assert compute_decision_state(sig, now=NOW)[0] == "superseded"


def test_rejected_beats_conflicting():
    """Rejected beats conflicting (rejected is higher precedence)."""
    sig = _decision(
        review_status="rejected",
        metadata={"conflicts_with": ["sig-other"]},
    )
    assert compute_decision_state(sig, now=NOW)[0] == "rejected"


def test_conflicting_beats_zombie():
    """Conflicting beats zombie (conflicting is higher precedence than zombie)."""
    sig = _decision(
        metadata={
            "conflicts_with": ["sig-other"],
            "revisit_date": "2025-01-01",  # in the past → would be zombie
        },
    )
    assert compute_decision_state(sig, now=NOW)[0] == "conflicting"


def test_conflicting_beats_temporary():
    """Conflicting beats temporary (conflicting is higher precedence than temporary)."""
    sig = _decision(
        metadata={
            "conflicts_with": ["sig-other"],
            "revisit_date": "2030-01-01",  # future → would be temporary
        },
    )
    assert compute_decision_state(sig, now=NOW)[0] == "conflicting"


def test_stale_beats_active():
    """Age-based stale takes precedence over active."""
    sig = _decision(
        review_status="confirmed",
        provenance_status="user_confirmed",
        source_timestamp="2025-12-01T10:00:00+00:00",
    )
    state, _ = compute_decision_state(sig, now=NOW)
    assert state == "stale"


def test_merged_without_successor_maps_to_candidate():
    """review_status=merged WITHOUT superseded_by is anomalous → candidate."""
    sig = _decision(review_status="merged")
    state, reason = compute_decision_state(sig, now=NOW)
    assert state == "candidate"
    assert "merged" in reason.lower()


def test_merged_with_successor_maps_to_superseded():
    """review_status=merged WITH superseded_by → superseded (via precedence)."""
    sig = _decision(review_status="merged", superseded_by="sig-2")
    state, reason = compute_decision_state(sig, now=NOW)
    assert state == "superseded"
    assert "sig-2" in reason


def test_all_states_declared_and_emitted():
    """All 8 lifecycle states are in DECISION_STATES; after Sprint 4 all are emitted.

    Sprint 3 (R2.3): temporary and zombie added to EMITTED_STATES.
    Sprint 4 (R3.5): conflicting added to EMITTED_STATES — no more reserved states.
    """
    all_states = {"candidate", "active", "stale", "superseded", "rejected",
                  "temporary", "zombie", "conflicting"}
    assert all_states <= set(DECISION_STATES)
    # All states are now emitted after Sprint 4
    assert "temporary" in EMITTED_STATES
    assert "zombie" in EMITTED_STATES
    assert "conflicting" in EMITTED_STATES
    assert set(EMITTED_STATES) == all_states


# ---------------------------------------------------------------------------
# Age computation
# ---------------------------------------------------------------------------


def test_decision_age_days_from_source_timestamp():
    """decision_age_days parses source_timestamp and computes age."""
    sig = _decision(source_timestamp="2026-06-05T10:00:00+00:00")
    age = decision_age_days(sig, now=NOW)
    assert age == 5


def test_decision_age_days_falls_back_to_created_at():
    """decision_age_days falls back to created_at if source_timestamp is invalid."""
    sig = _decision(
        source_timestamp="not-a-date",
        created_at="2026-06-04T10:00:00+00:00",
    )
    age = decision_age_days(sig, now=NOW)
    assert age == 6


def test_decision_age_days_returns_none_if_both_unparseable():
    """decision_age_days returns None if both timestamps are unparseable."""
    sig = _decision(source_timestamp="not-a-date", created_at="also-not-a-date")
    age = decision_age_days(sig, now=NOW)
    assert age is None


def test_decision_age_days_handles_naive_timestamps():
    """decision_age_days defensively handles timezone-naive timestamps (assumes UTC)."""
    # Simulate a naive timestamp by dropping the +00:00
    sig = _decision(source_timestamp="2026-06-05T10:00:00")
    age = decision_age_days(sig, now=NOW)
    assert age == 5


def test_decision_age_days_future_timestamp_returns_negative():
    """decision_age_days returns negative int for future timestamps."""
    sig = _decision(source_timestamp="2026-07-11T10:00:00+00:00")  # 30 days in future
    age = decision_age_days(sig, now=NOW)
    assert age is not None and age < 0


def test_future_timestamp_not_stale():
    """A decision with future timestamp is not stale."""
    sig = _decision(
        review_status="confirmed",
        provenance_status="user_confirmed",
        source_timestamp="2026-12-11T10:00:00+00:00",  # 6 months in future
    )
    state, reason = compute_decision_state(sig, now=NOW)
    assert (
        state == "active"
    ), f"Future timestamp should be active, got {state}: {reason}"


# ---------------------------------------------------------------------------
# Reason strings
# ---------------------------------------------------------------------------


def test_candidate_reason():
    """Candidate state has explanatory reason."""
    _, reason = compute_decision_state(_decision(), now=NOW)
    assert isinstance(reason, str) and len(reason) > 0


def test_active_reason_includes_confirmed():
    """Active state reason mentions confirmation."""
    sig = _decision(review_status="confirmed", provenance_status="user_confirmed")
    _, reason = compute_decision_state(sig, now=NOW)
    assert "confirmed" in reason.lower()


def test_stale_age_reason_includes_threshold():
    """Stale age reason includes the 90d threshold."""
    sig = _decision(
        review_status="confirmed",
        provenance_status="user_confirmed",
        source_timestamp="2025-12-01T10:00:00+00:00",
    )
    _, reason = compute_decision_state(sig, now=NOW)
    assert "90" in reason


def test_stale_manual_reason():
    """Manual stale marking has explanatory reason."""
    _, reason = compute_decision_state(_decision(review_status="stale"), now=NOW)
    assert "stale" in reason.lower()


def test_superseded_reason_includes_successor():
    """Superseded reason mentions successor signal."""
    sig = _decision(superseded_by="sig-2")
    _, reason = compute_decision_state(sig, now=NOW)
    assert "sig-2" in reason


def test_rejected_reason():
    """Rejected state has explanatory reason."""
    _, reason = compute_decision_state(_decision(review_status="rejected"), now=NOW)
    assert "rejected" in reason.lower()


# ---------------------------------------------------------------------------
# Edge cases and default behavior
# ---------------------------------------------------------------------------


def test_default_now_parameter():
    """compute_decision_state uses current time when now=None."""
    sig = _decision()
    # Just verify it doesn't crash; we can't assert exact state without mocking.
    state, reason = compute_decision_state(sig, now=None)
    assert state in DECISION_STATES
    assert isinstance(reason, str)


def test_custom_stale_age_threshold():
    """compute_decision_state respects custom stale_age_days parameter."""
    sig = _decision(
        review_status="confirmed",
        provenance_status="user_confirmed",
        source_timestamp="2026-06-01T10:00:00+00:00",  # 10 days old
    )
    # With default 90d threshold, should be active
    state, _ = compute_decision_state(sig, now=NOW, stale_age_days=90)
    assert state == "active"

    # With 5d threshold, should be stale
    state, _ = compute_decision_state(sig, now=NOW, stale_age_days=5)
    assert state == "stale"


def test_constants_are_correct_types():
    """Module constants have expected types."""
    assert isinstance(STALE_AGE_DAYS, int) and STALE_AGE_DAYS == 90
    assert isinstance(DECISION_STATES, tuple)
    assert isinstance(EMITTED_STATES, tuple)
    assert all(isinstance(s, str) for s in DECISION_STATES)
    assert all(isinstance(s, str) for s in EMITTED_STATES)
