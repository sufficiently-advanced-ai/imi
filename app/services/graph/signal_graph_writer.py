"""
Signal Graph Writer — Write Signal nodes and entity relationships to Neo4j.

Creates :Signal nodes and links them to :Entity nodes via :MENTIONS and
:ASSIGNED_TO relationships. Signals are system-level objects (not domain
entities) that bridge meeting intelligence with the knowledge graph.
"""

import logging
from typing import Any

from app.core.middleware.request_context import ambient_tenant_id
from app.models.signal import MeetingSignals, Signal
from app.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

# Cypher for upserting a Signal node
_UPSERT_SIGNAL = """
MERGE (s:Signal {id: $id})
SET s.signal_type = $signal_type,
    s.content = $content,
    s.source_meeting_id = $source_meeting_id,
    s.source_meeting_title = $source_meeting_title,
    s.source_timestamp = $source_timestamp,
    s.status = $status,
    s.position = $position,
    s.created_at = $created_at,
    s.owner_name = $owner_name,
    s.confidence = $confidence,
    s.client_id = $client_id,
    s.provenance_status = $provenance_status,
    s.review_status = $review_status,
    s.can_use_as_evidence = $can_use_as_evidence,
    s.can_use_as_instruction = $can_use_as_instruction,
    s.tenant_id = $tenant_id,
    s.valid_from = $valid_from,
    s.valid_to = $valid_to
"""

# Cypher for MENTIONS relationship (Signal -> Entity)
_UPSERT_MENTIONS = """
MATCH (s:Signal {id: $signal_id})
MATCH (e:Entity {id: $entity_id})
MERGE (s)-[r:MENTIONS]->(e)
SET r.entity_role = $role
"""

# Cypher for ASSIGNED_TO relationship (Signal -> Entity, action item owners)
_UPSERT_ASSIGNED_TO = """
MATCH (s:Signal {id: $signal_id})
MATCH (e:Entity {id: $owner_id})
MERGE (s)-[r:ASSIGNED_TO]->(e)
"""

# Cypher for FOR_CLIENT relationship (Signal -> Client entity)
_UPSERT_FOR_CLIENT = """
MATCH (s:Signal {id: $signal_id})
MATCH (c:Entity {id: $client_id})
MERGE (s)-[:FOR_CLIENT]->(c)
"""


# Cypher for updating specific properties on a Signal node
_UPDATE_SIGNAL_PROPS = """
MATCH (s:Signal {id: $id})
SET s += $props
RETURN s.id AS id
"""

# Cypher for deleting a Signal node and all its relationships
_DELETE_SIGNAL = """
MATCH (s:Signal {id: $id})
DETACH DELETE s
RETURN count(*) AS deleted
"""

# Cypher for upserting a SUPERSEDES edge between two Signal nodes
# Direction: (new)-[:SUPERSEDES]->(old) — the new signal supersedes the old one.
# MERGE makes it idempotent (safe to call multiple times / during backfill).
_UPSERT_SUPERSEDES = """
MATCH (new:Signal {id: $new_id})
MATCH (old:Signal {id: $old_id})
MERGE (new)-[r:SUPERSEDES]->(old)
ON CREATE SET r.superseded_at = $superseded_at, r.actor = $actor, r.tenant_id = $tenant_id
ON MATCH SET
  r.superseded_at = coalesce(r.superseded_at, $superseded_at),
  r.actor = coalesce(r.actor, $actor),
  r.tenant_id = coalesce(r.tenant_id, $tenant_id)
RETURN old.id AS id
"""

# Cypher for upserting a CONFLICTS_WITH edge between two Signal nodes.
#
# Direction is CANONICAL: (a)-[:CONFLICTS_WITH]->(b) where a_id < b_id
# lexicographically. This ensures that regardless of which order the caller
# passes the two IDs, there is only ever ONE edge in the graph (undirected
# semantics via deterministic canonical direction). The caller is responsible
# for sorting before calling (write_conflicts_with_edge does this automatically).
#
# MERGE makes it idempotent (safe to call multiple times / during backfill).
_UPSERT_CONFLICTS_WITH = """
MATCH (a:Signal {id: $a_id})
MATCH (b:Signal {id: $b_id})
MERGE (a)-[r:CONFLICTS_WITH]->(b)
ON CREATE SET r.confirmed_at = $confirmed_at, r.actor = $actor, r.tenant_id = $tenant_id
ON MATCH SET
  r.confirmed_at = coalesce(r.confirmed_at, $confirmed_at),
  r.actor = coalesce(r.actor, $actor),
  r.tenant_id = coalesce(r.tenant_id, $tenant_id)
RETURN b.id AS id
"""


