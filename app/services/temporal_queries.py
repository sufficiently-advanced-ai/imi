"""
Temporal Query Service — Higher-order temporal queries for Issue #864.

Builds on SemanticaKnowledge temporal methods (get_state_at, get_active_relationships)
to provide composite temporal intelligence:

- what_changed: Diff entity state between a past timestamp and now
- what_changed_between: Diff entity state between two arbitrary timestamps
- graph_as_of: Reconstruct subgraph around entity at a past point in time
- find_contradictions: Detect signals that conflict with prior signals for same entity
- temporal_blast_radius: BFS traversal filtered to relationships active at a specific time
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Signal store is imported here so callers can patch
# ``app.services.temporal_queries.signal_store`` in tests.
# The proxy resolves to the correct tenant-scoped store at call time.
from app.services.signal_store import signal_store  # noqa: E402


class TemporalQueryService:
    """Higher-order temporal queries built on SemanticaKnowledge."""

    def __init__(self, semantica_knowledge: Any):
        self.sk = semantica_knowledge

    # ------------------------------------------------------------------
    # what_changed
    # ------------------------------------------------------------------

    async def what_changed(
        self,
        entity_id: str,
        since: datetime,
    ) -> dict[str, Any]:
        """Diff entity state between `since` and now.

        Returns a list of field-level changes (added, removed, modified).
        If the entity didn't exist at `since`, flags it as created_after_since.
        """
        now = datetime.now(UTC)

        state_then = await self.sk.get_state_at(entity_id, since)
        state_now = await self.sk.get_state_at(entity_id, now)

        if state_then is None and state_now is None:
            return {
                "entity_id": entity_id,
                "since": since.isoformat(),
                "changes": [],
                "error": "Entity not found at either timestamp",
            }

        if state_then is None:
            return {
                "entity_id": entity_id,
                "since": since.isoformat(),
                "now": now.isoformat(),
                "created_after_since": True,
                "current_state": state_now,
                "changes": [],
            }

        if state_now is None:
            return {
                "entity_id": entity_id,
                "since": since.isoformat(),
                "now": now.isoformat(),
                "deleted_since": True,
                "previous_state": state_then,
                "changes": [],
            }

        changes = _diff_states(state_then, state_now)
        return {
            "entity_id": entity_id,
            "since": since.isoformat(),
            "now": now.isoformat(),
            "changes": changes,
        }

    # ------------------------------------------------------------------
    # what_changed_between
    # ------------------------------------------------------------------

    async def what_changed_between(
        self,
        entity_id: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        """Diff entity state between two arbitrary timestamps."""
        state_start = await self.sk.get_state_at(entity_id, start)
        state_end = await self.sk.get_state_at(entity_id, end)

        if state_start is None and state_end is None:
            return {
                "entity_id": entity_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "changes": [],
                "error": "Entity not found at either timestamp",
            }

        if state_start is None:
            return {
                "entity_id": entity_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "created_between": True,
                "current_state": state_end,
                "changes": [],
            }

        if state_end is None:
            return {
                "entity_id": entity_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "deleted_between": True,
                "previous_state": state_start,
                "changes": [],
            }

        changes = _diff_states(state_start, state_end)
        return {
            "entity_id": entity_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "changes": changes,
        }

    # ------------------------------------------------------------------
    # graph_as_of
    # ------------------------------------------------------------------

    async def graph_as_of(
        self,
        entity_id: str,
        timestamp: datetime,
        depth: int = 2,
    ) -> dict[str, Any]:
        """Reconstruct subgraph around entity at a past point in time.

        Uses BFS to traverse active relationships up to `depth` hops.
        """
        root_state = await self.sk.get_state_at(entity_id, timestamp)
        if root_state is None:
            return {"nodes": [], "edges": [], "timestamp": timestamp.isoformat()}

        # Use resolved ID from the state, not the raw lookup string
        resolved_id = root_state.get("id", entity_id)
        nodes = [root_state]
        edges: list[dict[str, Any]] = []
        seen_edges: set[tuple[str, str, str]] = set()
        visited = {resolved_id}
        queue: deque[tuple[str, int]] = deque([(resolved_id, 0)])

        while queue:
            current_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue

            rels = await self.sk.get_active_relationships(current_id, timestamp)
            for rel in rels:
                neighbor_id, edge_source, edge_target = _resolve_neighbor(
                    rel, current_id
                )
                if not neighbor_id:
                    continue

                # Deduplicate edges (same relationship seen from both sides)
                edge_key = (edge_source, edge_target, rel.get("relationship_type", ""))
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source": edge_source,
                        "target": edge_target,
                        "relationship_type": rel.get("relationship_type", ""),
                        "properties": rel.get("properties", {}),
                    })

                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    target_state = await self.sk.get_state_at(neighbor_id, timestamp)
                    if target_state:
                        nodes.append(target_state)
                        queue.append((neighbor_id, current_depth + 1))

        return {
            "nodes": nodes,
            "edges": edges,
            "timestamp": timestamp.isoformat(),
            "depth": depth,
        }

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

        The keyword/sentiment heuristic (_detect_contradictions) has been removed
        (P3 exit — see S4-4).  Only LLM-verified conflicts are surfaced.

        Window filtering (date_from / date_to):
        - Applied by the Cypher query to restrict which signals are fetched.
        - Also applied to candidates: a candidate whose proposed_at falls outside
          the window is excluded.  Falls back to the signal's source_timestamp
          when proposed_at is absent.
        """
        # ------------------------------------------------------------------
        # 1. Fetch signal IDs from the graph.
        #    Signals link to entities via REFERENCES_* (Semantica ingest path)
        #    OR MENTIONS (SignalGraphWriter path) — live signal data uses
        #    MENTIONS, so matching only REFERENCES_* returns nothing there.
        # ------------------------------------------------------------------
        cypher = (
            "MATCH (s:Signal)-[r]->(e:Entity {id: $entity_id}) "
            "WHERE (type(r) STARTS WITH 'REFERENCES_' OR type(r) = 'MENTIONS') "
        )
        params: dict[str, Any] = {"entity_id": entity_id}

        # _ingest_signals() stores created_at and signal_type on signal nodes;
        # use coalesce for backward compatibility with any older data.
        if date_from:
            cypher += "AND coalesce(s.created_at, s.timestamp) >= $date_from "
            params["date_from"] = date_from.isoformat()
        if date_to:
            cypher += "AND coalesce(s.created_at, s.timestamp) <= $date_to "
            params["date_to"] = date_to.isoformat()

        cypher += (
            "RETURN s.id AS signal_id, s.content AS content, "
            "coalesce(s.created_at, s.timestamp) AS timestamp, "
            "coalesce(s.signal_type, s.type) AS type "
            "ORDER BY coalesce(s.created_at, s.timestamp) ASC"
        )

        raw = self.sk.graph_store.execute_query(cypher, params)
        graph_rows = raw.get("records", []) if isinstance(raw, dict) else (raw or [])

        # Build a fast index: signal_id → graph row (for timestamps / types)
        graph_index: dict[str, dict[str, Any]] = {
            r["signal_id"]: r for r in graph_rows if r.get("signal_id")
        }

        # ------------------------------------------------------------------
        # 2. Resolve each signal's full metadata from the JSON store
        # ------------------------------------------------------------------
        contradictions: list[dict[str, Any]] = []
        confirmed_seen: set[frozenset[str]] = set()

        for signal_id in list(graph_index):
            result = signal_store.find_signal_by_id(signal_id)
            if result is None:
                continue
            signal, _container = result
            meta: dict[str, Any] = signal.metadata or {}
            graph_row = graph_index[signal_id]

            # --------------------------------------------------------------
            # 2a. Pending candidates → status "candidate"
            # --------------------------------------------------------------
            for cand in meta.get("conflict_candidates", []):
                if cand.get("status") != "pending":
                    continue

                # Window filter on proposed_at (fall back to signal timestamp)
                proposed_at_str: str = cand.get("proposed_at") or graph_row.get("timestamp", "")
                if not _in_window(proposed_at_str, date_from, date_to):
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

            # --------------------------------------------------------------
            # 2b. Confirmed conflicts → status "confirmed", deduped
            # --------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # temporal_blast_radius
    # ------------------------------------------------------------------

    async def temporal_blast_radius(
        self,
        entity_id: str,
        at_time: datetime,
        max_depth: int = 3,
    ) -> dict[str, Any]:
        """BFS traversal filtered to relationships active at a specific time.

        Returns all entities reachable from entity_id through relationships
        that were active at at_time, up to max_depth hops.
        """
        root_state = await self.sk.get_state_at(entity_id, at_time)
        if root_state is None:
            return {"nodes": [], "edges": [], "depth_map": {}}

        # Use resolved ID from the state, not the raw lookup string
        resolved_id = root_state.get("id", entity_id)
        nodes = [root_state]
        edges: list[dict[str, Any]] = []
        depth_map: dict[str, int] = {resolved_id: 0}
        visited = {resolved_id}
        queue: deque[tuple[str, int]] = deque([(resolved_id, 0)])

        while queue:
            current_id, current_depth = queue.popleft()
            if current_depth >= max_depth:
                continue

            rels = await self.sk.get_active_relationships(current_id, at_time)
            for rel in rels:
                neighbor_id, edge_source, edge_target = _resolve_neighbor(
                    rel, current_id
                )
                if not neighbor_id or neighbor_id in visited:
                    continue

                visited.add(neighbor_id)
                edges.append({
                    "source": edge_source,
                    "target": edge_target,
                    "relationship_type": rel.get("relationship_type", ""),
                    "properties": rel.get("properties", {}),
                })

                neighbor_state = await self.sk.get_state_at(neighbor_id, at_time)
                if neighbor_state:
                    nodes.append(neighbor_state)
                    depth_map[neighbor_id] = current_depth + 1
                    queue.append((neighbor_id, current_depth + 1))

        return {
            "nodes": nodes,
            "edges": edges,
            "depth_map": depth_map,
            "at_time": at_time.isoformat(),
            "max_depth": max_depth,
        }


