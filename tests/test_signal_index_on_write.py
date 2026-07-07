"""Tests for signal index-on-write + backfill (Task 2 — G3 wiring).

Covers:
  - SignalStore.save() still succeeds when indexing raises (monkeypatched).
  - SignalStore.save() calls index_meeting_signals with the saved container.
  - SignalStore.update_signal() triggers re-index of the updated signal.
  - signal_indexing.vector_stack_available() returns False when facade unavailable.
  - signal_indexing.index_one() returns None gracefully when stack unavailable.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from app.models.signal import MeetingSignals, Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_meeting_signals(bot_id: str = "bot-123") -> MeetingSignals:
    sig = Signal(
        id="sig-abc",
        type="decision",
        content="We will adopt the governance ladder",
        source_meeting_id=bot_id,
        source_timestamp="2026-06-05T10:00:00+00:00",
    )
    return MeetingSignals(
        bot_id=bot_id,
        meeting_id="meeting-1",
        signals=[sig],
        signal_count=1,
        extracted_at="2026-06-05T10:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# SignalStore.save() — best-effort indexing
# ---------------------------------------------------------------------------


def test_save_succeeds_when_indexing_raises(tmp_path):
    """save() must NOT raise even when index_meeting_signals throws."""
    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path)
    ms = _make_meeting_signals()

    with patch(
        "app.services.signal_indexing.index_meeting_signals",
        side_effect=RuntimeError("vector store exploded"),
    ):
        # Should not raise
        result = store.save(ms)

    assert result == tmp_path / "meeting-bot-123.json"
    assert result.exists()


def test_save_calls_index_meeting_signals(tmp_path):
    """save() should call index_meeting_signals with the saved container."""
    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path)
    ms = _make_meeting_signals()

    captured = []

    def fake_index(meeting_signals):
        captured.append(meeting_signals)

    with patch("app.services.signal_indexing.index_meeting_signals", side_effect=fake_index):
        store.save(ms)

    assert len(captured) == 1
    assert captured[0].bot_id == "bot-123"


def test_save_indexing_import_error_is_silent(tmp_path, monkeypatch):
    """save() must succeed even if signal_indexing module can't be imported."""
    import builtins
    import sys

    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path)
    ms = _make_meeting_signals()

    # Force the lazy `from app.services.signal_indexing import ...` inside
    # save() to raise ImportError, as if the module were absent.
    monkeypatch.delitem(sys.modules, "app.services.signal_indexing", raising=False)
    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "app.services.signal_indexing":
            raise ImportError("no module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)

    result = store.save(ms)
    assert result.exists()


# ---------------------------------------------------------------------------
# SignalStore.update_signal() — re-index on update
# ---------------------------------------------------------------------------


def test_update_signal_reindexes_exactly_once_via_save(tmp_path):
    """update_signal() persists via save(), whose container indexing covers the
    updated signal — there must be NO separate index_one call (FAISS has no
    upsert; a second call would append a duplicate vector on every update)."""
    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path)
    ms = _make_meeting_signals()
    store.save(ms)  # save without patching index first — clear state

    batches = []
    singles = []

    with patch(
        "app.services.signal_indexing.index_meeting_signals",
        side_effect=lambda container: batches.append(container),
    ), patch(
        "app.services.signal_indexing.index_one",
        side_effect=lambda signal: singles.append(signal),
    ):
        updated = store.update_signal("sig-abc", status="done")

    assert updated is not None
    assert updated.status == "done"
    assert len(batches) == 1  # one container indexing via save()
    assert singles == []  # no duplicate per-signal indexing
    assert any(s.id == "sig-abc" and s.status == "done" for s in batches[0].signals)


def test_update_signal_succeeds_when_reindex_raises(tmp_path):
    """update_signal() must NOT fail when indexing throws inside save()."""
    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path)
    ms = _make_meeting_signals()
    store.save(ms)

    with patch(
        "app.services.signal_indexing.index_meeting_signals",
        side_effect=RuntimeError("index exploded"),
    ):
        updated = store.update_signal("sig-abc", content="new content")

    assert updated is not None
    assert updated.content == "new content"


# ---------------------------------------------------------------------------
# signal_indexing module helpers
# ---------------------------------------------------------------------------


def test_vector_stack_available_false_when_facade_unavailable():
    from app.services.signal_indexing import vector_stack_available

    with patch(
        "app.services.signal_indexing._get_semantica",
        return_value=None,
    ):
        assert vector_stack_available() is False


def test_vector_stack_available_false_when_facade_raises():
    from app.services.signal_indexing import vector_stack_available

    with patch(
        "app.services.signal_indexing._get_semantica",
        side_effect=RuntimeError("no semantica"),
    ):
        assert vector_stack_available() is False


def test_index_one_returns_none_when_stack_unavailable():
    from app.services.signal_indexing import index_one
    from app.models.signal import Signal

    sig = Signal(
        id="sig-test",
        type="key_point",
        content="Test signal",
        source_meeting_id="bot-456",
        source_timestamp="2026-06-05T10:00:00+00:00",
    )

    with patch("app.services.signal_indexing._get_semantica", return_value=None):
        result = index_one(sig)

    assert result is None


def test_index_meeting_signals_returns_counts(monkeypatch):
    """index_meeting_signals returns (total, indexed, skipped) tuple."""
    from app.config import settings
    from app.services.signal_indexing import index_meeting_signals

    # Pin the legacy passthrough: the fake facade's store is only used on the
    # (non-default) faiss backend.
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "faiss", raising=False)
    ms = _make_meeting_signals()

    fake_sk = MagicMock()
    fake_sk.vector_store = MagicMock()
    fake_sk.embedder = MagicMock()

    with patch("app.services.signal_indexing._get_semantica", return_value=fake_sk):
        with patch(
            "app.services.signal_retrieval.index_signal",
            return_value="vec-1",
        ):
            total, indexed, skipped = index_meeting_signals(ms)

    assert total == 1
    assert indexed == 1
    assert skipped == 0
