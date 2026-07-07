"""Tests for T4: Git persistence of audit + capture files (G2/G4 wiring).

Covers:
  - After a review_action, git_ops.commit_file is awaited with the audit JSONL
    relative path and a message of the form "audit: {action} signal {signal_id}".
  - Git commit failure after a successful audit append does NOT fail the review
    (the primary persistence guarantee is the JSONL append; git is best-effort).
  - CaptureStore.capture writes a file at the expected relative path. There is
    currently no production caller of CaptureStore — the git-commit helper is
    present but unexercised in the live pipeline (noted in test docstrings).
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.models.signal import Signal
from app.services.signal_audit import SignalAuditStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(**overrides) -> Signal:
    fields = dict(
        id="sig-git-test-1",
        type="decision",
        content="Adopt the new platform.",
        source_meeting_id="bot-456",
        source_timestamp="2026-06-05T12:00:00+00:00",
    )
    fields.update(overrides)
    return Signal(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T4a: git_ops.commit_file called with audit JSONL path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_commit_file_called_with_audit_path(tmp_path):
    """After a successful review, commit_file is awaited with the audit JSONL path."""
    signal = _make_signal()

    mock_store = MagicMock()
    mock_store.find_signal_by_id.return_value = (signal, MagicMock(bot_id="bot-456"))
    mock_store.save = MagicMock()
    mock_store.replace_signal = MagicMock()

    audit_store = SignalAuditStore(
        audit_dir=tmp_path / "signals" / "audit",
        repo_root=tmp_path,
    )

    mock_git = MagicMock()
    mock_git.commit_file = AsyncMock()

    with (
        patch("app.services.chat_tools.SignalStore", return_value=mock_store),
        patch("app.services.chat_tools.SignalAuditStore", return_value=audit_store),
        patch("app.services.chat_tools.git_ops", mock_git),
    ):
        from app.services.chat_tools import update_signal
        result = await update_signal(signal.id, review_action="confirm", actor="alice")

    assert result["success"] is True
    assert result.get("review_applied") is True

    # commit_file must have been awaited at least once for the audit JSONL
    assert mock_git.commit_file.called, "git_ops.commit_file was never called"

    # Find the audit commit call
    audit_calls = [
        c for c in mock_git.commit_file.call_args_list
        if "audit" in (c.args[2] if c.args else c.kwargs.get("commit_message", ""))
    ]
    assert audit_calls, (
        f"No commit_file call with 'audit' in the message. Calls: {mock_git.commit_file.call_args_list}"
    )

    audit_call = audit_calls[0]
    commit_path = audit_call.args[0] if audit_call.args else audit_call.kwargs["file_path"]
    commit_msg = audit_call.args[2] if audit_call.args else audit_call.kwargs["commit_message"]

    # Path must be the audit JSONL repo-relative path
    expected_path = f"signals/audit/{signal.id}.jsonl"
    assert commit_path == expected_path, (
        f"commit_file path {commit_path!r} != expected {expected_path!r}"
    )
    # Message format: "audit: {action} signal {signal_id}"
    assert f"audit: confirm signal {signal.id}" == commit_msg, (
        f"commit message {commit_msg!r} != expected format"
    )


# ---------------------------------------------------------------------------
# T4b: Git commit failure does NOT fail the review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_commit_failure_does_not_fail_review(tmp_path):
    """When git_ops.commit_file raises, the review still succeeds."""
    signal = _make_signal()

    mock_store = MagicMock()
    mock_store.find_signal_by_id.return_value = (signal, MagicMock(bot_id="bot-456"))
    mock_store.save = MagicMock()
    mock_store.replace_signal = MagicMock()

    audit_store = SignalAuditStore(
        audit_dir=tmp_path / "signals" / "audit",
        repo_root=tmp_path,
    )

    mock_git = MagicMock()
    mock_git.commit_file = AsyncMock(side_effect=RuntimeError("git remote not available"))

    with (
        patch("app.services.chat_tools.SignalStore", return_value=mock_store),
        patch("app.services.chat_tools.SignalAuditStore", return_value=audit_store),
        patch("app.services.chat_tools.git_ops", mock_git),
    ):
        from app.services.chat_tools import update_signal
        result = await update_signal(signal.id, review_action="reject", actor="reviewer")

    # Review must succeed despite git failure
    assert result["success"] is True
    assert result.get("review_applied") is True

    # Audit row must have been appended (the guarantee survives git failure)
    history = audit_store.read_for_signal(signal.id)
    assert len(history) == 1, f"Expected 1 audit row, got {len(history)}"
    assert history[0].action == "reject"


# ---------------------------------------------------------------------------
# T4c: Audit append failure → error surfaced, not silently swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_append_failure_surfaced_in_result(tmp_path, caplog):
    """When SignalAuditStore.append raises, the error is surfaced in the result."""
    signal = _make_signal()

    mock_store = MagicMock()
    mock_store.find_signal_by_id.return_value = (signal, MagicMock(bot_id="bot-456"))
    mock_store.save = MagicMock()
    mock_store.replace_signal = MagicMock()

    bad_audit_store = MagicMock()
    bad_audit_store.append = MagicMock(side_effect=OSError("disk full"))
    bad_audit_store.relative_path = MagicMock(return_value=f"signals/audit/{signal.id}.jsonl")

    mock_git = MagicMock()
    mock_git.commit_file = AsyncMock()

    import logging
    with (
        patch("app.services.chat_tools.SignalStore", return_value=mock_store),
        patch("app.services.chat_tools.SignalAuditStore", return_value=bad_audit_store),
        patch("app.services.chat_tools.git_ops", mock_git),
        caplog.at_level(logging.ERROR, logger="app.services.chat_tools"),
    ):
        from app.services.chat_tools import update_signal
        result = await update_signal(signal.id, review_action="confirm")

    # Result still has success=True (signal was saved) but reports audit_error
    assert result["success"] is True
    assert "audit_error" in result, "audit_error must be in response when append fails"
    assert "disk full" in result["audit_error"]
    # Must log at ERROR level (not just warning)
    assert any("AUDIT APPEND FAILED" in r.message for r in caplog.records), (
        "Expected ERROR log for audit append failure"
    )


# ---------------------------------------------------------------------------
# T4d: CaptureStore writes file at expected relative path
# (No production caller exists yet — noted in docstring)
# ---------------------------------------------------------------------------


def test_capture_store_relative_path(tmp_path):
    """CaptureStore.capture writes the file at memory/captures/{id}.json.

    CaptureStore has no production caller in the live pipeline yet (the G4
    capture path is implemented but not wired to any ingest route). This test
    documents the expected file layout so the git-commit helper added alongside
    this task can be validated against a real path.
    """
    from app.services.memory_capture import CaptureStore

    store = CaptureStore(
        capture_dir=tmp_path / "memory" / "captures",
        repo_root=tmp_path,
    )
    result = store.capture(
        content="Meeting notes from the strategy session.",
        source="manual",
        source_id="test-cap-001",
    )
    assert not result.deduped
    memory = result.memory

    # File must exist at the expected path
    expected_file = tmp_path / "memory" / "captures" / f"{memory.id}.json"
    assert expected_file.is_file(), f"Expected file at {expected_file}"

    # relative_path must match
    assert store.relative_path(memory.id) == f"memory/captures/{memory.id}.json"