# ===========================================================================
# Private helpers
# ===========================================================================


def _resolve_neighbor(
    rel: dict[str, Any],
    current_id: str,
) -> tuple[str, str, str]:
    """Extract the neighbor ID and edge direction from a relationship.

    Returns (neighbor_id, edge_source, edge_target).
    For incoming relationships, the neighbor is the source; for outgoing, the target.
    """
    if rel.get("direction") == "incoming":
        neighbor_id = rel.get("source_id", "")
        return neighbor_id, neighbor_id, current_id
    else:
        neighbor_id = rel.get("target_id", "")
        return neighbor_id, current_id, neighbor_id


def _diff_states(
    old: dict[str, Any],
    new: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare two entity state dicts and return a list of changes.

    Compares top-level fields and nested attribute dicts.
    Skips metadata fields (as_of, valid_from, valid_to).
    """
    changes: list[dict[str, Any]] = []
    skip_fields = {"as_of", "valid_from", "valid_to"}

    # Compare top-level fields
    all_keys = set(old.keys()) | set(new.keys())
    for key in all_keys:
        if key in skip_fields:
            continue
        if key == "attributes":
            # Deep compare attributes
            old_attrs = old.get("attributes", {}) or {}
            new_attrs = new.get("attributes", {}) or {}
            attr_keys = set(old_attrs.keys()) | set(new_attrs.keys())
            for ak in attr_keys:
                old_val = old_attrs.get(ak)
                new_val = new_attrs.get(ak)
                if old_val != new_val:
                    changes.append({
                        "field": f"attributes.{ak}",
                        "old": old_val,
                        "new": new_val,
                    })
        else:
            old_val = old.get(key)
            new_val = new.get(key)
            if old_val != new_val:
                changes.append({
                    "field": key,
                    "old": old_val,
                    "new": new_val,
                })

    return changes


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
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if date_from and ts < date_from:
            return False
        if date_to and ts > date_to:
            return False
        return True
    except (ValueError, AttributeError):
        return True  # conservative: include on parse error


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
