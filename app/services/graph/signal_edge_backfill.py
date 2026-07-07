"""Reconstruct SUPERSEDES / CONFLICTS_WITH edges from persisted signal state.

Both edge types are derivable from the signal store without any LLM calls:

* ``signal.superseded_by`` (+ ``valid_to``) → ``(new)-[:SUPERSEDES]->(old)``
* ``signal.metadata["conflicts_with"]`` (symmetric list of IDs, written by the
  confirm flow in app/routes/conflicts.py) → canonical
  ``(a)-[:CONFLICTS_WITH]->(b)`` with a_id < b_id

This is the server-importable counterpart of
``scripts/backfill_supersedes_edges.py`` (which remains the standalone CLI for
the SUPERSEDES half); the rebuild orchestrator calls ``backfill_all_edges``
after replaying signal nodes into a freshly wiped tenant graph.

Store-agnostic: any object with ``load_all() -> list[MeetingSignals]`` works
(file-based SignalStore in community, _PgSignalStore in hosted).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# (new_id, old_id, superseded_at, tenant_id) — matches the tuple shape of
# scripts/backfill_supersedes_edges.collect_pairs for drop-in compatibility.
SupersedesPair = tuple[str, str, str, "str | None"]
# (a_id, b_id, confirmed_at, tenant_id) with a_id < b_id (canonical direction).
ConflictPair = tuple[str, str, str, "str | None"]


def _signals_by_id(all_meetings: list[Any]) -> dict[str, Any]:
    return {sig.id: sig for ms in all_meetings for sig in ms.signals}


def collect_supersedes_pairs(store: Any) -> list[SupersedesPair]:
    """Collect (new_id, old_id, superseded_at, tenant_id) for every signal
    with ``superseded_by`` set.

    superseded_at fallback chain (prefer the boundary when the successor
    became valid): old.valid_to → successor.valid_from →
    successor.source_timestamp → old.created_at → now.
    """
    all_meetings = store.load_all()
    by_id = _signals_by_id(all_meetings)

    pairs: list[SupersedesPair] = []
    for ms in all_meetings:
        for sig in ms.signals:
            if not sig.superseded_by:
                continue
            successor = by_id.get(sig.superseded_by)
            superseded_at: str = (
                sig.valid_to
                or (getattr(successor, "valid_from", None) if successor else None)
                or (getattr(successor, "source_timestamp", None) if successor else None)
                or sig.created_at
                or datetime.now(UTC).isoformat()
            )
            tenant_id = (
                getattr(successor, "tenant_id", None) if successor else None
            ) or getattr(sig, "tenant_id", None)
            pairs.append((sig.superseded_by, sig.id, superseded_at, tenant_id))
    return pairs


def collect_conflict_pairs(store: Any) -> list[ConflictPair]:
    """Collect canonical (a_id, b_id, confirmed_at, tenant_id) conflict pairs.

    ``metadata["conflicts_with"]`` is symmetric — both signals carry each
    other's ID — so pairs are canonicalised (a_id < b_id) and deduplicated.

    confirmed_at fallback chain: the matching ``conflict_candidates`` entry's
    confirmed_at → its proposed_at → signal.created_at → now.
    """
    all_meetings = store.load_all()
    by_id = _signals_by_id(all_meetings)

    def _candidate_for(signal: Any, other_id: str) -> dict:
        if signal is None:
            return {}
        return next(
            (
                c
                for c in (signal.metadata or {}).get("conflict_candidates", [])
                if c.get("other_signal_id") == other_id
            ),
            {},
        )

    seen: set[tuple[str, str]] = set()
    pairs: list[ConflictPair] = []
    for ms in all_meetings:
        for sig in ms.signals:
            conflicts_with = (sig.metadata or {}).get("conflicts_with") or []
            for other_id in conflicts_with:
                if not other_id or other_id == sig.id:
                    continue
                a_id, b_id = sorted([sig.id, str(other_id)])
                if (a_id, b_id) in seen:
                    continue
                seen.add((a_id, b_id))

                # The confirm flow (app/routes/conflicts.py) flips the matching
                # conflict_candidates entry to status="confirmed" but stamps
                # confirmed_at only on the Neo4j edge — for replay the best
                # persisted approximation is the candidate's proposed_at. Only
                # one side carries the candidate entry, so check both signals.
                other_sig = by_id.get(str(other_id))
                candidate = _candidate_for(sig, str(other_id)) or _candidate_for(
                    other_sig, sig.id
                )
                confirmed_at: str = (
                    candidate.get("proposed_at")
                    or sig.created_at
                    or datetime.now(UTC).isoformat()
                )
                tenant_id = getattr(sig, "tenant_id", None) or (
                    getattr(other_sig, "tenant_id", None) if other_sig else None
                )
                pairs.append((a_id, b_id, confirmed_at, tenant_id))
    return pairs


async def backfill_all_edges(store: Any, writer: Any) -> dict[str, int]:
    """Write SUPERSEDES and CONFLICTS_WITH edges for everything in the store.

    Idempotent (the writer's MERGE semantics). Pairs whose nodes are missing
    in Neo4j are counted in ``skipped_missing`` — the writers return False
    rather than raising.

    Returns counts: supersedes_written, conflicts_written, skipped_missing.
    """
    counts = {"supersedes_written": 0, "conflicts_written": 0, "skipped_missing": 0}

    for new_id, old_id, superseded_at, tenant_id in collect_supersedes_pairs(store):
        ok = await writer.write_supersedes_edge(
            new_id, old_id, superseded_at=superseded_at, tenant_id=tenant_id
        )
        if ok:
            counts["supersedes_written"] += 1
        else:
            counts["skipped_missing"] += 1

    for a_id, b_id, confirmed_at, tenant_id in collect_conflict_pairs(store):
        ok = await writer.write_conflicts_with_edge(
            a_id, b_id, confirmed_at=confirmed_at, tenant_id=tenant_id
        )
        if ok:
            counts["conflicts_written"] += 1
        else:
            counts["skipped_missing"] += 1

    logger.info("backfill_all_edges: %s", counts)
    return counts
