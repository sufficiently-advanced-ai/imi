"""Governance durability: audit-first ordering, atomic persistence, container locks.

Invariant under test (G2, ADR-002): no persisted governance change can exist
without a preceding audit row.

  (a) audit append fails      -> signal file on disk unchanged, success False
  (b) signal persist fails    -> compensating audit row appended, success False
  (c) atomic_write_text fails -> no partial file, original content intact
  (d) happy-path confirm      -> audit row + persisted instruction-grade signal
"""

import json
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.signal import MeetingSignals, Signal
from app.services.signal_audit import SignalAuditStore
from app.services.signal_store import SignalStore
from app.utils.atomic_write import atomic_write_text

BOT_ID = "bot-durable"
SIGNAL_ID = "sig-durable-1"


def _make_signal(**overrides) -> Signal:
    fields = dict(
        id=SIGNAL_ID,
        type="decision",
        content="Adopt audit-first governance writes.",
        source_meeting_id=BOT_ID,
        source_timestamp="2026-06-05T12:00:00+00:00",
    )
    fields.update(overrides)
    return Signal(**fields)  # type: ignore[arg-type]


@pytest.fixture
def env(tmp_path):
    """Real SignalStore + SignalAuditStore rooted in tmp_path, I/O side effects stubbed."""
    store = SignalStore(signals_dir=tmp_path / "signals")
    audit_store = SignalAuditStore(audit_dir=tmp_path / "signals" / "audit", repo_root=tmp_path)
    with patch("app.services.signal_indexing.index_meeting_signals"):
        store.save(
            MeetingSignals(meeting_id=BOT_ID, bot_id=BOT_ID, signals=[_make_signal()])
        )
    signal_file = store._file_path(BOT_ID)

    mock_git = MagicMock()
    mock_git.commit_file = AsyncMock()

    with (
        patch("app.services.chat_tools.SignalStore", return_value=store),
        patch("app.services.chat_tools.SignalAuditStore", return_value=audit_store),
        patch("app.services.chat_tools.git_ops", mock_git),
        patch("app.neo4j_client.get_neo4j_client", return_value=None),
        patch("app.services.signal_indexing.index_meeting_signals"),
    ):
        yield store, audit_store, signal_file


def _persisted(signal_file: Path) -> dict:
    data = json.loads(signal_file.read_text(encoding="utf-8"))
    return next(s for s in data["signals"] if s["id"] == SIGNAL_ID)


# ---------------------------------------------------------------------------
# (a) audit append fails -> nothing persisted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_failure_persists_nothing(env):
    store, audit_store, signal_file = env
    before_bytes = signal_file.read_bytes()

    from app.services.chat_tools import update_signal

    with patch.object(audit_store, "append", side_effect=OSError("disk full")):
        result = await update_signal(SIGNAL_ID, review_action="confirm", actor="reviewer")

    assert result["success"] is False
    assert result["review_applied"] is False
    assert "disk full" in result["error"]
    assert signal_file.read_bytes() == before_bytes, "signal file must be untouched"
    assert _persisted(signal_file)["can_use_as_instruction"] is False
    assert audit_store.read_for_signal(SIGNAL_ID) == []


# ---------------------------------------------------------------------------
# (b) persist fails after audit row -> compensating row, success False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_failure_appends_compensating_row(env):
    store, audit_store, signal_file = env
    before_bytes = signal_file.read_bytes()

    from app.services.chat_tools import update_signal

    with patch(
        "app.services.signal_store.atomic_write_text",
        side_effect=OSError("read-only filesystem"),
    ):
        result = await update_signal(SIGNAL_ID, review_action="confirm", actor="reviewer")

    assert result["success"] is False
    assert result["review_applied"] is False
    assert "read-only filesystem" in result["error"]
    assert signal_file.read_bytes() == before_bytes

    rows = audit_store.read_for_signal(SIGNAL_ID)
    assert [r.action for r in rows] == ["confirm", "confirm_failed"]
    attempt, compensation = rows
    assert result["audit_row_id"] == attempt.id
    assert result["compensating_audit_row_id"] == compensation.id
    # The compensating row records "no change": after == before
    assert compensation.after == attempt.before
    assert compensation.after["can_use_as_instruction"] is False
    assert compensation.gate_response is None
    assert compensation.actor == "reviewer"
    assert attempt.id in compensation.reasoning


