"""Tests for app/services/graph/signal_lineage.py (Issue #909 — Sprint 2 Task 17).

Covers:
- build_chain_from_store: full ordered chain from each member of a 3-link chain
- SignalLineageReader fallback path: correct entries from any member
- decision_as_of: returns correct member for time inside each window
- decision_as_of: returns None before earliest valid_from
- Cycle guard: A↔B cycle doesn't hang
- Neo4j path: fake client returning canned rows → correct entries
- Neo4j client raising → fallback to JSON walk, still correct
- Cypher string contains SUPERSEDES and traversal depth bounds
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---------------------------------------------------------------------------
# Time constants for 3-link chain: A (oldest) ← B (middle) ← C (newest)
#
#   A: valid_from=T0, valid_to=T1,  superseded_by=B  (superseded)
#   B: valid_from=T1, valid_to=T2,  superseded_by=C  (superseded)
#   C: valid_from=T2, valid_to=None                  (current)
# ---------------------------------------------------------------------------
T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-02-01T00:00:00+00:00"
T2 = "2026-03-01T00:00:00+00:00"

_DT0 = datetime(2026, 1, 1, tzinfo=UTC)
_DT1 = datetime(2026, 2, 1, tzinfo=UTC)
_DT2 = datetime(2026, 3, 1, tzinfo=UTC)
_DT_BEFORE = _DT0 - timedelta(days=1)
_DT_IN_A = _DT0 + timedelta(days=5)
_DT_IN_B = _DT1 + timedelta(days=5)
_DT_IN_C = _DT2 + timedelta(days=5)


def _sig(
    sig_id: str,
    *,
    source_timestamp: str = T0,
    valid_from: str | None = T0,
    valid_to: str | None = None,
    superseded_by: str | None = None,
    provenance_status: str = "generated",
    created_at: str = T0,
    content: str | None = None,
) -> dict:
    return {
        "id": sig_id,
        "type": "decision",
        "content": content or f"Content {sig_id}",
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
        "extracted_at": T0,
        "signal_count": len(signals),
        "signals": signals,
    }


def _build_store(tmp_path: Path, meetings: dict[str, list[dict]]) -> object:
    from app.services.signal_store import SignalStore

    signals_dir = tmp_path / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    for bot_id, sigs in meetings.items():
        path = signals_dir / f"meeting-{bot_id}.json"
        path.write_text(json.dumps(_ms(bot_id, sigs)), encoding="utf-8")
    return SignalStore(signals_dir=signals_dir)


def _three_link_store(tmp_path: Path) -> object:
    """3-link chain: A (oldest) ← B (middle) ← C (newest).

    A.superseded_by = B   (A was superseded by B)
    B.superseded_by = C   (B was superseded by C)
    C has no superseded_by
    """
    return _build_store(
        tmp_path,
        {
            "bot1": [
                _sig(
                    "sig-A",
                    valid_from=T0,
                    valid_to=T1,
                    superseded_by="sig-B",
                    source_timestamp=T0,
                ),
                _sig(
                    "sig-B",
                    valid_from=T1,
                    valid_to=T2,
                    superseded_by="sig-C",
                    source_timestamp=T1,
                ),
                _sig("sig-C", valid_from=T2, valid_to=None, source_timestamp=T2),
            ]
        },
    )


# ---------------------------------------------------------------------------
# Tests: build_chain_from_store (sync helper)
# ---------------------------------------------------------------------------


def test_build_chain_from_store_from_anchor(tmp_path):
    """Chain built from any member contains all three signals."""
    from app.services.graph.signal_lineage import build_chain_from_store

    store = _three_link_store(tmp_path)
    all_signals_by_id = {}
    predecessors_of = {}
    for ms in store.load_all():
        for sig in ms.signals:
            all_signals_by_id[sig.id] = sig
            if sig.superseded_by:
                predecessors_of.setdefault(sig.superseded_by, []).append(sig)

    # From middle node B
    chain = build_chain_from_store("sig-B", all_signals_by_id, predecessors_of)
    ids = [e["id"] for e in chain]
    assert set(ids) == {"sig-A", "sig-B", "sig-C"}
    # NEWEST → OLDEST: C, B, A
    assert ids == ["sig-C", "sig-B", "sig-A"]


def test_build_chain_from_store_self_relation(tmp_path):
    """The anchor signal has relation='self'."""
    from app.services.graph.signal_lineage import build_chain_from_store

    store = _three_link_store(tmp_path)
    all_signals_by_id = {}
    predecessors_of = {}
    for ms in store.load_all():
        for sig in ms.signals:
            all_signals_by_id[sig.id] = sig
            if sig.superseded_by:
                predecessors_of.setdefault(sig.superseded_by, []).append(sig)

    chain = build_chain_from_store("sig-B", all_signals_by_id, predecessors_of)
    self_entries = [e for e in chain if e["relation"] == "self"]
    assert len(self_entries) == 1
    assert self_entries[0]["id"] == "sig-B"


def test_build_chain_from_store_from_oldest(tmp_path):
    """Chain from oldest node (A) includes all three signals."""
    from app.services.graph.signal_lineage import build_chain_from_store

    store = _three_link_store(tmp_path)
    all_signals_by_id = {}
    predecessors_of = {}
    for ms in store.load_all():
        for sig in ms.signals:
            all_signals_by_id[sig.id] = sig
            if sig.superseded_by:
                predecessors_of.setdefault(sig.superseded_by, []).append(sig)

    chain = build_chain_from_store("sig-A", all_signals_by_id, predecessors_of)
    ids = [e["id"] for e in chain]
    assert set(ids) == {"sig-A", "sig-B", "sig-C"}
    assert ids[0] == "sig-C"  # newest first


def test_build_chain_from_store_from_newest(tmp_path):
    """Chain from newest node (C) includes all three signals."""
    from app.services.graph.signal_lineage import build_chain_from_store

    store = _three_link_store(tmp_path)
    all_signals_by_id = {}
    predecessors_of = {}
    for ms in store.load_all():
        for sig in ms.signals:
            all_signals_by_id[sig.id] = sig
            if sig.superseded_by:
                predecessors_of.setdefault(sig.superseded_by, []).append(sig)

    chain = build_chain_from_store("sig-C", all_signals_by_id, predecessors_of)
    ids = [e["id"] for e in chain]
    assert set(ids) == {"sig-A", "sig-B", "sig-C"}
    assert ids[-1] == "sig-A"  # oldest last


def test_build_chain_from_store_valid_from_valid_to_present(tmp_path):
    """Each chain entry includes valid_from and valid_to."""
    from app.services.graph.signal_lineage import build_chain_from_store

    store = _three_link_store(tmp_path)
    all_signals_by_id = {}
    predecessors_of = {}
    for ms in store.load_all():
        for sig in ms.signals:
            all_signals_by_id[sig.id] = sig
            if sig.superseded_by:
                predecessors_of.setdefault(sig.superseded_by, []).append(sig)

    chain = build_chain_from_store("sig-B", all_signals_by_id, predecessors_of)
    for entry in chain:
        assert "valid_from" in entry
        assert "valid_to" in entry


# ---------------------------------------------------------------------------
# Tests: SignalLineageReader fallback path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lineage_reader_fallback_returns_chain(tmp_path):
    """Fallback path returns full ordered chain from any member."""
    from app.services.graph.signal_lineage import SignalLineageReader

    store = _three_link_store(tmp_path)
    reader = SignalLineageReader(neo4j_client=None, signal_store=store)

    # Query from the middle node
    chain = await reader.get_supersession_chain("sig-B")
    ids = [e["id"] for e in chain]
    assert set(ids) == {"sig-A", "sig-B", "sig-C"}
    assert ids == ["sig-C", "sig-B", "sig-A"]  # newest → oldest


@pytest.mark.asyncio
async def test_lineage_reader_fallback_from_each_member(tmp_path):
    """Fallback path returns same set from A, B, or C."""
    from app.services.graph.signal_lineage import SignalLineageReader

    store = _three_link_store(tmp_path)
    reader = SignalLineageReader(neo4j_client=None, signal_store=store)

    for anchor in ("sig-A", "sig-B", "sig-C"):
        chain = await reader.get_supersession_chain(anchor)
        ids = {e["id"] for e in chain}
        assert ids == {"sig-A", "sig-B", "sig-C"}, f"Failed for anchor={anchor}"


@pytest.mark.asyncio
async def test_lineage_reader_missing_signal_returns_empty(tmp_path):
    """Non-existent signal_id returns empty list."""
    from app.services.graph.signal_lineage import SignalLineageReader

    store = _build_store(tmp_path, {"b": [_sig("sig-1")]})
    reader = SignalLineageReader(neo4j_client=None, signal_store=store)
    chain = await reader.get_supersession_chain("nonexistent-id")
    assert chain == []


# ---------------------------------------------------------------------------
# Tests: decision_as_of
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_as_of_in_window_A(tmp_path):
    """at inside A's window → returns A's entry."""
    from app.services.graph.signal_lineage import SignalLineageReader

    store = _three_link_store(tmp_path)
    reader = SignalLineageReader(neo4j_client=None, signal_store=store)

    result = await reader.decision_as_of("sig-B", _DT_IN_A)
    assert result is not None
    assert result["id"] == "sig-A"


@pytest.mark.asyncio
async def test_decision_as_of_in_window_B(tmp_path):
    """at inside B's window → returns B's entry."""
    from app.services.graph.signal_lineage import SignalLineageReader

    store = _three_link_store(tmp_path)
    reader = SignalLineageReader(neo4j_client=None, signal_store=store)

    result = await reader.decision_as_of("sig-A", _DT_IN_B)
    assert result is not None
    assert result["id"] == "sig-B"


@pytest.mark.asyncio
async def test_decision_as_of_in_window_C(tmp_path):
    """at inside C's window (open-ended) → returns C's entry."""
    from app.services.graph.signal_lineage import SignalLineageReader

    store = _three_link_store(tmp_path)
    reader = SignalLineageReader(neo4j_client=None, signal_store=store)

    result = await reader.decision_as_of("sig-A", _DT_IN_C)
    assert result is not None
    assert result["id"] == "sig-C"


@pytest.mark.asyncio
async def test_decision_as_of_before_earliest_valid_from_returns_none(tmp_path):
    """at before A's valid_from → None (signal didn't exist yet)."""
    from app.services.graph.signal_lineage import SignalLineageReader

    store = _three_link_store(tmp_path)
    reader = SignalLineageReader(neo4j_client=None, signal_store=store)

    result = await reader.decision_as_of("sig-B", _DT_BEFORE)
    assert result is None


@pytest.mark.asyncio
async def test_decision_as_of_boundary_at_valid_from_is_included(tmp_path):
    """Half-open window: at == valid_from → that member is returned."""
    from app.services.graph.signal_lineage import SignalLineageReader

    store = _three_link_store(tmp_path)
    reader = SignalLineageReader(neo4j_client=None, signal_store=store)

    # T1 is B's valid_from AND A's valid_to: [valid_from, valid_to) → B wins.
    result = await reader.decision_as_of("sig-B", datetime(2026, 2, 1, tzinfo=UTC))
    assert result is not None
    assert result["id"] == "sig-B"


@pytest.mark.asyncio
async def test_decision_as_of_boundary_at_valid_to_is_excluded(tmp_path):
    """Half-open window: at == valid_to of A excludes A (returns successor B)."""
    from app.services.graph.signal_lineage import SignalLineageReader

    store = _three_link_store(tmp_path)
    reader = SignalLineageReader(neo4j_client=None, signal_store=store)

    result = await reader.decision_as_of("sig-A", datetime(2026, 2, 1, tzinfo=UTC))
    assert result is not None
    assert result["id"] == "sig-B"


@pytest.mark.asyncio
async def test_decision_as_of_naive_datetime_treated_as_utc(tmp_path):
    """Naive datetime passed to decision_as_of is treated as UTC."""
    from app.services.graph.signal_lineage import SignalLineageReader

    store = _three_link_store(tmp_path)
    reader = SignalLineageReader(neo4j_client=None, signal_store=store)

    # Naive datetime equivalent to _DT_IN_B
    naive_dt = datetime(2026, 2, 6, 0, 0, 0)  # no tzinfo
    result = await reader.decision_as_of("sig-A", naive_dt)
    assert result is not None
    assert result["id"] == "sig-B"


# ---------------------------------------------------------------------------
# Tests: Cycle guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_guard_does_not_hang(tmp_path):
    """Cycle A↔B doesn't loop forever (visited guard stops traversal)."""
    from app.services.graph.signal_lineage import SignalLineageReader

    # Create a cycle: A.superseded_by = B, B.superseded_by = A
    store = _build_store(
        tmp_path,
        {
            "bot-cycle": [
                _sig(
                    "sig-cycle-A",
                    superseded_by="sig-cycle-B",
                    valid_from=T0,
                    source_timestamp=T0,
                ),
                _sig(
                    "sig-cycle-B",
                    superseded_by="sig-cycle-A",
                    valid_from=T1,
                    source_timestamp=T1,
                ),
            ]
        },
    )
    reader = SignalLineageReader(neo4j_client=None, signal_store=store)

    # Should return without hanging and without raising
    chain = await reader.get_supersession_chain("sig-cycle-A")
    assert isinstance(chain, list)
    assert len(chain) <= 2  # At most both signals, no infinite loop