class SignalGraphWriter:
    """Write Signal nodes and their entity relationships to Neo4j."""

    def __init__(self, neo4j_client: Neo4jClient):
        self._client = neo4j_client

    async def write_meeting_signals(self, meeting_signals: MeetingSignals) -> int:
        """Write all signals from a meeting to Neo4j.

        Creates:
        - (:Signal) node per signal
        - (Signal)-[:MENTIONS]->(Entity) per entity ref
        - (Signal)-[:ASSIGNED_TO]->(Entity) for action item owners
        - (Signal)-[:FOR_CLIENT]->(Entity) for client-scoped signals (best-effort)

        Args:
            meeting_signals: Container with all signals from a meeting

        Returns:
            Number of signal nodes written
        """
        if not meeting_signals.signals:
            return 0

        written = 0
        for signal in meeting_signals.signals:
            try:
                await self._write_signal_node(signal)
                await self._write_entity_relationships(signal)
                written += 1
            except Exception as e:
                logger.warning(
                    "[SIGNAL_GRAPH] Failed to write signal %s: %s", signal.id, e
                )

        logger.info(
            "[SIGNAL_GRAPH] Wrote %d/%d signal nodes for meeting %s",
            written,
            len(meeting_signals.signals),
            meeting_signals.bot_id,
        )
        return written

    async def _write_signal_node(self, signal: Signal) -> None:
        """Create or update a Signal node in Neo4j."""
        params: dict[str, Any] = {
            "id": signal.id,
            "signal_type": signal.type,
            "content": signal.content,
            "source_meeting_id": signal.source_meeting_id,
            "source_meeting_title": signal.source_meeting_title or "",
            "source_timestamp": signal.source_timestamp,
            "status": signal.status or "",
            "position": signal.position,
            "created_at": signal.created_at,
            "owner_name": signal.owner.name if signal.owner else "",
            "confidence": signal.confidence,
            "client_id": signal.client_id,
            # G2/G3 wiring: governance / trust axis (memory-governance PRD §4-6)
            "provenance_status": signal.provenance_status,
            "review_status": signal.review_status,
            "can_use_as_evidence": signal.can_use_as_evidence,
            "can_use_as_instruction": signal.can_use_as_instruction,
            # Issue #953 / spec §5.2: the graph write layer is the central
            # scoping chokepoint — fall back to the ambient tenant so no
            # Signal node lands unscoped in a multi-tenant deployment.
            "tenant_id": (
                signal.tenant_id
                if signal.tenant_id is not None
                else ambient_tenant_id()
            ),
            # R1.1 — validity window (valid_from defaults to source_timestamp
            # via model_validator; valid_to is set on supersession only).
            "valid_from": signal.valid_from,
            "valid_to": signal.valid_to,
        }
        await self._client.execute_write(_UPSERT_SIGNAL, params)

    async def _write_entity_relationships(self, signal: Signal) -> None:
        """Create MENTIONS and ASSIGNED_TO relationships for a signal."""
        # MENTIONS relationships for all entity refs
        for ref in signal.entities:
            try:
                await self._client.execute_write(
                    _UPSERT_MENTIONS,
                    {
                        "signal_id": signal.id,
                        "entity_id": ref.id,
                        "role": "subject",
                    },
                )
            except Exception as e:
                # Entity might not exist in graph yet — skip silently
                logger.debug(
                    "[SIGNAL_GRAPH] Could not link signal %s -> entity %s: %s",
                    signal.id,
                    ref.id,
                    e,
                )

        # ASSIGNED_TO relationship for action item owners
        if signal.owner:
            try:
                await self._client.execute_write(
                    _UPSERT_ASSIGNED_TO,
                    {
                        "signal_id": signal.id,
                        "owner_id": signal.owner.id,
                    },
                )
            except Exception as e:
                logger.debug(
                    "[SIGNAL_GRAPH] Could not link signal %s -> owner %s: %s",
                    signal.id,
                    signal.owner.id,
                    e,
                )

        # FOR_CLIENT relationship — scopes the signal to a client entity
        if signal.client_id:
            try:
                await self._client.execute_write(
                    _UPSERT_FOR_CLIENT,
                    {"signal_id": signal.id, "client_id": signal.client_id},
                )
            except Exception as e:
                logger.debug(
                    "[SIGNAL_GRAPH] Could not link signal %s -> client %s: %s",
                    signal.id,
                    signal.client_id,
                    e,
                )

    # ------------------------------------------------------------------
    # Mutation helpers (best-effort — failures logged, not raised)
    # ------------------------------------------------------------------

    async def update_signal_properties(
        self,
        signal_id: str,
        *,
        status: str | None = None,
        content: str | None = None,
        owner_name: str | None = None,
        # G2/G3 wiring: governance / trust axis params (optional — only set on review)
        review_status: str | None = None,
        provenance_status: str | None = None,
        can_use_as_evidence: bool | None = None,
        can_use_as_instruction: bool | None = None,
        tenant_id: str | None = None,
        # R1.1 — validity window; valid_to is set by supersede only (ADR-002)
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> bool:
        """Update selected properties on an existing Signal node in Neo4j.

        Supports both plain field updates (status, content, owner_name) and
        governance transition updates (review_status, provenance_status,
        can_use_as_evidence, can_use_as_instruction). All params are optional;
        only non-None values are written to the node.

        Returns True on success, False if the node was not found or on error.
        """
        props: dict[str, Any] = {}
        if status is not None:
            props["status"] = status
        if content is not None:
            props["content"] = content
        if owner_name is not None:
            props["owner_name"] = owner_name
        # Governance fields (G2 wiring)
        if review_status is not None:
            props["review_status"] = review_status
        if provenance_status is not None:
            props["provenance_status"] = provenance_status
        if can_use_as_evidence is not None:
            props["can_use_as_evidence"] = can_use_as_evidence
        if can_use_as_instruction is not None:
            props["can_use_as_instruction"] = can_use_as_instruction
        if tenant_id is not None:
            props["tenant_id"] = tenant_id
        # R1.1 validity window fields (valid_to only set by supersede — ADR-002)
        if valid_from is not None:
            props["valid_from"] = valid_from
        if valid_to is not None:
            props["valid_to"] = valid_to

        if not props:
            return True  # Nothing to update

        try:
            result = await self._client.execute_write(
                _UPDATE_SIGNAL_PROPS, {"id": signal_id, "props": props}
            )
            found = bool(result and len(result) > 0)
            if found:
                logger.info(
                    "[SIGNAL_GRAPH] Updated signal %s: %s",
                    signal_id,
                    list(props.keys()),
                )
            else:
                logger.warning("[SIGNAL_GRAPH] Signal %s not found in Neo4j", signal_id)
            return found
        except Exception as e:
            logger.warning(
                "[SIGNAL_GRAPH] Failed to update signal %s: %s", signal_id, e
            )
            return False

    async def write_supersedes_edge(
        self,
        new_signal_id: str,
        old_signal_id: str,
        *,
        superseded_at: str,
        actor: str | None = None,
        tenant_id: str | None = None,
    ) -> bool:
        """Upsert a (new)-[:SUPERSEDES]->(old) edge between two Signal nodes.

        Best-effort: returns True iff both nodes existed and the edge was merged
        (i.e., the RETURN clause yielded a row); False otherwise. Never raises —
        logs a warning on error (matches the class's existing best-effort
        convention for mutation helpers).

        MERGE makes the operation idempotent, so repeated calls (e.g. during
        backfill) are safe.

        Args:
            new_signal_id: ID of the signal that supersedes the old one.
            old_signal_id: ID of the signal being superseded.
            superseded_at: ISO-8601 timestamp when supersession occurred (required).
            actor: Who performed the review action (optional, stored on edge).
            tenant_id: Tenant scope (optional, stored on edge for filtering).

        Returns:
            True if the edge was successfully created/updated, False otherwise.
        """
        try:
            result = await self._client.execute_write(
                _UPSERT_SUPERSEDES,
                {
                    "new_id": new_signal_id,
                    "old_id": old_signal_id,
                    "superseded_at": superseded_at,
                    "actor": actor,
                    "tenant_id": tenant_id,
                },
            )
            found = bool(result and len(result) > 0)
            if found:
                logger.info(
                    "[SIGNAL_GRAPH] SUPERSEDES edge written: %s → %s at %s",
                    new_signal_id,
                    old_signal_id,
                    superseded_at,
                )
            else:
                logger.warning(
                    "[SIGNAL_GRAPH] SUPERSEDES edge not written — one or both signals "
                    "not found: new=%s old=%s",
                    new_signal_id,
                    old_signal_id,
                )
            return found
        except Exception as e:
            logger.warning(
                "[SIGNAL_GRAPH] Failed to write SUPERSEDES edge %s → %s: %s",
                new_signal_id,
                old_signal_id,
                e,
            )
            return False

    async def write_conflicts_with_edge(
        self,
        signal_id_a: str,
        signal_id_b: str,
        *,
        confirmed_at: str,
        actor: str | None = None,
        tenant_id: str | None = None,
    ) -> bool:
        """Upsert a (a)-[:CONFLICTS_WITH]->(b) edge between two Signal nodes.

        CANONICAL DIRECTION: IDs are sorted lexicographically before writing so
        that (min_id)-[:CONFLICTS_WITH]->(max_id) is always the stored direction,
        regardless of the order the caller passes the arguments. This gives the
        edge undirected semantics via a deterministic canonical direction —
        exactly one edge per conflicting pair in the graph.

        Best-effort: returns True iff both nodes existed and the edge was merged;
        False otherwise. Never raises — logs a warning on error (matches the
        class's existing best-effort convention for mutation helpers).

        MERGE makes the operation idempotent, so repeated calls (e.g. during
        backfill) are safe.

        Args:
            signal_id_a: ID of one conflicting signal.
            signal_id_b: ID of the other conflicting signal.
            confirmed_at: ISO-8601 timestamp when the conflict was confirmed (required).
            actor: Who confirmed the conflict (optional, stored on edge).
            tenant_id: Tenant scope (optional, stored on edge for filtering).

        Returns:
            True if the edge was successfully created/updated, False otherwise.
        """
        # Canonicalize direction: sort IDs so the edge is deterministic
        a_id, b_id = sorted([signal_id_a, signal_id_b])
        # Mirror _write_signal_node's ambient-tenant fallback: when no tenant_id
        # is provided by the caller, fall back to the ambient tenant so no edge
        # lands unscoped in a multi-tenant deployment (issue #953 / spec §5.2).
        resolved_tenant_id = tenant_id if tenant_id is not None else ambient_tenant_id()
        try:
            result = await self._client.execute_write(
                _UPSERT_CONFLICTS_WITH,
                {
                    "a_id": a_id,
                    "b_id": b_id,
                    "confirmed_at": confirmed_at,
                    "actor": actor,
                    "tenant_id": resolved_tenant_id,
                },
            )
            found = bool(result and len(result) > 0)
            if found:
                logger.info(
                    "[SIGNAL_GRAPH] CONFLICTS_WITH edge written: %s <-> %s at %s",
                    a_id,
                    b_id,
                    confirmed_at,
                )
            else:
                logger.warning(
                    "[SIGNAL_GRAPH] CONFLICTS_WITH edge not written — one or both signals "
                    "not found: a=%s b=%s",
                    a_id,
                    b_id,
                )
            return found
        except Exception as e:
            logger.warning(
                "[SIGNAL_GRAPH] Failed to write CONFLICTS_WITH edge %s <-> %s: %s",
                a_id,
                b_id,
                e,
            )
            return False

    async def delete_signal_node(self, signal_id: str) -> bool:
        """Delete a Signal node and all its relationships from Neo4j.

        Returns True on success, False if not found or on error.
        """
        try:
            result = await self._client.execute_write(_DELETE_SIGNAL, {"id": signal_id})
            deleted = bool(
                result and len(result) > 0 and result[0].get("deleted", 0) > 0
            )
            if deleted:
                logger.info("[SIGNAL_GRAPH] Deleted signal node %s", signal_id)
            else:
                logger.warning(
                    "[SIGNAL_GRAPH] Signal %s not found in Neo4j for deletion",
                    signal_id,
                )
            return deleted
        except Exception as e:
            logger.warning(
                "[SIGNAL_GRAPH] Failed to delete signal %s: %s", signal_id, e
            )
            return False