# ---------------------------------------------------------------------------
# (c) atomic_write_text leaves no partial file
# ---------------------------------------------------------------------------


def test_atomic_write_failure_leaves_original_and_no_temp(tmp_path):
    target = tmp_path / "data" / "file.json"
    atomic_write_text(target, '{"v": 1}')

    class Boom(Exception):
        pass

    # Fail after the temp file has been written but before the rename.
    with patch("app.utils.atomic_write.os.fsync", side_effect=Boom("crash mid-write")):
        with pytest.raises(Boom):
            atomic_write_text(target, '{"v": 2, "partial": "' + "y" * 10_000)

    assert target.read_text(encoding="utf-8") == '{"v": 1}'
    assert sorted(p.name for p in target.parent.iterdir()) == ["file.json"], "temp file leaked"


def test_atomic_write_creates_and_replaces(tmp_path):
    target = tmp_path / "nested" / "out.md"
    atomic_write_text(target, "one")
    target.chmod(0o640)
    atomic_write_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert target.stat().st_mode & 0o777 == 0o640, "existing permissions preserved"
    assert [p.name for p in target.parent.iterdir()] == ["out.md"]


# ---------------------------------------------------------------------------
# (d) happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_writes_audit_row_and_persists_signal(env):
    store, audit_store, signal_file = env

    from app.services.chat_tools import update_signal

    result = await update_signal(SIGNAL_ID, review_action="confirm", actor="reviewer")

    assert result["success"] is True
    assert result["review_applied"] is True

    rows = audit_store.read_for_signal(SIGNAL_ID)
    assert len(rows) == 1
    assert rows[0].action == "confirm"
    assert rows[0].id == result["audit_row_id"]

    persisted = _persisted(signal_file)
    assert persisted["can_use_as_instruction"] is True
    assert persisted["provenance_status"] == "user_confirmed"


@pytest.mark.asyncio
async def test_audit_row_is_written_before_signal(env):
    """Ordering: at the moment the signal is persisted, the audit row exists."""
    store, audit_store, signal_file = env
    seen_rows_at_persist: list[int] = []
    real_replace = store.replace_signal

    def spying_replace(new_signal, container):
        seen_rows_at_persist.append(len(audit_store.read_for_signal(SIGNAL_ID)))
        return real_replace(new_signal, container)

    from app.services.chat_tools import update_signal

    with patch.object(store, "replace_signal", side_effect=spying_replace):
        result = await update_signal(SIGNAL_ID, review_action="confirm", actor="reviewer")

    assert result["success"] is True
    assert seen_rows_at_persist == [1]


# ---------------------------------------------------------------------------
# Container lock: concurrent field updates to one file are not lost
# ---------------------------------------------------------------------------


def test_concurrent_updates_to_same_container_are_serialized(tmp_path):
    store = SignalStore(signals_dir=tmp_path / "signals")
    signals = [_make_signal(id=f"sig-{i}", content=f"signal {i}") for i in range(8)]
    with patch("app.services.signal_indexing.index_meeting_signals"):
        store.save(MeetingSignals(meeting_id=BOT_ID, bot_id=BOT_ID, signals=signals))

        barrier = threading.Barrier(len(signals))
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            try:
                barrier.wait()
                store.update_signal(f"sig-{i}", status="done")
            except BaseException as e:  # pragma: no cover - surfaced below
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(signals))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert errors == []
    final = store.load(BOT_ID)
    assert final is not None
    assert {s.id: s.status for s in final.signals} == {f"sig-{i}": "done" for i in range(8)}