# ---------------------------------------------------------------------------
# Tests: Neo4j path
# ---------------------------------------------------------------------------


def _make_neo4j_rows(anchor: str = "sig-B") -> list[dict]:
    """Return canned Neo4j rows matching the 3-link chain shape.

    The Cypher buckets nodes by traversal direction from the anchor:
      bucket='newer' — nodes that SUPERSEDE the anchor (relation → 'successor')
      bucket='self'  — the anchor itself
      bucket='older' — nodes the anchor SUPERSEDES (relation → 'predecessor')

    With anchor=sig-B (middle):
      sig-C is newer (supersedes B), sig-A is older (superseded by B).
    """
    # Chain: sig-A superseded_by sig-B superseded_by sig-C  (A is oldest, C is newest)
    # Buckets are relative to the anchor node:
    #   'self'  = the anchor itself
    #   'newer' = nodes that directly or transitively SUPERSEDE the anchor
    #   'older' = nodes the anchor directly or transitively SUPERSEDES
    bucket_map = {
        "sig-A": {"sig-A": "self", "sig-B": "newer", "sig-C": "newer"},
        "sig-B": {"sig-A": "older", "sig-B": "self", "sig-C": "newer"},
        "sig-C": {"sig-A": "older", "sig-B": "older", "sig-C": "self"},
    }
    buckets = bucket_map.get(anchor, bucket_map["sig-B"])
    base_rows = [
        {
            "id": "sig-A",
            "content": "Content sig-A",
            "source_timestamp": T0,
            "valid_from": T0,
            "valid_to": T1,
            "provenance_status": "superseded",
            "review_status": "pending",
        },
        {
            "id": "sig-B",
            "content": "Content sig-B",
            "source_timestamp": T1,
            "valid_from": T1,
            "valid_to": T2,
            "provenance_status": "superseded",
            "review_status": "pending",
        },
        {
            "id": "sig-C",
            "content": "Content sig-C",
            "source_timestamp": T2,
            "valid_from": T2,
            "valid_to": None,
            "provenance_status": "generated",
            "review_status": "pending",
        },
    ]
    for row in base_rows:
        row["bucket"] = buckets[row["id"]]
    return base_rows


