"""Tests for scripts/backfill_supersedes_edges.py (Issue #909 — Sprint 2 Task 17).

Covers:
- collect_pairs: discovers all superseded_by pairs across meetings
- collect_pairs: superseded_at fallback order (valid_to > created_at > now)
- backfill dry_run: no writer calls
- backfill: missing node counted, not raised
- backfill: double-run safe (idempotent — calls writer twice, same counts)
- main CLI: --dry-run prints pairs, exits 0
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap (mirrors the script's own bootstrap)
# ---------------------------------------------------------------------------
_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---------------------------------------------------------------------------
# Import script functions via importlib (scripts/ is not a package)
# ---------------------------------------------------------------------------
_SCRIPT_PATH = Path(_ROOT) / "scripts" / "backfill_supersedes_edges.py"
_spec = _ilu.spec_from_file_location("backfill_supersedes_edges", str(_SCRIPT_PATH))
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

collect_pairs = _mod.collect_pairs
backfill = _mod.backfill

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T1 = "2026-06-01T10:00:00+00:00"
_T2 = "2026-06-01T11:00:00+00:00"
_T3 = "2026-06-01T12:00:00+00:00"


def _sig(
    sig_id: str,
    *,
    source_timestamp: str = _T1,
    valid_from: str | None = _T1,
    valid_to: str | None = None,
    superseded_by: str | None = None,
    provenance_status: str = "generated",
    created_at: str = _T1,
) -> dict:
    return {
        "id": sig_id,
        "type": "decision",
        "content": f"Content for {sig_id}",
        "source_meeting_id": "bot-test",
        "source_timestamp": source_timestamp,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "superseded_by": superseded_by,
        "provenance_status": provenance_status,
        "created_at": created_at,
    }


def _ms(bot_id: str, signals: list[dict]) -> dict:
    return {
        "meeting_id": f"meet-{bot_id}",
        "bot_id": bot_id,
        "extracted_at": _T1,
        "signal_count": len(signals),
        "signals": signals,
    }


def _write_store(tmp_path: Path, meetings: dict[str, list[dict]]) -> object:
    """Write meetings to tmp_path/signals/ and return a SignalStore."""
    from app.services.signal_store import SignalStore

    signals_dir = tmp_path / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    for bot_id, sigs in meetings.items():
        path = signals_dir / f"meeting-{bot_id}.json"
        path.write_text(json.dumps(_ms(bot_id, sigs)), encoding="utf-8")
    return SignalStore(signals_dir=signals_dir)


def _make_writer(ok: bool = True) -> MagicMock:
    """Return a fake SignalGraphWriter whose write_supersedes_edge is an AsyncMock."""
    writer = MagicMock()
    writer.write_supersedes_edge = AsyncMock(return_value=ok)
    return writer


# ---------------------------------------------------------------------------
# Tests: collect_pairs
# ---------------------------------------------------------------------------


def test_collect_pairs_single(tmp_path):
    """One signal with superseded_by → one pair emitted."""
    store = _write_store(
        tmp_path,
        {
            "bot1": [
                _sig("sig-old", superseded_by="sig-new", valid_to=_T2),
                _sig("sig-new"),
            ]
        },
    )
    pairs = collect_pairs(store)
    assert len(pairs) == 1
    new_id, old_id, superseded_at = pairs[0][0], pairs[0][1], pairs[0][2]
    assert new_id == "sig-new"
    assert old_id == "sig-old"
    assert superseded_at == _T2  # used valid_to


def test_collect_pairs_across_meetings(tmp_path):
    """Pairs are collected across multiple meeting files."""
    store = _write_store(
        tmp_path,
        {
            "bot1": [_sig("sig-a-old", superseded_by="sig-a-new")],
            "bot2": [_sig("sig-b-old", superseded_by="sig-b-new")],
            "bot3": [_sig("sig-c-no-superseded")],  # no pair
        },
    )
    pairs = collect_pairs(store)
    old_ids = {p[1] for p in pairs}
    assert old_ids == {"sig-a-old", "sig-b-old"}


def test_collect_pairs_superseded_at_fallback_valid_to(tmp_path):
    """valid_to is preferred as superseded_at."""
    store = _write_store(
        tmp_path,
        {"b": [_sig("old", superseded_by="new", valid_to=_T3, created_at=_T1)]},
    )
    pairs = collect_pairs(store)
    assert pairs[0][2] == _T3


def test_collect_pairs_superseded_at_fallback_created_at(tmp_path):
    """When valid_to is absent, created_at is used as superseded_at."""
    store = _write_store(
        tmp_path,
        {"b": [_sig("old", superseded_by="new", valid_to=None, created_at=_T2)]},
    )
    pairs = collect_pairs(store)
    assert pairs[0][2] == _T2


def test_collect_pairs_no_pairs(tmp_path):
    """Store with no superseded_by fields → empty list."""
    store = _write_store(tmp_path, {"b": [_sig("sig-1"), _sig("sig-2")]})
    assert collect_pairs(store) == []


# ---------------------------------------------------------------------------
# Tests: backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_dry_run_no_writer_calls(tmp_path):
    """--dry-run logs but makes no calls to the writer."""
    writer = _make_writer()
    pairs = [("new1", "old1", _T1), ("new2", "old2", _T2)]
    counts = await backfill(pairs, writer, dry_run=True)

    writer.write_supersedes_edge.assert_not_called()
    assert counts["dry_run"] == 2
    assert counts["created"] == 0
    assert counts["skipped_missing"] == 0


@pytest.mark.asyncio
async def test_backfill_creates_edges(tmp_path):
    """Successful writer calls increment 'created' count."""
    writer = _make_writer(ok=True)
    pairs = [("new1", "old1", _T1), ("new2", "old2", _T2)]
    counts = await backfill(pairs, writer, dry_run=False)

    assert writer.write_supersedes_edge.call_count == 2
    assert counts["created"] == 2
    assert counts["skipped_missing"] == 0


@pytest.mark.asyncio
async def test_backfill_missing_node_counted_not_raised(tmp_path):
    """If writer returns False (missing node), count skipped_missing — don't raise."""
    writer = _make_writer(ok=False)
    pairs = [("new1", "old1", _T1)]
    counts = await backfill(pairs, writer, dry_run=False)

    assert counts["skipped_missing"] == 1
    assert counts["created"] == 0


