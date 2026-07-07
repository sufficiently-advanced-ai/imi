"""Tests for temporary/zombie decision states via metadata.revisit_date (Sprint 3, R2.3).

Tests the pure state machine extension: when a signal carries metadata.revisit_date,
it surfaces as "temporary" (future date) or "zombie" (past date), at higher precedence
than stale but lower than superseded/rejected.

State precedence (highest → lowest):
  superseded > rejected > zombie > temporary > stale > active > candidate

See:
  - docs/superpowers/plans/2026-06-10-p5-control-plane.md (R2.3)
  - app/services/decision_states.py
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.signal import Signal
from app.services.decision_states import (
    EMITTED_STATES,
    compute_decision_state,
)

NOW = datetime(2026, 6, 11, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _with_revisit(date_str: str, **overrides) -> Signal:
    """Decision with metadata.revisit_date set."""
    meta = overrides.pop("metadata", {})
    meta["revisit_date"] = date_str
    return _decision(metadata=meta, **overrides)


# ---------------------------------------------------------------------------
# Core temporary / zombie tests
# ---------------------------------------------------------------------------


def test_future_revisit_returns_temporary():
    """Signal with revisit_date in the future → state='temporary', reason contains the date."""
    sig = _with_revisit("2026-09-15")
    state, reason = compute_decision_state(sig, now=NOW)
    assert state == "temporary", f"Expected temporary, got {state}: {reason}"
    assert "2026-09-15" in reason


def test_past_revisit_returns_zombie():
    """Signal with revisit_date in the past → state='zombie', reason mentions 'passed'."""
    sig = _with_revisit("2026-01-01")
    state, reason = compute_decision_state(sig, now=NOW)
    assert state == "zombie", f"Expected zombie, got {state}: {reason}"
    assert "passed" in reason.lower()


def test_revisit_exactly_now_is_temporary():
    """Boundary: revisit_date == now is NOT past yet (strict <) → temporary."""
    sig = _with_revisit(NOW.isoformat())
    state, _ = compute_decision_state(sig, now=NOW)
    assert state == "temporary"


def test_date_only_string_accepted():
    """'YYYY-MM-DD' format (no time component) is parsed correctly."""
    # Future date-only
    sig_future = _with_revisit("2026-09-01")
    state, _ = compute_decision_state(sig_future, now=NOW)
    assert state == "temporary"

    # Past date-only
    sig_past = _with_revisit("2025-12-31")
    state, _ = compute_decision_state(sig_past, now=NOW)
    assert state == "zombie"


def test_unparseable_revisit_ignored():
    """Unparseable revisit_date falls through to existing state ladder without error."""
    sig = _with_revisit("not-a-date")
    # Should not raise; falls through to candidate (no other special flags)
    state, _ = compute_decision_state(sig, now=NOW)
    assert state == "candidate"


# ---------------------------------------------------------------------------
# Precedence tests
# ---------------------------------------------------------------------------


def test_superseded_beats_zombie():
    """superseded=True + past revisit_date → superseded wins (higher precedence)."""
    sig = _with_revisit("2026-01-01", superseded_by="sig-2")
    state, _ = compute_decision_state(sig, now=NOW)
    assert state == "superseded"


def test_rejected_beats_temporary():
    """rejected=True + future revisit_date → rejected wins (higher precedence)."""
    sig = _with_revisit("2026-09-15", review_status="rejected")
    state, _ = compute_decision_state(sig, now=NOW)
    assert state == "rejected"


def test_zombie_beats_stale():
    """Old signal (>90d) + past revisit_date → zombie, not stale."""
    sig = _with_revisit(
        "2026-01-01",
        source_timestamp="2025-12-01T10:00:00+00:00",  # ~192 days old
    )
    state, _ = compute_decision_state(sig, now=NOW)
    assert state == "zombie", f"Expected zombie to beat stale, got {state}"


def test_temporary_beats_stale():
    """Old signal (>90d) + future revisit_date → temporary, not stale."""
    sig = _with_revisit(
        "2026-09-15",
        source_timestamp="2025-12-01T10:00:00+00:00",  # ~192 days old
    )
    state, _ = compute_decision_state(sig, now=NOW)
    assert state == "temporary", f"Expected temporary to beat stale, got {state}"


# ---------------------------------------------------------------------------
# EMITTED_STATES
# ---------------------------------------------------------------------------


def test_emitted_states_includes_temporary_zombie():
    """EMITTED_STATES must include both 'temporary' and 'zombie' after this sprint."""
    assert "temporary" in EMITTED_STATES, "'temporary' missing from EMITTED_STATES"
    assert "zombie" in EMITTED_STATES, "'zombie' missing from EMITTED_STATES"


def test_conflicting_now_emitted():
    """'conflicting' is now emitted (Sprint 4, R3.5) and must appear in EMITTED_STATES."""
    assert "conflicting" in EMITTED_STATES, "'conflicting' must be in EMITTED_STATES after Sprint 4"


# ---------------------------------------------------------------------------
# update_signal plain-field path tests
# ---------------------------------------------------------------------------


def _make_signal(**overrides) -> Signal:
    fields: dict[str, object] = dict(
        id="sig-abc",
        type="decision",
        content="Decision content.",
        source_meeting_id="bot-456",
        source_timestamp="2026-06-05T10:00:00+00:00",
    )
    fields.update(overrides)
    return Signal(**fields)  # type: ignore[arg-type]










# ---------------------------------------------------------------------------
# MCP tool definition test
# ---------------------------------------------------------------------------


def test_mcp_def_includes_revisit_date():
    """update_signal MCP tool definition must include revisit_date property."""
    from app.services.mcp_tool_definitions import TOOL_DEFS

    schema = TOOL_DEFS["update_signal"]["inputSchema"]
    props = schema.get("properties", {})
    assert "revisit_date" in props, (
        f"'revisit_date' missing from update_signal inputSchema. "
        f"Found: {list(props.keys())}"
    )
    # Should have a string type and description
    rd_prop = props["revisit_date"]
    assert rd_prop.get("type") == "string"
    assert "description" in rd_prop
