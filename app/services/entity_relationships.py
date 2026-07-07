"""
Entity Relationship Service - Issue #244.

This service handles relationship management between entities,
including bidirectional relationship synchronization and
cardinality constraint validation.
"""

import logging
from typing import Any

from app.core.dependencies import get_entity_repository
from app.domain.entities.services import EntityRepository

from ..model_schemas.domain_config import DomainConfiguration
from .domain_graph_service import clear_graph_cache
from .entity_file_service import EntityFileService, clear_entity_cache

logger = logging.getLogger(__name__)


class EntityRelationshipService:
    """Service for managing entity relationships."""

    def __init__(
        self,
        domain_config: DomainConfiguration | None = None,
        entity_registry: EntityRepository | None = None,
    ):
        """
        Initialize the relationship service.

        Args:
            domain_config: Domain configuration (deprecated - use entity_registry.domain_config)
            entity_registry: Entity registry instance
        """
        self.entity_registry = entity_registry or get_entity_repository()

        # Get domain config from registry singleton (already loaded by domain switch endpoint)
        # The domain_config parameter is kept for backward compatibility but not used
        self.domain_config = self.entity_registry.domain_config

        # Initialize file service for persistence
        self.entity_file_service = EntityFileService(self.domain_config)

        # Relationship storage: source_entity_id -> {rel_name: [target_ids]}
        # Note: This is now used only for request-scoped caching
        # Persistence is handled via entity_file_service
        self._relationships: dict[str, dict[str, list[str]]] = {}

    async def _load_entity_from_file(self, entity_id: str) -> dict[str, Any] | None:
        """
        Load entity data from its markdown file.

        Args:
            entity_id: The entity ID

        Returns:
            Entity dictionary with relationships from frontmatter, or None if not found
        """
        try:
            entity = await self.entity_file_service.get_entity(entity_id)
            return entity
        except Exception:
            logger.exception(f"Error loading entity {entity_id} from file")
            return None

    async def _save_entity_with_relationships(
        self,
        entity_id: str,
        relationships: dict[str, list[str]],
        commit_message: str
    ) -> bool:
        """
        Save relationship changes to entity file.

        Args:
            entity_id: The entity ID
            relationships: Dictionary of {relationship_type: [target_ids]}
            commit_message: Git commit message

        Returns:
            True if successful, False otherwise
        """
        try:
            # Load existing entity
            entity = await self._load_entity_from_file(entity_id)
            if not entity:
                logger.error(f"Cannot save relationships - entity {entity_id} not found")
                return False

            # Update relationships in entity
            entity["relationships"] = relationships

            # Save to file with git commit
            success = await self.entity_file_service.save_entity(entity, commit_message)

            if success:
                # Clear caches to force reload
                entity_type = self._extract_entity_type(entity_id)
                clear_entity_cache(entity_type, entity_id)
                clear_graph_cache()
                logger.info(f"Saved relationships for {entity_id} to file")

            return success

        except Exception as e:
            logger.error(f"Error saving relationships for {entity_id}: {e}")
            return False

    async def add_relationship(
        self, source_entity_id: str, relationship_type: str, target_entity_id: str
    ) -> dict[str, Any]:
        """
        Add a relationship between two entities.

        Args:
            source_entity_id: ID of the source entity
            relationship_type: Type of relationship
            target_entity_id: ID of the target entity

        Returns:
            Result dictionary with success status
        """
        try:
            # Validate domain config is loaded
            logger.debug(f"add_relationship: entity_registry.domain_config = {self.entity_registry.domain_config}")
            if not self.entity_registry.domain_config:
                logger.error("Domain configuration not loaded in entity registry")
                return {
                    "success": False,
                    "error": "Domain configuration not loaded. Please switch to a domain first."
                }

            # Extract entity types from IDs
            source_entity_type = self._extract_entity_type(source_entity_id)
            target_entity_type = self._extract_entity_type(target_entity_id)
            logger.debug(f"add_relationship: source_type={source_entity_type}, target_type={target_entity_type}")

            if not source_entity_type or not target_entity_type:
                return {"success": False, "error": "Invalid entity ID format"}

            # TODO: Add validation for target entity existence
            # This would require access to the entity storage service
            # For now, we validate the relationship schema only

            # Validate relationship exists in schema
            if not self.entity_registry.validate_relationship(
                source_entity_type, relationship_type, target_entity_type
            ):
                return {
                    "success": False,
                    "error": f"Invalid relationship '{relationship_type}' between {source_entity_type} and {target_entity_type}",
                }

            # Get relationship schema for cardinality validation
            source_schema = self.entity_registry.get_entity_schema(source_entity_type)
            logger.debug(f"add_relationship: source_schema type = {type(source_schema)}, value={source_schema}")
            if not source_schema:
                logger.error(
                    f"Entity schema not found for type '{source_entity_type}' "
                    f"(domain: {self.entity_registry.domain_config.id if self.entity_registry.domain_config else 'none'})"
                )
                return {
                    "success": False,
                    "error": f"Unknown entity type: {source_entity_type}",
                }

            # Safely access relationships_dict
            try:
                relationships_dict = source_schema.relationships_dict
                if not isinstance(relationships_dict, dict):
                    logger.error(
                        f"relationships_dict returned {type(relationships_dict)} instead of dict "
                        f"for entity type '{source_entity_type}'"
                    )
                    return {
                        "success": False,
                        "error": f"Invalid schema structure for entity type: {source_entity_type}",
                    }
                rel_schema = relationships_dict.get(relationship_type)
            except Exception as e:
                logger.error(
                    f"Error accessing relationships_dict for '{source_entity_type}': {e}",
                    exc_info=True
                )
                return {
                    "success": False,
                    "error": f"Schema access error for entity type: {source_entity_type}",
                }

            if not rel_schema:
                return {
                    "success": False,
                    "error": f"Relationship '{relationship_type}' not found in schema",
                }

            # Load source entity from file to get current relationships
            source_entity = await self._load_entity_from_file(source_entity_id)
            if not source_entity:
                return {
                    "success": False,
                    "error": f"Source entity {source_entity_id} not found in storage"
                }

            # Get or initialize relationships
            if "relationships" not in source_entity:
                source_entity["relationships"] = {}
            if relationship_type not in source_entity["relationships"]:
                source_entity["relationships"][relationship_type] = []

            # Check cardinality constraints
            existing_relationships = source_entity["relationships"][relationship_type]
            if rel_schema.cardinality in ["one-to-one", "many-to-one"]:
                if existing_relationships:
                    return {
                        "success": False,
                        "error": f"Cardinality violation: {relationship_type} allows only one target",
                    }

            # Add the relationship if not already present
            if target_entity_id not in existing_relationships:
                existing_relationships.append(target_entity_id)

                # Save to file
                commit_msg = f"Add relationship: {source_entity_id} {relationship_type} {target_entity_id}"
                saved = await self._save_entity_with_relationships(
                    source_entity_id,
                    source_entity["relationships"],
                    commit_msg
                )

                if not saved:
                    return {
                        "success": False,
                        "error": "Failed to persist relationship to file"
                    }

            # Handle bidirectional relationships
            bidirectional_created = False
            inverse_rel = self.entity_registry.get_inverse_relationship(
                source_entity_type, relationship_type
            )
            if inverse_rel:
                # Load target entity and add inverse relationship
                target_entity = await self._load_entity_from_file(target_entity_id)
                if target_entity:
                    if "relationships" not in target_entity:
                        target_entity["relationships"] = {}
                    if inverse_rel["name"] not in target_entity["relationships"]:
                        target_entity["relationships"][inverse_rel["name"]] = []

                    if source_entity_id not in target_entity["relationships"][inverse_rel["name"]]:
                        target_entity["relationships"][inverse_rel["name"]].append(source_entity_id)

                        # Save target entity
                        commit_msg = f"Add inverse relationship: {target_entity_id} {inverse_rel['name']} {source_entity_id}"
                        await self._save_entity_with_relationships(
                            target_entity_id,
                            target_entity["relationships"],
                            commit_msg
                        )
                        bidirectional_created = True

            logger.info(
                f"Added relationship {relationship_type} from {source_entity_id} to {target_entity_id}"
            )

            return {"success": True, "bidirectional_created": bidirectional_created}

        except Exception as e:
            logger.error(f"Error adding relationship: {e}")
            return {"success": False, "error": str(e)}

    async def remove_relationship(
        self, source_entity_id: str, relationship_type: str, target_entity_id: str
    ) -> dict[str, Any]:
        """
        Remove a relationship between two entities.

        Args:
            source_entity_id: ID of the source entity
            relationship_type: Type of relationship
            target_entity_id: ID of the target entity

        Returns:
            Result dictionary with success status
        """
        try:
            # Load source entity from file
            source_entity = await self._load_entity_from_file(source_entity_id)
            if not source_entity:
                return {
                    "success": False,
                    "error": f"Source entity {source_entity_id} not found in storage"
                }

            # Check if relationship exists
            if "relationships" not in source_entity:
                return {
                    "success": False,
                    "error": f"No relationships found for entity {source_entity_id}"
                }

            if relationship_type not in source_entity["relationships"]:
                return {
                    "success": False,
                    "error": f"Relationship type '{relationship_type}' not found"
                }

            relationships = source_entity["relationships"][relationship_type]
            if target_entity_id not in relationships:
                return {
                    "success": False,
                    "error": f"Relationship to {target_entity_id} not found"
                }

            # Remove the relationship
            relationships.remove(target_entity_id)

            # Clean up empty relationship structures
            if not relationships:
                del source_entity["relationships"][relationship_type]
            if not source_entity["relationships"]:
                del source_entity["relationships"]

            # Save to file
            commit_msg = f"Remove relationship: {source_entity_id} {relationship_type} {target_entity_id}"
            saved = await self._save_entity_with_relationships(
                source_entity_id,
                source_entity.get("relationships", {}),
                commit_msg
            )

            if not saved:
                return {
                    "success": False,
                    "error": "Failed to persist relationship removal to file"
                }

            # Handle bidirectional removal
            bidirectional_removed = False
            source_entity_type = self._extract_entity_type(source_entity_id)
            inverse_rel = self.entity_registry.get_inverse_relationship(
                source_entity_type, relationship_type
            )
            if inverse_rel:
                # Load target entity and remove inverse relationship
                target_entity = await self._load_entity_from_file(target_entity_id)
                if target_entity:
                    if "relationships" in target_entity and inverse_rel["name"] in target_entity["relationships"]:
                        inverse_relationships = target_entity["relationships"][inverse_rel["name"]]
                        if source_entity_id in inverse_relationships:
                            inverse_relationships.remove(source_entity_id)

                            # Clean up empty structures
                            if not inverse_relationships:
                                del target_entity["relationships"][inverse_rel["name"]]
                            if not target_entity["relationships"]:
                                del target_entity["relationships"]

                            # Save target entity
                            commit_msg = f"Remove inverse relationship: {target_entity_id} {inverse_rel['name']} {source_entity_id}"
                            await self._save_entity_with_relationships(
                                target_entity_id,
                                target_entity.get("relationships", {}),
                                commit_msg
                            )
                            bidirectional_removed = True

            logger.info(
                f"Removed relationship {relationship_type} from {source_entity_id} to {target_entity_id}"
            )

            return {"success": True, "bidirectional_removed": bidirectional_removed}

        except Exception as e:
            logger.error(f"Error removing relationship: {e}")
            return {"success": False, "error": str(e)}

    async def get_entity_relationships(self, entity_id: str) -> dict[str, Any]:
        """
        Get all relationships for an entity.

        Args:
            entity_id: The entity ID

        Returns:
            Dictionary with outgoing and incoming relationships
        """
        try:
            # Load entity from file to get outgoing relationships
            entity = await self._load_entity_from_file(entity_id)

            # Get outgoing relationships from entity file
            outgoing = []
            if entity and "relationships" in entity:
                for rel_type, targets in entity["relationships"].items():
                    # Handle both list and single value formats
                    target_list = targets if isinstance(targets, list) else [targets]
                    for target_id in target_list:
                        outgoing.append(
                            {
                                "relationship_type": rel_type,
                                "target_id": target_id,
                                "target_type": self._extract_entity_type(target_id),
                            }
                        )

            # Get incoming relationships by scanning all entities
            # TODO: Performance optimization opportunity
            # This O(n*m) complexity could be improved with:
            # 1. Maintaining a reverse index: target_id -> [(source_id, rel_type)]
            # 2. Using a graph database for complex relationship queries
            # 3. Caching frequently accessed relationship patterns
            incoming = []

            # List all entities to find incoming relationships
            all_entities = await self.entity_file_service.list_entities(
                entity_type=None,
                include_archived=False,
                filters=None
            )

            for other_entity in all_entities:
                # Skip the entity we're querying
                if other_entity.get("id") == entity_id:
                    continue

                # Check if this entity has relationships pointing to our target
                if "relationships" in other_entity:
                    for rel_type, targets in other_entity["relationships"].items():
                        # Handle both list and single value formats
                        target_list = targets if isinstance(targets, list) else [targets]
                        if entity_id in target_list:
                            incoming.append(
                                {
                                    "relationship_type": rel_type,
                                    "source_id": other_entity["id"],
                                    "source_type": self._extract_entity_type(other_entity["id"]),
                                }
                            )

            return {"outgoing": outgoing, "incoming": incoming}

        except Exception as e:
            logger.error(f"Error getting relationships for {entity_id}: {e}")
            return {"outgoing": [], "incoming": []}

    async def validate_relationship_cardinality(
        self, source_entity_type: str, relationship_type: str, target_count: int
    ) -> dict[str, Any]:
        """
        Validate relationship cardinality constraints.

        Args:
            source_entity_type: Source entity type
            relationship_type: Relationship type
            target_count: Number of target entities

        Returns:
            Validation result
        """
        try:
            entity_schema = self.entity_registry.get_entity_schema(source_entity_type)
            if not entity_schema:
                return {
                    "valid": False,
                    "error": f"Unknown entity type: {source_entity_type}",
                }

            rel_schema = entity_schema.relationships_dict.get(relationship_type)
            if not rel_schema:
                return {
                    "valid": False,
                    "error": f"Unknown relationship type: {relationship_type}",
                }

            # Check cardinality constraints
            if (
                rel_schema.cardinality in ["one-to-one", "many-to-one"]
                and target_count > 1
            ):
                return {
                    "valid": False,
                    "error": f"Relationship '{relationship_type}' allows only one target, got {target_count}",
                }

            return {"valid": True}

        except Exception as e:
            logger.error(f"Error validating cardinality: {e}")
            return {"valid": False, "error": str(e)}

    async def get_relationship_schema(
        self, entity_type: str, relationship_type: str
    ) -> dict[str, Any | None]:
        """
        Get schema information for a specific relationship.

        Args:
            entity_type: Entity type
            relationship_type: Relationship type

        Returns:
            Relationship schema information or None
        """
        entity_schema = self.entity_registry.get_entity_schema(entity_type)
        if not entity_schema:
            return None

        rel_schema = entity_schema.relationships_dict.get(relationship_type)
        if not rel_schema:
            return None

        return {
            "name": rel_schema.name,
            "target_entity": rel_schema.target_entity,
            "cardinality": rel_schema.cardinality,
            "inverse_name": rel_schema.inverse_name,
        }

    def _extract_entity_type(self, entity_id: str) -> str | None:
        """
        Extract entity type from entity ID with proper validation.

        Args:
            entity_id: Entity ID in format "type-uuid" or "type-slug"

        Returns:
            Entity type or None if invalid format
        """
        if not entity_id or "-" not in entity_id:
            return None

        parts = entity_id.split("-", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return None

        # Validate that the type part contains only valid characters
        entity_type = parts[0]
        if not entity_type.replace("_", "").isalnum():
            return None

        # Optionally validate the ID part is either a UUID or valid slug
        id_part = parts[1]
        if not id_part:
            return None

        # Check if it looks like a UUID (basic check)
        if len(id_part) == 36 and id_part.count("-") == 4:
            # Basic UUID format check
            try:
                import uuid

                uuid.UUID(id_part)
            except (ValueError, ImportError):
                pass  # Not a UUID, but could be a valid slug

        return entity_type

    def _cleanup_empty_relationships(
        self, entity_id: str, relationship_type: str
    ) -> None:
        """
        Clean up empty relationship structures.

        Args:
            entity_id: The entity ID
            relationship_type: The relationship type
        """
        if (
            entity_id in self._relationships
            and relationship_type in self._relationships[entity_id]
        ):
            if not self._relationships[entity_id][relationship_type]:
                del self._relationships[entity_id][relationship_type]
            if not self._relationships[entity_id]:
                del self._relationships[entity_id]

    async def _add_inverse_relationship(
        self, source_entity_id: str, relationship_type: str, target_entity_id: str
    ) -> None:
        """Add inverse relationship without validation."""
        if source_entity_id not in self._relationships:
            self._relationships[source_entity_id] = {}
        if relationship_type not in self._relationships[source_entity_id]:
            self._relationships[source_entity_id][relationship_type] = []

        if (
            target_entity_id
            not in self._relationships[source_entity_id][relationship_type]
        ):
            self._relationships[source_entity_id][relationship_type].append(
                target_entity_id
            )

    async def _remove_inverse_relationship(
        self, source_entity_id: str, relationship_type: str, target_entity_id: str
    ) -> None:
        """Remove inverse relationship."""
        if (
            source_entity_id in self._relationships
            and relationship_type in self._relationships[source_entity_id]
        ):
            relationships = self._relationships[source_entity_id][relationship_type]
            if target_entity_id in relationships:
                relationships.remove(target_entity_id)

                # Clean up empty relationship structures
                self._cleanup_empty_relationships(source_entity_id, relationship_type)

    def get_all_relationships(self) -> dict[str, dict[str, list[str]]]:
        """
        Get all relationships in the system.

        Returns:
            Dictionary mapping entity IDs to their relationships
        """
        return self._relationships.copy()

    def clear_relationships(self) -> None:
        """Clear all relationships."""
        self._relationships.clear()
        logger.info("Cleared all entity relationships")