@pytest.mark.asyncio
async def test_neo4j_path_uses_execute_read():
    """When neo4j_client is provided, execute_read is called with the SUPERSEDES Cypher."""
    from app.services.graph.signal_lineage import SignalLineageReader

    client = MagicMock()
    client.execute_read = AsyncMock(return_value=_make_neo4j_rows("sig-B"))

    reader = SignalLineageReader(neo4j_client=client, signal_store=None)
    await reader.get_supersession_chain("sig-B")

    client.execute_read.assert_called_once()
    # Verify the Cypher contains SUPERSEDES and depth bounds
    called_cypher = client.execute_read.call_args[0][0]
    assert "SUPERSEDES" in called_cypher
    assert "1..25" in called_cypher
    # Verify bucket labels come from graph topology, not a stored property
    assert "bucket" in called_cypher
    assert "'newer'" in called_cypher
    assert "'self'" in called_cypher
    assert "'older'" in called_cypher


@pytest.mark.asyncio
async def test_neo4j_path_returns_correct_chain():
    """Neo4j path returns all 3 entries in correct NEWEST→OLDEST order."""
    from app.services.graph.signal_lineage import SignalLineageReader

    client = MagicMock()
    client.execute_read = AsyncMock(return_value=_make_neo4j_rows("sig-B"))

    reader = SignalLineageReader(neo4j_client=client, signal_store=None)
    chain = await reader.get_supersession_chain("sig-B")

    ids = [e["id"] for e in chain]
    assert set(ids) == {"sig-A", "sig-B", "sig-C"}
    # C is newest (bucket=newer → successor), A is oldest (bucket=older → predecessor)
    assert ids[0] == "sig-C"
    assert ids[-1] == "sig-A"


