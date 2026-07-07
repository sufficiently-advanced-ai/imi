"""
Signal lineage reader — full supersession chains and as-of-T reconstruction.

Provides two complementary read paths for following SUPERSEDES chains:

  1. Neo4j path  — variable-length traversal across the graph (fast, canonical)
  2. JSON fallback — walks superseded_by / reverse-index over SignalStore JSON
                    (truth source; used when Neo4j is unavailable or raises)

Both paths return the same entry shape so callers never need to branch on which
path was taken.

Public API
----------
    build_chain_from_store(signal_id, all_signals_by_id, predecessors_of)
        Sync helper used by both the reader's fallback and decision_view.get_decision.

    SignalLineageReader
        .get_supersession_chain(signal_id) -> list[dict]  (async)
        .decision_as_of(signal_id, at) -> dict | None     (async)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.neo4j_client import Neo4jClient
    from app.services.signal_store import SignalStore


# ---------------------------------------------------------------------------
# Cypher for variable-length SUPERSEDES traversal
# ---------------------------------------------------------------------------

# Direction: (newer)-[:SUPERSEDES]->(older)
# We start from signal $id and collect:
#   - itself
#   - all signals that (transitively) SUPERSEDE it  → newer end of the chain
#   - all signals it (transitively) SUPERSEDES       → older end of the chain
#
# Depth cap: 1..25 (matches the JSON-walk depth cap)

_CHAIN_CYPHER = """
MATCH (s:Signal {id: $id})
OPTIONAL MATCH (newer:Signal)-[:SUPERSEDES*1..25]->(s)
OPTIONAL MATCH (s)-[:SUPERSEDES*1..25]->(older:Signal)
WITH s, collect(DISTINCT newer) AS newer_list, collect(DISTINCT older) AS older_list
WITH [n IN newer_list | {node: n, bucket: 'newer'}] +
     [{node: s, bucket: 'self'}] +
     [o IN older_list | {node: o, bucket: 'older'}] AS items
UNWIND items AS item
WITH DISTINCT item.node AS node, item.bucket AS bucket
RETURN node.id            AS id,
       node.content       AS content,
       node.source_timestamp AS source_timestamp,
       node.valid_from    AS valid_from,
       node.valid_to      AS valid_to,
       node.provenance_status AS provenance_status,
       node.review_status AS review_status,
       bucket
