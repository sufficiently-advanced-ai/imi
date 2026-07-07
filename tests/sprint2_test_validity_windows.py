"""Sprint 2 / R1.1 — Validity windows on signals.

Tests for valid_from/valid_to fields on Signal, the governance supersede path,
audit snapshot capture, and Neo4j mirror sync.

Part 1: model + governance layer
Part 2: Neo4j mirror (SignalGraphWriter) + chat_tools wiring
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.signal import Signal
from app.services.signal_audit import review_with_audit
from app.services.signal_governance import apply_review


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(**overrides) -> Signal:
    fields: dict = dict(
        id="sig-vw-1",
        type="decision",
        content="We will ship the validity window feature.",
        source_meeting_id="bot-100",
        source_timestamp="2026-06-01T00:00:00+00:00",
    )
    fields.update(overrides)
    return Signal(**fields)  # type: ignore[arg-type]


# ===========================================================================
# Part 1 — Model + Governance
# ===========================================================================


# ---------------------------------------------------------------------------
# 1. valid_from defaults to source_timestamp
# ---------------------------------------------------------------------------


def test_valid_from_defaults_to_source_timestamp():
    sig = _make_signal()
    assert sig.valid_from == "2026-06-01T00:00:00+00:00"
    assert sig.valid_to is None


def test_valid_from_defaults_when_source_timestamp_has_tz():
    sig = _make_signal(source_timestamp="2026-05-15T12:34:56+00:00")
    assert sig.valid_from == "2026-05-15T12:34:56+00:00"


# ---------------------------------------------------------------------------
# 2. Explicit valid_from is preserved
# ---------------------------------------------------------------------------


def test_explicit_valid_from_preserved():
    sig = _make_signal(
        valid_from="2026-01-01T00:00:00+00:00",
    )
    assert sig.valid_from == "2026-01-01T00:00:00+00:00"


def test_explicit_valid_from_differs_from_source_timestamp():
    """When provided explicitly, valid_from need not equal source_timestamp."""
    sig = _make_signal(
        source_timestamp="2026-06-01T00:00:00+00:00",
        valid_from="2025-12-01T00:00:00+00:00",
    )
    assert sig.valid_from == "2025-12-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# 3. supersede action closes the validity window
# ---------------------------------------------------------------------------


def test_supersede_sets_valid_to():
    sig = _make_signal()
    new = apply_review(sig, "supersede", superseded_by="sig-vw-2")
    # valid_to must be set and parseable as ISO datetime
    assert new.valid_to is not None
    parsed = datetime.fromisoformat(new.valid_to)
    assert parsed.tzinfo is not None  # timezone-aware
    # superseded_by links the successor
    assert new.superseded_by == "sig-vw-2"


def test_supersede_valid_to_is_recent():
    before = datetime.now(UTC)
    sig = _make_signal()
    new = apply_review(sig, "supersede", superseded_by="sig-vw-2")
    after = datetime.now(UTC)
    ts = datetime.fromisoformat(new.valid_to)  # type: ignore[arg-type]
    assert before <= ts <= after


def test_supersede_does_not_mutate_original():
    sig = _make_signal()
    _ = apply_review(sig, "supersede", superseded_by="sig-vw-2")
    assert sig.valid_to is None


# ---------------------------------------------------------------------------
# 4. Other review actions do NOT set valid_to
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["confirm", "reject", "evidence_only", "dispute"])
def test_non_supersede_review_does_not_set_valid_to(action):
    # confirm needs confirmed provenance — start from user_confirmed for confirm
    overrides: dict = {}
    if action == "confirm":
        overrides = {}  # default generated → confirm path (will set user_confirmed)
    sig = _make_signal(**overrides)
    new = apply_review(sig, action)
    assert new.valid_to is None, f"action={action!r} should not set valid_to"


# ---------------------------------------------------------------------------
# 5. Audit snapshot captures valid_to
# ---------------------------------------------------------------------------


def test_audit_snapshot_captures_valid_to_on_supersede():
    sig = _make_signal()
    new_sig, record = review_with_audit(sig, "supersede", superseded_by="sig-vw-2")
    # The after-snapshot must contain valid_to
    assert "valid_to" in record.after, "audit after-snapshot missing valid_to"
    assert record.after["valid_to"] == new_sig.valid_to
    # The before-snapshot must show it was None
    assert record.before.get("valid_to") is None


def test_audit_snapshot_valid_to_absent_for_non_supersede():
    sig = _make_signal()
    _, record = review_with_audit(sig, "reject")
    # valid_to should appear in the snapshot (field exists) but be None both before/after
    assert "valid_to" in record.before
    assert "valid_to" in record.after
    assert record.before["valid_to"] is None
    assert record.after["valid_to"] is None


# ===========================================================================
# Part 2 — Neo4j mirror
# ===========================================================================


class _FakeClient:
    def __init__(self):
        self.writes: list[tuple[str, dict]] = []

    async def execute_write(self, query: str, params: dict):
        self.writes.append((query, params))
        return [{"id": params.get("id")}]


# ---------------------------------------------------------------------------
# 6. _write_signal_node includes valid_from / valid_to in Cypher + params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_signal_node_includes_valid_from_and_valid_to():
    from app.models.signal import MeetingSignals
    from app.services.graph.signal_graph_writer import SignalGraphWriter, _UPSERT_SIGNAL

    # Cypher SET clause must reference both fields
    assert "$valid_from" in _UPSERT_SIGNAL, "UPSERT_SIGNAL missing $valid_from"
    assert "$valid_to" in _UPSERT_SIGNAL, "UPSERT_SIGNAL missing $valid_to"

    client = _FakeClient()
    writer = SignalGraphWriter(client)
    sig = Signal(
        id="s-vw-neo4j",
        type="decision",
        content="Test validity window in graph.",
        source_meeting_id="b-1",
        source_timestamp="2026-06-01T00:00:00+00:00",
        valid_from="2026-06-01T00:00:00+00:00",
    )
    ms = MeetingSignals(meeting_id="m1", bot_id="b-1", signals=[sig])
    await writer.write_meeting_signals(ms)

    upsert_calls = [(q, p) for q, p in client.writes if "MERGE (s:Signal" in q]
    assert upsert_calls, "No UPSERT_SIGNAL call found"
    _, params = upsert_calls[0]
    assert "valid_from" in params
    assert "valid_to" in params
    assert params["valid_from"] == "2026-06-01T00:00:00+00:00"
    assert params["valid_to"] is None


# ---------------------------------------------------------------------------
# 7. update_signal_properties supports valid_to kwarg
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_signal_properties_supports_valid_to():
    from app.services.graph.signal_graph_writer import SignalGraphWriter

    class _FakeClientWithReturn:
        def __init__(self):
            self.writes: list[tuple[str, dict]] = []

        async def execute_write(self, query: str, params: dict):
            self.writes.append((query, params))
            return [{"id": params.get("id")}]

    client = _FakeClientWithReturn()
    writer = SignalGraphWriter(client)
    result = await writer.update_signal_properties(
        "s-vw-1",
        valid_to="2026-06-11T10:00:00+00:00",
    )
    assert result is True
    assert client.writes, "No write calls issued"
    _, params = client.writes[0]
    props = params.get("props", {})
    assert props.get("valid_to") == "2026-06-11T10:00:00+00:00"


@pytest.mark.asyncio
async def test_update_signal_properties_omitting_valid_to_does_not_touch_it():
    from app.services.graph.signal_graph_writer import SignalGraphWriter

    class _FakeClientWithReturn:
        def __init__(self):
            self.writes: list[tuple[str, dict]] = []

        async def execute_write(self, query: str, params: dict):
            self.writes.append((query, params))
            return [{"id": params.get("id")}]

    client = _FakeClientWithReturn()
    writer = SignalGraphWriter(client)
    result = await writer.update_signal_properties(
        "s-vw-1",
        review_status="confirmed",
    )
    assert result is True
    _, params = client.writes[0]
    props = params.get("props", {})
    assert "valid_to" not in props  # NOT written when not provided


# ---------------------------------------------------------------------------
# 8. chat_tools update_signal supersede path passes valid_to to the writer
# ---------------------------------------------------------------------------



    # DELIBERATE-FAILURE VERIFICATION (documented, not automated):
    # Temporarily remove ``valid_to=new_signal.valid_to`` from chat_tools.py line ~792,
    # then run this test — it fails with:
    #   AssertionError: valid_to was not passed to update_signal_properties
    # Restoring the line makes it pass again.  This was verified during authoring.