@pytest.mark.asyncio
async def test_neo4j_path_relation_mapping():
    """Bucket labels map to correct relation values in chain entries."""
    from app.services.graph.signal_lineage import SignalLineageReader

    client = MagicMock()
    client.execute_read = AsyncMock(return_value=_make_neo4j_rows("sig-B"))

    reader = SignalLineageReader(neo4j_client=client, signal_store=None)
    chain = await reader.get_supersession_chain("sig-B")

    relation_by_id = {e["id"]: e["relation"] for e in chain}
    assert relation_by_id["sig-C"] == "successor"  # newer → successor
    assert relation_by_id["sig-B"] == "self"  # self → self
    assert relation_by_id["sig-A"] == "predecessor"  # older → predecessor


@pytest.mark.asyncio
async def test_neo4j_raising_falls_back_to_json(tmp_path):
    """When Neo4j raises, fallback to JSON walk returns correct chain."""
    from app.services.graph.signal_lineage import SignalLineageReader

    client = MagicMock()
    client.execute_read = AsyncMock(side_effect=RuntimeError("connection refused"))

    store = _three_link_store(tmp_path)
    reader = SignalLineageReader(neo4j_client=client, signal_store=store)

    # Should still return correct chain via fallback
    chain = await reader.get_supersession_chain("sig-B")
    ids = [e["id"] for e in chain]
    assert set(ids) == {"sig-A", "sig-B", "sig-C"}
    assert ids == ["sig-C", "sig-B", "sig-A"]


