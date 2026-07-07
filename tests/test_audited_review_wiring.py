"""Tests for T3: audited promotion + explicit provenance (G2 wiring).

Covers:
  - Promoter stamps provenance_status/review_status explicitly on every
    constructed Signal (LLM path → inferred, regex path → observed, both
    review_status=pending).
  - update_signal with a review_action routes through review_with_audit,
    emits exactly one audit row, and persists the governance change.
  - Authority / governance fields (can_use_as_evidence, can_use_as_instruction,
    provenance_status, review_status) are NOT settable directly via
    update_signal — review_action is the only governance entry point.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.observation import Observation
from app.models.signal import Signal
from app.services.signal_audit import SignalAuditStore, review_with_audit
from app.services.signal_promoter import SignalPromoter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_observation(**overrides) -> Observation:
    fields: dict[str, Any] = dict(
        observation_id="ingest-obs1",
        external_id="ingest-bot1",
        observed_at=datetime(2026, 6, 4, 15, 0, tzinfo=UTC),
        title="Strategy call",
        participants=["Alice"],
        entities_mentioned={"person": ["Alice"]},
        content=(
            "## Decisions\n- Adopt the new pipeline\n\n"
            "## Action Items\n- [ ] Alice to draft the plan\n"
        ),
    )
    fields.update(overrides)
    return Observation(**fields)  # type: ignore[arg-type]


def _make_signal(**overrides) -> Signal:
    fields: dict[str, Any] = dict(
        id="sig-test-1",
        type="decision",
        content="Adopt the governance ladder.",
        source_meeting_id="bot-123",
        source_timestamp="2026-06-05T10:00:00+00:00",
    )
    fields.update(overrides)
    return Signal(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T3a: Promoter stamps provenance/review explicitly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regex_promoter_stamps_observed_and_pending():
    """Regex path → provenance_status='observed', review_status='pending'."""
    promoter = SignalPromoter(claude_client=None, knowledge_graph=None)
    obs = _make_observation()
    result = await promoter.promote(obs)
    assert result is not None, "Promoter returned None — no signals extracted"
    for sig in result.signals:
        assert sig.provenance_status == "observed", (
            f"Regex signal {sig.id} has provenance_status={sig.provenance_status!r}, expected 'observed'"
        )
        assert sig.review_status == "pending", (
            f"Regex signal {sig.id} has review_status={sig.review_status!r}, expected 'pending'"
        )


@pytest.mark.asyncio
async def test_llm_promoter_stamps_inferred_and_pending():
    """LLM path → provenance_status='inferred', review_status='pending'."""
    fake_llm_response = [
        {
            "type": "decision",
            "content": "Adopt the new architecture approach for the system.",
            "confidence": 0.9,
            "entities": [],
            "owner": None,
            "status": None,
        }
    ]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="[" + str(fake_llm_response)[1:-1] + "]")]

    import json
    mock_response.content[0].text = json.dumps(fake_llm_response)

    mock_claude = MagicMock()
    mock_claude.generate_message = AsyncMock(return_value=mock_response)

    promoter = SignalPromoter(claude_client=mock_claude, knowledge_graph=None)
    obs = _make_observation()
    result = await promoter.promote(obs)

    # LLM path must have been used (mock was called)
    mock_claude.generate_message.assert_called_once()

    assert result is not None
    for sig in result.signals:
        assert sig.provenance_status == "inferred", (
            f"LLM signal {sig.id} has provenance_status={sig.provenance_status!r}, expected 'inferred'"
        )
        assert sig.review_status == "pending", (
            f"LLM signal {sig.id} has review_status={sig.review_status!r}, expected 'pending'"
        )


# ---------------------------------------------------------------------------
# T3b: update_signal review_action routes through audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_action_emits_one_audit_row(tmp_path):
    """review_action via update_signal emits exactly one audit row."""
    signal = _make_signal()

    # Patch SignalStore to return our signal
    mock_store = MagicMock()
    mock_store.find_signal_by_id.return_value = (signal, MagicMock(bot_id="bot-123"))
    mock_store.save = MagicMock()

    audit_store = SignalAuditStore(
        audit_dir=tmp_path / "signals" / "audit",
        repo_root=tmp_path,
    )

    with (
        patch("app.services.chat_tools.SignalStore", return_value=mock_store),
        patch("app.services.chat_tools.SignalAuditStore", return_value=audit_store),
        patch("app.services.chat_tools.git_ops") as mock_git,
    ):
        mock_git.commit_and_push = AsyncMock()
        mock_git.commit_file = AsyncMock()

        from app.services.chat_tools import update_signal

        result = await update_signal(signal.id, review_action="confirm", actor="alice")

    assert result["success"] is True, f"update_signal failed: {result}"

    history = audit_store.read_for_signal(signal.id)
    assert len(history) == 1, f"Expected 1 audit row, got {len(history)}"
    assert history[0].action == "confirm"
    assert history[0].actor == "alice"
    assert history[0].gate_response == "allow"


@pytest.mark.asyncio
async def test_review_action_persists_governance_change(tmp_path):
    """review_action changes governance fields on the signal (reject → can_use_as_evidence=False)."""
    signal = _make_signal()

    captured_signal: list[Signal] = []

    mock_store = MagicMock()

    def _fake_find(sid):
        return (signal, MagicMock(bot_id="bot-123"))

    def _fake_save_reviewed(sig):
        captured_signal.append(sig)

    mock_store.find_signal_by_id.side_effect = _fake_find
    mock_store.save_reviewed_signal = _fake_save_reviewed
    mock_store.save = MagicMock()

    audit_store = SignalAuditStore(
        audit_dir=tmp_path / "signals" / "audit",
        repo_root=tmp_path,
    )

    with (
        patch("app.services.chat_tools.SignalStore", return_value=mock_store),
        patch("app.services.chat_tools.SignalAuditStore", return_value=audit_store),
        patch("app.services.chat_tools.git_ops") as mock_git,
    ):
        mock_git.commit_and_push = AsyncMock()
        mock_git.commit_file = AsyncMock()

        from app.services.chat_tools import update_signal

        result = await update_signal(signal.id, review_action="reject", actor="reviewer")

    assert result["success"] is True
    assert result["review_applied"] is True
    # Governance state reflected in the returned dict
    assert result["signal"]["review_status"] == "rejected"
    assert result["signal"]["can_use_as_evidence"] is False


@pytest.mark.asyncio
async def test_review_action_invalid_action_returns_error():
    """Unknown review_action returns {"success": False, "error": ...}."""
    signal = _make_signal()

    mock_store = MagicMock()
    mock_store.find_signal_by_id.return_value = (signal, MagicMock(bot_id="bot-123"))

    with patch("app.services.chat_tools.SignalStore", return_value=mock_store):
        from app.services.chat_tools import update_signal

        result = await update_signal(signal.id, review_action="frobnicate")

    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_plain_field_update_does_not_create_audit_row(tmp_path):
    """Plain field updates (status/content/owner_id/due_date) do NOT emit audit rows."""
    # This verifies backward-compatibility: the review boundary is only crossed
    # when review_action is provided.
    updated_signal = _make_signal(status="done")

    mock_store = MagicMock()
    mock_store.update_signal.return_value = updated_signal
    mock_store.find_signal_by_id.return_value = (updated_signal, MagicMock(bot_id="bot-123"))
    mock_store.relative_path = MagicMock(return_value="signals/meeting-bot-123.json")

    audit_store = SignalAuditStore(
        audit_dir=tmp_path / "signals" / "audit",
        repo_root=tmp_path,
    )

    with (
        patch("app.services.chat_tools.SignalStore", return_value=mock_store),
        patch("app.services.chat_tools.SignalAuditStore", return_value=audit_store),
        patch("app.services.chat_tools.git_ops") as mock_git,
        patch("app.services.chat_tools.get_knowledge_graph", side_effect=Exception("no graph")),
    ):
        mock_git.commit_and_push = AsyncMock()
        mock_git.commit_file = AsyncMock()

        from app.services.chat_tools import update_signal

        result = await update_signal(signal_id=updated_signal.id, status="done")

    assert result["success"] is True
    # No audit rows for plain field updates
    history = audit_store.read_for_signal(updated_signal.id)
    assert len(history) == 0, f"Expected 0 audit rows for plain update, got {len(history)}"


# ---------------------------------------------------------------------------
# T3c: Governance fields NOT directly settable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_tool_def_does_not_expose_governance_fields():
    """The update_signal MCP tool def must NOT have governance fields as settable params."""
    from app.services.mcp_tool_definitions import TOOL_DEFS

    schema = TOOL_DEFS["update_signal"]["inputSchema"]
    props = schema.get("properties", {})

    # These must NOT be directly settable
    forbidden = {"can_use_as_evidence", "can_use_as_instruction", "provenance_status", "review_status"}
    exposed_forbidden = forbidden & set(props.keys())
    assert not exposed_forbidden, (
        f"Governance fields exposed as direct params (ADR-002 violation): {exposed_forbidden}"
    )

    # review_action MUST be present (the only governance entry point)
    assert "review_action" in props, (
        "review_action must be in update_signal inputSchema — it's the governance entry point"
    )