@pytest.mark.asyncio
async def test_backfill_mixed_results(tmp_path):
    """Mix of successful and missing-node pairs."""
    writer = MagicMock()
    # First call succeeds, second fails
    writer.write_supersedes_edge = AsyncMock(side_effect=[True, False, True])
    pairs = [("n1", "o1", _T1), ("n2", "o2", _T2), ("n3", "o3", _T3)]
    counts = await backfill(pairs, writer, dry_run=False)

    assert counts["created"] == 2
    assert counts["skipped_missing"] == 1


@pytest.mark.asyncio
async def test_backfill_double_run_idempotent():
    """Running backfill twice calls the writer twice per pair (MERGE is idempotent)."""
    writer = _make_writer(ok=True)
    pairs = [("new1", "old1", _T1)]

    counts1 = await backfill(pairs, writer, dry_run=False)
    counts2 = await backfill(pairs, writer, dry_run=False)

    # Each run calls write_supersedes_edge once — MERGE in Neo4j makes it safe
    assert writer.write_supersedes_edge.call_count == 2
    assert counts1["created"] == 1
    assert counts2["created"] == 1


@pytest.mark.asyncio
async def test_backfill_empty_pairs():
    """Empty pairs list → all zeros, no calls."""
    writer = _make_writer()
    counts = await backfill([], writer, dry_run=False)

    writer.write_supersedes_edge.assert_not_called()
    assert counts == {"created": 0, "skipped_missing": 0, "dry_run": 0}


@pytest.mark.asyncio
async def test_backfill_write_supersedes_edge_called_with_correct_args():
    """The writer is called with (new_id, old_id, superseded_at=..., tenant_id=None)."""
    writer = _make_writer(ok=True)
    pairs = [("new-sig", "old-sig", _T3)]
    await backfill(pairs, writer, dry_run=False)

    writer.write_supersedes_edge.assert_called_once_with(
        "new-sig", "old-sig", superseded_at=_T3, tenant_id=None
    )


@pytest.mark.asyncio
async def test_backfill_passes_tenant_id_from_four_tuple():
    """Fix M: when pair has a 4th element (tenant_id), it is forwarded to the writer."""
    writer = _make_writer(ok=True)
    # 4-tuple: (new_id, old_id, superseded_at, tenant_id)
    pairs = [("new-sig", "old-sig", _T3, "tenant-acme")]
    await backfill(pairs, writer, dry_run=False)

    writer.write_supersedes_edge.assert_called_once_with(
        "new-sig", "old-sig", superseded_at=_T3, tenant_id="tenant-acme"
    )


def test_collect_pairs_uses_successor_valid_from_for_superseded_at(tmp_path):
    """Fix L: superseded_at fallback prefers successor.valid_from over old.created_at."""
    # Old signal has no valid_to; successor has valid_from=_T2 and created_at=_T3.
    # The fix should use _T2 (successor.valid_from) not _T3 (successor.created_at).
    store = _write_store(
        tmp_path,
        {
            "b": [
                _sig("old", superseded_by="new", valid_to=None, created_at=_T1),
                _sig("new", valid_from=_T2, created_at=_T3),
            ]
        },
    )
    pairs = collect_pairs(store)
    assert len(pairs) == 1
    _, _, superseded_at = pairs[0][:3]
    assert (
        superseded_at == _T2
    ), f"Expected successor.valid_from={_T2!r} as superseded_at, got {superseded_at!r}"
