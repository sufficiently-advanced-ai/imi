"""
SemanticaKnowledge — Unified knowledge layer facade backed by Semantica.

This single class replaces ~16K LOC across 15+ files with a coherent API:
- Graph operations (replaces neo4j_graph.py, builder.py, query_handler.py)
- Search (replaces chat_tools search functions)
- Entity extraction (replaces domain_aware_entity_extractor, entity_registry)
- Decision intelligence (replaces signal_store decision parts, signal_promoter)
- Temporal queries (NEW capability from Semantica)
- Provenance tracking (NEW capability from Semantica)
- Visualization (replaces visualization_adapter)

All methods are async for consistency with the existing FastAPI codebase.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.model_schemas.domain_config import DomainConfiguration
from app.services.blocking import run_blocking
from app.services.semantica_config import (
    build_neo4j_schema_description,
    domain_entity_to_neo4j_properties,
    entity_type_to_label,
    get_entity_schema,
    get_entity_types,
    relationship_type_to_neo4j,
)
from app.services.semantica_decisions import SemanticaDecisions
from app.services.semantica_extraction import SemanticaExtraction
from app.services.semantica_search import SemanticaSearch
from app.services.semantica_visualization import SemanticaVisualizationAdapter

logger = logging.getLogger(__name__)

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_cypher_id(value: str) -> str:
    """Validate a string is safe for Cypher interpolation (alphanumeric + underscore)."""
    if not _SAFE_IDENTIFIER_RE.match(value):
        raise ValueError(f"Unsafe Cypher identifier rejected: {value!r}")
    return value


class SemanticaKnowledge:
    """Unified knowledge layer backed by Semantica.

    Provides a single facade for all knowledge operations:
    graph, search, extraction, decisions, visualization.

    Backward-compatible with the Neo4jKnowledgeGraph interface
    for smooth migration.
    """

    def __init__(
        self,
        graph_store: Any,
        vector_store: Any,
        embedding_generator: Any,
        context_graph: Any,
        ner_extractor: Any,
        duplicate_detector: Any,
        domain_config: DomainConfiguration | None = None,
    ):
        # Core Semantica components
        self.graph_store = graph_store
        self.vector_store = vector_store
        self.embedder = embedding_generator
        self.context_graph = context_graph

        # Domain config
        self.domain = domain_config

        # Sub-services
        self.search = SemanticaSearch(vector_store, embedding_generator, graph_store)
        self.extraction = SemanticaExtraction(ner_extractor, duplicate_detector, domain_config)
        self.decisions = SemanticaDecisions(context_graph)
        self.visualization = SemanticaVisualizationAdapter(graph_store, domain_config)

        # Backward-compatible caches (populated on build_graph)
        self.nodes: dict[str, Any] = {}
        self.edges: dict[tuple[str, str], Any] = {}
        self.semantic_edges: dict[tuple[str, str, str], Any] = {}
        self.document_entities: dict[str, set[str]] = {}
        self.entity_documents: dict[str, set[str]] = defaultdict(set)

        # Build state
        self.last_build: datetime | None = None
        self._git_ops = None

    @property
    def git_ops(self):
        if self._git_ops is None:
            from app.git_ops import git_ops
            self._git_ops = git_ops
        return self._git_ops

    # ──────────────────────────────────────────────────────────────
    # Blocking-call boundary
    # ──────────────────────────────────────────────────────────────

    async def _query(self, cypher: str, params: dict[str, Any] | None = None) -> Any:
        """Run a Semantica GraphStore query off the event loop.

        ``GraphStore.execute_query`` uses the synchronous Neo4j driver; calling
        it inline from a coroutine blocks the loop for the round trip. During
        a full build that was ~26k sequential stalls (9.5 min on LCARS) during
        which no request could be served.
        """
        if params is None:
            return await run_blocking(self.graph_store.execute_query, cypher)
        return await run_blocking(self.graph_store.execute_query, cypher, params)

    # ──────────────────────────────────────────────────────────────
    # Graph Operations
    # ──────────────────────────────────────────────────────────────

    async def ingest_file(self, file_path: str, content: str) -> bool:
        """Index the entity profile ``file_path`` into the vector store.

        The legacy graph owns every Neo4j write (the reconcile runs
        ``kg.ingest_files`` first); this only embeds the node(s) it wrote for
        the file, so ids, names and properties are the graph's own. Returns
        True when a node was indexed, False when the file is intentionally
        skipped (no frontmatter, unknown type, archived), and RAISES when the
        node is missing or indexing failed — callers that track retries must
        be able to tell a skip from a failure.
        """
        metadata = self._extract_metadata(content)
        if not metadata:
            return False
        if not self._resolve_entity_type(file_path, metadata):
            return False
        if metadata.get("is_archived"):
            return False
        rows = self._extract_records(
            await self._query(
                "MATCH (n:Entity) WHERE NOT n:Signal AND (n.source_file = $p OR n.file_path = $p) "
                "RETURN n",
                {"p": file_path},
            )
        )
        if not rows:
            raise RuntimeError(f"no graph node for {file_path}; nothing to index")
        for row in rows:
            node = row.get("n", {}) or {}
            if not await self._index_node(node):
                raise RuntimeError(f"vector indexing failed for {node.get('id')} ({file_path})")
        return True

    async def remove_entities_for_file(self, file_path: str) -> int:
        """Drop entity vectors for every entity whose profile was ``file_path``.

        The reconcile path's counterpart to ``ingest_file`` for deleted files.
        Graph nodes are left to the legacy graph (which demotes them to stubs);
        this only makes sure their vectors stop matching. Returns the count.
        """
        rows = self._extract_records(
            await self._query(
                "MATCH (n:Entity) WHERE n.file_path = $p OR n.source_file = $p RETURN n.id AS id",
                {"p": file_path},
            )
        )
        removed = 0
        for row in rows:
            entity_id = row.get("id")
            if not entity_id:
                continue
            await self.search.delete_entity_vector(entity_id)
            self.nodes.pop(entity_id, None)
            removed += 1
        return removed

    async def reindex_entities_from_graph(self) -> int:
        """Rebuild the entity vector index from Neo4j — no corpus read.

        Used on boot when the graph is populated but the (persistent) vector
        store has no entity vectors yet, e.g. the first start after enabling
        ``VECTOR_BACKEND=sqlite``. Returns the number of entities indexed.
        """
        rows = self._extract_records(
            await self._query("MATCH (n:Entity) WHERE NOT n:Signal RETURN n")
        )
        try:
            from app.config import settings

            throttle = float(getattr(settings, "VECTOR_BOOTSTRAP_THROTTLE_MS", 0) or 0) / 1000.0
        except Exception:
            throttle = 0.0
        indexed = 0
        for row in rows:
            node = row.get("n", {}) or {}
            entity_id = node.get("id")
            if not entity_id:
                continue
            if throttle:
                await asyncio.sleep(throttle)
            if await self._index_node(node):
                indexed += 1
        logger.info("[SEMANTICA] reindexed %d/%d entities from graph", indexed, len(rows))
        return indexed

    async def _index_node(self, node: dict[str, Any]) -> bool:
        """Embed one graph node into the entity vector index."""
        entity_id = node.get("id")
        if not entity_id:
            return False
        skip = {"id", "name", "entity_type", "canonical_name", "file_path", "source_file"}
        attributes = {k: v for k, v in node.items() if k not in skip and v is not None}
        vector_id = await self.search.index_entity(
            entity_id=entity_id,
            name=node.get("name", ""),
            entity_type=node.get("entity_type", ""),
            attributes=attributes,
            file_path=node.get("source_file") or node.get("file_path") or "",
        )
        return bool(vector_id)

    async def _purge_legacy_signal_vectors(self) -> int:
        """Drop the ``signal-<uuid>`` entity vectors older builds wrote (each
        signal was also indexed as an entity, so entity search returned
        signals). Signals have their own ``signal`` content type."""
        try:
            from app.services.signal_store import SignalStore

            ids = [s.id for ms in SignalStore().load_all() for s in ms.signals]
        except Exception as e:
            logger.warning("[SEMANTICA] legacy signal vector purge skipped: %s", e)
            return 0
        for sid in ids:
            await self.search.delete_entity_vector(f"signal-{sid}")
        return len(ids)

    async def build_graph(
        self,
        force_rebuild: bool = False,
        clean: bool = False,
        sources: list[Path] | None = None,
    ) -> dict[str, Any]:
        """Rebuild the entity vector index from the legacy graph.

        The legacy graph (``Neo4jKnowledgeGraph``) and the signal pipeline own
        every Neo4j node and edge. This layer used to write its own copies —
        entity nodes keyed by ``make_entity_id(type, name)`` (so a renamed
        entity came back under its new-name id), ``type-type-x`` stubs, and a
        second ``signal-<uuid>`` node per signal — which a rebuild then had
        to reconcile with the real graph. Now it only embeds what the graph
        holds.

        Args:
            force_rebuild: Rebuild even if already built.
            clean: Drop every entity vector first (never touches Neo4j).
            sources: Unused; kept for call compatibility.

        Returns:
            Build summary with the indexed node count.
        """
        if not force_rebuild and self.last_build and not clean:
            return {
                "status": "already_built",
                "last_build": self.last_build.isoformat(),
                "nodes": len(self.nodes),
                "edges": len(self.edges),
            }

        start = datetime.utcnow()
        logger.info("Rebuilding entity vectors from the graph...")
        try:
            if clean:
                await self.search.clear_entity_vectors()
            purged = await self._purge_legacy_signal_vectors()
            node_count = await self.reindex_entities_from_graph()
            await self._sync_caches()

            self.last_build = datetime.utcnow()
            duration = (self.last_build - start).total_seconds()
            summary = {
                "status": "built",
                "nodes": node_count,
                "edges": 0,
                "legacy_signal_vectors_purged": purged,
                "duration_seconds": duration,
                "last_build": self.last_build.isoformat(),
            }
            logger.info(f"Entity vectors rebuilt: {node_count} nodes in {duration:.1f}s")
            return summary

        except Exception as e:
            logger.error(f"Vector build failed: {e}")
            return {"status": "error", "error": str(e)}

    async def clear_all_data(self) -> dict[str, Any]:
        """Clear all graph data."""
        try:
            # Vectors first: a vector whose node is gone is the harmful state
            # (hybrid_search returns ghosts), a node without a vector is not.
            # If this fails the graph is left untouched and the reset reports
            # the error instead of claiming success.
            cleared = await self.search.clear_entity_vectors()
            logger.info("Cleared %d entity vectors", cleared)
            await self._query("MATCH (n) DETACH DELETE n")
            self.nodes.clear()
            self.edges.clear()
            self.semantic_edges.clear()
            self.document_entities.clear()
            self.entity_documents.clear()
            self.last_build = None
            logger.info("All graph data cleared")
            return {"status": "cleared"}
        except Exception as e:
            logger.error(f"Failed to clear data: {e}")
            return {"status": "error", "error": str(e)}

    async def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Get a single entity by ID.

        Args:
            entity_id: Entity identifier (e.g. 'person-jane-doe').

        Returns:
            Entity dict with id, name, type, attributes, or None.
        """
        try:
            rows = self._extract_records(await self._query(
                "MATCH (n:Entity {id: $id}) RETURN n",
                {"id": entity_id},
            ))
            if not rows:
                return None

            node = rows[0].get("n", {})
            return self._format_entity(node)

        except Exception as e:
            logger.error(f"Failed to get entity {entity_id}: {e}")
            return None

    async def get_relationships(
        self,
        entity_id: str,
        relationship_type: str | None = None,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """Get relationships for an entity.

        Args:
            entity_id: Entity identifier.
            relationship_type: Optional relationship type filter.
            direction: 'outgoing', 'incoming', or 'both'.

        Returns:
            List of relationship dicts.
        """
        try:
            rels = []

            if direction in ("outgoing", "both"):
                cypher = "MATCH (a:Entity {id: $id})-[r]->(b:Entity) "
                if relationship_type:
                    neo4j_type = _validate_cypher_id(relationship_type_to_neo4j(relationship_type))
                    cypher = f"MATCH (a:Entity {{id: $id}})-[r:{neo4j_type}]->(b:Entity) "
                cypher += "RETURN type(r) AS rel_type, properties(r) AS props, b.id AS target_id, b.name AS target_name, b.entity_type AS target_type"

                rows = self._extract_records(await self._query(cypher, {"id": entity_id}))
                for r in rows:
                    rels.append({
                        "source": entity_id,
                        "target": r.get("target_id", ""),
                        "target_name": r.get("target_name", ""),
                        "target_type": r.get("target_type", ""),
                        "relationship_type": r.get("rel_type", ""),
                        "direction": "outgoing",
                        "properties": r.get("props", {}),
                    })

            if direction in ("incoming", "both"):
                cypher = "MATCH (a:Entity)-[r]->(b:Entity {id: $id}) "
                if relationship_type:
                    neo4j_type = _validate_cypher_id(relationship_type_to_neo4j(relationship_type))
                    cypher = f"MATCH (a:Entity)-[r:{neo4j_type}]->(b:Entity {{id: $id}}) "
                cypher += "RETURN type(r) AS rel_type, properties(r) AS props, a.id AS source_id, a.name AS source_name, a.entity_type AS source_type"

                rows = self._extract_records(await self._query(cypher, {"id": entity_id}))
                for r in rows:
                    rels.append({
                        "source": r.get("source_id", ""),
                        "source_name": r.get("source_name", ""),
                        "source_type": r.get("source_type", ""),
                        "target": entity_id,
                        "relationship_type": r.get("rel_type", ""),
                        "direction": "incoming",
                        "properties": r.get("props", {}),
                    })

            return rels

        except Exception as e:
            logger.error(f"Failed to get relationships for {entity_id}: {e}")
            return []

    async def add_entity(
        self,
        entity_id: str,
        entity_type: str,
        name: str,
        properties: dict[str, Any] | None = None,
        file_path: str = "",
    ) -> bool:
        """Add or update an entity in the graph.

        Args:
            entity_id: Entity identifier.
            entity_type: Entity type slug (e.g. 'person').
            name: Display name.
            properties: Optional attribute properties.
            file_path: Source file path.

        Returns:
            True if successful.
        """
        try:
            label = _validate_cypher_id(entity_type_to_label(entity_type))
            entity_def = get_entity_schema(self.domain, entity_type)

            # Build properties
            props = domain_entity_to_neo4j_properties(
                properties or {}, entity_def, entity_id
            )
            props["name"] = name
            props["entity_type"] = entity_type
            props["canonical_name"] = name.lower()
            if file_path:
                props["file_path"] = file_path

            # Set temporal validity from available metadata
            if "valid_from" not in props:
                for date_field in ("updated_at", "created_at", "last_seen"):
                    if date_field in props and props[date_field]:
                        props["valid_from"] = props[date_field]
                        break
                else:
                    props["valid_from"] = datetime.now(
                        tz=__import__("datetime").timezone.utc
                    ).isoformat()
            # valid_to left as-is (NULL = still active)

            # MERGE to upsert
            cypher = (
                f"MERGE (n:Entity:{label} {{id: $id}}) "
                f"SET n += $props "
                f"RETURN n"
            )
            await self._query(cypher, {"id": entity_id, "props": props})

            # Update in-memory cache
            from app.services.graph.models import GraphNode
            self.nodes[entity_id] = GraphNode(
                id=entity_id,
                name=name,
                type=entity_type,
                metadata=props,
            )

            # Index in vector store for search
            await self.search.index_entity(
                entity_id=entity_id,
                name=name,
                entity_type=entity_type,
                attributes=properties or {},
                file_path=file_path,
            )

            return True

        except Exception as e:
            logger.error(f"Failed to add entity {entity_id}: {e}")
            return False

    async def delete_entity(self, entity_id: str, cascade: bool = True) -> bool:
        """Delete an entity from the graph.

        Args:
            entity_id: Entity identifier.
            cascade: Also delete relationships (default True).

        Returns:
            True if successful.
        """
        try:
            # Vector first (see clear_all_data): if this fails the node stays
            # and the deletion is reported as incomplete rather than leaving a
            # searchable vector that points at nothing.
            await self.search.delete_entity_vector(entity_id)

            if cascade:
                cypher = "MATCH (n:Entity {id: $id}) DETACH DELETE n"
            else:
                cypher = "MATCH (n:Entity {id: $id}) DELETE n"

            await self._query(cypher, {"id": entity_id})

            # Remove from cache
            self.nodes.pop(entity_id, None)

            return True

        except Exception as e:
            logger.error(f"Failed to delete entity {entity_id}: {e}")
            return False

    async def add_relationship(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> bool:
        """Add a relationship between two entities.

        Args:
            source_id: Source entity ID.
            target_id: Target entity ID.
            rel_type: Relationship type.
            properties: Optional relationship properties.

        Returns:
            True if successful.
        """
        try:
            neo4j_type = _validate_cypher_id(relationship_type_to_neo4j(rel_type))
            props = properties or {}
            props.setdefault("created_at", datetime.now(
                tz=__import__("datetime").timezone.utc
            ).isoformat())
            props.setdefault("valid_from", props["created_at"])

            cypher = (
                f"MATCH (a:Entity {{id: $source}}), (b:Entity {{id: $target}}) "
                f"MERGE (a)-[r:{neo4j_type}]->(b) "
                f"SET r += $props "
                f"RETURN r"
            )
            await self._query(
                cypher,
                {"source": source_id, "target": target_id, "props": props},
            )

            # Update cache
            from app.services.graph.models import GraphEdge
            self.edges[(source_id, target_id)] = GraphEdge(
                source=source_id,
                target=target_id,
                relationship_type=rel_type,
                strength=props.get("strength", 1.0),
            )

            return True

        except Exception as e:
            logger.error(f"Failed to add relationship {source_id}->{target_id}: {e}")
            return False

    async def delete_relationship(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
    ) -> bool:
        """Delete a relationship between two entities."""
        try:
            neo4j_type = _validate_cypher_id(relationship_type_to_neo4j(rel_type))
            cypher = (
                f"MATCH (a:Entity {{id: $source}})-[r:{neo4j_type}]->(b:Entity {{id: $target}}) "
                f"DELETE r"
            )
            await self._query(
                cypher,
                {"source": source_id, "target": target_id},
            )

            self.edges.pop((source_id, target_id), None)
            return True

        except Exception as e:
            logger.error(f"Failed to delete relationship: {e}")
            return False

    async def update_entity(
        self,
        entity_id: str,
        properties: dict[str, Any],
    ) -> bool:
        """Update properties on an existing entity."""
        try:
            cypher = (
                "MATCH (n:Entity {id: $id}) "
                "SET n += $props "
                "RETURN n"
            )
            await self._query(
                cypher,
                {"id": entity_id, "props": properties},
            )

            # Update cache
            if entity_id in self.nodes:
                self.nodes[entity_id].metadata.update(properties)

            # Reindex in vector store if name changed
            if "name" in properties:
                try:
                    node = self.nodes.get(entity_id)
                    if node:
                        await self.search.index_entity(
                            entity_id=entity_id,
                            name=node.name,
                            entity_type=node.type,
                            attributes=node.metadata or {},
                            file_path=node.metadata.get("file_path", "") if node.metadata else "",
                        )
                except Exception as vec_err:
                    logger.debug(f"Vector reindex for {entity_id} skipped: {vec_err}")

            return True

        except Exception as e:
            logger.error(f"Failed to update entity {entity_id}: {e}")
            return False

    async def merge_entities(
        self,
        primary_id: str,
        duplicate_id: str,
        strategy: str = "primary_wins",
    ) -> dict[str, Any]:
        """Merge a duplicate entity into a primary entity.

        Transfers all relationships from duplicate to primary,
        then deletes the duplicate.
        """
        try:
            # Transfer outgoing relationships
            await self._query(
                "MATCH (dup:Entity {id: $dup_id})-[r]->(t:Entity) "
                "WHERE NOT (t.id = $primary_id) "
                "WITH dup, r, t, type(r) AS rel_type, properties(r) AS props "
                "MATCH (primary:Entity {id: $primary_id}) "
                "CALL apoc.create.relationship(primary, rel_type, props, t) YIELD rel "
                "DELETE r",
                {"primary_id": primary_id, "dup_id": duplicate_id},
            )

            # Transfer incoming relationships
            await self._query(
                "MATCH (s:Entity)-[r]->(dup:Entity {id: $dup_id}) "
                "WHERE NOT (s.id = $primary_id) "
                "WITH s, r, dup, type(r) AS rel_type, properties(r) AS props "
                "MATCH (primary:Entity {id: $primary_id}) "
                "CALL apoc.create.relationship(s, rel_type, props, primary) YIELD rel "
                "DELETE r",
                {"primary_id": primary_id, "dup_id": duplicate_id},
            )

            # Delete duplicate
            await self.delete_entity(duplicate_id, cascade=True)

            return {
                "status": "merged",
                "primary_id": primary_id,
                "duplicate_id": duplicate_id,
            }

        except Exception as e:
            # Fallback without APOC: manually transfer relationships then delete
            logger.warning(f"APOC merge failed, using manual fallback: {e}")
            try:
                # Transfer outgoing relationships from duplicate to primary
                out_rels = self._extract_records(await self._query(
                    "MATCH (dup:Entity {id: $dup_id})-[r]->(t:Entity) "
                    "WHERE t.id <> $primary_id "
                    "RETURN t.id AS target_id, type(r) AS rel_type, properties(r) AS props",
                    {"dup_id": duplicate_id, "primary_id": primary_id},
                ))
                for rel in out_rels:
                    neo4j_type = _validate_cypher_id(rel["rel_type"])
                    await self._query(
                        f"MATCH (p:Entity {{id: $primary_id}}), (t:Entity {{id: $target_id}}) "
                        f"MERGE (p)-[r:{neo4j_type}]->(t) SET r += $props",
                        {"primary_id": primary_id, "target_id": rel["target_id"], "props": rel.get("props", {})},
                    )

                # Transfer incoming relationships from duplicate to primary
                in_rels = self._extract_records(await self._query(
                    "MATCH (s:Entity)-[r]->(dup:Entity {id: $dup_id}) "
                    "WHERE s.id <> $primary_id "
                    "RETURN s.id AS source_id, type(r) AS rel_type, properties(r) AS props",
                    {"dup_id": duplicate_id, "primary_id": primary_id},
                ))
                for rel in in_rels:
                    neo4j_type = _validate_cypher_id(rel["rel_type"])
                    await self._query(
                        f"MATCH (s:Entity {{id: $source_id}}), (p:Entity {{id: $primary_id}}) "
                        f"MERGE (s)-[r:{neo4j_type}]->(p) SET r += $props",
                        {"source_id": rel["source_id"], "primary_id": primary_id, "props": rel.get("props", {})},
                    )

                # Now safe to delete the duplicate with all its (now-transferred) relationships
                await self.delete_entity(duplicate_id, cascade=True)
                return {
                    "status": "merged_fallback",
                    "primary_id": primary_id,
                    "duplicate_id": duplicate_id,
                    "transferred_outgoing": len(out_rels),
                    "transferred_incoming": len(in_rels),
                }
            except Exception as e2:
                logger.error(f"Merge fallback also failed: {e2}")
                return {"status": "error", "error": str(e2)}

    async def query_cypher(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Execute a read-only Cypher query.

        Args:
            query: Cypher query string.
            params: Query parameters.
            limit: Maximum rows (applied if no LIMIT in query).

        Returns:
            List of result row dicts.
        """
        try:
            # Safety: block write operations via keyword blocklist.
            # Use word-boundary regex to avoid false positives on string literals.
            query_stripped = query.strip()
            _WRITE_RE = re.compile(
                r"\b(CREATE|SET|DELETE|MERGE|REMOVE|DROP|DETACH|FOREACH)\b"
                r"|LOAD\s+CSV|CALL\s*\{|CALL\s+APOC\b",
                re.IGNORECASE,
            )
            match = _WRITE_RE.search(query_stripped)
            if match:
                return [{"error": f"Write operations ({match.group()}) not allowed in read-only queries"}]

            # Add LIMIT if not present
            if "LIMIT" not in query_stripped.upper():
                query = query_stripped.rstrip(";") + f" LIMIT {limit}"

            # Use read transaction at the driver level for defense in depth
            if hasattr(self.graph_store, 'execute_read_query'):
                raw = self.graph_store.execute_read_query(query, params or {})
            else:
                raw = await self._query(query, params or {})
            # execute_query returns {"success": bool, "records": [...]} —
            # callers expect a flat list of row dicts.
            return self._extract_records(raw)

        except Exception as e:
            logger.error(f"Cypher query failed: {e}")
            return [{"error": str(e)}]

    async def search_entities(
        self,
        query: str,
        entity_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search for entities (delegates to SemanticaSearch)."""
        return await self.search.hybrid_search(query, entity_types, limit)

    # ──────────────────────────────────────────────────────────────
    # Entity Extraction (delegates to SemanticaExtraction)
    # ──────────────────────────────────────────────────────────────

    async def extract_entities(
        self,
        text: str,
        entity_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Extract entities from text."""
        return await self.extraction.extract_entities(text, entity_types)

    async def extract_entities_grouped(
        self,
        text: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Extract entities grouped by type."""
        return await self.extraction.extract_entities_grouped(text)

    async def deduplicate(
        self,
        entities: list[dict[str, Any]],
        existing: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Deduplicate entities."""
        return await self.extraction.deduplicate(entities, existing)

    # ──────────────────────────────────────────────────────────────
    # Search (delegates to SemanticaSearch)
    # ──────────────────────────────────────────────────────────────

    async def hybrid_search(
        self,
        query: str,
        entity_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Hybrid vector + keyword search."""
        return await self.search.hybrid_search(query, entity_types, limit)

    async def search_transcripts(
        self,
        query: str,
        speaker: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search meeting transcripts."""
        return await self.search.search_transcripts(
            query, speaker, date_from, date_to, max_results
        )

    # ──────────────────────────────────────────────────────────────
    # Decision Intelligence (delegates to SemanticaDecisions)
    # ──────────────────────────────────────────────────────────────

    async def record_decision(
        self,
        category: str,
        content: str,
        reasoning: str = "",
        outcome: str = "",
        confidence: float = 0.8,
        decision_maker: str = "",
        entities: list[str] | None = None,
        meeting_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record a decision."""
        return await self.decisions.record_decision(
            category=category,
            content=content,
            reasoning=reasoning,
            outcome=outcome,
            confidence=confidence,
            decision_maker=decision_maker,
            entities=entities,
            meeting_id=meeting_id,
            metadata=metadata,
        )

    async def find_precedents(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Find decision precedents."""
        return await self.decisions.find_precedents(query, category, limit)

    async def trace_decision_chain(
        self,
        decision_id: str,
    ) -> list[dict[str, Any]]:
        """Trace a decision's causal chain."""
        return await self.decisions.trace_decision_chain(decision_id)

    # ──────────────────────────────────────────────────────────────
    # Temporal Queries & Provenance — Issue #864
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_records(result: Any) -> list[dict[str, Any]]:
        """Extract row records from graph_store.execute_query() result.

        Semantica's execute_query returns {"success": bool, "records": [...]}
        rather than a plain list. This helper normalizes the return value.
        """
        if isinstance(result, dict):
            return result.get("records", [])
        if isinstance(result, list):
            return result
        return []

    async def get_state_at(
        self,
        entity_id: str,
        at_time: datetime,
    ) -> dict[str, Any] | None:
        """Get entity state at a specific point in time.

        Uses temporal validity windows stored on entity nodes.
        Accepts either an entity ID or entity name — tries ID first,
        falls back to case-insensitive name match.
        """
        at_iso = at_time.isoformat()

        cypher = (
            "MATCH (n:Entity) "
            "WHERE (n.id = $lookup OR toLower(n.name) = toLower($lookup)) "
            "AND (n.valid_from IS NULL OR n.valid_from <= $at_time) "
            "AND (n.valid_to IS NULL OR n.valid_to > $at_time) "
            "RETURN n.id AS id, n.name AS name, n.entity_type AS entity_type, "
            "properties(n) AS props, n.valid_from AS valid_from, n.valid_to AS valid_to "
            "ORDER BY CASE WHEN n.id = $lookup THEN 0 ELSE 1 END "
            "LIMIT 1"
        )

        results = self._extract_records(
            await self._query(cypher, {"lookup": entity_id, "at_time": at_iso})
        )

        if not results:
            return None

        row = results[0]
        props = row.get("props", {})
        attributes = {
            k: v for k, v in props.items()
            if k not in ("id", "name", "entity_type", "valid_from", "valid_to")
        }

        return {
            "id": row.get("id", entity_id),
            "name": row.get("name", ""),
            "type": row.get("entity_type", ""),
            "attributes": attributes,
            "valid_from": row.get("valid_from"),
            "valid_to": row.get("valid_to"),
            "as_of": at_iso,
        }

    async def get_active_relationships(
        self,
        entity_id: str,
        at_time: datetime,
    ) -> list[dict[str, Any]]:
        """Get relationships that were active at a specific time.

        Queries both outgoing and incoming relationships filtered by temporal validity.
        """
        at_iso = at_time.isoformat()
        rels = []

        # Outgoing relationships
        out_cypher = (
            "MATCH (a:Entity {id: $id})-[r]->(b:Entity) "
            "WHERE (r.valid_from IS NULL OR r.valid_from <= $at_time) "
            "AND (r.valid_to IS NULL OR r.valid_to > $at_time) "
            "RETURN type(r) AS rel_type, b.id AS target_id, b.name AS target_name, "
            "b.entity_type AS target_type, properties(r) AS props, "
            "r.valid_from AS valid_from, r.valid_to AS valid_to"
        )
        out_results = self._extract_records(
            await self._query(out_cypher, {"id": entity_id, "at_time": at_iso})
        )
        for r in out_results:
            rels.append({
                "relationship_type": r.get("rel_type", ""),
                "target_id": r.get("target_id", ""),
                "target_name": r.get("target_name", ""),
                "target_type": r.get("target_type", ""),
                "direction": "outgoing",
                "properties": r.get("props", {}),
                "valid_from": r.get("valid_from"),
                "valid_to": r.get("valid_to"),
            })

        # Incoming relationships
        in_cypher = (
            "MATCH (a:Entity)-[r]->(b:Entity {id: $id}) "
            "WHERE (r.valid_from IS NULL OR r.valid_from <= $at_time) "
            "AND (r.valid_to IS NULL OR r.valid_to > $at_time) "
            "RETURN type(r) AS rel_type, a.id AS source_id, a.name AS source_name, "
            "a.entity_type AS source_type, properties(r) AS props, "
            "r.valid_from AS valid_from, r.valid_to AS valid_to"
        )
        in_results = self._extract_records(
            await self._query(in_cypher, {"id": entity_id, "at_time": at_iso})
        )
        for r in in_results:
            rels.append({
                "relationship_type": r.get("rel_type", ""),
                "source_id": r.get("source_id", ""),
                "source_name": r.get("source_name", ""),
                "source_type": r.get("source_type", ""),
                "target_id": entity_id,
                "direction": "incoming",
                "properties": r.get("props", {}),
                "valid_from": r.get("valid_from"),
                "valid_to": r.get("valid_to"),
            })

        return rels

    async def get_provenance(self, entity_id: str) -> dict[str, Any]:
        """Get provenance chain for an entity.

        Queries document-entity relationships and modification history
        to build a timeline of how the entity was created and updated.
        """
        # Documents the entity appears in (MENTIONED_IN, written by the graph
        # build) and signals about it (SignalGraphWriter's edges).
        cypher = (
            "CALL { "
            "  MATCH (n:Entity {id: $id})-[r:MENTIONED_IN]->(d:Document) "
            "  RETURN d, r "
            "  UNION "
            "  MATCH (n:Entity {id: $id})<-[r]-(d) "
            "  WHERE type(r) IN ['MENTIONS', 'ASSIGNED_TO', 'FOR_CLIENT', 'EXTRACTED_FROM'] "
            "  RETURN d, r "
            "} "
            "RETURN coalesce(d.path, d.file_path, d.source_meeting_id, d.name, d.id) AS source, "
            "type(r) AS action, "
            "coalesce(r.timestamp, d.created_at, d.date) AS timestamp, "
            "r.actor AS actor "
            "ORDER BY timestamp ASC"
        )
        results = self._extract_records(
            await self._query(cypher, {"id": entity_id})
        )

        history = [
            {
                "source": r.get("source", ""),
                "action": r.get("action", "unknown"),
                "timestamp": r.get("timestamp", ""),
                "actor": r.get("actor", ""),
            }
            for r in results
        ]

        return {
            "entity_id": entity_id,
            "history": history,
        }

    # ──────────────────────────────────────────────────────────────
    # Visualization
    # ──────────────────────────────────────────────────────────────

    async def get_visualization_data(
        self,
        entity_types: list[str] | None = None,
        relationship_types: list[str] | None = None,
        include_semantic_edges: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        """Get graph data in Cytoscape.js format for visualization."""
        return await self.visualization.build_visualization_data(
            entity_types, relationship_types, include_semantic_edges
        )

    def to_cytoscape(
        self,
        nodes: list[dict],
        edges: list[dict],
    ) -> dict[str, list[dict]]:
        """Convert raw graph data to Cytoscape format."""
        return self.visualization.to_cytoscape_elements(nodes, edges)

    # ──────────────────────────────────────────────────────────────
    # Schema Info
    # ──────────────────────────────────────────────────────────────

    def get_schema_description(self) -> str:
        """Get human-readable schema description for LLM prompts."""
        return build_neo4j_schema_description(self.domain)

    def get_entity_types(self) -> list[str]:
        """Get list of available entity types from domain config."""
        return get_entity_types(self.domain)

    # ──────────────────────────────────────────────────────────────
    # Backward Compatibility
    # ──────────────────────────────────────────────────────────────

    def invalidate_cache(self) -> None:
        """Invalidate in-memory caches (backward compat with old KG)."""
        self.nodes.clear()
        self.edges.clear()
        self.semantic_edges.clear()
        self.document_entities.clear()
        self.entity_documents.clear()
        self.last_build = None

    async def add_node(
        self,
        entity_id: str,
        entity_type: str,
        label: str = "",
        properties: dict[str, Any] | None = None,
    ) -> bool:
        """Add a node (backward compat with Neo4jKnowledgeGraph)."""
        name = (properties or {}).get("name", label or entity_id)
        return await self.add_entity(
            entity_id=entity_id,
            entity_type=entity_type,
            name=name,
            properties=properties,
        )

    async def add_edge(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> bool:
        """Add an edge (backward compat with Neo4jKnowledgeGraph)."""
        return await self.add_relationship(source_id, target_id, rel_type, properties)

    async def find_edges(
        self,
        source: str | None = None,
        target: str | None = None,
        rel_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find edges matching criteria (backward compat)."""
        conditions = ["1=1"]
        params: dict[str, Any] = {}

        if source:
            conditions.append("a.id = $source")
            params["source"] = source
        if target:
            conditions.append("b.id = $target")
            params["target"] = target

        where = " AND ".join(conditions)

        if rel_type:
            neo4j_type = relationship_type_to_neo4j(rel_type)
            cypher = (
                f"MATCH (a:Entity)-[r:{neo4j_type}]->(b:Entity) "
                f"WHERE {where} "
                f"RETURN a.id AS source, b.id AS target, type(r) AS rel_type, properties(r) AS props"
            )
        else:
            cypher = (
                f"MATCH (a:Entity)-[r]->(b:Entity) "
                f"WHERE {where} "
                f"RETURN a.id AS source, b.id AS target, type(r) AS rel_type, properties(r) AS props"
            )

        try:
            return self._extract_records(await self._query(cypher, params))
        except Exception as e:
            logger.error(f"find_edges failed: {e}")
            return []

    # ──────────────────────────────────────────────────────────────
    # Private Helpers
    # ──────────────────────────────────────────────────────────────

    def _extract_metadata(self, content: str) -> dict[str, Any] | None:
        """Extract YAML frontmatter from markdown content."""
        if not content or "---" not in content:
            return None
        try:
            import yaml
            parts = content.split("---", 2)
            if len(parts) < 3:
                return None
            return yaml.safe_load(parts[1])
        except Exception:
            return None

    def _resolve_entity_type(
        self,
        file_path: str,
        metadata: dict[str, Any],
    ) -> str | None:
        """Determine entity type from file path and metadata.

        Only configured domain entity types count: the graph writes any other
        file as a Document, so indexing it as an entity would find no Entity
        node and fail (and be retried) on every boot."""
        if not self.domain or not self.domain.entities:
            return None
        known = set(self.domain.entities)

        for key in ("type", "entity_type"):
            if metadata.get(key) in known:
                return metadata[key]
        entity_id = metadata.get("id")
        if isinstance(entity_id, str) and "-" in entity_id and entity_id.split("-")[0] in known:
            return entity_id.split("-")[0]

        parts = Path(file_path).parts
        for entity_type, entity_def in self.domain.entities.items():
            plural = getattr(entity_def, "plural", entity_type + "s")
            if plural in parts or entity_type in parts:
                return entity_type

        return None

    async def _sync_caches(self) -> None:
        """Sync Neo4j data to in-memory caches for backward compat."""
        try:
            # Clear stale caches before repopulating
            self.nodes.clear()
            self.edges.clear()
            self.semantic_edges.clear()
            self.document_entities.clear()
            self.entity_documents.clear()

            # Sync nodes
            node_rows = self._extract_records(await self._query(
                "MATCH (n:Entity) RETURN n"
            ))
            from app.services.graph.models import GraphEdge, GraphNode

            for r in node_rows:
                node = r.get("n", {})
                node_id = node.get("id", "")
                if node_id:
                    self.nodes[node_id] = GraphNode(
                        id=node_id,
                        name=node.get("name", ""),
                        type=node.get("entity_type", ""),
                        metadata=node,
                    )

            # Sync edges
            edge_rows = self._extract_records(await self._query(
                "MATCH (a:Entity)-[r]->(b:Entity) "
                "RETURN a.id AS source, b.id AS target, type(r) AS rel_type, properties(r) AS props"
            ))
            for r in edge_rows:
                source = r.get("source", "")
                target = r.get("target", "")
                if source and target:
                    self.edges[(source, target)] = GraphEdge(
                        source=source,
                        target=target,
                        relationship_type=r.get("rel_type", ""),
                        strength=r.get("props", {}).get("strength", 1.0),
                    )

            logger.info(
                f"Synced caches: {len(self.nodes)} nodes, {len(self.edges)} edges"
            )

        except Exception as e:
            logger.warning(f"Cache sync failed (non-fatal): {e}")

    def _format_entity(self, node: dict[str, Any]) -> dict[str, Any]:
        """Format a Neo4j node dict into standard entity format."""
        skip_keys = {"id", "name", "entity_type", "canonical_name", "is_archived", "file_path"}
        attributes = {
            k: v for k, v in node.items()
            if k not in skip_keys and v is not None
        }
        return {
            "id": node.get("id", ""),
            "name": node.get("name", ""),
            "type": node.get("entity_type", ""),
            "attributes": attributes,
            "file_path": node.get("file_path", ""),
        }