"""


# ---------------------------------------------------------------------------
# Entry shape helpers
# ---------------------------------------------------------------------------


def _make_entry(sig_obj, relation: str, now: datetime) -> dict:
    """Build a lineage entry dict from a Signal object."""
    from app.services.decision_states import compute_decision_state

    state, _ = compute_decision_state(sig_obj, now=now)
    return {
        "id": sig_obj.id,
        "content": sig_obj.content,
        "state": state,
        "source_timestamp": sig_obj.source_timestamp,
        "valid_from": sig_obj.valid_from,
        "valid_to": sig_obj.valid_to,
        "relation": relation,
    }


def _make_entry_from_row(row: dict, now: datetime) -> dict:
    """Build a lineage entry dict from a Neo4j result row.

    The row contains: id, content, source_timestamp, valid_from, valid_to,
    provenance_status, review_status, bucket.
    superseded_by and created_at are NOT present (not returned by _CHAIN_CYPHER).
    The caller overwrites 'relation' after calling this function.
    """
    from app.models.signal import Signal
    from app.services.decision_states import compute_decision_state

    # Build a minimal Signal-like object from the row for state computation.
    # superseded_by is absent from Neo4j rows (not a stored node property) — pass None.
    # created_at is absent from Neo4j rows — default to now.
    try:
        sig = Signal(
            id=row["id"],
            type="decision",  # placeholder — not used in state computation
            content=row.get("content") or "",
            source_meeting_id="",
            source_timestamp=row.get("source_timestamp") or "",
            provenance_status=row.get("provenance_status") or "generated",
            review_status=row.get("review_status") or "pending",
            superseded_by=None,
            valid_from=row.get("valid_from"),
            valid_to=row.get("valid_to"),
            created_at=datetime.now(UTC).isoformat(),
        )
    except Exception:
        # If model construction fails, skip state computation
        return {
            "id": row.get("id", ""),
            "content": row.get("content") or "",
            "state": "candidate",
            "source_timestamp": row.get("source_timestamp") or "",
            "valid_from": row.get("valid_from"),
            "valid_to": row.get("valid_to"),
            "relation": "predecessor",  # caller will override
        }

    state, _ = compute_decision_state(sig, now=now)
    return {
        "id": sig.id,
        "content": sig.content,
        "state": state,
        "source_timestamp": sig.source_timestamp,
        "valid_from": sig.valid_from,
        "valid_to": sig.valid_to,
        "relation": "predecessor",  # caller will override
    }


# ---------------------------------------------------------------------------
# Shared sync JSON-walk (used by both fallback and decision_view)
# ---------------------------------------------------------------------------


def build_chain_from_store(
    signal_id: str,
    all_signals_by_id: dict[str, object],
    predecessors_of: dict[str, list],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Build the full supersession chain containing *signal_id*.

    Uses the same convention as decision_view:
      - predecessors_of[X] = signals whose superseded_by == X
        These are the signals OLDER than X (X superseded them).
        decision_view calls them "predecessors" (they came before X).
      - signal.superseded_by points to the NEWER signal that replaced it.
        decision_view calls those "successors" (they came after).

    Lineage ordering (NEWEST → OLDEST):
      successors (newest end) → self → predecessors (oldest end)

    Relations relative to signal_id:
      - 'predecessor': signals OLDER than signal_id (in predecessors_of chain)
      - 'self': signal_id itself
      - 'successor': signals NEWER than signal_id (via superseded_by hops)

    Args:
        signal_id: The anchor signal whose chain we reconstruct.
        all_signals_by_id: Dict mapping signal id → Signal object (all signals).
        predecessors_of: Reverse index mapping id → list of Signal objects
                         whose superseded_by == id (the older signals).
        now: Reference time for state computation.

    Returns:
        List of lineage entry dicts ordered NEWEST → OLDEST.
        Each entry: {id, content, state, source_timestamp, valid_from, valid_to,
                     relation} where relation is 'predecessor', 'self', or
                     'successor' relative to signal_id.
    """
    if now is None:
        now = datetime.now(UTC)

    target = all_signals_by_id.get(signal_id)
    if target is None:
        return []

    visited: set[str] = {signal_id}

    # --- Collect predecessors (OLDER signals) ---
    # predecessors_of[X] = signals with superseded_by == X → they are OLDER than X.
    # Walk transitively: older_signals are the "predecessors" in decision_view naming.
    older_signals = []
    frontier = [signal_id]
    depth = 0
    while frontier and depth < 25:
        next_frontier = []
        for current_id in frontier:
            for pred in predecessors_of.get(current_id, []):
                if pred.id not in visited:
                    visited.add(pred.id)
                    older_signals.append(pred)
                    next_frontier.append(pred.id)
        frontier = next_frontier
        depth += 1

    # --- Collect successors (NEWER signals) ---
    # signal.superseded_by points to the NEWER signal that replaced this one.
    # Walk forward via superseded_by to collect all newer chain members.
    newer_signals = []
    current_id = signal_id
    for _ in range(25):
        sig = all_signals_by_id.get(current_id)
        if sig is None or not sig.superseded_by:
            break
        next_id = sig.superseded_by
        if next_id in visited:
            break
        successor = all_signals_by_id.get(next_id)
        if successor is None:
            break
        visited.add(next_id)
        newer_signals.append(successor)
        current_id = next_id

    # Sort on parsed aware datetimes so mixed-offset timestamps (e.g. +02:00 vs Z)
    # compare correctly.  None sorts last (epoch sentinel = earliest possible).
    _EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

    def _sort_key_signal(s) -> datetime:
        return _parse_iso_defensive(s.valid_from or s.source_timestamp) or _EPOCH

    # newer signals newest-first (highest timestamp first)
    newer_signals.sort(key=_sort_key_signal, reverse=True)
    # older_signals newest-first (so they appear less-old → most-old after self)
    older_signals.sort(key=_sort_key_signal, reverse=True)

    # Build chain NEWEST→OLDEST:
    #   successors (newest) + self + predecessors (oldest)
    entries = (
        [_make_entry(s, "successor", now) for s in newer_signals]
        + [_make_entry(target, "self", now)]
        + [_make_entry(s, "predecessor", now) for s in older_signals]
    )
    return entries


# ---------------------------------------------------------------------------
# Main reader class
# ---------------------------------------------------------------------------


