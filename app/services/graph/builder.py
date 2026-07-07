"""
Knowledge Graph Builder

Main KnowledgeGraph class responsible for building and maintaining the graph structure.
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..entity_utils import extract_entity_type_from_id
from ..frontmatter import frontmatter
from .cache import GraphCache
from .models import GraphEdge, GraphNode
from .query_handler import GraphQueryHandler

if TYPE_CHECKING:
    from app.services.knowledge_graph import SemanticEdge

# Configure logger for knowledge graph operations
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# OpenTelemetry imports for manual instrumentation
try:
    from opentelemetry import trace

    OTEL_AVAILABLE = True
    tracer = trace.get_tracer(__name__)
except ImportError:
    OTEL_AVAILABLE = False
    tracer = None


# Minimum shared documents before two entities get a weak-tie co_occurrence
# edge. Parity with Neo4jKnowledgeGraph.CO_OCCURRENCE_MIN_DOCS — keep in sync.
# (Defined here rather than imported to avoid pulling the Neo4j driver into the
# in-memory fallback graph.)
CO_OCCURRENCE_MIN_DOCS = 3


class KnowledgeGraph:
    """Build and query a knowledge graph from document metadata."""

    def __init__(self, git_ops=None, registry=None):
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[tuple[str, str], GraphEdge] = {}
        self.semantic_edges: dict[tuple[str, str, str], SemanticEdge] = {}
        self.document_entities: dict[str, set[str]] = {}  # doc_path -> entity_ids
        self.entity_documents: dict[str, set[str]] = defaultdict(
            set
        )  # entity_id -> doc_paths
        self.last_build: datetime | None = None

        if git_ops is None:
            from ...git_ops import git_ops as default_git_ops
            self.git_ops = default_git_ops
        else:
            self.git_ops = git_ops

        self.registry = registry  # Optional EntityRegistry for canonical resolution

        cache_path = os.path.join(self.git_ops.repo_path, ".knowledge_graph.json")
        self._cache = GraphCache(cache_path)
        self._query_handler = GraphQueryHandler(self.nodes, self.edges, self.document_entities)

    # --- Domain-aware entity type resolution ---

    _FALLBACK_ENTITY_TYPES: set[str] = {"person", "team", "project", "account", "organization"}

    def _get_known_entity_types(self) -> set[str]:
        """Get entity types from the active domain config.

        Falls back to a hardcoded set when no domain is configured so that
        existing deployments without a domain YAML keep working.
        """
        try:
            from app.core.domain_config.domain_config_service import get_domain_config_service
            domain = get_domain_config_service().get_active_domain()
            if domain and domain.entities:
                return set(domain.entities.keys())
        except Exception as exc:
            logger.warning("Failed to load active domain config; using fallback entity types: %s", exc)
        return self._FALLBACK_ENTITY_TYPES

    def _get_known_entity_prefixes(self) -> tuple[str, ...]:
        """Return ``("{type}-", ...)`` for every known entity type."""
        return tuple(f"{t}-" for t in self._get_known_entity_types())

    def _resolve_entity_type_from_id(self, entity_id: str) -> str | None:
        """Derive entity type from an ID prefix like ``person-tom-williams``."""
        for t in self._get_known_entity_types():
            if entity_id.startswith(f"{t}-"):
                return t
        return None

    # Query methods delegated to query handler
    async def find_related_entities(self, entity_id: str, max_results: int = 10) -> list[dict[str, Any]]:
        """Find entities related to the given entity."""
        return await self._query_handler.find_related_entities(entity_id, max_results)

    async def find_related_entities_directed(self, entity_id: str, max_results: int = 100) -> dict[str, list[dict[str, Any]]]:
        """Find related entities separated by direction.

        Uses edge source/target to determine direction for each relationship.
        """
        related = await self.find_related_entities(entity_id, max_results)
        outgoing = []
        incoming = []
        undirected = []
        for r in related:
            rel_type = r.get("relationship", {}).get("type", "")
            if rel_type == "co_occurrence":
                undirected.append(r)
                continue
            # Look up actual edge to determine direction
            related_id = r.get("entity", {}).get("id", "")
            edge = self._query_handler._find_edge_between(entity_id, related_id)
            if edge and edge.source == entity_id:
                outgoing.append(r)
            elif edge and edge.target == entity_id:
                incoming.append(r)
            else:
                outgoing.append(r)  # fallback: treat as outgoing
        return {"outgoing": outgoing, "incoming": incoming, "undirected": undirected}

    async def find_contextual_documents(self, query_entities: list[str], max_results: int = 10) -> list[dict[str, Any]]:
        """Find documents that are contextually relevant to the given entities."""
        return await self._query_handler.find_contextual_documents(query_entities, max_results)

    async def search_by_topic(self, topic: str, max_results: int = 10, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Search for documents and entities related to a topic with fuzzy matching."""
        if force_refresh:
            await self.build_graph(force_rebuild=True)
        return await self._query_handler.search_by_topic(topic, max_results)

    def get_entity_by_name(self, name: str, entity_type: str | None = None) -> dict[str, Any] | None:
        """Find an entity by name and optionally type."""
        return self._query_handler.get_entity_by_name(name, entity_type)

    async def create_semantic_relationship(
        self,
        from_entity_id: str,
        to_entity_id: str,
        relationship_type: str,
        strength: float,
        evidence: str,
        reasoning: str,
        source: str
    ) -> SemanticEdge:
        """Create a semantic relationship with evidence and reasoning.

        Args:
            from_entity_id: Source entity ID
            to_entity_id: Target entity ID
            relationship_type: Domain-defined relationship type
            strength: Confidence score (0.0-1.0)
            evidence: Quote or supporting text
            reasoning: Why this relationship was inferred
            source: Where it came from (e.g., "meeting_completion_skill")

        Raises:
            ValueError: If validation fails
        """
        # Import at method level to avoid circular dependencies
        import time

        from app.core.domain_config.domain_config_service import get_domain_config_service
        from app.services.knowledge_graph import SemanticEdge

        # Validate strength
        if not isinstance(strength, (int, float)):
            raise TypeError(f"Strength must be numeric, got {type(strength).__name__}")
        if strength < 0.0 or strength > 1.0:
            raise ValueError(f"Strength must be between 0.0 and 1.0, got {strength}")

        # Validate evidence and reasoning not empty
        if not evidence or not evidence.strip():
            raise ValueError("Evidence cannot be empty")
        if not reasoning or not reasoning.strip():
            raise ValueError("Reasoning cannot be empty")

        # Validate relationship type against domain schema
        domain_service = get_domain_config_service()
        active_domain = domain_service.get_active_domain()

        if not active_domain:
            raise ValueError("No active domain configuration found")

        # Collect all valid relationship types from domain schema
        valid_types = set()
        for _, entity_schema in active_domain.entities.items():
            for rel in entity_schema.relationships:
                valid_types.add(rel.type)  # Use rel.type, NOT rel.name

        if relationship_type not in valid_types:
            raise ValueError(
                f"Invalid relationship type '{relationship_type}'. "
                f"Valid types: {', '.join(sorted(valid_types))}"
            )

        # Validate entities exist (optional - could skip for now)
        if from_entity_id not in self.nodes:
            logger.warning(f"Entity {from_entity_id} not found in graph")
        if to_entity_id not in self.nodes:
            logger.warning(f"Entity {to_entity_id} not found in graph")

        # Create semantic edge
        edge = SemanticEdge(
            from_entity=from_entity_id,
            to_entity=to_entity_id,
            relationship_type=relationship_type,
            strength=float(strength),
            evidence=evidence,
            reasoning=reasoning,
            source=source,
            created_at=time.time()
        )

        # Store in a separate semantic edges dictionary (add to __init__ if not exists)
        if not hasattr(self, 'semantic_edges'):
            self.semantic_edges = {}

        # Key by (from, to, type) to allow multiple relationship types between same entities
        edge_key = (from_entity_id, to_entity_id, relationship_type)
        self.semantic_edges[edge_key] = edge

        logger.info(
            f"Created semantic relationship: {from_entity_id} --[{relationship_type}]--> {to_entity_id} "
            f"(strength: {strength})"
        )
        return edge

    def query_semantic_relationships(
        self,
        relationship_type: str | None = None,
        min_strength: float | None = None,
        from_entity: str | None = None,
        to_entity: str | None = None
    ) -> list[Any]:
        """Query semantic relationships with optional filters.

        Args:
            relationship_type: Filter by specific relationship type
            min_strength: Minimum strength threshold (0.0-1.0)
            from_entity: Filter by source entity ID
            to_entity: Filter by target entity ID

        Returns:
            List of SemanticEdge objects matching the filters
        """
        results = []

        for _, edge in self.semantic_edges.items():
            # Apply filters
            if relationship_type and edge.relationship_type != relationship_type:
                continue

            if min_strength is not None and edge.strength < min_strength:
                continue

            if from_entity and edge.from_entity != from_entity:
                continue

            if to_entity and edge.to_entity != to_entity:
                continue

            results.append(edge)

        # Sort by strength (highest first)
        results.sort(key=lambda e: e.strength, reverse=True)

        return results

    def invalidate_cache(self) -> None:
        """Invalidate the cache by setting last_build to None."""
        self.last_build = None
        self._cache.invalidate_cache()

    async def build_graph(self, force_rebuild: bool = False, **kwargs) -> dict[str, Any]:
        """Build the knowledge graph from all document metadata."""
        if kwargs:
            logger.debug("Ignoring build_graph kwargs: %s", ", ".join(kwargs))

        # Create span if OpenTelemetry is available
        if OTEL_AVAILABLE and tracer:
            with tracer.start_as_current_span("knowledge_graph.build") as span:
                span.set_attribute("graph.force_rebuild", force_rebuild)
                span.set_attribute("graph.nodes_before", len(self.nodes))
                span.set_attribute("graph.edges_before", len(self.edges))

                result = await self._build_graph_with_span(force_rebuild, span)

                span.set_attribute("graph.nodes_after", len(self.nodes))
                span.set_attribute("graph.edges_after", len(self.edges))
                span.set_attribute("graph.build_complete", True)

                return result
        else:
            return await self._build_graph_with_span(force_rebuild, None)

    async def _build_graph_with_span(
        self, force_rebuild: bool, span: Any | None
    ) -> dict[str, Any]:
        """Internal method to build graph with optional span."""
        if not force_rebuild and self.last_build and self._cache.is_cache_valid(self.last_build):
            if span:
                span.add_event("cache_hit", attributes={"cache.valid": True})
            await self._load_from_cache()
            return self._get_graph_stats()

        print("Building knowledge graph from document metadata...")

        # Clear existing graph
        self.nodes.clear()
        self.edges.clear()
        self.document_entities.clear()
        self.entity_documents.clear()

        # Get all markdown files using existing method
        all_files = []
        try:
            # Use the existing read_markdown_files method but get paths only
            files_obj = await self.git_ops.read_markdown_files()
            all_files = [f.path for f in files_obj]
        except Exception as e:
            print(f"Error getting markdown files: {e}")
            # Fallback: scan directory directly
            import os

            for root, _, files in os.walk(self.git_ops.repo_path):
                for file in files:
                    if file.endswith(".md"):
                        rel_path = os.path.relpath(
                            os.path.join(root, file), self.git_ops.repo_path
                        )
                        all_files.append(rel_path)

        # Filter out README files
        markdown_files = [f for f in all_files if not f.endswith("README.md")]

        processed_files = 0

        if span:
            span.set_attribute("graph.file_count", len(markdown_files))

        for file_path in markdown_files:
            try:
                content = await self.git_ops.read_file(file_path)
                if not content:
                    continue

                # Try flexible YAML extraction first (handles YAML blocks anywhere in file)
                metadata = None
                if "---" in content:
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
                        yaml_lines = lines[start_idx + 1 : end_idx]
                        yaml_content = "\n".join(yaml_lines)
                        try:
                            import yaml

                            metadata = yaml.safe_load(yaml_content)
                        except yaml.YAMLError:
                            pass

                # Fall back to standard frontmatter if no inline YAML found
                if not metadata:
                    metadata = frontmatter.extract_all(content)

                if not metadata:
                    continue

                # Skip archived entities — soft-deleted via purge or UI
                # (Matches the check in neo4j_graph.py for parity)
                archived = metadata.get("is_archived", False)
                if isinstance(archived, str):
                    archived = archived.strip().lower() in ("true", "1", "yes")
                elif not isinstance(archived, bool):
                    archived = bool(archived)
                if archived:
                    logger.debug(f"Skipping archived entity: {file_path}")
                    continue

                # Process entities from metadata
                await self._process_document_entities(file_path, metadata)
                processed_files += 1

                if span and processed_files % 10 == 0:
                    span.add_event(
                        "progress_update",
                        attributes={
                            "files_processed": processed_files,
                            "percentage": (processed_files / len(markdown_files)) * 100,
                        },
                    )

            except Exception as e:
                print(f"Error processing {file_path}: {e}")
                if span:
                    span.record_exception(e)
                continue

        if span:
            span.set_attribute("graph.files_processed", processed_files)

        # Build relationships between co-occurring entities
        await self._build_relationships()

        # Purge orphan entity nodes — entities with zero connections after
        # relationship building.  Mirrors the Neo4j purge_orphan_nodes.py
        # logic so in-memory fallback stays clean too.
        orphan_count = self._purge_orphan_nodes()
        if orphan_count:
            logger.info(f"Purged {orphan_count} orphan entity nodes from in-memory graph")

        # Set timestamp first so cache metadata is valid across restarts
        self.last_build = datetime.utcnow()
        await self._save_to_cache()

        stats = self._get_graph_stats()
        print(
            f"Knowledge graph built: {processed_files} files, {stats['total_nodes']} nodes, "
            f"{stats['total_edges']} edges ({orphan_count} orphans purged)"
        )
        return stats

    async def _process_document_entities(
        self, file_path: str, metadata: dict[str, Any]
    ):
        """Extract entities from document metadata - purely metadata-driven, no detection logic."""
        logger.info(f"Processing document entities for: {file_path}")

        if not metadata:
            logger.warning(f"No metadata provided for document: {file_path}")
            return

        if not isinstance(metadata, dict):
            logger.error(f"Invalid metadata type for {file_path}: {type(metadata)}")
            return

        document_entities = set()

        # Migration: Remove any existing topic entities for this document
        await self._cleanup_topic_entities_for_document(file_path)

        # Skip creating document nodes for entity profile files
        # Use schema (YAML frontmatter) to determine if this is an entity profile
        # Schema sources (in order of precedence):
        # 1. id field with recognized prefix (e.g., "person-tom-williams")
        # 2. type / entity_type field with a value from the active domain config
        entity_id = metadata.get("id", "")
        entity_type_field = metadata.get("type", "") or metadata.get("entity_type", "")
        known_types = self._get_known_entity_types()
        known_prefixes = self._get_known_entity_prefixes()

        is_entity_profile = (
            (isinstance(entity_id, str) and entity_id.startswith(known_prefixes))
            or (isinstance(entity_type_field, str) and entity_type_field in known_types)
        )

        if not is_entity_profile:
            # Create document entity for non-entity files
            doc_id = f"doc:{file_path}"
            logger.debug(f"Creating document entity: {doc_id}")

            doc_node = GraphNode(
                id=doc_id,
                name=os.path.basename(file_path),
                type="document",
                metadata={
                    "path": file_path,
                    "type": metadata.get("type", "document"),
                    "created": metadata.get("created"),
                    "modified": metadata.get("modified"),
                },
            )
            self.nodes[doc_id] = doc_node
            document_entities.add(doc_id)

        # Process entity profile documents - create nodes from their own ID field
        entity_from_profile = self._process_entity_profile(metadata, file_path)
        if entity_from_profile:
            document_entities.add(entity_from_profile)
            logger.debug(f"Processed entity profile: {entity_from_profile}")

        # Process referenced entities from explicit metadata fields.
        # Legacy per-type processors are called first for backwards compat,
        # then a generic pass picks up any domain-defined entity type that
        # the legacy processors don't cover (e.g. member, focus_area, cohort).
        _legacy_types = {"person", "project", "team", "account"}

        people_processed = self._process_people_entities(metadata, file_path)
        document_entities.update(people_processed)

        projects_processed = self._process_project_entities(metadata, file_path)
        document_entities.update(projects_processed)

        teams_processed = self._process_team_entities(metadata, file_path)
        document_entities.update(teams_processed)

        accounts_processed = self._process_account_entities(metadata, file_path)
        document_entities.update(accounts_processed)

        # Generic pass for domain-defined types not covered above
        generic_processed = self._process_generic_referenced_entities(
            metadata, file_path, skip_types=_legacy_types
        )
        document_entities.update(generic_processed)

        # Store document entity associations
        self.document_entities[file_path] = document_entities
        for entity_id in document_entities:
            self.entity_documents[entity_id].add(file_path)

        logger.info(
            f"Completed processing document: {file_path} - {len(people_processed)} people, {len(projects_processed)} projects, {len(teams_processed)} teams"
        )

    async def _build_relationships(self):
        """Build relationships between entities based on explicit metadata and co-occurrence."""
        # First, build explicit relationships from entity metadata
        await self._build_explicit_relationships()

        # Then, co-occurrence as a genuine weak-tie layer: only when entities
        # share >= CO_OCCURRENCE_MIN_DOCS documents, and never when a typed
        # relationship already connects them. Parity with
        # Neo4jKnowledgeGraph._build_co_occurrence_relationships. A single shared
        # meeting is NOT a relationship — that was the main source of profile
        # attribution leakage (co-attendees inheriting each other's activities).

        # Pairs already joined by a typed (non co-occurrence) edge — skip these.
        typed_pairs: set[frozenset] = {
            frozenset((edge.source, edge.target))
            for (_s, rel_type, _t), edge in self.edges.items()
            if rel_type != "co_occurrence"
        }

        # Count shared documents per entity pair across all documents.
        pair_docs: dict[tuple[str, str], list[str]] = defaultdict(list)
        for doc_path, entities in self.document_entities.items():
            entities_list = sorted(entities)
            for i, entity1 in enumerate(entities_list):
                for entity2 in entities_list[i + 1:]:
                    if entity1 != entity2:
                        pair_docs[(entity1, entity2)].append(doc_path)

        for (entity1, entity2), docs in pair_docs.items():
            doc_count = len(docs)
            if doc_count < CO_OCCURRENCE_MIN_DOCS:
                continue
            if frozenset((entity1, entity2)) in typed_pairs:
                continue
            # Strength tiers mirror the Neo4j builder.
            if doc_count > 5:
                strength = 1.0
            elif doc_count > 2:
                strength = 0.7
            else:
                strength = 0.5
            self._create_relationship(
                entity1, entity2, "co_occurrence", strength, docs
            )

    def _purge_orphan_nodes(self) -> int:
        """Remove entity nodes that have zero connections after relationship building.

        An orphan is an entity node (person, project, team, account, etc.) with
        no edges to any other node.  Document nodes are excluded — they are
        structural, not entities.

        This is the in-memory equivalent of purge_orphan_nodes.py's Neo4j
        Cypher queries and prevents stale entities from appearing in the graph
        when running without Neo4j.
        """
        orphan_ids: list[str] = []
        for node_id, node in self.nodes.items():
            # Keep document nodes — they're structural, not entities
            if node.type == "document":
                continue
            # An entity with zero connections is an orphan
            if not node.connections:
                orphan_ids.append(node_id)

        if not orphan_ids:
            return 0

        for node_id in orphan_ids:
            # Remove from nodes dict
            del self.nodes[node_id]
            # Clean up edges (shouldn't have any, but be safe)
            edge_keys_to_remove = [
                k for k in self.edges if node_id in k
            ]
            for k in edge_keys_to_remove:
                del self.edges[k]
            # Clean up entity<->document mappings
            if node_id in self.entity_documents:
                for doc_path in self.entity_documents[node_id]:
                    if doc_path in self.document_entities:
                        self.document_entities[doc_path].discard(node_id)
                del self.entity_documents[node_id]

            logger.debug(f"Purged orphan node: {node_id}")

        return len(orphan_ids)

    async def _build_explicit_relationships(self):
        """Build relationships from explicit metadata fields in entity files.

        This processes relationship fields like:
        - manages_accounts, works_on_projects, member_of_team (for persons)
        - account relationships, project assignments, team memberships
        """
        # Define relationship field mappings: field_name -> (relationship_type, target_prefix)
        relationship_fields = {
            # Person relationships
            "manages_accounts": ("manages", "account-"),
            "works_on_projects": ("works_on", "project-"),
            "member_of_team": ("member_of", "team-"),
            "reports_to": ("reports_to", "person-"),
            "manages": ("manages", "person-"),
            "collaborates_with": ("collaborates_with", "person-"),
            # Project relationships
            "account": ("belongs_to", "account-"),
            "team": ("assigned_to", "team-"),
            "members": ("has_member", "person-"),
            "discussed_topic": ("discussed_by", "person-"),  # Meeting-generated relationships
            # Team relationships
            "lead": ("led_by", "person-"),
            "projects": ("works_on", "project-"),
        }

        explicit_edges_created = 0

        for node_id, node in self.nodes.items():
            # Skip document nodes
            if node.type == "document":
                continue

            metadata = node.metadata or {}

            for field_name, (rel_type, target_prefix) in relationship_fields.items():
                field_value = metadata.get(field_name)

                if field_value is None:
                    continue

                # Handle both single values and lists
                targets = field_value if isinstance(field_value, list) else [field_value]

                for target in targets:
                    if not target or not isinstance(target, str):
                        continue

                    # Normalize target ID
                    target_id = target.strip()

                    # If target doesn't have the expected prefix, try to find it
                    if not target_id.startswith(target_prefix):
                        # Try to find the target in existing nodes
                        found_target = None
                        for existing_id in self.nodes.keys():
                            if (
                                existing_id.startswith(target_prefix)
                                and (existing_id.endswith(target_id) or target_id in existing_id)
                            ):
                                found_target = existing_id
                                break
                        if found_target:
                            target_id = found_target
                        else:
                            # Skip if we can't find the target
                            continue

                    # Only create edge if target exists
                    if target_id in self.nodes:
                        self._create_relationship(
                            node_id,
                            target_id,
                            rel_type,
                            0.8,  # Higher strength for explicit relationships
                            [f"explicit:{field_name}"]
                        )
                        explicit_edges_created += 1

        logger.info(f"Created {explicit_edges_created} explicit relationship edges")

    def _create_relationship(self, entity1: str, entity2: str, relationship_type: str, strength: float, context: list[str]):
        """Create or update a relationship between two entities."""
        # Normalize undirected relationship keys so A-B and B-A collapse
        if relationship_type == "co_occurrence":
            source, target = sorted((entity1, entity2))
        else:
            source, target = entity1, entity2
        edge_key = (source, relationship_type, target)

        if edge_key in self.edges:
            # Update existing relationship
            edge = self.edges[edge_key]
            edge.strength = max(edge.strength, strength)
            edge.context.extend(context)
        else:
            # Create new relationship
            edge = GraphEdge(
                source=source,
                target=target,
                relationship_type=relationship_type,
                strength=strength,
                context=context
            )
            self.edges[edge_key] = edge

            # Update node connections
            if entity1 in self.nodes:
                self.nodes[entity1].connections.add(entity2)
            if entity2 in self.nodes:
                self.nodes[entity2].connections.add(entity1)

    async def _cleanup_topic_entities_for_document(self, file_path: str):
        """Remove any existing topic entities associated with this document (migration logic)."""
        logger.debug(f"Cleaning up topic entities for document: {file_path}")

        entities_to_remove = []
        edges_to_remove = []

        # Find topic entities associated with this document
        for entity_id, documents in self.entity_documents.items():
            if entity_id.startswith("topic:") and file_path in documents:
                logger.info(f"Found topic entity to remove: {entity_id}")
                entities_to_remove.append(entity_id)

        # Remove topic entities and their connections
        for entity_id in entities_to_remove:
            if entity_id in self.nodes:
                # Remove edges connected to this topic entity
                for edge_key, _ in list(self.edges.items()):
                    if entity_id in edge_key:
                        edges_to_remove.append(edge_key)
                        logger.debug(
                            f"Removing edge involving topic entity: {edge_key}"
                        )

                # Remove the node
                del self.nodes[entity_id]
                logger.debug(f"Removed topic node: {entity_id}")

            # Clean up document associations
            self.entity_documents[entity_id].discard(file_path)
            if not self.entity_documents[entity_id]:
                del self.entity_documents[entity_id]

        # Remove edges
        for edge_key in edges_to_remove:
            if edge_key in self.edges:
                del self.edges[edge_key]

        # Clean up document entities mapping
        if file_path in self.document_entities:
            old_entities = self.document_entities[file_path]
            topic_entities = {e for e in old_entities if e.startswith("topic:")}
            if topic_entities:
                logger.info(
                    f"Cleaned {len(topic_entities)} topic entities from document: {file_path}"
                )
                self.document_entities[file_path] = old_entities - topic_entities

    def _process_people_entities(
        self, metadata: dict[str, Any], file_path: str
    ) -> set[str]:
        """Process people entities from explicit metadata fields only."""
        people_entities = set()
        processed_names = set()  # Prevent duplicates

        # 1. From explicit people field
        people_list = metadata.get("people", [])
        if isinstance(people_list, list):
            for person_name in people_list:
                if isinstance(person_name, str) and person_name.strip():
                    person_id = self._create_person_entity(
                        person_name.strip(), file_path
                    )
                    if person_id and person_id not in processed_names:
                        people_entities.add(person_id)
                        processed_names.add(person_id)

        # 2. From legacy participants field (backward compatibility)
        summary = metadata.get("summary", {})
        if isinstance(summary, dict):
            participants = summary.get("participants", [])
            if isinstance(participants, list):
                for person_name in participants:
                    if isinstance(person_name, str) and person_name.strip():
                        person_id = self._create_person_entity(
                            person_name.strip(), file_path
                        )
                        if person_id and person_id not in processed_names:
                            people_entities.add(person_id)
                            processed_names.add(person_id)

        # 3. From name field (ONLY if document's id indicates it's a person)
        # Use schema (id prefix) to determine entity type, NOT directory structure
        entity_id = metadata.get("id", "")
        is_person_entity = isinstance(entity_id, str) and entity_id.startswith("person-")

        if is_person_entity:
            name_field = metadata.get("name")
            if isinstance(name_field, str) and name_field.strip():
                person_id = self._create_person_entity(
                    name_field.strip(), file_path, metadata
                )
                if person_id and person_id not in processed_names:
                    people_entities.add(person_id)
                    processed_names.add(person_id)

        return people_entities

    def _normalize_person_name(self, person_name: str) -> tuple[str, str]:
        """Normalize person name by extracting base name and title.

        Returns:
            Tuple of (base_name, title)
        """
        # Handle patterns like "David Kim (IT Director)" or "Sarah Chen (Global Technology Practice Lead)"
        match = re.match(r"^([^(]+?)(?:\\s*\\(([^)]+)\\))?$", person_name.strip())
        if match:
            base_name = match.group(1).strip()
            title = match.group(2).strip() if match.group(2) else ""
            return base_name, title
        return person_name.strip(), ""

    def _find_best_person_match(self, name: str) -> str | None:
        """Find the best matching person entity for a given name."""
        if not self.registry:
            return None

        # First try exact match
        # Try to determine entity type from the name format
        entity_type = extract_entity_type_from_id(name)
        if entity_type:
            canonical = self.registry.get_canonical_entity(entity_id=name, entity_type=entity_type)
        else:
            # Assume it's a person if no type prefix
            canonical = self.registry.get_canonical_entity(entity_id=name, entity_type="person")

        if canonical:
            return canonical["id"]

        # Try to find if this is a partial name match
        name_parts = name.lower().split()
        best_match = None
        best_score = 0

        for person_id, person_model in self.registry.people.items():
            # Access dict values (registry.people returns dicts, not Pydantic models)
            person_name = person_model["canonical_name"].lower()
            person_parts = person_name.split()

            # Check if all parts of the query name are in the person name
            if all(part in person_parts for part in name_parts):
                score = len(name_parts) / len(person_parts)
                if score > best_score:
                    best_score = score
                    best_match = person_id

        return best_match

    def _create_person_entity(
        self,
        person_name: str,
        file_path: str,
        profile_metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Create a person entity from a clean name."""
        if not person_name:
            return None

        # Normalize the name to extract base name and title
        base_name, extracted_title = self._normalize_person_name(person_name)

        # Use registry for canonical resolution if available
        # Note: registry can be either EntityRegistry (has get_canonical_entity) or
        # EntityRepository (doesn't have it), so check for method existence
        if self.registry and hasattr(self.registry, 'get_canonical_entity'):
            # Look up using base name for better matching
            # Try with person entity type since this is _create_person_entity
            canonical_entity = self.registry.get_canonical_entity(entity_id=base_name, entity_type="person")
            if canonical_entity:
                person_id = canonical_entity["id"]
                canonical_name = canonical_entity["canonical_name"]
            else:
                # Try to find partial matches (e.g., "David" matching "David Kim")
                best_match = self._find_best_person_match(base_name)
                if best_match:
                    # best_match should be a person ID
                    entity_type = extract_entity_type_from_id(best_match) or "person"
                    canonical_entity = self.registry.get_canonical_entity(
                        entity_id=best_match, entity_type=entity_type
                    )
                    if canonical_entity:
                        person_id = canonical_entity["id"]
                        canonical_name = canonical_entity["canonical_name"]
                    else:
                        person_id = best_match
                        person_model = self.registry.people.get(best_match)
                        canonical_name = (
                            person_model["canonical_name"] if person_model else base_name
                        )
                else:
                    # Register new person if not found
                    titles = []
                    if extracted_title:
                        titles.append(extracted_title)
                    if profile_metadata and profile_metadata.get("title"):
                        titles.append(profile_metadata.get("title"))

                    entity_id = self.registry.register_person(
                        canonical_name=base_name, titles=titles
                    )
                    canonical_entity = self.registry.get_canonical_entity(
                        entity_id=entity_id, entity_type="person"
                    )
                    if canonical_entity:
                        person_id = canonical_entity["id"]
                        canonical_name = canonical_entity["canonical_name"]
                    else:
                        # Fallback if entity lookup fails after registration
                        person_id = entity_id
                        canonical_name = base_name
        else:
            # Fallback to simple ID generation using base name
            person_id = f"person-{base_name.lower().replace(' ', '-')}"
            canonical_name = base_name

        if person_id not in self.nodes:
            # Basic metadata
            person_metadata = {}

            # Add profile metadata if this is a person profile document
            if profile_metadata:
                # Store all metadata for relationship building
                person_metadata.update(
                    {
                        "title": profile_metadata.get("title", ""),
                        "role": profile_metadata.get("role", ""),
                        "department": profile_metadata.get("department", ""),
                        "company": profile_metadata.get("company", ""),
                        "contact": profile_metadata.get("contact", ""),
                        "email": profile_metadata.get("email", ""),
                        # Relationship fields for edge building
                        "manages_accounts": profile_metadata.get("manages_accounts", []),
                        "works_on_projects": profile_metadata.get("works_on_projects", []),
                        "member_of_team": profile_metadata.get("member_of_team"),
                        "reports_to": profile_metadata.get("reports_to"),
                        "manages": profile_metadata.get("manages", []),
                        "collaborates_with": profile_metadata.get("collaborates_with", []),
                    }
                )

            self.nodes[person_id] = GraphNode(
                id=person_id,
                name=canonical_name,  # Use canonical name
                type="person",
                metadata=person_metadata,
            )
            logger.debug(
                f"Created person entity: {person_id} with canonical name: {canonical_name}"
            )

        # Associate with document
        self.nodes[person_id].documents.add(file_path)
        return person_id

    def _process_project_entities(
        self, metadata: dict[str, Any], file_path: str
    ) -> set[str]:
        """Process project entities from explicit metadata fields only."""
        project_entities = set()

        # Only from explicit projects field
        projects_list = metadata.get("projects", [])
        if isinstance(projects_list, list):
            for project_name in projects_list:
                if isinstance(project_name, str) and project_name.strip():
                    project_id = self._create_project_entity(
                        project_name.strip(), file_path
                    )
                    if project_id:
                        project_entities.add(project_id)

        return project_entities

    def _create_project_entity(
        self, project_name: str, file_path: str
    ) -> str | None:
        """Create a project entity."""
        if not project_name:
            return None

        # Use registry for canonical resolution if available
        if self.registry:
            # Try to determine if project_name has entity type prefix
            entity_type = extract_entity_type_from_id(project_name)
            if entity_type:
                canonical_entity = self.registry.get_canonical_entity(
                    entity_id=project_name, entity_type=entity_type
                )
            else:
                # Assume it's a project if no type prefix
                canonical_entity = self.registry.get_canonical_entity(
                    entity_id=project_name, entity_type="project"
                )

            if canonical_entity:
                project_id = canonical_entity["id"]
                canonical_name = canonical_entity["canonical_name"]
            else:
                # Register new project if not found
                entity_id = self.registry.register_project(
                    canonical_name=project_name, status="active"
                )
                canonical_entity = self.registry.get_canonical_entity(
                    entity_id=entity_id, entity_type="project"
                )
                if canonical_entity:
                    project_id = canonical_entity["id"]
                    canonical_name = canonical_entity["canonical_name"]
                else:
                    # Fallback if entity lookup fails after registration
                    project_id = entity_id
                    canonical_name = project_name
        else:
            # Fallback to simple ID generation
            project_id = f"project-{project_name.lower().replace(' ', '-')}"
            canonical_name = project_name

        if project_id not in self.nodes:
            self.nodes[project_id] = GraphNode(
                id=project_id,
                name=canonical_name,  # Use canonical name
                type="project",
                metadata={"status": "active"},
            )
            logger.debug(f"Created project entity: {project_id}")

        self.nodes[project_id].documents.add(file_path)
        return project_id

    def _process_team_entities(
        self, metadata: dict[str, Any], file_path: str
    ) -> set[str]:
        """Process team entities from explicit metadata fields only."""
        team_entities = set()

        # Only from explicit teams field
        teams_list = metadata.get("teams", [])
        if isinstance(teams_list, list):
            for team_name in teams_list:
                if isinstance(team_name, str) and team_name.strip():
                    team_id = self._create_team_entity(team_name.strip(), file_path)
                    if team_id:
                        team_entities.add(team_id)

        return team_entities

    def _create_team_entity(self, team_name: str, file_path: str) -> str | None:
        """Create a team entity."""
        if not team_name:
            return None

        # Use registry for canonical resolution if available
        if self.registry:
            # Try to determine if team_name has entity type prefix
            entity_type = extract_entity_type_from_id(team_name)
            if entity_type:
                canonical_entity = self.registry.get_canonical_entity(
                    entity_id=team_name, entity_type=entity_type
                )
            else:
                # Assume it's a team if no type prefix
                canonical_entity = self.registry.get_canonical_entity(
                    entity_id=team_name, entity_type="team"
                )

            if canonical_entity:
                team_id = canonical_entity["id"]
                canonical_name = canonical_entity["canonical_name"]
            else:
                # Register new team if not found
                entity_id = self.registry.register_team(name=team_name)
                canonical_entity = self.registry.get_canonical_entity(
                    entity_id=entity_id, entity_type="team"
                )
                if canonical_entity:
                    team_id = canonical_entity["id"]
                    canonical_name = canonical_entity["canonical_name"]
                else:
                    # Fallback if entity lookup fails after registration
                    team_id = entity_id
                    canonical_name = team_name
        else:
            # Fallback to simple ID generation
            team_id = f"team-{team_name.lower().replace(' ', '-')}"
            canonical_name = team_name

        if team_id not in self.nodes:
            self.nodes[team_id] = GraphNode(
                id=team_id,
                name=canonical_name,  # Use canonical name
                type="team",
                metadata={},
            )
            logger.debug(f"Created team entity: {team_id}")

        self.nodes[team_id].documents.add(file_path)
        return team_id

    def _process_entity_profile(
        self, metadata: dict[str, Any], file_path: str
    ) -> str | None:
        """Process an entity profile document and create a node.

        This handles entity profile files that have schema information in YAML frontmatter.
        Schema sources (in order of precedence):
        1. id field with recognized prefix (e.g., "person-tom-williams")
        2. type / entity_type field + filename (e.g., type: "person")
        """
        entity_id = metadata.get("id")
        entity_type_field = metadata.get("type", "") or metadata.get("entity_type", "")
        known_types = self._get_known_entity_types()

        # If we have an id field, use it directly
        if entity_id and isinstance(entity_id, str):
            # Determine entity type from ID prefix dynamically
            entity_type = self._resolve_entity_type_from_id(entity_id)
            if entity_type is None:
                return None
        # Otherwise, if we have a recognized type field, derive ID from filename
        elif isinstance(entity_type_field, str) and entity_type_field in known_types:
            entity_type = entity_type_field
            # Derive ID from filename (e.g., "person-tom-williams.md" -> "person-tom-williams")
            filename = os.path.basename(file_path)
            entity_id = os.path.splitext(filename)[0]  # Remove .md extension
        else:
            # Not an entity profile
            return None

        entity_name = metadata.get("name", "")
        if not entity_name:
            # Derive name from ID
            entity_name = entity_id.replace("-", " ").title()

        # Don't create if already exists (might have been created by reference)
        if entity_id in self.nodes:
            # Update metadata if we have more info
            existing_node = self.nodes[entity_id]
            if metadata:
                # Merge in any new metadata
                for key, value in metadata.items():
                    if key not in existing_node.metadata or not existing_node.metadata[key]:
                        existing_node.metadata[key] = value
            existing_node.documents.add(file_path)
            return entity_id

        # Create the entity node with full metadata
        self.nodes[entity_id] = GraphNode(
            id=entity_id,
            name=entity_name,
            type=entity_type,
            metadata=metadata.copy(),  # Store all metadata for relationship building
        )
        self.nodes[entity_id].documents.add(file_path)
        logger.debug(f"Created {entity_type} entity from profile: {entity_id}")

        return entity_id

    def _process_generic_referenced_entities(
        self, metadata: dict[str, Any], file_path: str,
        skip_types: set[str] | None = None,
    ) -> set[str]:
        """Process referenced entities for any domain-defined type.

        Looks for frontmatter list fields whose key matches an entity type's
        plural form (from the domain config).  For example, a domain with
        entity type ``focus_area`` (plural ``focus_areas``) will pick up a
        YAML field ``focus_areas: ["childcare-policy", ...]`` and create
        graph nodes for each entry.

        Also handles the ``people:`` field generically when ``person`` is a
        known type, creating person entity nodes from name strings.
        """
        if skip_types is None:
            skip_types = set()

        entities: set[str] = set()

        try:
            from app.core.domain_config.domain_config_service import get_domain_config_service
            domain = get_domain_config_service().get_active_domain()
            if not domain or not domain.entities:
                return entities
        except Exception as exc:
            logger.debug("Skipping generic referenced entities: active domain unavailable (%s)", exc)
            return entities

        for entity_type, entity_def in domain.entities.items():
            if entity_type in skip_types:
                continue

            # Look for the plural field name in metadata (e.g. focus_areas, cohorts)
            plural = getattr(entity_def, "plural", None) or f"{entity_type}s"
            ref_list = metadata.get(plural, [])
            if not isinstance(ref_list, list):
                # Also try singular field (e.g. cohort: "nyc-2023")
                singular_val = metadata.get(entity_type)
                if singular_val and isinstance(singular_val, str):
                    ref_list = [singular_val]
                else:
                    continue

            for ref_value in ref_list:
                if not isinstance(ref_value, str) or not ref_value.strip():
                    continue
                ref_value = ref_value.strip()

                # Build entity ID: prefix with type if not already prefixed
                if ref_value.startswith(f"{entity_type}-"):
                    eid = ref_value
                else:
                    slug = re.sub(r"[^a-z0-9]+", "-", ref_value.lower()).strip("-")
                    if not slug:
                        continue
                    eid = f"{entity_type}-{slug}"

                # Derive display name
                display_name = ref_value.replace("-", " ").title()

                if eid not in self.nodes:
                    self.nodes[eid] = GraphNode(
                        id=eid,
                        name=display_name,
                        type=entity_type,
                        metadata={},
                    )
                    logger.debug(f"Created {entity_type} entity from reference: {eid}")

                self.nodes[eid].documents.add(file_path)
                entities.add(eid)

        return entities

    def _process_account_entities(
        self, metadata: dict[str, Any], file_path: str
    ) -> set[str]:
        """Process account entities from explicit metadata fields."""
        account_entities = set()

        # From explicit accounts field
        accounts_list = metadata.get("accounts", [])
        if isinstance(accounts_list, list):
            for account_name in accounts_list:
                if isinstance(account_name, str) and account_name.strip():
                    account_id = self._create_account_entity(
                        account_name.strip(), file_path
                    )
                    if account_id:
                        account_entities.add(account_id)

        return account_entities

    def _create_account_entity(
        self, account_name: str, file_path: str
    ) -> str | None:
        """Create an account entity."""
        if not account_name:
            return None

        # Normalize account ID
        if account_name.startswith("account-"):
            account_id = account_name
        else:
            account_id = f"account-{account_name.lower().replace(' ', '-')}"

        canonical_name = account_name.replace("account-", "").replace("-", " ").title()

        if account_id not in self.nodes:
            self.nodes[account_id] = GraphNode(
                id=account_id,
                name=canonical_name,
                type="account",
                metadata={},
            )
            logger.debug(f"Created account entity: {account_id}")

        self.nodes[account_id].documents.add(file_path)
        return account_id

    # Cache-related methods
    async def _save_to_cache(self):
        """Save the knowledge graph to cache file."""
        await self._cache.save_to_cache(self.nodes, self.edges, self.document_entities, self.last_build)

    async def _load_from_cache(self):
        """Load the knowledge graph from cache file."""
        nodes, edges, document_entities, last_build = await self._cache.load_from_cache()
        # Update in place to keep GraphQueryHandler references valid
        self.nodes.clear()
        self.nodes.update(nodes)
        self.edges.clear()
        self.edges.update(edges)
        self.document_entities.clear()
        self.document_entities.update(document_entities)
        self.last_build = last_build

        # Rebuild entity_documents mapping
        self.entity_documents.clear()
        for doc_path, entities in self.document_entities.items():
            for entity_id in entities:
                self.entity_documents[entity_id].add(doc_path)

    def _get_graph_stats(self) -> dict[str, Any]:
        """Get statistics about the knowledge graph."""
        return self._query_handler.get_graph_stats()
