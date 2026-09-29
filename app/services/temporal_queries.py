"""Point-in-time queries over evidence (ADR-004).

Time belongs to evidence. Documents, signals and relationship assertions carry
``occurred_at`` (when it happened) and ``recorded_at`` (when imi ingested it);
entities are timeless identities. "The graph as of T" is the subgraph supported
by evidence with ``occurred_at <= T`` — computed by traversal, never read from
validity windows on entity nodes.

- entity_at: what was known about an entity at T
- relationships_at: relationships asserted, and entities co-mentioned, by T
- what_changed / what_changed_between: evidence that arrived in a window,
  plus evidence recorded late about an earlier time
- graph_as_of: the subgraph around an entity at T
- temporal_blast_radius: the same traversal with hop distances
- provenance: every piece of evidence about an entity, in event order
- find_contradictions: reviewed and pending conflicts between signals

All comparisons are on Neo4j ``DATETIME`` values in UTC. Backfilled content is
placed by its event time, so it appears in every point-in-time query from the
moment it is ingested.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

from app.utils.event_time import to_utc

logger = logging.getLogger(__name__)

# Signal store is imported here so callers can patch
# ``app.services.temporal_queries.signal_store`` in tests.
# The proxy resolves to the correct tenant-scoped store at call time.
from app.services.signal_store import signal_store  # noqa: E402

# Edges a signal uses to point at the entities it is about.
_SIGNAL_EDGES = ["MENTIONS", "ASSIGNED_TO", "FOR_CLIENT"]
# Derived, current-state cache; point-in-time co-occurrence is computed from
# MENTIONED_IN filtered by event time.
_DERIVED_EDGE = "CO_OCCURRENCE"

_RESOLVE = (
    "MATCH (n:Entity) WHERE NOT n:Signal "
    "AND (n.id = $lookup OR toLower(n.name) = toLower($lookup)) "
    "RETURN n.id AS id, n.name AS name, n.entity_type AS entity_type, properties(n) AS props "
    "ORDER BY CASE WHEN n.id = $lookup THEN 0 ELSE 1 END LIMIT 1"
)

_DOCUMENT_EVIDENCE = (
    "MATCH (e:Entity {id: $id})-[:MENTIONED_IN]->(d:Document) "
    "WHERE d.occurred_at <= $at "
    "RETURN count(d) AS n, min(d.occurred_at) AS first, max(d.occurred_at) AS last"
)

_SIGNAL_EVIDENCE = (
    "MATCH (s:Signal)-[r]->(e:Entity {id: $id}) "
    "WHERE type(r) IN $signal_edges AND s.occurred_at <= $at "
    "RETURN count(DISTINCT s) AS n, min(s.occurred_at) AS first, max(s.occurred_at) AS last"
)

_ASSERTION_EVIDENCE = (
    "MATCH (e:Entity {id: $id})-[r]-(o:Entity) "
    "WHERE NOT o:Signal AND type(r) <> $derived AND r.occurred_at <= $at "
    "RETURN count(r) AS n, min(r.occurred_at) AS first, max(r.occurred_at) AS last"
)

_STANDING_SIGNALS = (
    "MATCH (s:Signal)-[r]->(e:Entity {id: $id}) "
    "WHERE type(r) IN $signal_edges AND s.valid_from <= $at "
    "AND (s.valid_to IS NULL OR s.valid_to > $at) "
    "RETURN DISTINCT s.id AS id, s.signal_type AS type, s.content AS content, "
    "s.valid_from AS valid_from, s.valid_to AS valid_to, "
    "s.source_meeting_id AS source_meeting_id, "
    "s.source_meeting_title AS source_meeting_title "
    "ORDER BY valid_from DESC LIMIT $max_results"
)

_TYPED_OUT = (
    "MATCH (a:Entity {id: $id})-[r]->(b:Entity) "
    "WHERE NOT b:Signal AND type(r) <> $derived AND r.occurred_at <= $at "
    "RETURN type(r) AS rel_type, b.id AS other_id, b.name AS other_name, "
    "b.entity_type AS other_type, count(r) AS assertions, "
    "min(r.occurred_at) AS first, max(r.occurred_at) AS last, "
    "collect(DISTINCT r.source_id) AS sources, "
    "collect(DISTINCT r.time_source) AS time_sources"
)

_TYPED_IN = (
    "MATCH (b:Entity)-[r]->(a:Entity {id: $id}) "
    "WHERE NOT b:Signal AND type(r) <> $derived AND r.occurred_at <= $at "
    "RETURN type(r) AS rel_type, b.id AS other_id, b.name AS other_name, "
    "b.entity_type AS other_type, count(r) AS assertions, "
    "min(r.occurred_at) AS first, max(r.occurred_at) AS last, "
    "collect(DISTINCT r.source_id) AS sources, "
    "collect(DISTINCT r.time_source) AS time_sources"
)

_UNDATED = (
    "MATCH (a:Entity {id: $id})-[r]-(b:Entity) "
    "WHERE NOT b:Signal AND type(r) <> $derived AND r.occurred_at IS NULL "
    "RETURN count(r) AS n"
)

_CO_MENTIONED = (
    "MATCH (a:Entity {id: $id})-[:MENTIONED_IN]->(d:Document)<-[:MENTIONED_IN]-(b:Entity) "
    "WHERE d.occurred_at <= $at AND b.id <> $id "
    "RETURN b.id AS other_id, b.name AS other_name, b.entity_type AS other_type, "
    "count(DISTINCT d) AS shared_documents, "
    "min(d.occurred_at) AS first, max(d.occurred_at) AS last"
)

_DOCUMENTS_IN_WINDOW = (
    "MATCH (e:Entity {id: $id})-[:MENTIONED_IN]->(d:Document) "
    "WHERE d.occurred_at > $start AND d.occurred_at <= $end "
    "RETURN d.id AS id, d.path AS path, d.title AS title, d.occurred_at AS occurred_at, "
    "d.recorded_at AS recorded_at, d.time_source AS time_source "
    "ORDER BY occurred_at ASC"
)

_SIGNALS_IN_WINDOW = (
    "MATCH (s:Signal)-[r]->(e:Entity {id: $id}) "
    "WHERE type(r) IN $signal_edges AND s.occurred_at > $start AND s.occurred_at <= $end "
    "RETURN DISTINCT s.id AS id, s.signal_type AS type, s.content AS content, "
    "s.occurred_at AS occurred_at, s.source_meeting_title AS source_meeting_title "
    "ORDER BY occurred_at ASC"
)

_SIGNALS_CLOSED_IN_WINDOW = (
    "MATCH (s:Signal)-[r]->(e:Entity {id: $id}) "
    "WHERE type(r) IN $signal_edges AND s.valid_to > $start AND s.valid_to <= $end "
    "OPTIONAL MATCH (n:Signal)-[:SUPERSEDES]->(s) "
    "RETURN DISTINCT s.id AS id, s.signal_type AS type, s.content AS content, "
    "s.valid_to AS valid_to, n.id AS superseded_by "
    "ORDER BY valid_to ASC"
)

_RELATIONSHIPS_IN_WINDOW = (
    "MATCH (a:Entity {id: $id})-[r]->(b:Entity) "
    "WHERE NOT b:Signal AND type(r) <> $derived AND r.occurred_at <= $end "
    "WITH type(r) AS rel_type, b, min(r.occurred_at) AS first, "
    "collect(DISTINCT r.source_id) AS sources "
    "WHERE first > $start "
    "RETURN rel_type, b.id AS other_id, b.name AS other_name, first, sources "
    "ORDER BY first ASC"
)

_CO_MENTIONED_IN_WINDOW = (
    "MATCH (a:Entity {id: $id})-[:MENTIONED_IN]->(d:Document)<-[:MENTIONED_IN]-(b:Entity) "
    "WHERE d.occurred_at <= $end AND b.id <> $id "
    "WITH b, min(d.occurred_at) AS first WHERE first > $start "
    "RETURN b.id AS other_id, b.name AS other_name, b.entity_type AS other_type, first "
    "ORDER BY first ASC"
)

# Evidence about a time at or before ``start`` that imi only ingested after it.
_RECORDED_LATE = (
    "MATCH (e:Entity {id: $id})-[:MENTIONED_IN]->(d:Document) "
    "WHERE d.occurred_at <= $start AND d.recorded_at > $start AND d.recorded_at <= $end "
    "RETURN d.id AS id, d.path AS path, d.title AS title, d.occurred_at AS occurred_at, "
    "d.recorded_at AS recorded_at, d.time_source AS time_source "
    "ORDER BY occurred_at ASC"
)

_PROVENANCE = (
    "CALL { "
    "  MATCH (n:Entity {id: $id})-[r:MENTIONED_IN]->(d:Document) "
    "  RETURN coalesce(d.path, d.id) AS source, d.title AS title, type(r) AS action, "
    "         d.occurred_at AS occurred_at, d.recorded_at AS recorded_at, "
    "         d.time_source AS time_source "
    "  UNION "
    "  MATCH (n:Entity {id: $id})<-[r]-(s:Signal) WHERE type(r) IN $signal_edges "
    "  RETURN s.id AS source, s.content AS title, type(r) AS action, "
    "         s.occurred_at AS occurred_at, s.recorded_at AS recorded_at, "
    "         null AS time_source "
    "} "
    "RETURN source, title, action, occurred_at, recorded_at, time_source "
    "ORDER BY occurred_at IS NULL, occurred_at ASC"
)

_CONTRADICTION_SIGNALS = (
    "MATCH (s:Signal)-[r]->(e:Entity {id: $id}) "
    "WHERE type(r) IN $signal_edges "
    "AND ($date_from IS NULL OR s.occurred_at >= $date_from) "
    "AND ($date_to IS NULL OR s.occurred_at <= $date_to) "
    "RETURN DISTINCT s.id AS signal_id, s.content AS content, "
    "s.occurred_at AS timestamp, "
    "coalesce(s.signal_type, s.type) AS type "
    "ORDER BY timestamp ASC"
)

_IDENTITY_KEYS = {"id", "name", "entity_type", "canonical_name", "updated_at", "stub"}


def _require_time(value: Any, name: str) -> datetime:
    when = to_utc(value)
    if when is None:
        raise ValueError(f"{name} is not a valid ISO-8601 time: {value!r}")
    return when


def _span(rows: list[dict]) -> dict[str, Any]:
    row = rows[0] if rows else {}
    return {"count": int(row.get("n") or 0), "first": row.get("first"), "last": row.get("last")}


class TemporalQueryService:
    """Point-in-time queries computed from evidence."""

    def __init__(self, neo4j_client: Any):
        self.neo4j = neo4j_client

    async def _read(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return await self.neo4j.execute_read(query, params)

    async def _resolve(self, entity_id: str) -> dict[str, Any] | None:
        rows = await self._read(_RESOLVE, {"lookup": entity_id})
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # entity_at
    # ------------------------------------------------------------------

    async def entity_at(
        self, entity_id: str, at: Any, max_results: int = 25
    ) -> dict[str, Any] | None:
        """What was known about an entity at ``at``.

        Returns None when the entity does not exist, or when no evidence dated
        at or before ``at`` mentions it (imi had not heard of it yet). The
        profile attributes are the current ones: entities are not versioned,
        and change over time is carried by ``standing_signals``.
        """
        when = _require_time(at, "timestamp")
        node = await self._resolve(entity_id)
        if node is None:
            return None
        eid = node["id"]
        params = {"id": eid, "at": when}

        documents = _span(await self._read(_DOCUMENT_EVIDENCE, params))
        signals = _span(
            await self._read(_SIGNAL_EVIDENCE, {**params, "signal_edges": _SIGNAL_EDGES})
        )
        assertions = _span(
            await self._read(_ASSERTION_EVIDENCE, {**params, "derived": _DERIVED_EDGE})
        )
        if not (documents["count"] or signals["count"] or assertions["count"]):
            return None

        firsts = [s["first"] for s in (documents, signals, assertions) if s["first"]]
        lasts = [s["last"] for s in (documents, signals, assertions) if s["last"]]
        standing = await self._read(
            _STANDING_SIGNALS,
            {**params, "signal_edges": _SIGNAL_EDGES, "max_results": max_results},
        )
        props = node.get("props") or {}
        return {
            "id": eid,
            "name": node.get("name") or "",
            "type": node.get("entity_type") or "",
            "as_of": when.isoformat(),
            "first_seen": min(firsts) if firsts else None,
            "last_seen": max(lasts) if lasts else None,
            "evidence": {
                "documents": documents["count"],
                "signals": signals["count"],
                "relationship_assertions": assertions["count"],
            },
            "standing_signals": standing,
            "attributes": {k: v for k, v in props.items() if k not in _IDENTITY_KEYS},
            "attributes_are_current": True,
        }

    # ------------------------------------------------------------------
    # relationships_at
    # ------------------------------------------------------------------

    async def relationships_at(
        self, entity_id: str, at: Any, include_co_mentions: bool = True
    ) -> list[dict[str, Any]]:
        """Relationships supported by evidence dated at or before ``at``.

        One entry per ``(type, other entity, direction)``; ``assertions``
        counts the pieces of evidence behind it. Co-mentions (entities named
        in the same document) are derived and returned as ``co_mentioned``.
        """
        when = _require_time(at, "timestamp")
        node = await self._resolve(entity_id)
        if node is None:
            return []
        eid = node["id"]
        params = {"id": eid, "at": when, "derived": _DERIVED_EDGE}

        out: list[dict[str, Any]] = []
        for direction, query in (("outgoing", _TYPED_OUT), ("incoming", _TYPED_IN)):
            for row in await self._read(query, params):
                out.append(_relationship(row, eid, direction, derived=False))
        if include_co_mentions:
            for row in await self._read(_CO_MENTIONED, {"id": eid, "at": when}):
                out.append(_relationship(row, eid, "undirected", derived=True))
        return out

    async def undated_relationships(self, entity_id: str) -> int:
        """Relationship edges with no event time (left out of every
        point-in-time answer)."""
        node = await self._resolve(entity_id)
        if node is None:
            return 0
        rows = await self._read(_UNDATED, {"id": node["id"], "derived": _DERIVED_EDGE})
        return int(rows[0]["n"]) if rows else 0

    # ------------------------------------------------------------------
    # what_changed
    # ------------------------------------------------------------------

    async def what_changed(self, entity_id: str, since: Any) -> dict[str, Any]:
        """Everything learned about an entity since ``since``."""
        result = await self.what_changed_between(entity_id, since, datetime.now(UTC))
        result["since"] = result.pop("start")
        result["now"] = result.pop("end")
        return result

    async def what_changed_between(
        self, entity_id: str, start: Any, end: Any
    ) -> dict[str, Any]:
        """Evidence about an entity with event time in ``(start, end]``.

        ``changes`` lists, in event order: documents that mention the entity,
        signals about it, signals that stopped being current, relationships
        first asserted, and entities first mentioned alongside it.
        ``recorded_late`` lists evidence about a time at or before ``start``
        that was only ingested inside the window — backfilled content.
        """
        begin = _require_time(start, "start")
        finish = _require_time(end, "end")
        base = {"entity_id": entity_id, "start": begin.isoformat(), "end": finish.isoformat()}
        if finish < begin:
            return {**base, "changes": [], "error": "end is before start"}

        node = await self._resolve(entity_id)
        if node is None:
            return {**base, "changes": [], "error": "Entity not found"}
        eid = node["id"]
        base["entity_id"] = eid
        window = {"id": eid, "start": begin, "end": finish}
        signal_window = {**window, "signal_edges": _SIGNAL_EDGES}

        changes: list[dict[str, Any]] = []
        for row in await self._read(_DOCUMENTS_IN_WINDOW, window):
            changes.append({"kind": "document", "at": row["occurred_at"], **row})
        for row in await self._read(_SIGNALS_IN_WINDOW, signal_window):
            changes.append({"kind": "signal", "at": row["occurred_at"], **row})
        for row in await self._read(_SIGNALS_CLOSED_IN_WINDOW, signal_window):
            changes.append({"kind": "signal_superseded", "at": row["valid_to"], **row})
        for row in await self._read(
            _RELATIONSHIPS_IN_WINDOW, {**window, "derived": _DERIVED_EDGE}
        ):
            changes.append(
                {
                    "kind": "relationship",
                    "at": row["first"],
                    "relationship_type": (row["rel_type"] or "").lower(),
                    "other_id": row["other_id"],
                    "other_name": row["other_name"],
                    "sources": [s for s in row.get("sources") or [] if s],
                }
            )
        for row in await self._read(_CO_MENTIONED_IN_WINDOW, window):
            changes.append({"kind": "co_mentioned", "at": row["first"], **row})
        changes.sort(key=lambda c: str(c.get("at") or ""))

        before = await self.entity_at(eid, begin)
        recorded_late = await self._read(_RECORDED_LATE, window)
        counts: dict[str, int] = {}
        for change in changes:
            counts[change["kind"]] = counts.get(change["kind"], 0) + 1
        result: dict[str, Any] = {
            **base,
            "changes": changes,
            "counts": counts,
            "recorded_late": recorded_late,
        }
        if before is None:
            result["first_known_in_window"] = bool(changes)
        return result

    # ------------------------------------------------------------------
    # graph_as_of / temporal_blast_radius
    # ------------------------------------------------------------------

    async def _traverse(
        self, entity_id: str, at: Any, max_depth: int, include_co_mentions: bool
    ) -> tuple[list[dict], list[dict], dict[str, int], datetime]:
        when = _require_time(at, "timestamp")
        root = await self.entity_at(entity_id, when)
        if root is None:
            return [], [], {}, when

        root_id = root["id"]
        nodes = [root]
        edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, str]] = set()
        depth_map = {root_id: 0}
        known: dict[str, dict[str, Any] | None] = {root_id: root}
        queue: deque[tuple[str, int]] = deque([(root_id, 0)])

        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for rel in await self.relationships_at(current, when, include_co_mentions):
                other = rel["other_id"]
                if other not in known:
                    known[other] = await self.entity_at(other, when, max_results=5)
                state = known[other]
                if state is None:
                    continue  # nothing dated by then mentions it: no node, no edge
                source, target = rel["source_id"], rel["target_id"]
                key = (
                    (min(source, target), max(source, target), rel["relationship_type"])
                    if rel["derived"]
                    else (source, target, rel["relationship_type"])
                )
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append(
                        {
                            "source": source,
                            "target": target,
                            "relationship_type": rel["relationship_type"],
                            "properties": {
                                "first_asserted": rel["valid_from"],
                                "last_asserted": rel["last_asserted"],
                                "assertions": rel["assertions"],
                                "derived": rel["derived"],
                            },
                        }
                    )
                if other in depth_map:
                    continue
                depth_map[other] = depth + 1
                nodes.append(state)
                queue.append((other, depth + 1))
        return nodes, edges, depth_map, when

    async def graph_as_of(
        self,
        entity_id: str,
        timestamp: Any,
        depth: int = 2,
        include_co_mentions: bool = True,
    ) -> dict[str, Any]:
        """The subgraph around an entity as evidence supported it at ``timestamp``."""
        nodes, edges, _, when = await self._traverse(
            entity_id, timestamp, depth, include_co_mentions
        )
        return {"nodes": nodes, "edges": edges, "timestamp": when.isoformat(), "depth": depth}

    async def temporal_blast_radius(
        self, entity_id: str, at_time: Any, max_depth: int = 3
    ) -> dict[str, Any]:
        """Entities reachable from ``entity_id`` through relationships that
        were asserted by ``at_time``, with their hop distance. Co-mentions are
        not followed: they say two entities appeared together, not that one
        affects the other."""
        nodes, edges, depth_map, when = await self._traverse(
            entity_id, at_time, max_depth, include_co_mentions=False
        )
        if not nodes:
            return {"nodes": [], "edges": [], "depth_map": {}}
        return {
            "nodes": nodes,
            "edges": edges,
            "depth_map": depth_map,
            "at_time": when.isoformat(),
            "max_depth": max_depth,
        }

    # ------------------------------------------------------------------
    # provenance
    # ------------------------------------------------------------------

    async def provenance(self, entity_id: str) -> dict[str, Any]:
        """Every piece of evidence about an entity, in event order."""
        node = await self._resolve(entity_id)
        if node is None:
            return {"entity_id": entity_id, "history": []}
        rows = await self._read(_PROVENANCE, {"id": node["id"], "signal_edges": _SIGNAL_EDGES})
        history = [
            {
                "source": r.get("source") or "",
                "title": r.get("title") or "",
                "action": r.get("action") or "unknown",
                "timestamp": r.get("occurred_at") or "",
                "recorded_at": r.get("recorded_at") or "",
                "time_source": r.get("time_source") or "",
            }
            for r in rows
        ]
        return {"entity_id": node["id"], "history": history}

    # ------------------------------------------------------------------
    # find_contradictions
    # ------------------------------------------------------------------

    async def find_contradictions(
        self,
        entity_id: str,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> dict[str, Any]:
        """Detect signals that conflict with prior signals for the same entity.

        Sources contradictions from the semantic conflict layer (S4-1/S4-3):
        - Pending semantic candidates: metadata.conflict_candidates entries with
          status == "pending", flagged as status "candidate".
        - Confirmed conflicts: metadata.conflicts_with IDs, flagged "confirmed".
          Deduped so a (a, b) pair appears exactly once.

        Only LLM-verified conflicts are surfaced.

        Window filtering (date_from / date_to):
        - Applied to the signals' event time in the graph query.
        - Also applied to candidates: a candidate whose proposed_at falls outside
          the window is excluded.  Falls back to the signal's event time
          when proposed_at is absent.
        """
        window_from = to_utc(date_from)
        window_to = to_utc(date_to)
        graph_rows = await self._read(
            _CONTRADICTION_SIGNALS,
            {
                "id": entity_id,
                "signal_edges": _SIGNAL_EDGES,
                "date_from": window_from,
                "date_to": window_to,
            },
        )

        # Build a fast index: signal_id → graph row (for timestamps / types)
        graph_index: dict[str, dict[str, Any]] = {
            r["signal_id"]: r for r in graph_rows if r.get("signal_id")
        }

        contradictions: list[dict[str, Any]] = []
        confirmed_seen: set[frozenset[str]] = set()

        for signal_id in list(graph_index):
            result = signal_store.find_signal_by_id(signal_id)
            if result is None:
                continue
            signal, _container = result
            meta: dict[str, Any] = signal.metadata or {}
            graph_row = graph_index[signal_id]

            # Pending candidates → status "candidate"
            for cand in meta.get("conflict_candidates", []):
                if cand.get("status") != "pending":
                    continue

                # Window filter on proposed_at (fall back to signal timestamp)
                proposed_at_str: str = cand.get("proposed_at") or graph_row.get("timestamp", "")
                if not _in_window(proposed_at_str, window_from, window_to):
                    continue

                other_id: str = cand.get("other_signal_id", "")
                contradictions.append({
                    "signal_a": signal_id,
                    "signal_b": other_id,
                    "type": graph_row.get("type", "unknown"),
                    "reason": cand.get("rationale", ""),
                    "timestamp_a": graph_row.get("timestamp", ""),
                    "timestamp_b": proposed_at_str,
                    "status": "candidate",
                    "confidence": cand.get("confidence"),
                    "speakers": cand.get("speakers", []),
                })

            # Confirmed conflicts → status "confirmed", deduped
            for other_id in meta.get("conflicts_with", []):
                pair_key = frozenset({signal_id, str(other_id)})
                if pair_key in confirmed_seen:
                    continue
                confirmed_seen.add(pair_key)

                # Fetch the other signal's timestamp from graph or store
                other_ts = _resolve_signal_timestamp(str(other_id), graph_index, signal_store)

                # Canonical ordering: smaller id → signal_a
                id_a, id_b = (
                    (signal_id, str(other_id))
                    if signal_id <= str(other_id)
                    else (str(other_id), signal_id)
                )
                ts_a = graph_row.get("timestamp", "") if id_a == signal_id else other_ts
                ts_b = other_ts if id_b == str(other_id) else graph_row.get("timestamp", "")

                contradictions.append({
                    "signal_a": id_a,
                    "signal_b": id_b,
                    "type": graph_row.get("type", "unknown"),
                    "reason": "Confirmed semantic conflict",
                    "timestamp_a": ts_a,
                    "timestamp_b": ts_b,
                    "status": "confirmed",
                    "confidence": None,
                    "speakers": [],
                })

        return {
            "entity_id": entity_id,
            "signals_analyzed": len(graph_rows),
            "contradictions": contradictions,
        }


# ===========================================================================
# Private helpers
# ===========================================================================


def _relationship(
    row: dict[str, Any], entity_id: str, direction: str, *, derived: bool
) -> dict[str, Any]:
    """One relationship entry. ``valid_from`` is the first assertion."""
    other = row["other_id"]
    incoming = direction == "incoming"
    return {
        "relationship_type": "co_mentioned" if derived else (row["rel_type"] or "").lower(),
        "direction": direction,
        "source_id": other if incoming else entity_id,
        "target_id": entity_id if incoming else other,
        "other_id": other,
        "other_name": row.get("other_name") or "",
        "other_type": row.get("other_type") or "",
        "assertions": int(row.get("assertions") or row.get("shared_documents") or 0),
        "valid_from": row.get("first"),
        "valid_to": None,
        "last_asserted": row.get("last"),
        "sources": [s for s in row.get("sources") or [] if s],
        "time_sources": [s for s in row.get("time_sources") or [] if s],
        "derived": derived,
    }


def _in_window(
    ts_str: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> bool:
    """Return True if ts_str falls within [date_from, date_to].

    Absent window bounds are treated as open (unbounded).  An unparseable
    timestamp passes through (conservative: include rather than exclude).
    """
    if not date_from and not date_to:
        return True
    if not ts_str:
        return True  # conservative: include when timestamp is unknown
    ts = to_utc(ts_str)
    if ts is None:
        return True  # conservative: include on parse error
    if date_from and ts < date_from:
        return False
    if date_to and ts > date_to:
        return False
    return True


def _resolve_signal_timestamp(
    signal_id: str,
    graph_index: dict[str, dict[str, Any]],
    store: Any,
) -> str:
    """Return the best available timestamp for a signal.

    Checks the graph index first (cheap), then the signal store (I/O).
    Returns empty string if nothing is found.
    """
    row = graph_index.get(signal_id)
    if row and row.get("timestamp"):
        return row["timestamp"]
    result = store.find_signal_by_id(signal_id)
    if result is not None:
        sig, _ = result
        return getattr(sig, "source_timestamp", None) or ""
    return ""