def _parse_iso_defensive(ts: str | None) -> datetime | None:
    """Parse an ISO timestamp string defensively; naive → UTC."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


class SignalLineageReader:
    """Read supersession chains and reconstruct signal state as-of a given time.

    Uses Neo4j when available (fast path) and falls back to SignalStore JSON
    walking when Neo4j is absent or raises.  Both paths return the same entry shape.
    """

    def __init__(
        self,
        neo4j_client: Neo4jClient | None = None,
        signal_store: SignalStore | None = None,
    ):
        self._neo4j_client = neo4j_client
        self._signal_store = signal_store

    def _get_store(self) -> SignalStore:
        if self._signal_store is not None:
            return self._signal_store
        from app.services.signal_store import signal_store as _default_store

        return _default_store  # type: ignore[return-value]

    async def get_supersession_chain(self, signal_id: str) -> list[dict]:
        """Return the full supersession chain containing *signal_id*.

        Returns entries ordered NEWEST → OLDEST.

        Entry shape:
            {id, content, state, source_timestamp, valid_from, valid_to,
             relation: 'predecessor'|'self'|'successor'}

        relation is relative to signal_id:
          - 'predecessor': signals older than signal_id (superseded by its chain)
          - 'self': signal_id itself
          - 'successor': signals newer than signal_id (they supersede it)

        Neo4j path: variable-length SUPERSEDES traversal (depth 1..25).
        Fallback: JSON walk via build_chain_from_store.
        """
        now = datetime.now(UTC)

        # --- Try Neo4j path ---
        if self._neo4j_client is not None:
            try:
                rows = await self._neo4j_client.execute_read(
                    _CHAIN_CYPHER, {"id": signal_id}
                )
                if rows:
                    return self._build_chain_from_neo4j_rows(rows, signal_id, now)
            except Exception as exc:
                logger.warning(
                    "[LINEAGE] Neo4j chain query failed for %s, falling back to JSON: %s",
                    signal_id,
                    exc,
                )

        # --- JSON fallback ---
        return self._fallback_chain(signal_id, now)

    def _build_chain_from_neo4j_rows(
        self, rows: list[dict], signal_id: str, now: datetime
    ) -> list[dict]:
        """Convert raw Neo4j rows (with bucket labels) into ordered chain entries.

        The Cypher query buckets each row by traversal direction:
          - 'newer': nodes that (transitively) SUPERSEDE signal_id  → relation 'successor'
          - 'self':  signal_id itself                                → relation 'self'
          - 'older': nodes that signal_id (transitively) SUPERSEDES → relation 'predecessor'

        Relations are derived from graph topology (bucket), NOT from a
        superseded_by property (which Neo4j Signal nodes do not carry).

        Chain ordering: NEWEST → OLDEST
          successors (newer, sorted by valid_from desc)
          + self
          + predecessors (older, sorted by valid_from desc)
        """
        if not rows:
            return []

        newer_rows: list[dict] = []
        self_row: dict | None = None
        older_rows: list[dict] = []

        for row in rows:
            if not row.get("id"):
                continue
            bucket = row.get("bucket", "")
            if bucket == "newer":
                newer_rows.append(row)
            elif bucket == "self":
                self_row = row
            elif bucket == "older":
                older_rows.append(row)
            # Unknown bucket: skip (defensive)

        if self_row is None:
            # self row missing — fall back gracefully
            logger.warning(
                "[LINEAGE] No self row for signal_id=%s in Neo4j result", signal_id
            )
            return []

        # Sort on parsed aware datetimes so mixed-offset timestamps compare correctly.
        _EPOCH_ROW = datetime(1970, 1, 1, tzinfo=UTC)

        def _sort_key_row(r: dict) -> datetime:
            return (
                _parse_iso_defensive(r.get("valid_from") or r.get("source_timestamp"))
                or _EPOCH_ROW
            )

        newer_rows.sort(key=_sort_key_row, reverse=True)
        older_rows.sort(key=_sort_key_row, reverse=True)

        def _row_to_entry(row: dict, relation: str) -> dict:
            entry = _make_entry_from_row(row, now)
            entry["relation"] = relation
            return entry

        # NEWEST → OLDEST: successors (newer) + self + predecessors (older)
        return (
            [_row_to_entry(r, "successor") for r in newer_rows]
            + [_row_to_entry(self_row, "self")]
            + [_row_to_entry(r, "predecessor") for r in older_rows]
        )

    def _fallback_chain(self, signal_id: str, now: datetime) -> list[dict]:
        """JSON-walk fallback when Neo4j is unavailable."""
        store = self._get_store()
        all_meetings = store.load_all()

        all_signals_by_id: dict[str, object] = {}
        predecessors_of: dict[str, list] = {}

        for ms in all_meetings:
            for sig in ms.signals:
                all_signals_by_id[sig.id] = sig
                if sig.superseded_by:
                    predecessors_of.setdefault(sig.superseded_by, []).append(sig)

        return build_chain_from_store(
            signal_id,
            all_signals_by_id,
            predecessors_of,
            now=now,
        )

    async def decision_as_of(self, signal_id: str, at: datetime) -> dict | None:
        """Return the chain member whose validity window contains *at*.

        Validity window: valid_from <= at < (valid_to or +infinity).

        Parses ISO strings defensively (naive → UTC, like decision_states).
        Returns None if no member of the chain matches.

        Args:
            signal_id: Any member of the supersession chain to search.
            at: The reference timestamp for reconstruction.

        Returns:
            A lineage entry dict (same shape as get_supersession_chain entries)
            or None if no member's validity window covers *at*.
        """
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)

        chain = await self.get_supersession_chain(signal_id)
        for entry in chain:
            valid_from_dt = _parse_iso_defensive(entry.get("valid_from"))
            if valid_from_dt is None:
                continue

            valid_to_dt = _parse_iso_defensive(entry.get("valid_to"))

            if at < valid_from_dt:
                continue

            if valid_to_dt is not None and at >= valid_to_dt:
                continue

            return entry

        return None
