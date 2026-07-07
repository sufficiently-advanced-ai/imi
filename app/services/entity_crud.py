"""
Entity CRUD Service - Issue #244.

This service provides CRUD operations for domain-aware entity management.
It integrates with the EntityRegistry for validation and the unified
KnowledgeGraph for relationship management.
"""

import logging
from datetime import datetime
from typing import Any

from app.core.dependencies import get_entity_repository
from app.domain.entities.services import EntityRepository

from ..model_schemas.domain_config import DomainConfiguration
from .entity_file_service import EntityFileService
from .graph import clear_knowledge_graph_cache

logger = logging.getLogger(__name__)


class EntityCrudService:
    """Service for domain-aware entity CRUD operations."""

    def __init__(
        self,
        domain_config: DomainConfiguration | None = None,
        entity_registry: EntityRepository | None = None,
    ):
        """
        Initialize the CRUD service.

        Args:
            domain_config: Domain configuration to use
            entity_registry: Entity registry instance
        """
        self.domain_config = domain_config
        self.entity_registry = entity_registry or get_entity_repository()

        # Initialize entity file service for persistent storage
        self.entity_file_service = EntityFileService(domain_config)

        # Load domain config into registry if provided
        if domain_config:
            self.entity_registry.load_domain_config(domain_config)

    async def list_entities(
        self,
        page: int = 1,
        size: int = 20,
        filters: dict[str, Any] | None = None,
        search_query: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """
        List entities with pagination, filtering, and sorting.

        Args:
            page: Page number (1-based)
            size: Page size
            filters: Filters to apply
            search_query: Search query string
            sort_by: Field to sort by
            sort_order: Sort direction (asc/desc)

        Returns:
            Dictionary with entities list and pagination info
        """
        try:
            # Get all entities from file storage
            entity_type_filter = filters.get("entity_type") if filters else None
            include_archived = (
                filters.get("include_archived", False) if filters else False
            )

            # Use entity file service to get entities
            all_entities = await self.entity_file_service.list_entities(
                entity_type=entity_type_filter,
                include_archived=include_archived,
                filters=filters,
            )

            # Apply search
            if search_query:
                all_entities = self._apply_search(all_entities, search_query)

            # Apply sorting
            all_entities = self._apply_sorting(all_entities, sort_by, sort_order)

            # Calculate pagination
            total_count = len(all_entities)
            offset = (page - 1) * size
            paginated_entities = all_entities[offset : offset + size]

            return {
                "entities": paginated_entities,
                "pagination": {
                    "page": page,
                    "size": size,
                    "total": total_count,
                    "pages": (total_count + size - 1) // size,
                },
            }
        except Exception as e:
            logger.error(f"Error listing entities: {e}")
            return {
                "entities": [],
                "pagination": {"page": page, "size": size, "total": 0, "pages": 0},
                "error": str(e),
            }

    async def create_entity(self, entity_data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new entity with validation.

        Args:
            entity_data: Entity data including type and attributes

        Returns:
            Result dictionary with success status and entity data
        """
        try:
            entity_type = entity_data.get("entity_type")
            attributes = entity_data.get("attributes", {})
            relationships = entity_data.get("relationships", {})

            if not entity_type:
                return {"success": False, "error": "entity_type is required"}

            # Validate entity data
            is_valid, errors = self.entity_registry.validate_entity(
                entity_type, attributes
            )
            if not is_valid:
                return {"success": False, "validation_errors": errors}

            # Create entity using registry
            entity = self.entity_registry.create_entity(entity_type, attributes)

            # Add relationships if provided
            if relationships:
                entity["relationships"] = relationships.copy()

            # Save entity to file storage
            commit_message = (
                f"Created {entity_type}: {attributes.get('name', entity['id'])}"
            )
            saved = await self.entity_file_service.save_entity(entity, commit_message)

            if not saved:
                return {"success": False, "error": "Failed to save entity to storage"}

            # Clear knowledge graph cache to reflect new entity
            clear_knowledge_graph_cache()

            logger.info(f"Created entity {entity['id']} of type {entity_type}")

            return {"success": True, "entity_id": entity["id"], "entity": entity}

        except Exception as e:
            logger.error(f"Error creating entity: {e}")
            return {"success": False, "error": str(e)}

    async def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """
        Get an entity by ID.

        Args:
            entity_id: The entity ID

        Returns:
            The entity or None if not found
        """
        try:
            # Use entity file service to get entity
            return await self.entity_file_service.get_entity(entity_id)

        except Exception as e:
            logger.error(f"Error getting entity {entity_id}: {e}")
            return None

    async def update_entity(
        self, entity_id: str, update_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Update an entity.

        Args:
            entity_id: The entity ID
            update_data: Data to update (attributes, relationships)

        Returns:
            Result dictionary with success status and updated entity
        """
        try:
            # Find existing entity
            existing_entity = await self.get_entity(entity_id)
            if not existing_entity:
                return {"success": False, "error": "Entity not found"}

            entity_type = existing_entity["entity_type"]

            # Update attributes if provided
            if "attributes" in update_data:
                # Merge with existing attributes
                new_attributes = existing_entity["attributes"].copy()
                new_attributes.update(update_data["attributes"])

                # Validate updated attributes
                is_valid, errors = self.entity_registry.validate_entity(
                    entity_type, new_attributes
                )
                if not is_valid:
                    return {"success": False, "validation_errors": errors}

                existing_entity["attributes"] = new_attributes

            # Update relationships if provided
            if "relationships" in update_data:
                existing_entity["relationships"] = update_data["relationships"].copy()

            # Update timestamp
            existing_entity["updated_at"] = datetime.utcnow().isoformat()

            # Save updated entity to file storage
            commit_message = f"Updated {entity_type}: {existing_entity['attributes'].get('name', entity_id)}"
            saved = await self.entity_file_service.save_entity(
                existing_entity, commit_message
            )

            if not saved:
                return {"success": False, "error": "Failed to save updated entity"}

            # Keep the live graph consistent with the file (source of truth).
            # Prefer an in-place node sync over a full rebuild so readers see the
            # edit immediately without re-ingesting the whole corpus.
            await self._sync_graph_node(entity_id, update_data)

            logger.info(f"Updated entity {entity_id}")

            return {"success": True, "entity": existing_entity}

        except Exception as e:
            logger.error(f"Error updating entity {entity_id}: {e}")
            return {"success": False, "error": str(e)}

    async def _sync_graph_node(
        self, entity_id: str, update_data: dict[str, Any]
    ) -> None:
        """Sync an attribute update into the live graph after the file is saved.

        The markdown file is the source of truth. We always invalidate the
        graph caches (``clear_knowledge_graph_cache`` covers both the Neo4j
        graph cache and the domain-graph visualization response cache) so the
        UI reflects the edit on the next read. For attribute edits we ALSO push
        the change into the live Neo4j node in place, so direct Neo4j consumers
        (MCP cypher, semantic search) see it immediately rather than waiting for
        the next rebuild.

        Never raises: a sync failure must not fail the user's edit, which is
        already durably persisted to disk.
        """
        from .graph.factory import get_semantica_knowledge

        # Attribute edits: push into the live node in place for immediate
        # Neo4j-direct freshness. Relationship edits are edges, not node props —
        # the cache invalidation below lets the rebuild materialize them.
        if "relationships" not in update_data:
            attributes = update_data.get("attributes") or {}
            # update_node rejects these; drop them so it doesn't raise.
            reserved = {"id", "entity_type", "canonical_name", "updated_at"}
            props = {k: v for k, v in attributes.items() if k not in reserved}
            if props:
                try:
                    # Use the Semantica (Neo4j-backed) graph — update_node is a
                    # Neo4j API; the legacy graph accessor may not implement it.
                    graph = get_semantica_knowledge()
                    if graph is None:
                        raise RuntimeError("Semantica knowledge graph unavailable")
                    await graph.update_node(entity_id, props)
                except Exception as e:
                    # Non-fatal: the cache invalidation below still makes the
                    # next read rebuild from the (correct) file.
                    logger.warning(
                        "In-place graph node sync failed for %s "
                        "(will rebuild on next read): %s",
                        entity_id,
                        e,
                    )

        # Always invalidate so the visualization + Neo4j caches refresh.
        clear_knowledge_graph_cache()

    async def delete_entity(
        self,
        entity_id: str,
        reason: str | None = None,
        handle_relationships: str = "preserve",
    ) -> dict[str, Any]:
        """
        Soft delete an entity.

        Args:
            entity_id: The entity ID
            reason: Reason for deletion
            handle_relationships: How to handle relationships ("preserve", "cascade", "reassign")

        Returns:
            Result dictionary with success status
        """
        try:
            # Use the underlying file service directly so a transient storage
            # error propagates instead of being silently swallowed by
            # self.get_entity() — that would otherwise drop us into the
            # graph-only delete path and hard-delete a perfectly valid node.
            existing_entity = await self.entity_file_service.get_entity(entity_id)
            if not existing_entity:
                # File-backed lookup confirmed the markdown file is missing.
                # The entity may still exist in Neo4j as an auto-extracted
                # node (e.g. a person mentioned in a meeting transcript that
                # never produced a markdown file). Fall back to a graph-only
                # delete so the user can still remove unwanted nodes.
                return await self._delete_graph_only_entity(
                    entity_id, reason, handle_relationships=handle_relationships
                )

            # Mark as archived/deleted (soft delete)
            now = datetime.utcnow().isoformat()
            existing_entity["is_archived"] = True
            existing_entity["deleted_at"] = now
            existing_entity["updated_at"] = now

            if reason:
                existing_entity["deletion_reason"] = reason

            # Handle relationships
            relationships_preserved = 0
            if handle_relationships == "preserve":
                # Keep relationships intact
                relationships = existing_entity.get("relationships", {})
                relationships_preserved = sum(
                    len(rel) if isinstance(rel, list) else 1
                    for rel in relationships.values()
                )

            # Save deleted entity to mark it as archived
            commit_message = f"Deleted {existing_entity['entity_type']}: {existing_entity['attributes'].get('name', entity_id)}"
            saved = await self.entity_file_service.save_entity(
                existing_entity, commit_message
            )

            if not saved:
                return {"success": False, "error": "Failed to save deletion status"}

            # Clear knowledge graph cache to reflect deletion
            clear_knowledge_graph_cache()

            logger.info(f"Deleted entity {entity_id} (soft delete)")

            return {
                "success": True,
                "deleted_at": now,
                "is_archived": True,
                "relationships_preserved": relationships_preserved,
            }

        except Exception as e:
            logger.error(f"Error deleting entity {entity_id}: {e}")
            return {"success": False, "error": str(e)}

    async def _delete_graph_only_entity(
        self,
        entity_id: str,
        reason: str | None = None,
        handle_relationships: str = "preserve",
    ) -> dict[str, Any]:
        """Fallback delete for entities that exist in Neo4j but have no
        backing markdown file (typically auto-extracted from meetings).

        Returns the same shape as the main delete path so the route layer
        doesn't need to special-case the response.

        `handle_relationships` is honored so the public delete contract
        applies even on this path:
        - "preserve": keep the node's relationships intact (cascade=False)
        - "cascade":  detach-delete the node and its edges (cascade=True)
        Any other mode (e.g. "reassign") is rejected — the graph-only path
        has no markdown source to read reassignment targets from.
        """
        # Lazy imports to keep the existing module-load order intact and
        # avoid any chance of a circular import via the graph factory.
        from .graph.factory import get_knowledge_graph

        if handle_relationships not in ("preserve", "cascade"):
            return {
                "success": False,
                "error": (
                    f"handle_relationships='{handle_relationships}' is not "
                    "supported for graph-only entities (no markdown source "
                    "to drive reassignment)."
                ),
            }

        graph = get_knowledge_graph()
        existing = await graph.get_entity_by_id(entity_id)
        if not existing:
            # Genuinely missing in both file storage and the graph.
            return {"success": False, "error": "Entity not found"}

        cascade = handle_relationships == "cascade"
        try:
            deleted = await graph.delete_node(
                entity_id=entity_id, cascade=cascade
            )
        except Exception as e:
            logger.error(
                f"Graph-only delete failed for {entity_id}: {e}"
            )
            return {"success": False, "error": f"Graph delete failed: {e}"}

        clear_knowledge_graph_cache()
        now = datetime.utcnow().isoformat()
        rels_removed = (
            deleted.get("relationships_removed", 0)
            if isinstance(deleted, dict)
            else 0
        )
        logger.info(
            f"Deleted graph-only entity {entity_id} "
            f"(no source file; cascade={cascade}; {rels_removed} edges removed)"
        )

        return {
            "success": True,
            "deleted_at": now,
            "is_archived": True,
            # When cascade=True we removed edges; when "preserve" we kept them.
            "relationships_preserved": 0 if cascade else rels_removed,
            "source": "graph_only",
            "deletion_reason": reason,
        }

    def _apply_filters(
        self, entities: list[dict[str, Any]], filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Apply filters to entity list."""
        filtered = []

        for entity in entities:
            # Skip archived entities unless explicitly requested
            if entity.get("is_archived") and not filters.get("include_archived", False):
                continue

            # Apply entity type filter
            if "entity_type" in filters:
                if entity.get("entity_type") != filters["entity_type"]:
                    continue

            # Apply attribute filters
            match = True
            attributes = entity.get("attributes", {})

            for filter_key, filter_value in filters.items():
                if filter_key in ["entity_type", "include_archived"]:
                    continue

                # Handle date range filters
                if filter_key.endswith("_from") or filter_key.endswith("_to"):
                    # Extract base field name
                    base_field = filter_key.rsplit("_", 1)[0]
                    if base_field in attributes:
                        attr_value = attributes.get(base_field)
                        if attr_value:
                            # Parse dates for comparison
                            from datetime import datetime

                            if isinstance(attr_value, str):
                                try:
                                    attr_date = datetime.fromisoformat(
                                        attr_value.replace("Z", "+00:00")
                                    )
                                except ValueError:
                                    continue
                            else:
                                attr_date = attr_value

                            filter_date = datetime.fromisoformat(
                                filter_value.replace("Z", "+00:00")
                            )

                            if filter_key.endswith("_from") and attr_date < filter_date:
                                match = False
                                break
                            elif filter_key.endswith("_to") and attr_date > filter_date:
                                match = False
                                break
                    continue

                # Handle min/max numeric filters
                if filter_key.endswith("_min") or filter_key.endswith("_max"):
                    # Extract base field name
                    base_field = filter_key.rsplit("_", 1)[0]
                    if base_field in attributes:
                        attr_value = attributes.get(base_field)
                        if attr_value is not None:
                            try:
                                attr_num = float(attr_value)
                                filter_num = float(filter_value)

                                if (
                                    filter_key.endswith("_min")
                                    and attr_num < filter_num
                                ):
                                    match = False
                                    break
                                elif (
                                    filter_key.endswith("_max")
                                    and attr_num > filter_num
                                ):
                                    match = False
                                    break
                            except (ValueError, TypeError):
                                continue
                    continue

                # Exact match filter
                if filter_key in attributes:
                    if attributes[filter_key] != filter_value:
                        match = False
                        break

            if match:
                filtered.append(entity)

        return filtered

    def _apply_search(
        self, entities: list[dict[str, Any]], search_query: str
    ) -> list[dict[str, Any]]:
        """Apply text search to entity list."""
        search_terms = search_query.lower().split()
        filtered = []

        for entity in entities:
            # Search in attributes
            text_content = []
            attributes = entity.get("attributes", {})

            for _key, value in attributes.items():
                if isinstance(value, str):
                    text_content.append(value.lower())

            # Check if any search term matches
            entity_text = " ".join(text_content)
            if any(term in entity_text for term in search_terms):
                filtered.append(entity)

        return filtered

    def _apply_sorting(
        self, entities: list[dict[str, Any]], sort_by: str, sort_order: str
    ) -> list[dict[str, Any]]:
        """Apply sorting to entity list."""
        reverse = sort_order.lower() == "desc"

        def get_sort_key(entity):
            # Try entity-level fields first
            if sort_by in entity:
                return entity[sort_by] or ""

            # Try attribute fields
            attributes = entity.get("attributes", {})
            if sort_by in attributes:
                value = attributes[sort_by]
                return value if value is not None else ""

            # Default to empty string
            return ""

        try:
            return sorted(entities, key=get_sort_key, reverse=reverse)
        except Exception as e:
            logger.error(f"Error sorting entities by {sort_by}: {e}")
            return entities