@pytest.mark.asyncio
async def test_neo4j_empty_result_falls_back_to_json(tmp_path):
    """When Neo4j returns empty list [], fallback to JSON walk returns correct chain."""
    from app.services.graph.signal_lineage import SignalLineageReader

    client = MagicMock()
    client.execute_read = AsyncMock(
        return_value=[]
    )  # Empty result, signal not yet in Neo4j

    store = _three_link_store(tmp_path)
    reader = SignalLineageReader(neo4j_client=client, signal_store=store)

    # Should fall back to JSON and return the full chain
    chain = await reader.get_supersession_chain("sig-B")
    ids = [e["id"] for e in chain]
    assert set(ids) == {"sig-A", "sig-B", "sig-C"}
    assert ids == ["sig-C", "sig-B", "sig-A"]


def test_chain_cypher_contains_supersedes():
    """_CHAIN_CYPHER contains SUPERSEDES, variable-length bounds, and bucket labels."""
    from app.services.graph.signal_lineage import _CHAIN_CYPHER

    assert "SUPERSEDES" in _CHAIN_CYPHER
    assert "1..25" in _CHAIN_CYPHER
    # Bucket labels must come from graph topology (edge direction), not a node property
    assert "bucket" in _CHAIN_CYPHER
    assert "'newer'" in _CHAIN_CYPHER
    assert "'self'" in _CHAIN_CYPHER
    assert "'older'" in _CHAIN_CYPHER
    # superseded_by must NOT be read back from node properties
    assert "node.superseded_by" not in _CHAIN_CYPHER


