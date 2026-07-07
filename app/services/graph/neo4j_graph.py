"""
Neo4j Knowledge Graph — Domain-Driven Graph Service

Replaces builder.py, cache.py, query_handler.py, and indexer.py with a single
Neo4j-backed service. All operations go to Neo4j. Entity types, attributes,
and relationships are read from the domain YAML — nothing is hardcoded.

Preserves the same interface as the old KnowledgeGraph so callers
(memory.py, domain_graph.py, visualization_adapter.py, chat_tools.py)
continue to work without changes to their business logic.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

import yaml

from app.model_schemas.domain_config import DomainConfiguration
from app.neo4j_client import Neo4jClient

from .models import GraphEdge, GraphNode
from .neo4j_models import (
    build_node_properties,
    extract_relationship_targets,
    serialize_metadata_for_neo4j,
)
from .neo4j_schema import entity_type_to_label, relationship_type_to_neo4j

logger = logging.getLogger(__name__)

# OpenTelemetry imports for manual instrumentation
try:
    from opentelemetry import trace
    OTEL_AVAILABLE = True
    tracer = trace.get_tracer(__name__)
except ImportError:
    OTEL_AVAILABLE = False
    tracer = None


class Neo4jKnowledgeGraph:
    """Neo4j-backed knowledge graph driven by domain configuration.

    This replaces the old in-memory KnowledgeGraph. All data lives in Neo4j.
    The domain YAML defines what entity types, attributes, and relationships
    exist — this class just reads the schema and operates generically.
    """

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        domain_config: DomainConfiguration | None = None,
    ):
        self.neo4j = neo4j_client
        self.domain = domain_config
        self.last_build: datetime | None = None

        # In-memory caches for backward compatibility with code that reads
        # .nodes, .edges, .document_entities directly (visualization_adapter,
        # memory.py graph/visualization endpoint). These are populated from
        # Neo4j on build_graph() and kept in sync.
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[tuple[str, str], GraphEdge] = {}
        self.semantic_edges: dict[tuple[str, str, str], Any] = {}
        self.document_entities: dict[str, set[str]] = {}
        self.entity_documents: dict[str, set[str]] = defaultdict(set)

        # Lazy git_ops reference
        self._git_ops = None

        # Build lock — prevents concurrent build_graph calls from racing
        self._build_lock = asyncio.Lock()
        self._build_in_progress = False

        # Per-entity locks for write-through file operations
        self._file_locks: dict[str, asyncio.Lock] = {}

    @property
    def git_ops(self):
        if self._git_ops is None:
            from app.git_ops import git_ops
            self._git_ops = git_ops
        return self._git_ops

    def _get_file_lock(self, entity_id: str) -> asyncio.Lock:
        """Get or create an asyncio.Lock for the given entity's file operations."""
        if entity_id not in self._file_locks:
            self._file_locks[entity_id] = asyncio.Lock()
        return self._file_locks[entity_id]

    # ──────────────────────────────────────────────────────────────
    # Graph Wipe / Clean Build Support
    # ──────────────────────────────────────────────────────────────

    async def clear_all_data(self) -> dict[str, Any]:
        """Delete every node and relationship in Neo4j and reset in-memory caches.

        Uses batched deletion to avoid Neo4j memory pressure on large graphs.
        Returns counts of deleted nodes and relationships.
        """
        logger.info("Clearing all Neo4j data...")

        # Count before deletion for stats
        count_result = await self.neo4j.execute_read(
            "MATCH (n) OPTIONAL MATCH (n)-[r]-() "
            "RETURN count(DISTINCT n) AS nodes, count(DISTINCT r) AS rels"
        )
        nodes_before = count_result[0]["nodes"] if count_result else 0
        rels_before = count_result[0]["rels"] if count_result else 0

        # Batched deletion — process 1000 nodes at a time to avoid OOM
        batch_size = 1000
        total_deleted = 0
        while True:
            result = await self.neo4j.execute_write(
                "MATCH (n) WITH n LIMIT $batch DETACH DELETE n "
                "RETURN count(*) AS deleted",
                {"batch": batch_size},
            )
            deleted = result[0]["deleted"] if result else 0
            total_deleted += deleted
            if deleted < batch_size:
                break

        # Reset all in-memory caches
        self.nodes.clear()
        self.edges.clear()
        self.semantic_edges.clear()
        self.document_entities.clear()
        self.entity_documents.clear()
        self.last_build = None

        stats = {
            "nodes_deleted": nodes_before,
            "relationships_deleted": rels_before,
        }
        logger.info(
            f"Neo4j cleared: {stats['nodes_deleted']} nodes, "
            f"{stats['relationships_deleted']} relationships removed"
        )
        return stats

    # ──────────────────────────────────────────────────────────────
    # Graph Build
    # ──────────────────────────────────────────────────────────────

    async def build_graph(self, force_rebuild: bool = False, clean: bool = False) -> dict[str, Any]:
        """Build the knowledge graph by scanning markdown files and writing to Neo4j.

        On subsequent calls, skips rebuild unless forced or cache is stale (24h).
        Also populates the in-memory dicts for backward compatibility.

        Uses an async lock so concurrent callers coalesce: the first caller
        builds while others wait, then all return the freshly-built stats.

        Args:
            force_rebuild: Skip staleness check and always rebuild.
            clean: If True, wipe all Neo4j data before rebuilding (stateless deploy).
        """
        # Fast path (no lock): if cache is fresh, return immediately
        if not force_rebuild and not clean and self.last_build:
            age = (datetime.utcnow() - self.last_build).total_seconds()
            if age < 24 * 3600 and len(self.nodes) > 0:
                return self._get_graph_stats()

        # Serialize builds — concurrent callers wait here instead of racing
        async with self._build_lock:
            # Re-check after acquiring lock: another caller may have just
            # finished building while we waited
            if not force_rebuild and not clean and self.last_build:
                age = (datetime.utcnow() - self.last_build).total_seconds()
                if age < 24 * 3600 and len(self.nodes) > 0:
                    logger.info("[BUILD_GRAPH] Skipping — another caller just finished building")
                    return self._get_graph_stats()

            # Cold-cache fast path: if no full build has happened in this
            # process AND Neo4j already has data, just sync from Neo4j
            # instead of doing the full file rebuild. The previous gate
            # (len(self.nodes) == 0) was too narrow — anything that
            # incrementally added a node would wedge us back into the slow
            # path. Using `not self.last_build` keys directly off the
            # "have we ever done a full build in this process?" signal,
            # which is what we actually care about.
            #
            # Sync is bounded by graph size (~1s for thousands of entities);
            # full rebuild is bounded by repo size (60+s for hundreds of
            # markdown files). Routes that call build_graph() lazily
            # (entity_profile, etc.) used to pay the full rebuild on the
            # first user request after restart — this avoids that cliff.
            if not force_rebuild and not clean and not self.last_build:
                try:
                    # Exclude :Signal so a database that only contains signals
                    # (dual-labeled :Entity:Signal) doesn't take the sync-only
                    # shortcut and skip the markdown rebuild that populates
                    # the rest of the entity graph.
                    sample = await self.neo4j.execute_read(
                        "MATCH (n:Entity) WHERE NOT n:Signal RETURN n.id LIMIT 1"
                    )
                    if sample:
                        logger.info(
                            "[BUILD_GRAPH] No full build yet but Neo4j has data — syncing only"
                        )
                        await self._sync_from_neo4j()
                        self.last_build = datetime.utcnow()
                        return self._get_graph_stats()
                except Exception as e:
                    # Sync failed — fall through to the full rebuild path.
                    logger.warning(
                        "[BUILD_GRAPH] Sync-only attempt failed (%s); falling back to full rebuild",
                        e,
                    )

            return await self._build_graph_locked(force_rebuild=force_rebuild, clean=clean)

    async def _build_graph_locked(self, force_rebuild: bool = False, clean: bool = False) -> dict[str, Any]:
        """Inner build logic — caller MUST hold self._build_lock.

        Reader visibility: The build writes to Neo4j (server-side) and lets
        the existing in-memory caches (self.nodes/edges/...) keep serving
        old-but-valid data throughout the ingest phase. Only `_sync_from_neo4j`
        at the end rewrites in-memory state, and it does so via an atomic
        swap (builds locals, rebinds self.* at the very end), so concurrent
        readers never observe an empty or half-built graph.
        """
        # Wipe everything first when clean=True (startup / manual reset)
        if clean:
            wipe_stats = await self.clear_all_data()
            logger.info(f"Clean build requested — wiped {wipe_stats}")

        start_time = time.time()
        logger.info("Building knowledge graph from document metadata → Neo4j...")

        # Get all markdown files
        all_files = []
        try:
            files_obj = await self.git_ops.read_markdown_files()
            all_files = [f.path for f in files_obj]
        except Exception:
            logger.exception("Error getting markdown files")
            for root, _, files in os.walk(self.git_ops.repo_path):
                for f in files:
                    if f.endswith(".md"):
                        rel_path = os.path.relpath(
                            os.path.join(root, f), self.git_ops.repo_path
                        )
                        all_files.append(rel_path)

        markdown_files = [f for f in all_files if not f.endswith("README.md")]
        processed = 0

        for file_path in markdown_files:
            try:
                content = await self.git_ops.read_file(file_path)
                if not content:
                    continue

                metadata = self._extract_metadata(content)
                if not metadata:
                    continue

                # Skip archived entities — soft-deleted via purge or UI
                archived = metadata.get("is_archived")
                if isinstance(archived, str):
                    archived = archived.strip().lower() in ("true", "1", "yes")
                if archived:
                    continue

                await self._ingest_file(file_path, metadata)
                processed += 1

            except Exception:
                logger.warning("Error processing %s", file_path, exc_info=True)
                continue

        # Build co-occurrence relationships
        await self._build_co_occurrence_relationships()

        # Build explicit relationships from entity metadata
        await self._build_explicit_relationships()

        # Re-ingest signals from disk (lost during clean rebuild)
        await self._reingest_signals()

        self.last_build = datetime.utcnow()

        # Clean up bad stub entities after every build (not just clean builds)
        try:
            stub_stats = await self.process_stubs(dry_run=False)
            logger.info(f"Post-build stub cleanup: {stub_stats}")
        except Exception as e:
            logger.warning(f"Post-build stub cleanup failed: {e}", exc_info=True)

        # Sync from Neo4j to in-memory caches — atomic swap inside.
        # If this raises, the original in-memory caches are preserved.
        await self._sync_from_neo4j()

        elapsed = time.time() - start_time
        stats = self._get_graph_stats()
        logger.info(
            f"Knowledge graph built: {processed} files, "
            f"{stats['total_nodes']} nodes, {stats['total_edges']} edges "
            f"in {elapsed:.1f}s"
        )
        return stats

    @staticmethod
    def _parse_datetime(value) -> datetime:
        """Safely parse a datetime from Neo4j, handling various types."""
        if value is None:
            return datetime.utcnow()
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except (ValueError, TypeError):
                return datetime.utcnow()
        # Neo4j DateTime objects have .iso_format() method
        if hasattr(value, "iso_format"):
            try:
                return datetime.fromisoformat(value.iso_format())
            except (ValueError, TypeError):
                return datetime.utcnow()
        # Last resort: try str conversion
        try:
            return datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return datetime.utcnow()

    def _extract_metadata(self, content: str) -> dict[str, Any] | None:
        """Extract YAML frontmatter from markdown content."""
        if "---" not in content:
            return None

        lines = content.split("\n")
        start_idx = None
        end_idx = None

        for i, line in enumerate(lines):
            if line.strip() == "---":
                if start_idx is None:
                    start_idx = i
                elif end_idx is None:
                    end_idx = i
                    break

        if start_idx is not None and end_idx is not None:
            yaml_content = "\n".join(lines[start_idx + 1: end_idx])
            try:
                return yaml.safe_load(yaml_content)
            except yaml.YAMLError:
                pass

        # Fallback to frontmatter service
        try:
            from app.services.frontmatter import frontmatter
            return frontmatter.extract_all(content)
        except Exception:
            return None

    # ──────────────────────────────────────────────────────────────
    # Entity Ingestion (Domain-Driven)
    # ──────────────────────────────────────────────────────────────

    async def _ingest_file(self, file_path: str, metadata: dict[str, Any]) -> None:
        """Ingest a file using domain config to determine entity type and attributes."""
        # Skip archived entities — they were soft-deleted and should not re-enter the graph
        if metadata.get("is_archived", False):
            logger.debug(f"Skipping archived file: {file_path}")
            return

        document_entities: set[str] = set()

        # Determine if this is an entity profile
        entity_id = metadata.get("id", "")
        entity_type_field = metadata.get("type", "") or metadata.get("entity_type", "")

        entity_type = self._resolve_entity_type(entity_id, entity_type_field, file_path)

        if entity_type and self.domain and entity_type in self.domain.entities:
            # This is an entity profile — create the entity node
            eid = entity_id or self._derive_entity_id(entity_type, file_path, metadata)
            entity_def = self.domain.entities[entity_type]
            properties = build_node_properties(metadata, entity_def, eid)
            properties["source_file"] = file_path
            properties["stub"] = False  # Real entity with source file — clear any stub flag

            label = entity_type_to_label(entity_type)
            await self._upsert_node(eid, entity_type, label, properties)
            document_entities.add(eid)

            # Extract and store relationship targets from metadata
            for rel_def in entity_def.relationships:
                targets = extract_relationship_targets(metadata, rel_def.type)
                for target_id in targets:
                    normalized = self._normalize_target_id(target_id, rel_def.target)
                    # Ensure target node exists (as stub) so the MERGE finds it
                    # Use _ensure_entity_exists (ON CREATE only) to avoid overwriting
                    # real entity data when the target was already ingested from its own file
                    await self._ensure_entity_exists(
                        normalized, rel_def.target, target_id
                    )
                    await self._upsert_relationship(
                        source_id=eid,
                        target_id=normalized,
                        rel_type=relationship_type_to_neo4j(rel_def.type),
                        properties={"source": "metadata", "file_path": file_path},
                    )

                    # Create inverse edge if inverse_name is defined
                    if rel_def.inverse_name:
                        await self._upsert_relationship(
                            source_id=normalized,
                            target_id=eid,
                            rel_type=relationship_type_to_neo4j(rel_def.inverse_name),
                            properties={"source": "inverse", "file_path": file_path},
                        )
        else:
            # Not an entity profile — create a Document node
            doc_id = f"doc:{file_path}"
            await self._upsert_document_node(doc_id, file_path, metadata)
            document_entities.add(doc_id)

        # Extract entity references from metadata fields (people, projects, etc.)
        ref_entities = await self._extract_entity_references(file_path, metadata)
        document_entities.update(ref_entities)

        # Store document-entity associations in Neo4j
        for eid in document_entities:
            if not eid.startswith("doc:"):
                await self._link_entity_to_document(eid, file_path)

        self.document_entities[file_path] = document_entities
        for eid in document_entities:
            self.entity_documents[eid].add(file_path)

    def _resolve_entity_type(
        self, entity_id: str, type_field: str, file_path: str
    ) -> str | None:
        """Determine entity type from metadata or file path.

        Uses domain config entity keys as the source of truth.
        """
        if not self.domain:
            return None

        known_types = set(self.domain.entities.keys())

        # Check explicit type field
        if isinstance(type_field, str) and type_field in known_types:
            return type_field

        # Check ID prefix (e.g., "person-tom-williams" → "person")
        if isinstance(entity_id, str) and "-" in entity_id:
            prefix = entity_id.split("-")[0]
            if prefix in known_types:
                return prefix

        # Check file path (e.g., "entities/person/tom-williams.md")
        parts = file_path.split("/")
        if len(parts) >= 3 and parts[0] == "entities":
            candidate = parts[1]
            if candidate in known_types:
                return candidate

        # Check directory-based plural mappings (e.g., "people/" → person, "projects/" → project)
        # These are the rich profile directories used by DomainAwareEntityProcessor
        if len(parts) >= 2 and self.domain:
            directory = parts[0]
            for entity_type, entity_def in self.domain.entities.items():
                if hasattr(entity_def, "plural") and entity_def.plural == directory:
                    return entity_type

        return None

    def _derive_entity_id(
        self, entity_type: str, file_path: str, metadata: dict
    ) -> str:
        """Derive an entity ID when not explicitly set in metadata."""
        # Prefer canonical_name (already a slug) over display name
        canonical = metadata.get("canonical_name", "")
        if canonical:
            slug = re.sub(r"[^a-z0-9]+", "-", canonical.lower()).strip("-")
            if slug:
                return f"{entity_type}-{slug}"
        name = metadata.get("name", "")
        if name:
            slug = name.lower().replace(" ", "-")
            return f"{entity_type}-{slug}"
        # Fallback: use filename
        filename = os.path.basename(file_path)
        stem = os.path.splitext(filename)[0]
        return stem if stem.startswith(f"{entity_type}-") else f"{entity_type}-{stem}"

    def _normalize_target_id(self, target_id: str, target_type: str) -> str:
        """Normalize a relationship target ID, adding type prefix if missing.

        Mirrors EntityService.normalize_entity_id() logic:
        1. Strip trailing entity type name (e.g., "Grants Team" -> "Grants" for type team)
        2. Use consistent regex slug generation (only a-z0-9, hyphens)
        3. Add type prefix
        """
        # Already prefixed with a known entity type — return as-is
        if any(
            target_id.startswith(f"{t}-")
            for t in (self.domain.entities.keys() if self.domain else [])
        ):
            return target_id

        # Re-prefix person-type IDs: e.g. "person-jane-doe" → "member-jane-doe"
        # when domain uses "member" instead of "person"
        # Only apply when target_type is also person-like to avoid "person-jane" → "team-jane"
        person_like = {"person", "member", "contact"}
        if target_type in person_like:
            for old_prefix in person_like:
                if target_id.startswith(f"{old_prefix}-") and old_prefix != target_type:
                    slug = target_id[len(old_prefix) + 1:]
                    return f"{target_type}-{slug}"

        name = target_id.strip()

        # Strip trailing entity type name to prevent "team-grants-team" duplicates
        if self.domain and target_type in self.domain.entities:
            entity_def = self.domain.entities[target_type]
            entity_schema_name = getattr(entity_def, "name", "").lower()
            # Remove trailing type name (e.g., " team" from "Grants Team")
            for suffix in [entity_schema_name, target_type]:
                if suffix:
                    name = re.sub(
                        rf"\s+{re.escape(suffix)}$", "", name, flags=re.IGNORECASE
                    )

        # Consistent slug: only a-z0-9 and hyphens (matches EntityService)
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not slug:
            slug = "unnamed"
        return f"{target_type}-{slug}"

    def _get_person_entity_type(self) -> str | None:
        """Return the domain's person-like entity type."""
        if not self.domain:
            return "person"
        for candidate in ("person", "member", "contact"):
            if candidate in self.domain.entities:
                return candidate
        for etype, econfig in self.domain.entities.items():
            if hasattr(econfig, 'icon') and econfig.icon in ("user", "person"):
                return etype
        return None

    async def _extract_entity_references(
        self, _file_path: str, metadata: dict[str, Any]
    ) -> set[str]:
        """Extract entity references from common metadata fields.

        Handles multiple metadata formats used by different parts of the pipeline:
        1. Top-level plural fields: people, projects, teams, accounts
        2. Nested entities_mentioned.{type}: person, project, team (singular)
        3. Nested entities_mentioned.participants: pre-formatted entity IDs
        4. Top-level participants/speakers: display names for person entities
        5. Legacy summary.participants format
        """
        refs: set[str] = set()
        if not self.domain:
            return refs

        # Map common top-level metadata fields to entity types (plural → type)
        field_mappings = {}
        if self.domain:
            for etype, econfig in self.domain.entities.items():
                plural = getattr(econfig, 'plural', None) or f"{etype}s"
                field_mappings[plural] = etype
            # Map "people" to the person-like type if not already mapped
            if "people" not in field_mappings:
                person_type = self._get_person_entity_type()
                if person_type:
                    field_mappings["people"] = person_type

        for field_name, entity_type in field_mappings.items():
            if entity_type not in self.domain.entities:
                continue

            values = metadata.get(field_name, [])
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list):
                continue

            for value in values:
                if isinstance(value, str) and value.strip():
                    eid = self._normalize_target_id(value.strip(), entity_type)
                    # Ensure the entity node exists (as a stub if not already present)
                    await self._ensure_entity_exists(eid, entity_type, value.strip())
                    refs.add(eid)

        # Handle nested entities_mentioned structure from meeting enrichment
        # Format: entities_mentioned: { person: [...], project: [...], team: [...], participants: [...] }
        entities_mentioned = metadata.get("entities_mentioned", {})
        if isinstance(entities_mentioned, dict):
            # Singular entity type keys (person, project, team) contain display names
            for entity_type in (self.domain.entities.keys() if self.domain else []):
                values = entities_mentioned.get(entity_type, [])
                if isinstance(values, str):
                    values = [values]
                if not isinstance(values, list):
                    continue
                for value in values:
                    if isinstance(value, str) and value.strip():
                        eid = self._normalize_target_id(value.strip(), entity_type)
                        await self._ensure_entity_exists(eid, entity_type, value.strip())
                        refs.add(eid)

            # Backward compatibility: read legacy "person" key when domain uses "member"/"contact"
            person_type = self._get_person_entity_type()
            if person_type and person_type != "person" and "person" not in (self.domain.entities if self.domain else {}):
                legacy_values = entities_mentioned.get("person", [])
                if isinstance(legacy_values, str):
                    legacy_values = [legacy_values]
                if isinstance(legacy_values, list):
                    for value in legacy_values:
                        if isinstance(value, str) and value.strip():
                            eid = self._normalize_target_id(value.strip(), person_type)
                            await self._ensure_entity_exists(eid, person_type, value.strip())
                            refs.add(eid)

            # participants sub-key contains pre-formatted entity IDs (e.g., "person-janna-glucksman")
            em_participants = entities_mentioned.get("participants", [])
            if isinstance(em_participants, str):
                em_participants = [em_participants]
            if isinstance(em_participants, list) and person_type:
                for value in em_participants:
                    if isinstance(value, str) and value.strip():
                        # These are already formatted as entity IDs — _normalize_target_id
                        # returns them as-is when they have a known type prefix
                        eid = self._normalize_target_id(value.strip(), person_type)
                        await self._ensure_entity_exists(eid, person_type, value.strip())
                        refs.add(eid)

        # Top-level participants and speakers fields (display names for person-like entities)
        person_type_for_fields = self._get_person_entity_type()
        if person_type_for_fields and person_type_for_fields in (self.domain.entities if self.domain else {}):
            for person_field in ("participants", "speakers"):
                values = metadata.get(person_field, [])
                if isinstance(values, str):
                    values = [values]
                if not isinstance(values, list):
                    continue
                for name in values:
                    if isinstance(name, str) and name.strip():
                        eid = self._normalize_target_id(name.strip(), person_type_for_fields)
                        await self._ensure_entity_exists(eid, person_type_for_fields, name.strip())
                        refs.add(eid)

        # Also check participants in summary (legacy meeting format)
        summary = metadata.get("summary", {})
        if isinstance(summary, dict) and person_type_for_fields:
            participants = summary.get("participants", [])
            if isinstance(participants, list):
                for name in participants:
                    if isinstance(name, str) and name.strip() and person_type_for_fields in (self.domain.entities if self.domain else {}):
                        eid = self._normalize_target_id(name.strip(), person_type_for_fields)
                        await self._ensure_entity_exists(eid, person_type_for_fields, name.strip())
                        refs.add(eid)

        return refs

    # ──────────────────────────────────────────────────────────────
    # Neo4j Write Operations
    # ──────────────────────────────────────────────────────────────

    async def _upsert_node(
        self,
        entity_id: str,
        entity_type: str,
        label: str,
        properties: dict[str, Any],
    ) -> None:
        """MERGE a node with the Entity base label and a type-specific label."""
        safe_props = serialize_metadata_for_neo4j(properties)
        safe_props["entity_type"] = entity_type
        safe_props["updated_at"] = datetime.utcnow().isoformat()

        query = (
            f"MERGE (n:Entity:{label} {{id: $id}}) "
            f"SET n += $props"
        )
        await self.neo4j.execute_write(query, {"id": entity_id, "props": safe_props})
        # Re-seed the type registry on every upsert so a clean rebuild
        # (which wipes _TypeRegistry meta-nodes alongside the graph) leaves
        # /api/type-registry consistent with the graph instances.
        await self._record_type_usage("entity", entity_type)

    async def _upsert_document_node(
        self,
        doc_id: str,
        file_path: str,
        metadata: dict[str, Any],
    ) -> None:
        """MERGE a Document node."""
        props = {
            "id": doc_id,
            "path": file_path,
            "name": os.path.basename(file_path),
            "type": metadata.get("type", "document"),
            "updated_at": datetime.utcnow().isoformat(),
        }
        # Add select metadata
        for key in ("title", "created", "modified", "date"):
            if key in metadata and isinstance(metadata[key], (str, int, float)):
                props[key] = metadata[key]

        query = "MERGE (d:Document {id: $id}) SET d += $props"
        await self.neo4j.execute_write(query, {"id": doc_id, "props": props})

    async def _ensure_entity_exists(
        self, entity_id: str, entity_type: str, name: str
    ) -> None:
        """Ensure an entity node exists, creating a stub if necessary.

        Skips stub creation when the entity has an archived file on disk
        to prevent resurrecting soft-deleted entities via cross-file references.
        """
        # Check if entity file is archived — if so, don't create a stub
        if self._is_entity_archived(entity_id):
            logger.debug("Skipping stub for archived entity: %s", entity_id)
            return

        label = entity_type_to_label(entity_type)
        query = (
            f"MERGE (n:Entity:{label} {{id: $id}}) "
            f"ON CREATE SET n.name = $name, n.canonical_name = $canonical, "
            f"n.entity_type = $etype, n.stub = true, "
            f"n.updated_at = $ts"
        )
        await self.neo4j.execute_write(
            query,
            {
                "id": entity_id,
                "name": name,
                "canonical": name.lower().strip(),
                "etype": entity_type,
                "ts": datetime.utcnow().isoformat(),
            },
        )

    async def _upsert_relationship(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """MERGE a relationship between two entity nodes."""
        props = serialize_metadata_for_neo4j(properties or {})
        props["updated_at"] = datetime.utcnow().isoformat()

        query = (
            f"MATCH (a:Entity {{id: $source}}) "
            f"MATCH (b:Entity {{id: $target}}) "
            f"MERGE (a)-[r:{rel_type}]->(b) "
            f"SET r += $props"
        )
        await self.neo4j.execute_write(
            query,
            {"source": source_id, "target": target_id, "props": props},
        )
        # Re-seed the type registry so clean rebuilds (which delete
        # _TypeRegistry rows along with the graph) leave the relationship
        # type listed in /api/type-registry.
        await self._record_type_usage("relationship", rel_type)

    async def _link_entity_to_document(self, entity_id: str, file_path: str) -> None:
        """Create MENTIONED_IN relationship between entity and document."""
        doc_id = f"doc:{file_path}"
        query = (
            "MATCH (e:Entity {id: $eid}) "
            "MERGE (d:Document {id: $did}) "
            "ON CREATE SET d.path = $path, d.name = $name "
            "MERGE (e)-[:MENTIONED_IN]->(d)"
        )
        await self.neo4j.execute_write(
            query,
            {
                "eid": entity_id,
                "did": doc_id,
                "path": file_path,
                "name": os.path.basename(file_path),
            },
        )

    # ──────────────────────────────────────────────────────────────
    # Relationship Building
    # ──────────────────────────────────────────────────────────────

    async def _build_co_occurrence_relationships(self) -> None:
        """Build CO_OCCURRENCE relationships between entities sharing documents."""
        query = (
            "MATCH (a:Entity)-[:MENTIONED_IN]->(d:Document)<-[:MENTIONED_IN]-(b:Entity) "
            "WHERE a.id < b.id "
            "WITH a, b, collect(d.path) AS shared_docs, count(d) AS doc_count "
            "MERGE (a)-[r:CO_OCCURRENCE]-(b) "
            "SET r.strength = CASE WHEN doc_count > 5 THEN 1.0 "
            "     WHEN doc_count > 2 THEN 0.7 ELSE 0.5 END, "
            "    r.shared_documents = doc_count, "
            "    r.context = shared_docs"
        )
        await self.neo4j.execute_write(query)

    async def _build_explicit_relationships(self) -> None:
        """No-op: relationships are created directly in _ingest_file().

        Previously this method looked for relationship fields stored as node
        properties and converted them to actual Neo4j relationships.  Since
        _ingest_file() already calls _upsert_relationship() for every
        domain-defined relationship found in the entity's metadata, relationship
        targets are never stored as node properties, making this pass redundant.

        Kept as a no-op placeholder so the build_graph() call-site stays clean
        and the method can be repurposed if a future ingestion path stores
        relationship data as properties.
        """
        return

    async def _reingest_signals(self) -> None:
        """Re-ingest meeting signals from disk into Neo4j.

        During a clean rebuild, Signal nodes are wiped along with everything
        else. This method reads the persisted signal JSON files and writes
        them back into Neo4j so the graph includes meeting intelligence.
        """
        try:
            from app.services.graph.signal_graph_writer import SignalGraphWriter
            from app.services.signal_store import SignalStore

            store = SignalStore()
            all_meetings = store.load_all()
            if not all_meetings:
                return

            writer = SignalGraphWriter(self.neo4j)
            total_written = 0
            for meeting_signals in all_meetings:
                try:
                    count = await writer.write_meeting_signals(meeting_signals)
                    total_written += count
                except Exception as e:
                    logger.warning(
                        "[GRAPH] Failed to reingest signals for %s: %s",
                        meeting_signals.bot_id, e,
                    )

            logger.info(
                "[GRAPH] Reingested %d signals from %d meeting(s)",
                total_written, len(all_meetings),
            )
        except Exception as e:
            logger.warning("[GRAPH] Signal reingestion failed: %s", e, exc_info=True)

    # ──────────────────────────────────────────────────────────────
    # Timestamp helpers
    # ──────────────────────────────────────────────────────────────

    def _parse_created_at(self, value: Any) -> float:
        """Convert a Neo4j created_at value to a Unix-epoch float.

        Handles None, numeric (int/float already epoch), and ISO-8601 strings
        (stored by ``add_semantic_relationship``).  Falls back to the current
        time when the value cannot be parsed.
        """
        if value is None:
            return time.time()
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).timestamp()
            except (ValueError, TypeError):
                return time.time()
        return time.time()

    # ──────────────────────────────────────────────────────────────
    # Sync Neo4j → In-Memory (backward compatibility)
    # ──────────────────────────────────────────────────────────────

    async def _sync_from_neo4j(self) -> None:
        """Load all nodes and edges from Neo4j into in-memory dicts.

        Builds a fresh set of caches into LOCALS, then rebinds `self.*` at the
        very end. This guarantees that concurrent readers see either the full
        previous state or the full new state, never a half-built mix. If any
        step raises, the original caches are preserved unchanged.

        Preserves backward compatibility with code that accesses .nodes,
        .edges, .document_entities, .entity_documents, .semantic_edges directly.
        """
        from collections import defaultdict as _defaultdict
        new_nodes: dict[str, GraphNode] = {}
        new_edges: dict[tuple, GraphEdge] = {}
        new_document_entities: dict[str, set[str]] = {}
        new_entity_documents: dict[str, set[str]] = _defaultdict(set)
        new_semantic_edges: dict[tuple[str, str, str], Any] = {}

        # Load entities
        entity_results = await self.neo4j.execute_read(
            "MATCH (n:Entity) RETURN n"
        )
        for record in entity_results:
            node_data = record["n"]
            node_id = node_data["id"]
            new_nodes[node_id] = GraphNode(
                id=node_id,
                name=node_data.get("name", ""),
                type=node_data.get("entity_type", "unknown"),
                metadata={k: v for k, v in node_data.items()
                         if k not in ("id", "name", "entity_type", "canonical_name",
                                      "updated_at", "stub")},
                last_updated=self._parse_datetime(node_data.get("updated_at")),
            )

        # Load document nodes too
        doc_results = await self.neo4j.execute_read(
            "MATCH (d:Document) RETURN d"
        )
        for record in doc_results:
            doc_data = record["d"]
            doc_id = doc_data["id"]
            new_nodes[doc_id] = GraphNode(
                id=doc_id,
                name=doc_data.get("name", ""),
                type="document",
                metadata={k: v for k, v in doc_data.items()
                         if k not in ("id", "name", "updated_at")},
            )

        # Load entity connections (directed query to preserve edge direction)
        conn_results = await self.neo4j.execute_read(
            "MATCH (a:Entity)-[r]->(b:Entity) "
            "RETURN a.id AS source, b.id AS target, type(r) AS rel_type, "
            "       r.strength AS strength, r.context AS context"
        )
        for record in conn_results:
            source = record["source"]
            target = record["target"]
            rel_type = record["rel_type"].lower()
            # Directional key: (source, rel_type, target) preserves A→B vs B→A
            edge_key = (source, rel_type, target)
            if edge_key not in new_edges:
                context = record.get("context")
                if context is None:
                    context = []
                elif isinstance(context, str):
                    context = [context]

                new_edges[edge_key] = GraphEdge(
                    source=source,
                    target=target,
                    relationship_type=rel_type,
                    strength=float(record.get("strength") or 0.5),
                    context=context,
                )

                # Update connections on nodes
                if source in new_nodes:
                    new_nodes[source].connections.add(target)
                if target in new_nodes:
                    new_nodes[target].connections.add(source)

        # Load document-entity associations
        mentioned_results = await self.neo4j.execute_read(
            "MATCH (e:Entity)-[:MENTIONED_IN]->(d:Document) "
            "RETURN e.id AS entity_id, d.path AS doc_path"
        )
        for record in mentioned_results:
            eid = record["entity_id"]
            path = record["doc_path"]
            if path:
                if path not in new_document_entities:
                    new_document_entities[path] = set()
                new_document_entities[path].add(eid)
                new_entity_documents[eid].add(path)

                # Also add to node.documents
                if eid in new_nodes:
                    new_nodes[eid].documents.add(path)

        # Load semantic relationships (only if any exist)
        # Check for the 'semantic' property key first to avoid Neo4j warnings
        # when the property has never been used in the database.
        has_semantic = await self.neo4j.execute_read(
            "CALL db.propertyKeys() YIELD propertyKey "
            "WHERE propertyKey = 'semantic' RETURN propertyKey LIMIT 1"
        )
        if has_semantic:
            semantic_results = await self.neo4j.execute_read(
                "MATCH (a:Entity)-[r]->(b:Entity) "
                "WHERE r.semantic = true "
                "RETURN a.id AS source, b.id AS target, type(r) AS rel_type, "
                "       r.strength AS strength, r.context AS context, "
                "       r.evidence AS evidence, r.reasoning AS reasoning, "
                "       r.source AS source_field, r.created_at AS created_at"
            )
        else:
            semantic_results = []
        from app.services.knowledge_graph import SemanticEdge
        for record in semantic_results:
            source = record["source"]
            target = record["target"]
            rel_type = record["rel_type"].lower()
            edge_key = (source, target, rel_type)

            new_semantic_edges[edge_key] = SemanticEdge(
                from_entity=source,
                to_entity=target,
                relationship_type=rel_type,
                strength=float(record.get("strength") or 0.5),
                evidence=record.get("evidence") or "",
                reasoning=record.get("reasoning") or "",
                source=record.get("source_field") or "neo4j",
                created_at=self._parse_created_at(record.get("created_at")),
            )

            # Update connections on nodes
            if source in new_nodes:
                new_nodes[source].connections.add(target)
            if target in new_nodes:
                new_nodes[target].connections.add(source)

        # Load Signal nodes
        signal_results = await self.neo4j.execute_read(
            "MATCH (s:Signal) RETURN s"
        )
        for record in signal_results:
            sig = record["s"]
            sig_id = sig["id"]
            new_nodes[sig_id] = GraphNode(
                id=sig_id,
                name=sig.get("content", "")[:60],
                type="signal",
                metadata={
                    "signal_type": sig.get("signal_type", ""),
                    "source_meeting_id": sig.get("source_meeting_id", ""),
                    "source_meeting_title": sig.get("source_meeting_title", ""),
                    "status": sig.get("status", ""),
                    "confidence": sig.get("confidence", 0),
                    "owner_name": sig.get("owner_name", ""),
                    "position": sig.get("position", 0),
                },
                last_updated=datetime.utcnow(),
            )

        # Load Signal → Entity edges (MENTIONS + ASSIGNED_TO)
        signal_edge_results = await self.neo4j.execute_read(
            "MATCH (s:Signal)-[r]->(e:Entity) "
            "WHERE type(r) IN ['MENTIONS', 'ASSIGNED_TO'] "
            "RETURN s.id AS source, e.id AS target, type(r) AS rel_type, "
            "       r.entity_role AS entity_role"
        )
        for record in signal_edge_results:
            source = record["source"]
            target = record["target"]
            rel_type = record["rel_type"].lower()
            edge_key = (source, rel_type, target)
            if edge_key not in new_edges:
                new_edges[edge_key] = GraphEdge(
                    source=source,
                    target=target,
                    relationship_type=rel_type,
                    strength=0.7,
                    context=[],
                )
                if source in new_nodes:
                    new_nodes[source].connections.add(target)
                if target in new_nodes:
                    new_nodes[target].connections.add(source)

        # Atomic swap — readers only ever see the previous state OR the new
        # state in full, never a half-built mix. If an earlier step raised,
        # this line never runs and self.* keeps its previous values.
        self.nodes = new_nodes
        self.edges = new_edges
        self.document_entities = new_document_entities
        self.entity_documents = new_entity_documents
        self.semantic_edges = new_semantic_edges

    # ──────────────────────────────────────────────────────────────
    # Query Methods (matching old KnowledgeGraph interface)
    # ──────────────────────────────────────────────────────────────

    async def find_related_entities_directed(
        self, entity_id: str, max_results: int = 100
    ) -> dict[str, list[dict[str, Any]]]:
        """Find entities related to the given entity, separated by direction."""
        outgoing_query = (
            "MATCH (e:Entity {id: $id})-[r]->(related:Entity) "
            "WHERE type(r) <> 'CO_OCCURRENCE' "
            "RETURN related, type(r) AS rel_type, r.strength AS strength "
            "ORDER BY r.strength DESC "
            "LIMIT $limit"
        )
        incoming_query = (
            "MATCH (e:Entity {id: $id})<-[r]-(related:Entity) "
            "WHERE type(r) <> 'CO_OCCURRENCE' "
            "RETURN related, type(r) AS rel_type, r.strength AS strength "
            "ORDER BY r.strength DESC "
            "LIMIT $limit"
        )
        params = {"id": entity_id, "limit": max_results}

        def _parse_records(records: list) -> list[dict[str, Any]]:
            result = []
            for record in records:
                node_data = record["related"]
                result.append({
                    "entity": {
                        "id": node_data["id"],
                        "name": node_data.get("name", ""),
                        "type": node_data.get("entity_type", "unknown"),
                        "metadata": {k: v for k, v in dict(node_data).items()
                                     if k not in ("id", "name", "entity_type")},
                    },
                    "relationship": {
                        "type": record["rel_type"].lower(),
                        "strength": float(record.get("strength") or 0.5),
                        "shared_documents": 0,
                    },
                })
            return result

        outgoing_results = await self.neo4j.execute_read(outgoing_query, params)
        incoming_results = await self.neo4j.execute_read(incoming_query, params)

        return {
            "outgoing": _parse_records(outgoing_results),
            "incoming": _parse_records(incoming_results),
        }

    async def query_for_visualization(
        self,
        entity_types: list[str] | None = None,
        relationship_types: list[str] | None = None,
        include_signals: bool = False,
        limit: int = 500,
    ) -> tuple[dict[str, GraphNode], dict[tuple, GraphEdge]]:
        """Return a filtered subgraph WITHOUT loading the full graph.

        This is the fast-path for the /api/domain-graph endpoint. Instead of
        calling build_graph() → _sync_from_neo4j() (which loads every entity,
        document, edge, and signal into Python dicts), this runs 3 focused
        Cypher queries and returns exactly what the visualization needs.

        Args:
            entity_types: Optional filter by entity_type property
            relationship_types: Optional lowercase rel names (e.g. 'works_with');
                converted to the Cypher UPPER_SNAKE form internally.
            include_signals: If True, append Signal nodes connected to any
                entity in scope plus their MENTIONS/ASSIGNED_TO edges.
            limit: Max entity nodes, ranked by degree so the structural backbone
                is preserved when truncating.

        Returns:
            (nodes, edges) with .connections populated on each node — format
            matches what adapter.convert_subgraph() expects.
        """
        from .neo4j_schema import relationship_type_to_neo4j

        params: dict[str, Any] = {"limit": limit}
        # Signal nodes are dual-labeled `:Entity:Signal` for polymorphic
        # ORM compatibility. That means `MATCH (n:Entity)` silently matches
        # signals too — so we need an explicit label-level exclusion, not
        # a property-level one, when the caller wants signals excluded.
        where_parts: list[str] = []
        if not include_signals:
            where_parts.append("NOT n:Signal")
        if entity_types:
            where_parts.append("n.entity_type IN $types")
            params["types"] = entity_types
        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        # Degree-ranked top-N. The COUNT subquery must apply the same visibility
        # predicates as the edges we actually return — otherwise a
        # relationship-heavy node connected only to hidden signals (or to
        # relationship types that get filtered out below) can win the cut
        # despite contributing zero edges to the output.
        degree_clauses: list[str] = []
        if not include_signals:
            degree_clauses.append("NOT m:Signal")
        if relationship_types:
            # Reuse the rel-type-to-neo4j mapping; allocate a separate param
            # name so the later edge query can filter on its own.
            params["degree_rel_types"] = [
                relationship_type_to_neo4j(t) for t in relationship_types
            ]
            degree_clauses.append("type(r) IN $degree_rel_types")
        degree_where = ("WHERE " + " AND ".join(degree_clauses)) if degree_clauses else ""

        # (Neo4j 5.x deprecated `size((n)--())` — must use COUNT{} subquery.)
        node_query = f"""
        MATCH (n:Entity)
        {where_clause}
        WITH n, COUNT {{ MATCH (n)-[r]-(m) {degree_where} }} AS degree
        ORDER BY degree DESC
        LIMIT $limit
        RETURN n
        """
        node_results = await self.neo4j.execute_read(node_query, params)

        nodes: dict[str, GraphNode] = {}
        for record in node_results:
            node_data = record["n"]
            node_id = node_data["id"]
            nodes[node_id] = GraphNode(
                id=node_id,
                name=node_data.get("name", ""),
                type=node_data.get("entity_type", "unknown"),
                metadata={
                    k: v for k, v in dict(node_data).items()
                    if k not in ("id", "name", "entity_type", "canonical_name",
                                 "updated_at", "stub")
                },
                last_updated=self._parse_datetime(node_data.get("updated_at")),
            )

        node_ids = list(nodes.keys())
        if not node_ids:
            return {}, {}

        edge_params: dict[str, Any] = {"ids": node_ids}
        rel_filter = ""
        if relationship_types:
            edge_params["rel_types"] = [
                relationship_type_to_neo4j(t) for t in relationship_types
            ]
            rel_filter = "AND type(r) IN $rel_types"

        edge_query = (
            f"MATCH (a:Entity)-[r]->(b:Entity) "
            f"WHERE a.id IN $ids AND b.id IN $ids {rel_filter} "
            f"RETURN a.id AS source, b.id AS target, type(r) AS rel_type, "
            f"       r.strength AS strength, r.context AS context"
        )
        edge_results = await self.neo4j.execute_read(edge_query, edge_params)

        edges: dict[tuple, GraphEdge] = {}
        for record in edge_results:
            source = record["source"]
            target = record["target"]
            rel_type = record["rel_type"].lower()
            edge_key = (source, rel_type, target)
            context = record.get("context") or []
            if isinstance(context, str):
                context = [context]
            edges[edge_key] = GraphEdge(
                source=source,
                target=target,
                relationship_type=rel_type,
                strength=float(record.get("strength") or 0.5),
                context=context,
            )
            if source in nodes:
                nodes[source].connections.add(target)
            if target in nodes:
                nodes[target].connections.add(source)

        if include_signals:
            signal_query = (
                "MATCH (s:Signal)-[r]->(e:Entity) "
                "WHERE e.id IN $ids AND type(r) IN ['MENTIONS', 'ASSIGNED_TO'] "
                "RETURN s AS signal, e.id AS entity_id, type(r) AS rel_type"
            )
            signal_results = await self.neo4j.execute_read(
                signal_query, {"ids": node_ids}
            )
            for record in signal_results:
                sig = record["signal"]
                sig_id = sig["id"]
                if sig_id not in nodes:
                    nodes[sig_id] = GraphNode(
                        id=sig_id,
                        name=sig.get("content", "")[:60],
                        type="signal",
                        metadata={
                            "signal_type": sig.get("signal_type", ""),
                            "source_meeting_id": sig.get("source_meeting_id", ""),
                            "source_meeting_title": sig.get("source_meeting_title", ""),
                            "status": sig.get("status", ""),
                            "confidence": sig.get("confidence", 0),
                            "owner_name": sig.get("owner_name", ""),
                        },
                        last_updated=datetime.utcnow(),
                    )
                entity_id = record["entity_id"]
                rel_type = record["rel_type"].lower()
                edge_key = (sig_id, rel_type, entity_id)
                edges[edge_key] = GraphEdge(
                    source=sig_id,
                    target=entity_id,
                    relationship_type=rel_type,
                    strength=0.7,
                    context=[],
                )
                nodes[sig_id].connections.add(entity_id)
                if entity_id in nodes:
                    nodes[entity_id].connections.add(sig_id)

        logger.info(
            f"[QUERY_VIZ] types={entity_types} rels={relationship_types} "
            f"signals={include_signals} limit={limit} "
            f"→ {len(nodes)} nodes, {len(edges)} edges"
        )
        return nodes, edges

    async def count_entities_for_visualization(
        self,
        entity_types: list[str] | None = None,
        include_signals: bool = True,
    ) -> int:
        """Count entities that would pass the visualization filter. Used to
        report `total_available_nodes` to the UI alongside a limited result set.

        Signals are dual-labeled `:Entity:Signal` in this graph, so they match
        `(n:Entity)` by default. When `include_signals=False` the count must
        exclude them, otherwise `total_available_nodes` is inflated whenever
        signals are hidden and the truncation banner reports wrong numbers.
        """
        params: dict[str, Any] = {}
        clauses: list[str] = []
        if entity_types:
            clauses.append("n.entity_type IN $types")
            params["types"] = entity_types
        if not include_signals:
            clauses.append("NOT n:Signal")
        where_clause = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        query = f"MATCH (n:Entity) {where_clause} RETURN count(n) AS total"
        result = await self.neo4j.execute_read(query, params)
        return int(result[0]["total"]) if result else 0

    async def neighborhood(
        self,
        seed_id: str,
        depth: int = 2,
        include_signals: bool = False,
        limit: int = 200,
    ) -> tuple[dict[str, GraphNode], dict[tuple, GraphEdge]]:
        """Return a k-hop subgraph centered on a seed entity.

        Does NOT call _sync_from_neo4j or build_graph — runs a small number of
        bounded Cypher queries so this stays fast regardless of total graph size.
        The cold-start problem with the main /api/domain-graph endpoint does not
        apply here. This method is the backbone of the context-graph UX.

        Args:
            seed_id: Entity ID to center on
            depth: Max hops from seed. 1-3 only; validated at route layer.
            include_signals: If True, include Signal nodes attached to any
                entity in scope (via MENTIONS or ASSIGNED_TO).
            limit: Max entity nodes in result. Signals add on top of this.

        Returns:
            (nodes, edges) — nodes maps id→GraphNode, edges maps (src, rel, tgt)→GraphEdge.
            Returns ({}, {}) if seed does not exist.
        """
        if depth < 1 or depth > 3:
            raise ValueError(f"depth must be 1-3, got {depth}")

        # Depth is validated int — safe to inline. Parameter-binding variable
        # path lengths is not reliable across Neo4j versions.
        # Signals are dual-labeled `:Entity:Signal`; filter by label (not by
        # `entity_type` property) so `include_signals=False` actually excludes.
        signal_guard = "" if include_signals else "AND NOT n:Signal"
        seed_signal_guard = "" if include_signals else "WHERE NOT seed:Signal"
        # Order matters before LIMIT: dense neighborhoods can have far more
        # than `limit` candidates, so `RETURN node LIMIT N` unordered is free
        # to drop the seed or the closest hops. We rank the seed first (0 hops)
        # then the nearest neighbours, so the limited result always includes
        # the seed and shrinks outward.
        node_query = f"""
        MATCH (seed:Entity {{id: $seed_id}})
        {seed_signal_guard}
        OPTIONAL MATCH path = (seed)-[*1..{depth}]-(n:Entity)
        WHERE n IS NOT NULL {signal_guard}
        WITH seed, n, min(length(path)) AS hops
        WITH seed, collect(DISTINCT {{node: n, hops: hops}}) AS neighbors
        UNWIND ([{{node: seed, hops: 0}}] + neighbors) AS entry
        WITH entry.node AS node, entry.hops AS hops
        WHERE node IS NOT NULL
        WITH DISTINCT node, min(hops) AS hops
        ORDER BY hops ASC, node.id ASC
        RETURN node
        LIMIT $limit
        """
        node_results = await self.neo4j.execute_read(
            node_query, {"seed_id": seed_id, "limit": limit}
        )
        if not node_results:
            return {}, {}

        nodes: dict[str, GraphNode] = {}
        for record in node_results:
            node_data = record["node"]
            node_id = node_data["id"]
            nodes[node_id] = GraphNode(
                id=node_id,
                name=node_data.get("name", ""),
                type=node_data.get("entity_type", "unknown"),
                metadata={
                    k: v for k, v in dict(node_data).items()
                    if k not in ("id", "name", "entity_type", "canonical_name",
                                 "updated_at", "stub")
                },
                last_updated=self._parse_datetime(node_data.get("updated_at")),
            )

        node_ids = list(nodes.keys())

        # Edges where both endpoints are in scope. Bounded by node set so this
        # cannot blow up even on a dense graph.
        edge_query = (
            "MATCH (a:Entity)-[r]->(b:Entity) "
            "WHERE a.id IN $ids AND b.id IN $ids "
            "RETURN a.id AS source, b.id AS target, type(r) AS rel_type, "
            "       r.strength AS strength, r.context AS context"
        )
        edge_results = await self.neo4j.execute_read(edge_query, {"ids": node_ids})

        edges: dict[tuple, GraphEdge] = {}
        for record in edge_results:
            source = record["source"]
            target = record["target"]
            rel_type = record["rel_type"].lower()
            edge_key = (source, rel_type, target)
            context = record.get("context") or []
            if isinstance(context, str):
                context = [context]
            edges[edge_key] = GraphEdge(
                source=source,
                target=target,
                relationship_type=rel_type,
                strength=float(record.get("strength") or 0.5),
                context=context,
            )
            if source in nodes:
                nodes[source].connections.add(target)
            if target in nodes:
                nodes[target].connections.add(source)

        # Optional signal nodes/edges for any entity in scope
        if include_signals:
            signal_query = (
                "MATCH (s:Signal)-[r]->(e:Entity) "
                "WHERE e.id IN $ids AND type(r) IN ['MENTIONS', 'ASSIGNED_TO'] "
                "RETURN s AS signal, e.id AS entity_id, type(r) AS rel_type"
            )
            signal_results = await self.neo4j.execute_read(
                signal_query, {"ids": node_ids}
            )
            for record in signal_results:
                sig = record["signal"]
                sig_id = sig["id"]
                if sig_id not in nodes:
                    nodes[sig_id] = GraphNode(
                        id=sig_id,
                        name=sig.get("content", "")[:60],
                        type="signal",
                        metadata={
                            "signal_type": sig.get("signal_type", ""),
                            "source_meeting_id": sig.get("source_meeting_id", ""),
                            "source_meeting_title": sig.get("source_meeting_title", ""),
                            "status": sig.get("status", ""),
                            "confidence": sig.get("confidence", 0),
                            "owner_name": sig.get("owner_name", ""),
                        },
                        last_updated=datetime.utcnow(),
                    )
                entity_id = record["entity_id"]
                rel_type = record["rel_type"].lower()
                edge_key = (sig_id, rel_type, entity_id)
                edges[edge_key] = GraphEdge(
                    source=sig_id,
                    target=entity_id,
                    relationship_type=rel_type,
                    strength=0.7,
                    context=[],
                )
                nodes[sig_id].connections.add(entity_id)
                if entity_id in nodes:
                    nodes[entity_id].connections.add(sig_id)

        logger.info(
            f"[NEIGHBORHOOD] seed={seed_id} depth={depth} signals={include_signals} "
            f"→ {len(nodes)} nodes, {len(edges)} edges"
        )
        return nodes, edges

    async def find_related_entities(
        self, entity_id: str, max_results: int = 10
    ) -> list[dict[str, Any]]:
        """Find entities related to the given entity via Neo4j traversal."""
        query = (
            "MATCH (e:Entity {id: $id})-[r]-(related:Entity) "
            "RETURN related, type(r) AS rel_type, r.strength AS strength "
            "ORDER BY r.strength DESC "
            "LIMIT $limit"
        )
        results = await self.neo4j.execute_read(
            query, {"id": entity_id, "limit": max_results}
        )

        related = []
        for record in results:
            node_data = record["related"]
            related.append({
                "entity": {
                    "id": node_data["id"],
                    "name": node_data.get("name", ""),
                    "type": node_data.get("entity_type", "unknown"),
                    "metadata": {k: v for k, v in node_data.items()
                                if k not in ("id", "name", "entity_type",
                                             "canonical_name", "updated_at")},
                },
                "relationship": {
                    "type": record["rel_type"].lower(),
                    "strength": float(record.get("strength") or 0.5),
                    "shared_documents": 0,
                },
            })
        return related

    async def find_contextual_documents(
        self, query_entities: list[str], max_results: int = 10
    ) -> list[dict[str, Any]]:
        """Find documents relevant to given entities via Neo4j."""
        if not query_entities:
            return []

        query = (
            "UNWIND $entities AS eid "
            "MATCH (e:Entity {id: eid})-[:MENTIONED_IN]->(d:Document) "
            "WITH d, count(DISTINCT e) AS entity_count "
            "RETURN d.path AS path, entity_count, "
            "       toFloat(entity_count) AS relevance_score "
            "ORDER BY entity_count DESC "
            "LIMIT $limit"
        )
        results = await self.neo4j.execute_read(
            query, {"entities": query_entities, "limit": max_results}
        )

        return [
            {
                "path": r["path"],
                "relevance_score": float(r["relevance_score"]),
                "matching_entities": r["entity_count"],
                "total_entities": 0,
            }
            for r in results
            if r["path"]
        ]

    async def search_by_topic(
        self, topic: str, max_results: int = 10, force_refresh: bool = False
    ) -> list[dict[str, Any]]:
        """Search entities using Neo4j full-text index."""
        if not topic or not topic.strip():
            return []

        if force_refresh:
            await self.build_graph(force_rebuild=True)

        # Try full-text search first
        try:
            query = (
                "CALL db.index.fulltext.queryNodes('entity_search', $query) "
                "YIELD node, score "
                "RETURN node.id AS id, node.name AS name, "
                "       node.entity_type AS type, score "
                "ORDER BY score DESC "
                "LIMIT $limit"
            )
            results = await self.neo4j.execute_read(
                query, {"query": topic, "limit": max_results}
            )

            if results:
                entity_ids = [r["id"] for r in results if r["id"]]
                return await self.find_contextual_documents(entity_ids, max_results)
        except Exception as e:
            logger.debug(f"Full-text search failed, falling back to CONTAINS: {e}")

        # Fallback: CONTAINS match on name
        query = (
            "MATCH (e:Entity) "
            "WHERE toLower(e.name) CONTAINS toLower($topic) "
            "RETURN e.id AS id "
            "LIMIT $limit"
        )
        results = await self.neo4j.execute_read(
            query, {"topic": topic, "limit": max_results}
        )

        entity_ids = [r["id"] for r in results if r["id"]]
        if entity_ids:
            return await self.find_contextual_documents(entity_ids, max_results)
        return []

    def get_entity_by_name(
        self, name: str, entity_type: str | None = None
    ) -> dict[str, Any] | None:
        """Find an entity by name with fuzzy matching (exact > starts-with > contains)."""
        if not isinstance(name, str) or not name.strip():
            return None

        name_lower = name.strip().lower()
        best_match = None
        best_score = 0  # 3=exact, 2=starts-with, 1=contains

        for node in self.nodes.values():
            if node.type in ("document", "signal") or node.type.startswith("doc:"):
                continue
            if entity_type and node.type != entity_type:
                continue
            node_name = node.name.lower()
            if node_name == name_lower:
                score = 3
            elif node_name.startswith(name_lower):
                score = 2
            elif name_lower in node_name:
                score = 1
            else:
                continue
            if score > best_score:
                best_score = score
                best_match = node

        if best_match:
            return {
                "id": best_match.id,
                "name": best_match.name,
                "type": best_match.type,
                "metadata": best_match.metadata,
                "document_count": len(best_match.documents),
                "connection_count": len(best_match.connections),
            }
        return None

    # ──────────────────────────────────────────────────────────────
    # Signal Query Methods (graph-native)
    # ──────────────────────────────────────────────────────────────

    async def find_signals_for_entity(
        self, entity_id: str, signal_type: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Find signals that MENTION a given entity via Neo4j traversal.

        Args:
            entity_id: Entity slug ID (e.g. "person-chris-fernandes")
            signal_type: Optional filter by signal type
            limit: Max results

        Returns:
            List of signal dicts
        """
        where_clauses = ["e.id = $entity_id"]
        params: dict[str, Any] = {"entity_id": entity_id, "limit": limit}

        if signal_type:
            where_clauses.append("s.signal_type = $signal_type")
            params["signal_type"] = signal_type

        where_str = " AND ".join(where_clauses)

        query = (
            f"MATCH (s:Signal)-[:MENTIONS|ASSIGNED_TO]->(e:Entity) "
            f"WHERE {where_str} "
            f"OPTIONAL MATCH (s)-[m:MENTIONS]->(other:Entity) "
            f"RETURN s, collect(DISTINCT {{id: other.id, name: other.name, role: m.entity_role}}) AS mentions "
            f"ORDER BY s.source_timestamp DESC, s.position ASC "
            f"LIMIT $limit"
        )

        try:
            results = await self.neo4j.execute_read(query, params)
        except Exception as e:
            logger.warning("[NEO4J] Signal query failed: %s", e)
            return []

        signals = []
        for record in results:
            s = record["s"]
            mentions = record.get("mentions", [])
            signals.append({
                "id": s["id"],
                "type": s.get("signal_type", ""),
                "content": s.get("content", ""),
                "source_meeting_id": s.get("source_meeting_id", ""),
                "source_meeting_title": s.get("source_meeting_title", ""),
                "source_timestamp": s.get("source_timestamp", ""),
                "status": s.get("status", ""),
                "owner": s.get("owner_name", ""),
                "position": s.get("position", 0),
                "confidence": s.get("confidence", 0.8),
                "mentions": [m for m in mentions if m.get("id")],
            })
        return signals

    async def get_entity_signal_stats(
        self, entity_id: str, recent_cutoff_iso: str
    ) -> dict[str, Any]:
        """Aggregate signal-derived statistics for an entity in one query.

        Counts signals that MENTION or are ASSIGNED_TO the entity. Each signal
        is a "mention"; each distinct source_meeting_id is a "document". This is
        the ingest-path counterpart to the file-world ``entity_documents`` map —
        the ingest pipeline writes Signal->Entity edges but no Document nodes,
        so document-based stats are always zero for ingested entities.

        Args:
            entity_id: Entity slug ID (e.g. "person-jordan-reyes")
            recent_cutoff_iso: ISO-8601 timestamp; signals at/after this count
                as "recent". ISO-8601 strings compare correctly lexicographically.

        Returns:
            Dict with mention_count, document_count, recent_count, last_ts.
            All zeroed (last_ts None) on no signals or any query error.
        """
        zero = {
            "mention_count": 0,
            "document_count": 0,
            "recent_count": 0,
            "last_ts": None,
        }
        # count(DISTINCT s): a signal can have BOTH a MENTIONS and an
        # ASSIGNED_TO edge to the same entity (e.g. an action item whose owner
        # is also named in the body). The pattern yields one row per edge, so a
        # non-distinct count would double-count that signal. DISTINCT collapses
        # it back to one "mention" per signal.
        query = (
            "MATCH (s:Signal)-[:MENTIONS|ASSIGNED_TO]->(e:Entity {id: $entity_id}) "
            "RETURN count(DISTINCT s) AS mention_count, "
            "       count(DISTINCT s.source_meeting_id) AS document_count, "
            "       count(DISTINCT CASE WHEN s.source_timestamp IS NOT NULL "
            "                  AND s.source_timestamp >= $cutoff THEN s END) AS recent_count, "
            "       max(s.source_timestamp) AS last_ts"
        )
        try:
            rows = await self.neo4j.execute_read(
                query, {"entity_id": entity_id, "cutoff": recent_cutoff_iso}
            )
        except Exception as e:  # noqa: BLE001 - stats must degrade, not raise
            logger.warning(
                "[NEO4J] Entity signal-stats query failed for %s: %s", entity_id, e
            )
            return dict(zero)
        if not rows:
            return dict(zero)
        r = rows[0]
        return {
            "mention_count": r.get("mention_count", 0) or 0,
            "document_count": r.get("document_count", 0) or 0,
            "recent_count": r.get("recent_count", 0) or 0,
            "last_ts": r.get("last_ts"),
        }

    async def find_signals_for_relationship(
        self, entity1_id: str, entity2_id: str
    ) -> list[dict[str, Any]]:
        """Find signals that mention both entities (evidence for relationships).

        Useful for answering questions like "What decisions involved both Jordan and Casey?"

        Args:
            entity1_id: First entity slug ID
            entity2_id: Second entity slug ID

        Returns:
            List of signal dicts that mention both entities
        """
        query = (
            "MATCH (s:Signal)-[:MENTIONS|ASSIGNED_TO]->(e1:Entity {id: $e1}), "
            "      (s)-[:MENTIONS|ASSIGNED_TO]->(e2:Entity {id: $e2}) "
            "RETURN s "
            "ORDER BY s.source_timestamp DESC "
            "LIMIT 20"
        )

        try:
            results = await self.neo4j.execute_read(
                query, {"e1": entity1_id, "e2": entity2_id}
            )
        except Exception as e:
            logger.warning("[NEO4J] Signal relationship query failed: %s", e)
            return []

        return [
            {
                "id": r["s"]["id"],
                "type": r["s"].get("signal_type", ""),
                "content": r["s"].get("content", ""),
                "source_meeting_id": r["s"].get("source_meeting_id", ""),
                "source_meeting_title": r["s"].get("source_meeting_title", ""),
                "source_timestamp": r["s"].get("source_timestamp", ""),
                "status": r["s"].get("status", ""),
            }
            for r in results
        ]

    async def create_semantic_relationship(
        self,
        from_entity_id: str,
        to_entity_id: str,
        relationship_type: str,
        strength: float,
        evidence: str,
        reasoning: str,
        source: str,
    ) -> Any:
        """Create a semantic relationship with evidence in Neo4j.

        Also validates against domain schema like the old implementation.
        """
        import time as _time

        from app.core.domain_config.domain_config_service import get_domain_config_service
        from app.services.knowledge_graph import SemanticEdge

        # Validate entity IDs
        if not from_entity_id or not from_entity_id.strip():
            raise ValueError("from_entity_id cannot be empty")
        if not to_entity_id or not to_entity_id.strip():
            raise ValueError("to_entity_id cannot be empty")
        from_entity_id = from_entity_id.strip()
        to_entity_id = to_entity_id.strip()

        # Validate
        if not isinstance(strength, (int, float)):
            raise TypeError(f"Strength must be numeric, got {type(strength).__name__}")
        if strength < 0.0 or strength > 1.0:
            raise ValueError(f"Strength must be between 0.0 and 1.0, got {strength}")
        if not evidence or not evidence.strip():
            raise ValueError("Evidence cannot be empty")
        if not reasoning or not reasoning.strip():
            raise ValueError("Reasoning cannot be empty")

        # Validate relationship type against domain schema
        domain_service = get_domain_config_service()
        active_domain = domain_service.get_active_domain()
        if not active_domain:
            raise ValueError("No active domain configuration found")

        valid_types = set()
        for _, entity_schema in active_domain.entities.items():
            for rel in entity_schema.relationships:
                valid_types.add(rel.type)

        if relationship_type not in valid_types:
            raise ValueError(
                f"Invalid relationship type '{relationship_type}'. "
                f"Valid types: {', '.join(sorted(valid_types))}"
            )

        # Write to Neo4j
        neo4j_rel_type = relationship_type_to_neo4j(relationship_type)
        props = {
            "strength": float(strength),
            "evidence": evidence,
            "reasoning": reasoning,
            "source": source,
            "semantic": True,
            "created_at": datetime.utcnow().isoformat(),
        }
        await self._upsert_relationship(
            source_id=from_entity_id,
            target_id=to_entity_id,
            rel_type=neo4j_rel_type,
            properties=props,
        )

        # Create in-memory SemanticEdge for backward compatibility
        edge = SemanticEdge(
            from_entity=from_entity_id,
            to_entity=to_entity_id,
            relationship_type=relationship_type,
            strength=float(strength),
            evidence=evidence,
            reasoning=reasoning,
            source=source,
            created_at=_time.time(),
        )

        edge_key = (from_entity_id, to_entity_id, relationship_type)
        self.semantic_edges[edge_key] = edge

        logger.info(
            f"Created semantic relationship: {from_entity_id} "
            f"--[{relationship_type}]--> {to_entity_id} (strength: {strength})"
        )
        return edge

    def query_semantic_relationships(
        self,
        relationship_type: str | None = None,
        min_strength: float | None = None,
        from_entity: str | None = None,
        to_entity: str | None = None,
    ) -> list[Any]:
        """Query semantic relationships with filters (from in-memory cache)."""
        results = []
        for _, edge in self.semantic_edges.items():
            if relationship_type and edge.relationship_type != relationship_type:
                continue
            if min_strength is not None and edge.strength < min_strength:
                continue
            if from_entity and edge.from_entity != from_entity:
                continue
            if to_entity and edge.to_entity != to_entity:
                continue
            results.append(edge)

        results.sort(key=lambda e: e.strength, reverse=True)
        return results

    # ──────────────────────────────────────────────────────────────
    # New: Async methods replacing direct dict access
    # ──────────────────────────────────────────────────────────────

    async def get_all_entities(
        self, entity_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Get all entities, optionally filtered by type."""
        if entity_type:
            label = entity_type_to_label(entity_type)
            query = (
                f"MATCH (n:{label}) "
                "RETURN n.id AS id, n.name AS name, n.entity_type AS type, n"
            )
        else:
            query = (
                "MATCH (n:Entity) "
                "RETURN n.id AS id, n.name AS name, n.entity_type AS type, n"
            )

        results = await self.neo4j.execute_read(query)
        entities = []
        for r in results:
            node_data = r["n"]
            entities.append({
                "id": r["id"],
                "name": r.get("name", ""),
                "type": r.get("type", "unknown"),
                "metadata": {k: v for k, v in node_data.items()
                            if k not in ("id", "name", "entity_type",
                                         "canonical_name", "updated_at", "stub")},
            })
        return entities

    async def get_all_edges(
        self, relationship_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Get all edges, optionally filtered by type."""
        if relationship_type:
            query = (
                f"MATCH (a:Entity)-[r:{relationship_type_to_neo4j(relationship_type)}]-(b:Entity) "
                "WHERE a.id < b.id "
                "RETURN a.id AS source, b.id AS target, type(r) AS rel_type, "
                "       r.strength AS strength"
            )
        else:
            query = (
                "MATCH (a:Entity)-[r]-(b:Entity) "
                "WHERE a.id < b.id "
                "RETURN a.id AS source, b.id AS target, type(r) AS rel_type, "
                "       r.strength AS strength"
            )

        results = await self.neo4j.execute_read(query)
        return [
            {
                "source": r["source"],
                "target": r["target"],
                "relationship_type": r["rel_type"].lower(),
                "strength": float(r.get("strength") or 0.5),
            }
            for r in results
        ]

    async def get_document_entities(self, document_path: str) -> set[str]:
        """Get entities mentioned in a specific document."""
        query = (
            "MATCH (e:Entity)-[:MENTIONED_IN]->(d:Document {path: $path}) "
            "RETURN e.id AS entity_id"
        )
        results = await self.neo4j.execute_read(query, {"path": document_path})
        return {r["entity_id"] for r in results if r["entity_id"]}

    async def get_entity_by_id(self, entity_id: str) -> dict[str, Any] | None:
        """Get a single entity by ID from Neo4j."""
        query = "MATCH (n:Entity {id: $id}) RETURN n"
        results = await self.neo4j.execute_read(query, {"id": entity_id})
        if not results:
            return None
        node_data = results[0]["n"]
        return {
            "id": node_data["id"],
            "name": node_data.get("name", ""),
            "type": node_data.get("entity_type", "unknown"),
            "metadata": {k: v for k, v in node_data.items()
                        if k not in ("id", "name", "entity_type",
                                     "canonical_name", "updated_at", "stub")},
        }

    async def get_edge(
        self, source_id: str, target_id: str
    ) -> dict[str, Any] | None:
        """Get a specific edge between two entities."""
        query = (
            "MATCH (a:Entity {id: $source})-[r]-(b:Entity {id: $target}) "
            "RETURN type(r) AS rel_type, r.strength AS strength, "
            "       r.context AS context "
            "LIMIT 1"
        )
        results = await self.neo4j.execute_read(
            query, {"source": source_id, "target": target_id}
        )
        if not results:
            return None
        r = results[0]
        return {
            "source": source_id,
            "target": target_id,
            "relationship_type": r["rel_type"].lower(),
            "strength": float(r.get("strength") or 0.5),
            "context": r.get("context") or [],
        }

    # ──────────────────────────────────────────────────────────────
    # Public CRUD Operations (used by agent tools)
    # ──────────────────────────────────────────────────────────────

    async def add_node(
        self,
        entity_type: str,
        name: str,
        entity_id: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add a new entity node to the graph.

        Args:
            entity_type: Must be a valid type in the domain config.
            name: Display name for the entity.
            entity_id: Optional explicit ID. Auto-generated from name+type if omitted.
            properties: Optional additional properties.

        Returns:
            Dict with id, name, type, properties of the created node.

        Raises:
            ValueError: If entity_type is not in the domain config.
        """
        # Validate name
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name cannot be empty")

        # Validate entity type against domain config
        if not self.domain or entity_type not in self.domain.entities:
            valid = list(self.domain.entities.keys()) if self.domain else []
            raise ValueError(
                f"Invalid entity type '{entity_type}'. "
                f"Valid types: {', '.join(sorted(valid))}"
            )

        # Strip reserved fields from properties to prevent identity corruption
        reserved = {"id", "name", "canonical_name", "entity_type", "updated_at", "aliases", "stub"}
        clean_props = {k: v for k, v in (properties or {}).items() if k not in reserved}

        # Generate ID if not provided
        if not entity_id:
            slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
            if not slug:
                raise ValueError("name must contain at least one alphanumeric character")
            entity_id = f"{entity_type}-{slug}"
        elif not str(entity_id).strip():
            raise ValueError("entity_id cannot be empty")

        label = entity_type_to_label(entity_type)
        node_props = {
            "id": entity_id,
            "name": name,
            "canonical_name": name.lower().strip(),
            **clean_props,
        }
        safe_props = serialize_metadata_for_neo4j(node_props)
        safe_props["entity_type"] = entity_type
        safe_props["updated_at"] = datetime.utcnow().isoformat()
        safe_props["_type_status"] = "canonical"

        # Serialize the existence-check / MERGE / persist / rollback per
        # entity_id so two concurrent add_node calls for the same id can't
        # both observe `existed_before == False` and have the loser's
        # rollback DETACH DELETE the winner's freshly-created node. Inner
        # helpers below (_persist_node_to_file etc.) do not themselves
        # acquire this lock, so there is no re-entrancy risk.
        async with self._get_file_lock(entity_id):
            # Determine whether this MERGE will create a new node or just
            # match an existing one. We need this to know whether a
            # write-through failure should roll the node back: if the node
            # existed before the call, an inability to write a fresh file
            # is recoverable; if the node is brand new and the file write
            # fails, leaving the node behind would create exactly the
            # orphan class we're trying to eliminate.
            existed_before = await self.get_entity_by_id(entity_id) is not None

            query = (
                f"MERGE (n:Entity:{label} {{id: $id}}) "
                f"SET n += $props "
                f"RETURN n"
            )
            await self.neo4j.execute_write(
                query, {"id": entity_id, "props": safe_props}
            )

            await self._record_type_usage("entity", entity_type)

            # Update in-memory cache
            self.nodes[entity_id] = GraphNode(
                id=entity_id,
                name=name,
                type=entity_type,
                metadata=clean_props,
            )

            logger.info(f"Added node: {entity_id} ({entity_type})")

            # Write-through: persist new entity to markdown file. If this
            # fails for a brand-new node we MUST roll back; existing nodes
            # (the MERGE-as-update path) don't get rolled back since the
            # node was already there before our call.
            try:
                persist_result = await self._persist_node_to_file(
                    entity_id, entity_type, name, dict(clean_props)
                )
            except Exception as e:
                persist_result = f"failed:exception:{e}"

            if persist_result.startswith("failed:") and not existed_before:
                logger.error(
                    "Write-through failed for new node %s (%s); rolling back Neo4j MERGE",
                    entity_id,
                    persist_result,
                )
                try:
                    await self.neo4j.execute_write(
                        "MATCH (n:Entity {id: $id}) DETACH DELETE n",
                        {"id": entity_id},
                    )
                finally:
                    self.nodes.pop(entity_id, None)
                raise RuntimeError(
                    f"add_node({entity_id}): file persistence failed ({persist_result}); "
                    f"Neo4j node rolled back to keep file as source of truth"
                )

        if persist_result.startswith("failed:"):
            # Pre-existing node + failed file write. Don't roll back (the node
            # was already there) but log loudly so we can spot accumulating
            # drift.
            logger.warning(
                "Write-through failed for existing node %s (%s); not rolling back",
                entity_id,
                persist_result,
            )

        return {
            "id": entity_id,
            "name": name,
            "type": entity_type,
            "properties": clean_props,
        }

    async def update_node(
        self,
        entity_id: str,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        """Update properties on an existing entity node.

        Args:
            entity_id: The entity to update.
            properties: Properties to merge onto the node.

        Returns:
            Dict with updated node info and list of updated fields.

        Raises:
            ValueError: If the node does not exist.
        """
        # Verify node exists
        existing = await self.get_entity_by_id(entity_id)
        if not existing:
            raise ValueError(f"Node '{entity_id}' not found")

        if not properties:
            raise ValueError("properties cannot be empty")

        # Reject protected identity fields
        reserved = {"id", "entity_type", "canonical_name", "updated_at"}
        invalid = reserved.intersection(properties)
        if invalid:
            raise ValueError(f"Cannot update protected fields: {', '.join(sorted(invalid))}")

        # Validate name if being updated
        if "name" in properties:
            if not isinstance(properties["name"], str) or not properties["name"].strip():
                raise ValueError("name cannot be empty")

        # Separate name from metadata updates
        metadata_updates = {k: v for k, v in properties.items() if k != "name"}

        safe_props = serialize_metadata_for_neo4j(properties)
        safe_props["updated_at"] = datetime.utcnow().isoformat()
        # Keep canonical_name in sync when name changes
        if "name" in properties:
            safe_props["canonical_name"] = properties["name"].lower().strip()

        query = (
            "MATCH (n:Entity {id: $id}) "
            "SET n += $props "
            "RETURN n"
        )
        await self.neo4j.execute_write(
            query, {"id": entity_id, "props": safe_props}
        )

        # Update in-memory cache
        if entity_id in self.nodes:
            self.nodes[entity_id].metadata.update(metadata_updates)
            if "name" in properties:
                self.nodes[entity_id].name = properties["name"]
            self.nodes[entity_id].last_updated = datetime.utcnow()

        updated_fields = list(properties.keys())
        logger.info(f"Updated node {entity_id}: {updated_fields}")
        return {
            "id": entity_id,
            "name": properties.get("name", existing.get("name", "")),
            "type": existing.get("type", "unknown"),
            "properties": {**existing.get("metadata", {}), **properties},
            "updated_fields": updated_fields,
        }

    async def delete_node(
        self,
        entity_id: str,
        cascade: bool = True,
    ) -> dict[str, Any]:
        """Delete an entity node from the graph.

        Args:
            entity_id: The entity to delete.
            cascade: If True, also deletes all relationships (DETACH DELETE).
                     If False, fails when relationships exist.

        Returns:
            Dict with deleted node info and count of removed relationships.

        Raises:
            ValueError: If the node doesn't exist or has relationships when cascade=False.
        """
        # Verify node exists
        existing = await self.get_entity_by_id(entity_id)
        if not existing:
            raise ValueError(f"Node '{entity_id}' not found")

        # Count relationships
        rel_query = (
            "MATCH (n:Entity {id: $id})-[r]-() "
            "RETURN count(r) AS rel_count"
        )
        rel_results = await self.neo4j.execute_read(
            rel_query, {"id": entity_id}
        )
        rel_count = rel_results[0]["rel_count"] if rel_results else 0

        if not cascade and rel_count > 0:
            raise ValueError(
                f"Node '{entity_id}' has {rel_count} relationships. "
                f"Use cascade=True to delete them, or remove relationships first."
            )

        # Delete from Neo4j
        if cascade:
            delete_query = "MATCH (n:Entity {id: $id}) DETACH DELETE n"
        else:
            delete_query = "MATCH (n:Entity {id: $id}) DELETE n"
        await self.neo4j.execute_write(delete_query, {"id": entity_id})

        # Update in-memory caches
        if entity_id in self.nodes:
            del self.nodes[entity_id]

        # Remove edges involving this node
        edge_keys_to_remove = [
            k for k in self.edges if entity_id in k
        ]
        for k in edge_keys_to_remove:
            del self.edges[k]

        # Remove semantic edges involving this node
        for key in list(self.semantic_edges.keys()):
            if entity_id in (key[0], key[1]):
                del self.semantic_edges[key]

        # Remove from document_entities
        for _doc_path, entities in list(self.document_entities.items()):
            entities.discard(entity_id)
        if entity_id in self.entity_documents:
            del self.entity_documents[entity_id]

        # Clean connections on other nodes
        for node in self.nodes.values():
            node.connections.discard(entity_id)

        logger.info(f"Deleted node {entity_id} (cascade={cascade}, rels={rel_count})")

        # Write-through: soft-delete (archive) the entity's markdown file
        await self._archive_entity_file(entity_id)

        return {
            "id": entity_id,
            "name": existing.get("name", ""),
            "type": existing.get("type", "unknown"),
            "relationships_removed": rel_count,
        }

    async def add_edge(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str,
        properties: dict[str, Any] | None = None,
        *,
        allow_provisional: bool = False,
    ) -> dict[str, Any]:
        """Add a relationship between two entities.

        Args:
            source_id: Source entity ID.
            target_id: Target entity ID.
            relationship_type: The relationship type. When
                `allow_provisional=False` (default), must be declared in the
                domain YAML. When True, unknown types are accepted as
                provisional and recorded in the type registry.
            properties: Optional relationship properties.
            allow_provisional: Opt-in to the graduated typing path.

        Returns:
            Dict with edge info.

        Raises:
            ValueError: If relationship type is invalid and
                `allow_provisional=False`, or entities don't exist.
        """
        # Normalize once so Neo4j label, in-memory cache key, registry usage,
        # and file write-through all stamp the same logical name. Canonical
        # types in the domain YAML are authored in snake_case, and reads pull
        # `type(r).lower()` from Neo4j — so lowercase is the canonical form.
        rel_key = relationship_type.strip().lower()

        valid_types: set[str] = set()
        if self.domain:
            for entity_def in self.domain.entities.values():
                for rel in entity_def.relationships:
                    valid_types.add(rel.type)
                    if rel.inverse_name:
                        valid_types.add(rel.inverse_name)

        is_canonical = rel_key in valid_types
        if not is_canonical and not allow_provisional:
            raise ValueError(
                f"Invalid relationship type '{relationship_type}'. "
                f"Valid types: {', '.join(sorted(valid_types))}"
            )

        # Verify both entities exist
        source = await self.get_entity_by_id(source_id)
        if not source:
            raise ValueError(f"Source entity '{source_id}' not found")
        target = await self.get_entity_by_id(target_id)
        if not target:
            raise ValueError(f"Target entity '{target_id}' not found")

        neo4j_rel_type = relationship_type_to_neo4j(rel_key)
        props = serialize_metadata_for_neo4j(properties or {})
        props["updated_at"] = datetime.utcnow().isoformat()
        # NOTE: _type_status is written here but not yet hydrated on read paths
        # (_sync_from_neo4j / neighborhood / get_all_edges / GraphEdge model).
        # Frontend currently distinguishes provisional vs canonical via the
        # /api/type-registry endpoint (joined by type name), so this is a
        # breadcrumb for direct-Cypher consumers and a future adapter upgrade.
        props["_type_status"] = "canonical" if is_canonical else "provisional"

        query = (
            f"MATCH (a:Entity {{id: $source}}) "
            f"MATCH (b:Entity {{id: $target}}) "
            f"MERGE (a)-[r:{neo4j_rel_type}]->(b) "
            f"SET r += $props "
            f"RETURN type(r) AS rel_type"
        )
        await self.neo4j.execute_write(
            query,
            {"source": source_id, "target": target_id, "props": props},
        )

        # Update in-memory cache. rel_key was normalized at entry.
        edge_key = (source_id, rel_key, target_id)
        ctx = (properties or {}).get("context")
        if ctx is None:
            context = []
        elif isinstance(ctx, list):
            context = ctx
        else:
            context = [ctx]
        self.edges[edge_key] = GraphEdge(
            source=source_id,
            target=target_id,
            relationship_type=rel_key,
            strength=float(props.get("strength", 0.5)),
            context=context,
        )
        if source_id in self.nodes:
            self.nodes[source_id].connections.add(target_id)
        if target_id in self.nodes:
            self.nodes[target_id].connections.add(source_id)

        logger.info(
            f"Added edge: {source_id} --[{rel_key}]--> {target_id}"
        )

        # Write-through only for canonical types. Provisional edges live
        # only in Neo4j until an admin promotes them; the rebuild path reads
        # frontmatter through entity_def.relationships (declared types only),
        # so writing a provisional key there would get silently dropped on
        # the next rebuild. See admin `promote` in app/routes/type_registry.py.
        if is_canonical:
            await self._persist_relationship_to_file(
                source_id, target_id, rel_key
            )

        await self._record_type_usage(
            "relationship",
            rel_key,
            context={
                "source_type": source.get("type") if isinstance(source, dict) else None,
                "target_type": target.get("type") if isinstance(target, dict) else None,
            },
        )

        return {
            "source": source_id,
            "target": target_id,
            "relationship_type": rel_key,
            "properties": properties or {},
            "_type_status": props["_type_status"],
        }

    async def _record_type_usage(
        self,
        kind: str,
        name: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort: record type usage in the registry. Never raises."""
        try:
            from app.model_schemas.type_registry import TypeKind
            from app.services.graph.type_registry import get_type_registry_service

            service = get_type_registry_service()
            if service is None:
                return
            domain_id = self.domain.id if self.domain else "unknown"
            kind_enum = TypeKind(kind)
            await service.record_usage(
                name,
                kind_enum,
                domain_id,
                context=context,
            )
        except Exception as e:
            logger.debug(f"[TYPE_REGISTRY] record_usage skipped: {e}")

    async def update_edge(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        """Update properties on an existing relationship.

        Args:
            source_id: Source entity ID.
            target_id: Target entity ID.
            relationship_type: The relationship type to update.
            properties: Properties to merge onto the relationship.

        Returns:
            Dict with updated edge info.

        Raises:
            ValueError: If the edge doesn't exist.
        """
        neo4j_rel_type = relationship_type_to_neo4j(relationship_type)

        # Verify edge exists
        check_query = (
            f"MATCH (a:Entity {{id: $source}})-[r:{neo4j_rel_type}]->(b:Entity {{id: $target}}) "
            f"RETURN type(r) AS rel_type"
        )
        results = await self.neo4j.execute_read(
            check_query, {"source": source_id, "target": target_id}
        )
        if not results:
            raise ValueError(
                f"Edge '{source_id}' --[{relationship_type}]--> '{target_id}' not found"
            )

        safe_props = serialize_metadata_for_neo4j(properties)
        safe_props["updated_at"] = datetime.utcnow().isoformat()

        update_query = (
            f"MATCH (a:Entity {{id: $source}})-[r:{neo4j_rel_type}]->(b:Entity {{id: $target}}) "
            f"SET r += $props "
            f"RETURN type(r) AS rel_type"
        )
        await self.neo4j.execute_write(
            update_query,
            {"source": source_id, "target": target_id, "props": safe_props},
        )

        # Update in-memory cache (lowercase to match _sync_from_neo4j)
        edge_key = (source_id, relationship_type.lower(), target_id)
        if edge_key in self.edges:
            edge = self.edges[edge_key]
            if "strength" in properties:
                edge.strength = float(properties["strength"])
            if "context" in properties:
                ctx = properties["context"]
                edge.context = ctx if isinstance(ctx, list) else [ctx]

        updated_fields = list(properties.keys())
        logger.info(
            f"Updated edge {source_id} --[{relationship_type}]--> {target_id}: "
            f"{updated_fields}"
        )
        return {
            "source": source_id,
            "target": target_id,
            "relationship_type": relationship_type,
            "properties": properties,
            "updated_fields": updated_fields,
        }

    async def delete_edge(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str,
    ) -> dict[str, Any]:
        """Delete a relationship between two entities.

        Args:
            source_id: Source entity ID.
            target_id: Target entity ID.
            relationship_type: The relationship type to delete.

        Returns:
            Dict with deleted edge info.

        Raises:
            ValueError: If the edge doesn't exist.
        """
        neo4j_rel_type = relationship_type_to_neo4j(relationship_type)

        # Verify edge exists
        check_query = (
            f"MATCH (a:Entity {{id: $source}})-[r:{neo4j_rel_type}]->(b:Entity {{id: $target}}) "
            f"RETURN type(r) AS rel_type"
        )
        results = await self.neo4j.execute_read(
            check_query, {"source": source_id, "target": target_id}
        )
        if not results:
            raise ValueError(
                f"Edge '{source_id}' --[{relationship_type}]--> '{target_id}' not found"
            )

        delete_query = (
            f"MATCH (a:Entity {{id: $source}})-[r:{neo4j_rel_type}]->(b:Entity {{id: $target}}) "
            f"DELETE r"
        )
        await self.neo4j.execute_write(
            delete_query, {"source": source_id, "target": target_id}
        )

        # Update in-memory cache (lowercase to match _sync_from_neo4j)
        edge_key = (source_id, relationship_type.lower(), target_id)
        if edge_key in self.edges:
            del self.edges[edge_key]

        # Check if any other edges remain between these nodes before removing connections
        remaining_query = (
            "MATCH (a:Entity {id: $source})-[r]-(b:Entity {id: $target}) "
            "RETURN count(r) AS remaining"
        )
        remaining = await self.neo4j.execute_read(
            remaining_query, {"source": source_id, "target": target_id}
        )
        if remaining and remaining[0]["remaining"] == 0:
            if source_id in self.nodes:
                self.nodes[source_id].connections.discard(target_id)
            if target_id in self.nodes:
                self.nodes[target_id].connections.discard(source_id)

        logger.info(
            f"Deleted edge: {source_id} --[{relationship_type}]--> {target_id}"
        )

        # Write-through: remove relationship from source entity's markdown file
        await self._remove_relationship_from_file(
            source_id, target_id, relationship_type
        )

        return {
            "source": source_id,
            "target": target_id,
            "relationship_type": relationship_type,
        }

    async def merge_nodes(
        self,
        primary_id: str,
        duplicate_id: str,
        strategy: str = "primary_wins",
    ) -> dict[str, Any]:
        """Merge a duplicate node into a primary node.

        All relationships from the duplicate are transferred to the primary.
        The duplicate node is then deleted.

        Args:
            primary_id: The surviving node ID.
            duplicate_id: The node to merge away.
            strategy: How to resolve property conflicts:
                - "primary_wins": Keep primary's properties (default).
                - "duplicate_wins": Overwrite with duplicate's properties.
                - "merge_all": Primary wins on scalar conflicts; lists are
                  concatenated; duplicate fills in keys missing from primary.

        Returns:
            Dict with merged node info, transferred relationship count, aliases.

        Raises:
            ValueError: If either node doesn't exist or strategy is invalid.
        """
        valid_strategies = {"primary_wins", "duplicate_wins", "merge_all"}
        if strategy not in valid_strategies:
            raise ValueError(
                f"Invalid merge strategy '{strategy}'. "
                f"Valid: {', '.join(sorted(valid_strategies))}"
            )

        if not primary_id or not primary_id.strip():
            raise ValueError("primary_id cannot be empty")
        if not duplicate_id or not duplicate_id.strip():
            raise ValueError("duplicate_id cannot be empty")
        if primary_id == duplicate_id:
            raise ValueError("primary_id and duplicate_id must be different")

        # Verify both nodes exist
        primary = await self.get_entity_by_id(primary_id)
        if not primary:
            raise ValueError(f"Primary node '{primary_id}' not found")
        duplicate = await self.get_entity_by_id(duplicate_id)
        if not duplicate:
            raise ValueError(f"Duplicate node '{duplicate_id}' not found")

        # Count relationships on duplicate
        rel_count_query = (
            "MATCH (n:Entity {id: $id})-[r]-() "
            "RETURN count(r) AS rel_count"
        )
        rel_results = await self.neo4j.execute_read(
            rel_count_query, {"id": duplicate_id}
        )
        relationships_transferred = rel_results[0]["rel_count"] if rel_results else 0

        # Transfer all outgoing relationships from duplicate to primary.
        # We read each relationship and recreate it on the primary node
        # (avoids APOC dependency).
        outgoing_query = (
            "MATCH (dup:Entity {id: $dup_id})-[r]->(target:Entity) "
            "WHERE target.id <> $primary_id "
            "RETURN target.id AS target_id, type(r) AS rel_type, properties(r) AS props"
        )
        outgoing = await self.neo4j.execute_read(
            outgoing_query, {"dup_id": duplicate_id, "primary_id": primary_id}
        )
        for rel in outgoing:
            rel_props = rel.get("props", {}) or {}
            rel_props.pop("updated_at", None)
            safe_rel_props = serialize_metadata_for_neo4j(rel_props)
            safe_rel_props["updated_at"] = datetime.utcnow().isoformat()
            merge_query = (
                f"MATCH (a:Entity {{id: $source}}) "
                f"MATCH (b:Entity {{id: $target}}) "
                f"MERGE (a)-[r:{rel['rel_type']}]->(b) "
                f"SET r += $props"
            )
            await self.neo4j.execute_write(
                merge_query,
                {"source": primary_id, "target": rel["target_id"], "props": safe_rel_props},
            )

        # Transfer all incoming relationships from duplicate to primary
        incoming_query = (
            "MATCH (source:Entity)-[r]->(dup:Entity {id: $dup_id}) "
            "WHERE source.id <> $primary_id "
            "RETURN source.id AS source_id, type(r) AS rel_type, properties(r) AS props"
        )
        incoming = await self.neo4j.execute_read(
            incoming_query, {"dup_id": duplicate_id, "primary_id": primary_id}
        )
        for rel in incoming:
            rel_props = rel.get("props", {}) or {}
            rel_props.pop("updated_at", None)
            safe_rel_props = serialize_metadata_for_neo4j(rel_props)
            safe_rel_props["updated_at"] = datetime.utcnow().isoformat()
            merge_query = (
                f"MATCH (a:Entity {{id: $source}}) "
                f"MATCH (b:Entity {{id: $target}}) "
                f"MERGE (a)-[r:{rel['rel_type']}]->(b) "
                f"SET r += $props"
            )
            await self.neo4j.execute_write(
                merge_query,
                {"source": rel["source_id"], "target": primary_id, "props": safe_rel_props},
            )

        # Merge properties based on strategy
        dup_props = duplicate.get("metadata", {})
        primary_props = primary.get("metadata", {})
        if strategy == "duplicate_wins":
            merged_props = {**primary_props, **dup_props}
        elif strategy == "merge_all":
            # Deep merge: concatenate lists, primary wins on scalar conflicts
            merged_props = {**primary_props}
            for k, v in dup_props.items():
                if k not in merged_props:
                    merged_props[k] = v
                elif isinstance(merged_props[k], list) and isinstance(v, list):
                    combined = merged_props[k] + v
                    try:
                        merged_props[k] = list(set(combined))
                    except TypeError:
                        # Unhashable items (e.g. dicts) — order-preserving dedup
                        seen: list = []
                        for item in combined:
                            if item not in seen:
                                seen.append(item)
                        merged_props[k] = seen
        else:
            # primary_wins: primary props take precedence
            merged_props = {**dup_props, **primary_props}

        # Track alias
        aliases_query = (
            "MATCH (n:Entity {id: $id}) "
            "RETURN n.aliases AS aliases"
        )
        alias_results = await self.neo4j.execute_read(
            aliases_query, {"id": primary_id}
        )
        existing_aliases = []
        if alias_results and alias_results[0].get("aliases"):
            existing_aliases = alias_results[0]["aliases"]
            if isinstance(existing_aliases, str):
                existing_aliases = [existing_aliases]

        new_aliases = list(set([*existing_aliases, duplicate_id]))

        # Update primary with merged props and aliases
        update_props = serialize_metadata_for_neo4j(merged_props)
        update_props["aliases"] = new_aliases
        update_props["updated_at"] = datetime.utcnow().isoformat()

        update_query = (
            "MATCH (n:Entity {id: $id}) "
            "SET n += $props"
        )
        await self.neo4j.execute_write(
            update_query, {"id": primary_id, "props": update_props}
        )

        # Delete the duplicate node
        delete_query = "MATCH (n:Entity {id: $id}) DETACH DELETE n"
        await self.neo4j.execute_write(delete_query, {"id": duplicate_id})

        # Update in-memory caches
        if duplicate_id in self.nodes:
            dup_node = self.nodes[duplicate_id]
            # Transfer connections
            if primary_id in self.nodes:
                self.nodes[primary_id].connections.update(dup_node.connections)
                self.nodes[primary_id].connections.discard(primary_id)
                self.nodes[primary_id].connections.discard(duplicate_id)
                self.nodes[primary_id].metadata.update(merged_props)
            del self.nodes[duplicate_id]

        # Rewrite edge keys that referenced the duplicate
        for k in list(self.edges.keys()):
            if duplicate_id in k:
                edge = self.edges.pop(k)
                new_source = primary_id if edge.source == duplicate_id else edge.source
                new_target = primary_id if edge.target == duplicate_id else edge.target
                if new_source != new_target:
                    new_key = (new_source, edge.relationship_type.lower(), new_target)
                    edge.source = new_source
                    edge.target = new_target
                    self.edges[new_key] = edge

        # Clean document_entities
        for _doc_path, entities in self.document_entities.items():
            if duplicate_id in entities:
                entities.discard(duplicate_id)
                entities.add(primary_id)
        if duplicate_id in self.entity_documents:
            if primary_id in self.entity_documents:
                self.entity_documents[primary_id].update(
                    self.entity_documents[duplicate_id]
                )
            else:
                self.entity_documents[primary_id] = self.entity_documents[duplicate_id]
            del self.entity_documents[duplicate_id]

        # Clean connections on other nodes
        for node in self.nodes.values():
            if duplicate_id in node.connections:
                node.connections.discard(duplicate_id)
                if node.id != primary_id:
                    node.connections.add(primary_id)

        logger.info(
            f"Merged node {duplicate_id} into {primary_id} "
            f"(strategy={strategy}, rels_transferred={relationships_transferred})"
        )
        return {
            "id": primary_id,
            "name": primary.get("name", ""),
            "type": primary.get("type", "unknown"),
            "properties": merged_props,
            "relationships_transferred": relationships_transferred,
            "aliases": new_aliases,
        }

    # ──────────────────────────────────────────────────────────────
    # Stub Processing Pipeline
    # ──────────────────────────────────────────────────────────────

    async def get_stub_entities(self) -> list[dict[str, Any]]:
        """Query Neo4j for all entity nodes marked as stubs (no source files)."""
        query = (
            "MATCH (n:Entity) WHERE n.stub = true "
            "RETURN n.id AS id, n.name AS name, n.entity_type AS type, "
            "       n.canonical_name AS canonical_name"
        )
        results = await self.neo4j.execute_read(query)
        return [
            {
                "id": r["id"],
                "name": r.get("name", ""),
                "type": r.get("type", "unknown"),
                "canonical_name": r.get("canonical_name", ""),
            }
            for r in results
        ]

    def _classify_stub(
        self,
        stub_id: str,
        stub_name: str,
        stub_type: str,
        real_entity_ids: dict[str, dict[str, str]],
    ) -> str | None:
        """Classify a stub entity as bad (returns reason) or good (returns None).

        Args:
            stub_id: The stub's entity ID (e.g., "team-grants-team").
            stub_name: The stub's display name.
            stub_type: The stub's entity type.
            real_entity_ids: Dict of entity_id -> {"name": ..., "type": ...}
                for all non-stub entities.

        Returns:
            A reason string if the stub should be deleted, None if it's legitimate.
        """
        # Bad: contains newlines or control characters (YAML contamination)
        if "\n" in stub_id or "\r" in stub_id or "\n" in stub_name:
            return "yaml_contamination: newline in ID or name"

        # Bad: empty or whitespace-only name
        if not stub_name or not stub_name.strip():
            return "empty_name"

        # Bad: the stub ID itself is already a real entity
        if stub_id in real_entity_ids:
            return f"duplicate_of_real: {stub_id}"

        # Compute a canonical slug for comparison
        canonical_stub = re.sub(r"[^a-z0-9]+", "-", stub_name.lower()).strip("-")

        person_types = {"person", "member", "contact"}
        for real_id, real_info in real_entity_ids.items():
            real_type = real_info.get("type")
            type_match = (real_type == stub_type) or \
                         (stub_type in person_types and real_type in person_types)
            if not type_match:
                continue
            real_name = real_info.get("name", "")
            canonical_real = re.sub(r"[^a-z0-9]+", "-", real_name.lower()).strip("-")

            # Bad: same canonical name as a real entity
            if canonical_stub and canonical_real and canonical_stub == canonical_real:
                return f"near_duplicate: canonical name '{canonical_stub}' matches real entity {real_id}"

            # Bad: stub is a shortened form where full-name version exists
            # e.g., stub "team-grants" when "team-grants-team" is real with same display name
            if stub_id != real_id and (
                real_id.startswith(stub_id + "-") or stub_id.startswith(real_id + "-")
            ):
                return f"suffix_variant: stub '{stub_id}' overlaps with real '{real_id}'"

        # Good: legitimate referenced entity without a file yet
        return None

    async def process_stubs(self, dry_run: bool = False) -> dict[str, Any]:
        """Iterate all stub entities, classify each, and delete bad ones.

        Args:
            dry_run: If True, classify but don't delete.

        Returns:
            Dict with counts and details of processed stubs.
        """
        stubs = await self.get_stub_entities()
        if not stubs:
            return {"total_stubs": 0, "bad": 0, "good": 0, "deleted": 0, "details": []}

        # Build map of real (non-stub) entities for comparison
        real_query = (
            "MATCH (n:Entity) WHERE n.stub IS NULL OR n.stub = false "
            "RETURN n.id AS id, n.name AS name, n.entity_type AS type"
        )
        real_results = await self.neo4j.execute_read(real_query)
        real_entity_ids = {
            r["id"]: {"name": r.get("name", ""), "type": r.get("type", "")}
            for r in real_results
        }

        bad_stubs = []
        good_stubs = []

        for stub in stubs:
            reason = self._classify_stub(
                stub["id"], stub["name"], stub["type"], real_entity_ids
            )
            if reason:
                bad_stubs.append({**stub, "reason": reason})
            else:
                good_stubs.append(stub)

        deleted_count = 0
        if not dry_run:
            for bad in bad_stubs:
                try:
                    await self.delete_node(bad["id"], cascade=True)
                    deleted_count += 1
                except Exception:
                    logger.warning(f"Failed to delete stub {bad['id']}", exc_info=True)

        logger.info(
            f"Stub processing: {len(stubs)} total, {len(bad_stubs)} bad, "
            f"{len(good_stubs)} good, {deleted_count} deleted (dry_run={dry_run})"
        )
        return {
            "total_stubs": len(stubs),
            "bad": len(bad_stubs),
            "good": len(good_stubs),
            "deleted": deleted_count,
            "dry_run": dry_run,
            "details": bad_stubs,
        }

    # ──────────────────────────────────────────────────────────────
    # Write-Through Persistence (graph → markdown files)
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _split_frontmatter_and_body(content: str) -> tuple[dict | None, str]:
        """Split markdown content into (frontmatter_dict, body_text).

        Returns (None, original_content) if no valid frontmatter is found.
        """
        if not content or "---" not in content:
            return None, content

        lines = content.split("\n")
        start_idx = None
        end_idx = None
        for i, line in enumerate(lines):
            if line.strip() == "---":
                if start_idx is None:
                    start_idx = i
                elif end_idx is None:
                    end_idx = i
                    break

        if start_idx is None or end_idx is None:
            return None, content

        yaml_content = "\n".join(lines[start_idx + 1 : end_idx])
        body = "\n".join(lines[end_idx + 1 :])

        try:
            metadata = yaml.safe_load(yaml_content) or {}
            return metadata, body
        except yaml.YAMLError:
            return None, content

    @staticmethod
    def _join_frontmatter_and_body(metadata: dict, body: str) -> str:
        """Rejoin a frontmatter dict and body text into a markdown file."""
        yaml_str = yaml.dump(
            metadata,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        return f"---\n{yaml_str}---\n{body}"

    def _find_entity_file(self, entity_id: str) -> str | None:
        """Find the markdown file for an entity on disk.

        Returns the full path if found, else None.
        """
        if "-" not in entity_id:
            return None

        entity_type = entity_id.split("-")[0]

        # Determine candidate directories
        candidate_dirs = []
        if self.domain and entity_type in self.domain.entities:
            entity_def = self.domain.entities[entity_type]
            plural = getattr(entity_def, "plural", None)
            candidate_dirs.append(plural or f"{entity_type}s")
        else:
            # Fallback pluralizations
            _plurals = {
                "person": "people", "project": "projects", "team": "teams",
                "account": "accounts", "contact": "contacts",
                "company": "companies", "opportunity": "opportunities",
                "engagement": "engagements",
            }
            candidate_dirs.append(_plurals.get(entity_type, f"{entity_type}s"))
        candidate_dirs.append(f"entities/{entity_type}")

        # Derive filename from entity_id (strip type prefix)
        slug = entity_id.replace(f"{entity_type}-", "", 1)
        filename = f"{slug}.md"

        repo = self.git_ops.repo_path
        for d in candidate_dirs:
            full = os.path.join(repo, d, filename)
            if os.path.isfile(full):
                return full
        return None

    async def _persist_relationship_to_file(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str,
    ) -> None:
        """Write-through: add a relationship to the source entity's markdown file.

        Reads the raw frontmatter, appends the target_id to the relationship
        type list, and writes back — preserving the top-level key format that
        ``extract_relationship_targets()`` expects during graph build.
        """
        try:
            async with self._get_file_lock(source_id):
                full_path = self._find_entity_file(source_id)
                if not full_path:
                    logger.debug(
                        "Write-through skip (no file): %s → %s", source_id, target_id
                    )
                    return

                with open(full_path, encoding="utf-8") as f:
                    raw = f.read()

                metadata, body = self._split_frontmatter_and_body(raw)
                if metadata is None:
                    return

                # Ensure the relationship type list exists and add target
                existing = metadata.get(relationship_type, [])
                if isinstance(existing, str):
                    existing = [existing] if existing else []
                if not isinstance(existing, list):
                    existing = [str(existing)]

                if target_id in existing:
                    return  # Already present

                existing.append(target_id)
                metadata[relationship_type] = existing

                # Write back
                new_content = self._join_frontmatter_and_body(metadata, body)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(new_content)

                # Commit to git
                rel_path = os.path.relpath(full_path, self.git_ops.repo_path)
                try:
                    await self.git_ops.commit_and_push(
                        [rel_path],
                        f"Graph: add {relationship_type} {source_id} → {target_id}",
                    )
                except Exception as e:
                    logger.warning("Write-through git commit failed: %s", e)

                logger.info(
                    "Write-through: persisted %s %s → %s",
                    relationship_type, source_id, target_id,
                )
        except Exception as e:
            logger.warning(
                "Write-through failed for relationship %s → %s: %s",
                source_id, target_id, e,
            )

    async def _remove_relationship_from_file(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str,
    ) -> None:
        """Write-through: remove a relationship from the source entity's markdown file."""
        try:
            async with self._get_file_lock(source_id):
                full_path = self._find_entity_file(source_id)
                if not full_path:
                    logger.debug(
                        "Write-through skip (no file): remove %s → %s",
                        source_id, target_id,
                    )
                    return

                with open(full_path, encoding="utf-8") as f:
                    raw = f.read()

                metadata, body = self._split_frontmatter_and_body(raw)
                if metadata is None:
                    return

                existing = metadata.get(relationship_type, [])
                if isinstance(existing, str):
                    existing = [existing] if existing else []
                if not isinstance(existing, list):
                    existing = [str(existing)]

                if target_id not in existing:
                    return  # Nothing to remove

                existing.remove(target_id)
                if existing:
                    metadata[relationship_type] = existing
                else:
                    metadata.pop(relationship_type, None)

                new_content = self._join_frontmatter_and_body(metadata, body)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(new_content)

                rel_path = os.path.relpath(full_path, self.git_ops.repo_path)
                try:
                    await self.git_ops.commit_and_push(
                        [rel_path],
                        f"Graph: remove {relationship_type} {source_id} → {target_id}",
                    )
                except Exception as e:
                    logger.warning("Write-through git commit failed: %s", e)

                logger.info(
                    "Write-through: removed %s %s → %s",
                    relationship_type, source_id, target_id,
                )
        except Exception as e:
            logger.warning(
                "Write-through failed for remove %s → %s: %s",
                source_id, target_id, e,
            )

    async def _persist_node_to_file(
        self,
        entity_id: str,
        entity_type: str,
        name: str,
        properties: dict[str, Any] | None = None,
    ) -> str:
        """Write-through: create a markdown file for a new entity.

        Returns one of:
          - "written": new file created on disk
          - "skipped": file already existed (no-op; file is source of truth)
          - "failed:<reason>": save attempt completed but did not produce a file

        Raises: any underlying I/O / validation exception. Callers that need
        to roll back a Neo4j MERGE on file-write failure must NOT swallow
        these — see add_node().
        """
        if self._find_entity_file(entity_id):
            logger.debug("Write-through skip (file exists): %s", entity_id)
            return "skipped"

        from app.services.entity_file_service import EntityFileService

        efs = EntityFileService(domain_config=self.domain)
        clean_props = dict(properties or {})
        entity_payload = {
            "id": entity_id,
            "entity_type": entity_type,
            "attributes": {
                "name": name,
                "canonical_name": name.lower().strip(),
                "source": clean_props.pop("source", "chat_agent"),
                **clean_props,
            },
            "content": f"# {name}\n\nEntity ID: {entity_id}\n",
        }
        success = await efs.save_entity(
            entity_payload,
            commit_message=f"Graph: add entity {entity_id}",
        )
        if success:
            logger.info("Write-through: created file for %s", entity_id)
            return "written"

        logger.warning("Write-through: save_entity returned False for %s", entity_id)
        return "failed:save_entity_returned_false"

    def _is_entity_archived(self, entity_id: str) -> bool:
        """Check if an entity's markdown file has is_archived: true."""
        full_path = self._find_entity_file(entity_id)
        if not full_path:
            return False
        try:
            with open(full_path, encoding="utf-8") as f:
                raw = f.read()
            metadata, _ = self._split_frontmatter_and_body(raw)
            if metadata is None:
                return False
            archived = metadata.get("is_archived")
            if isinstance(archived, str):
                return archived.strip().lower() in ("true", "1", "yes")
            return bool(archived)
        except Exception:
            return False

    async def _archive_entity_file(self, entity_id: str) -> None:
        """Write-through: soft-delete an entity by setting is_archived: true.

        This mirrors the purge script's behavior — the file stays on disk but
        ``build_graph()`` skips it on subsequent rebuilds.
        """
        try:
            async with self._get_file_lock(entity_id):
                full_path = self._find_entity_file(entity_id)
                if not full_path:
                    logger.debug("Write-through skip (no file): archive %s", entity_id)
                    return

                with open(full_path, encoding="utf-8") as f:
                    raw = f.read()

                metadata, body = self._split_frontmatter_and_body(raw)
                if metadata is None:
                    return

                if metadata.get("is_archived"):
                    return  # Already archived

                metadata["is_archived"] = True
                metadata["archived_at"] = datetime.utcnow().isoformat()

                new_content = self._join_frontmatter_and_body(metadata, body)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(new_content)

                rel_path = os.path.relpath(full_path, self.git_ops.repo_path)
                try:
                    await self.git_ops.commit_and_push(
                        [rel_path],
                        f"Graph: archive entity {entity_id}",
                    )
                except Exception as e:
                    logger.warning("Write-through git commit failed: %s", e)

                logger.info("Write-through: archived entity file %s", entity_id)
        except Exception as e:
            logger.warning(
                "Write-through failed for archive %s: %s", entity_id, e
            )

    # ──────────────────────────────────────────────────────────────
    # Cache Management
    # ──────────────────────────────────────────────────────────────

    def invalidate_cache(self) -> None:
        """Invalidate the in-memory cache, forcing next build_graph to rebuild.

        Only clears last_build so the staleness check triggers a rebuild.
        We intentionally keep stale node/edge data available so readers
        see something useful while the rebuild is in progress.
        """
        self.last_build = None
        logger.info("Knowledge graph cache invalidated (will rebuild on next access)")

    def _get_graph_stats(self) -> dict[str, Any]:
        """Get statistics about the knowledge graph."""
        from collections import Counter
        entity_counts = Counter(
            node.type for node in self.nodes.values() if node.type != "document"
        )
        return {
            "total_nodes": len([n for n in self.nodes.values() if n.type != "document"]),
            "total_edges": len(self.edges),
            "entity_counts": dict(entity_counts),
            "total_documents": len(self.document_entities),
            "connection_density": len(self.edges)
            / max(1, len(self.nodes) * (len(self.nodes) - 1) / 2),
        }
