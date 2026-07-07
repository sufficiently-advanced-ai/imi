"""app/services/graph/signal_edge_backfill — edge reconstruction from the store.

Server-importable counterpart of scripts/backfill_supersedes_edges.py used by
the rebuild orchestrator. Covers: SUPERSEDES pair collection (fallback chain),
CONFLICTS_WITH canonical pair dedup + proposed_at sourcing from either side,
and backfill_all_edges counting (written vs skipped_missing).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph.signal_edge_backfill import (
    backfill_all_edges,
    collect_conflict_pairs,
    collect_supersedes_pairs,
)

_T1 = "2026-06-01T10:00:00+00:00"
_T2 = "2026-06-01T11:00:00+00:00"


def _sig(sig_id: str, **overrides) -> dict:
    base = {
        "id": sig_id,
        "type": "decision",
        "content": f"Content for {sig_id}",
        "source_meeting_id": "bot-test",
        "source_timestamp": _T1,
        "valid_from": _T1,
        "valid_to": None,
        "superseded_by": None,
        "created_at": _T1,
        "metadata": {},
    }
    base.update(overrides)
    return base


def _write_store(tmp_path: Path, meetings: dict[str, list[dict]]):
    from app.services.signal_store import SignalStore

    signals_dir = tmp_path / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    for bot_id, sigs in meetings.items():
        payload = {
            "meeting_id": f"meet-{bot_id}",
            "bot_id": bot_id,
            "extracted_at": _T1,
            "signal_count": len(sigs),
            "signals": sigs,
        }
        (signals_dir / f"meeting-{bot_id}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    return SignalStore(signals_dir=signals_dir)


def _writer(supersedes_ok: bool = True, conflicts_ok: bool = True) -> MagicMock:
    writer = MagicMock()
    writer.write_supersedes_edge = AsyncMock(return_value=supersedes_ok)
    writer.write_conflicts_with_edge = AsyncMock(return_value=conflicts_ok)
    return writer


# ---------------------------------------------------------------------------
# collect_supersedes_pairs
# ---------------------------------------------------------------------------


def test_supersedes_pair_uses_valid_to(tmp_path):
    store = _write_store(
        tmp_path,
        {"b1": [_sig("old", superseded_by="new", valid_to=_T2), _sig("new")]},
    )
    pairs = collect_supersedes_pairs(store)
    assert pairs == [("new", "old", _T2, None)]


def test_supersedes_fallback_to_successor_valid_from(tmp_path):
    store = _write_store(
        tmp_path,
        {"b1": [_sig("old", superseded_by="new"), _sig("new", valid_from=_T2)]},
    )
    pairs = collect_supersedes_pairs(store)
    assert pairs[0][2] == _T2


def test_no_supersedes_pairs_when_field_unset(tmp_path):
    store = _write_store(tmp_path, {"b1": [_sig("a"), _sig("b")]})
    assert collect_supersedes_pairs(store) == []


# ---------------------------------------------------------------------------
# collect_conflict_pairs
# ---------------------------------------------------------------------------


def test_conflict_pairs_canonical_and_deduped(tmp_path):
    """Symmetric conflicts_with on both signals yields exactly one canonical pair."""
    store = _write_store(
        tmp_path,
        {
            "b1": [
                _sig("sig-b", metadata={"conflicts_with": ["sig-a"]}),
                _sig(
                    "sig-a",
                    metadata={
                        "conflicts_with": ["sig-b"],
                        "conflict_candidates": [
                            {
                                "other_signal_id": "sig-b",
                                "status": "confirmed",
                                "proposed_at": _T2,
                            }
                        ],
                    },
                ),
            ]
        },
    )
    pairs = collect_conflict_pairs(store)
    assert len(pairs) == 1
    a_id, b_id, confirmed_at, _tenant = pairs[0]
    assert (a_id, b_id) == ("sig-a", "sig-b")  # canonical: a_id < b_id
    # proposed_at found even though iteration hit sig-b (no candidate) first
    assert confirmed_at == _T2


def test_conflict_pairs_fall_back_to_created_at(tmp_path):
    store = _write_store(
        tmp_path,
        {
            "b1": [
                _sig("x", metadata={"conflicts_with": ["y"]}, created_at=_T2),
                _sig("y"),
            ]
        },
    )
    pairs = collect_conflict_pairs(store)
    assert len(pairs) == 1
    assert pairs[0][2] == _T2


def test_conflict_pairs_ignore_self_reference(tmp_path):
    store = _write_store(
        tmp_path, {"b1": [_sig("x", metadata={"conflicts_with": ["x", ""]})]}
    )
    assert collect_conflict_pairs(store) == []


# ---------------------------------------------------------------------------
# backfill_all_edges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_counts_both_edge_types(tmp_path):
    store = _write_store(
        tmp_path,
        {
            "b1": [
                _sig("old", superseded_by="new", valid_to=_T2),
                _sig("new"),
                _sig("c1", metadata={"conflicts_with": ["c2"]}),
                _sig("c2", metadata={"conflicts_with": ["c1"]}),
            ]
        },
    )
    writer = _writer()
    counts = await backfill_all_edges(store, writer)

    assert counts == {
        "supersedes_written": 1,
        "conflicts_written": 1,
        "skipped_missing": 0,
    }
    writer.write_supersedes_edge.assert_awaited_once()
    writer.write_conflicts_with_edge.assert_awaited_once()
    # Canonical conflict direction passed through
    args, kwargs = writer.write_conflicts_with_edge.await_args
    assert args == ("c1", "c2")
    assert "confirmed_at" in kwargs


@pytest.mark.asyncio
async def test_backfill_counts_missing_nodes(tmp_path):
    store = _write_store(
        tmp_path,
        {"b1": [_sig("old", superseded_by="ghost", valid_to=_T2)]},
    )
    writer = _writer(supersedes_ok=False)
    counts = await backfill_all_edges(store, writer)
    assert counts["supersedes_written"] == 0
    assert counts["skipped_missing"] == 1


@pytest.mark.asyncio
async def test_backfill_idempotent_double_run(tmp_path):
    store = _write_store(
        tmp_path,
        {"b1": [_sig("old", superseded_by="new", valid_to=_T2), _sig("new")]},
    )
    writer = _writer()
    first = await backfill_all_edges(store, writer)
    second = await backfill_all_edges(store, writer)
    assert first == second  # MERGE semantics: same counts on re-run