# ---------------------------------------------------------------------------
# Fix G: mixed-offset timestamp sort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_sorts_mixed_offset_timestamps_chronologically(tmp_path):
    """Chain with +02:00 and Z timestamps sorts chronologically (Fix G)."""
    import json

    from app.services.graph.signal_lineage import build_chain_from_store

    # T_UTC is "2026-06-01T10:00:00+00:00" (Z)
    # T_PLUS2 is "2026-06-01T11:00:00+02:00" which is the SAME wall time but
    # a different offset string — actually the same instant, just with a different offset.
    # For a more interesting test: make T_PLUS2 represent a LATER absolute time:
    # "2026-06-01T13:00:00+02:00" = 11:00 UTC  > "2026-06-01T10:00:00+00:00" = 10:00 UTC
    T_Z = "2026-06-01T10:00:00+00:00"  # 10:00 UTC — older
    T_PLUS2 = (
        "2026-06-01T13:00:00+02:00"  # 11:00 UTC — newer (13:00 local - 2h = 11:00 UTC)
    )

    signals_dir = tmp_path / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)

    def _make_raw(sig_id: str, vf: str, superseded_by: str | None = None) -> dict:
        return {
            "id": sig_id,
            "type": "decision",
            "content": f"content {sig_id}",
            "source_meeting_id": "bot-mixed",
            "source_timestamp": vf,
            "valid_from": vf,
            "valid_to": None,
            "superseded_by": superseded_by,
            "provenance_status": "generated",
            "created_at": vf,
        }

    ms = {
        "meeting_id": "meet-mixed",
        "bot_id": "bot-mixed",
        "extracted_at": T_Z,
        "signal_count": 2,
        "signals": [
            _make_raw("sig-z", T_Z, superseded_by="sig-plus2"),
            _make_raw("sig-plus2", T_PLUS2),
        ],
    }
    (signals_dir / "meeting-bot-mixed.json").write_text(
        json.dumps(ms), encoding="utf-8"
    )

    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=signals_dir)
    all_meetings = list(store.load_all())
    all_signals_by_id = {s.id: s for m in all_meetings for s in m.signals}
    predecessors_of: dict = {}
    for s in all_signals_by_id.values():
        if s.superseded_by:
            predecessors_of.setdefault(s.superseded_by, []).append(s)

    from datetime import datetime, UTC

    chain = build_chain_from_store(
        "sig-z",
        all_signals_by_id,
        predecessors_of,
        now=datetime.now(UTC),
    )

    ids = [e["id"] for e in chain]
    # sig-plus2 is the successor (newer), sig-z is older (self)
    assert ids[0] == "sig-plus2", f"Expected sig-plus2 (newer) first; got {ids}"
    assert ids[-1] == "sig-z", f"Expected sig-z (self) last; got {ids}"


def test_upsert_supersedes_cypher_uses_on_create_on_match():
    """Fix F: _UPSERT_SUPERSEDES uses ON CREATE / ON MATCH to avoid clobbering audited values."""
    from app.services.graph.signal_graph_writer import _UPSERT_SUPERSEDES

    assert (
        "ON CREATE SET" in _UPSERT_SUPERSEDES
    ), "_UPSERT_SUPERSEDES must use ON CREATE SET so initial values are preserved"
    assert (
        "ON MATCH SET" in _UPSERT_SUPERSEDES
    ), "_UPSERT_SUPERSEDES must use ON MATCH SET for fill-if-missing semantics"
    assert (
        "coalesce" in _UPSERT_SUPERSEDES
    ), "_UPSERT_SUPERSEDES ON MATCH must use coalesce() to fill-if-missing"
