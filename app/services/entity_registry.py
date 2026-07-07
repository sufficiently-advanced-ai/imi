"""
Dynamic Entity Registry System for Domain-Aware Platform.

DEPRECATED: Entity type management is now handled by SemanticaKnowledge
(app/services/semantica_knowledge.py) via domain config adapter. This
module is kept for backward compatibility during the transition.
"""

import logging
import re
import uuid
from datetime import datetime
from typing import Any

from dateutil import parser as date_parser

from ..model_schemas.domain_config import (
    DomainConfiguration,
    DomainEntity,
)

logger = logging.getLogger(__name__)


class EntityRegistry:
    """Registry for managing entity types from domain configurations."""

    _instance = None

    def __new__(cls):
        """Ensure singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the entity registry."""
        # Only initialize once
        if not hasattr(self, "_initialized"):
            self._entities: dict[str, DomainEntity] = {}
            self._domain_config: DomainConfiguration | None = None
            self.entities: dict[str, dict[str, Any]] = {}  # Store actual entity instances
            self.entity_schemas: dict[str, DomainEntity] = {}  # Store entity schemas
            self.domain_config: DomainConfiguration | None = None
            self._initialized = True

    def register_domain(self, domain_config: DomainConfiguration) -> None:
        """
        Register a domain configuration, replacing any existing configuration.

        Args:
            domain_config: The domain configuration to register
        """
        self._domain_config = domain_config
        self._entities = domain_config.entities.copy()
        self.domain_config = domain_config
        self.entity_schemas = domain_config.entities.copy()
        # Initialize entity storage for each entity type
        self.entities = {entity_type: {} for entity_type in domain_config.entities.keys()}
        logger.info(f"Registered domain '{domain_config.id}' with {len(self._entities)} entity types")

        # Forward to EntityRepository to ensure both registries have domain config
        # This ensures EntityService and EntityFileService get the correct entity types
        try:
            from ..domain.entities.services import get_entity_repository

            entity_repo = get_entity_repository()
            # Only forward if the repository has the load_domain_config method
            # and is a different instance (not self)
            if entity_repo is not self and hasattr(entity_repo, "load_domain_config"):
                entity_repo.load_domain_config(domain_config)
                logger.info("Forwarded domain registration to EntityRepository")
        except ImportError:
            logger.debug("EntityRepository not available, skipping forward")

    def load_domain_config(self, domain_config: DomainConfiguration) -> None:
        """
        Load a domain configuration (alias for register_domain).

        Args:
            domain_config: The domain configuration to load
        """
        # Use the consolidated repository's backward compatibility method
        from ..domain.entities.services import get_entity_repository

        entity_repo = get_entity_repository()
        entity_repo.load_domain_config(domain_config)

    def clear(self) -> None:
        """Clear all registered entities and domain configuration."""
        self._entities.clear()
        self._domain_config = None
        logger.info("Cleared entity registry")

    def get_entity_types(self) -> list[str]:
        """
        Get all registered entity type IDs.

        Returns:
            List of entity type IDs
        """
        return list(self._entities.keys())

    def get_entity_schema(self, entity_type: str) -> DomainEntity | None:
        """
        Get the schema for a specific entity type.

        Args:
            entity_type: The entity type ID

        Returns:
            The entity schema or None if not found
        """
        return self._entities.get(entity_type)

    def validate_entity(self, entity_type: str, attributes: dict[str, Any]) -> tuple[bool, list[str]]:
        """
        Validate entity attributes against the entity schema.

        Args:
            entity_type: The entity type ID
            attributes: The attributes to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Check if entity type exists
        entity_schema = self._entities.get(entity_type)
        if not entity_schema:
            errors.append(f"Unknown entity type: {entity_type}")
            return False, errors

        # Check required fields
        for attr_id, attr_schema in entity_schema.attributes_dict.items():
            if attr_schema.required and attr_id not in attributes:
                errors.append(f"Required field '{attr_id}' is missing")

        # Validate each provided attribute
        for attr_id, value in attributes.items():
            if attr_id not in entity_schema.attributes_dict:
                # Skip unknown attributes (they're allowed but not validated)
                continue

            attr_schema = entity_schema.attributes_dict[attr_id]

            # Skip None values for optional fields
            if value is None and not attr_schema.required:
                continue

            # Type validation
            if attr_schema.type == "string":
                if not isinstance(value, str):
                    errors.append(f"Field '{attr_id}' must be a string")
                # Check enum constraint for string type
                elif attr_schema.enum and value not in attr_schema.enum:
                    errors.append(f"Invalid value for attribute '{attr_id}': {value}")

            elif attr_schema.type == "number":
                if not isinstance(value, int | float):
                    errors.append(f"Field '{attr_id}' must be a number")

            elif attr_schema.type == "boolean":
                if not isinstance(value, bool):
                    errors.append(f"Field '{attr_id}' must be a boolean")

            elif attr_schema.type == "date":
                if isinstance(value, str):
                    try:
                        date_parser.parse(value).date()
                    except (ValueError, TypeError):
                        errors.append(f"Field '{attr_id}' must be a valid date")
                else:
                    errors.append(f"Field '{attr_id}' must be a date string")

            elif attr_schema.type == "datetime":
                if isinstance(value, str):
                    try:
                        date_parser.parse(value)
                    except (ValueError, TypeError):
                        errors.append(f"Field '{attr_id}' must be a valid datetime")
                else:
                    errors.append(f"Field '{attr_id}' must be a datetime string")

            elif attr_schema.type == "enum":
                if attr_schema.enum and value not in attr_schema.enum:
                    errors.append(f"Field '{attr_id}' must be one of: {', '.join(attr_schema.enum)}")

        return len(errors) == 0, errors

    def create_entity(self, entity_type: str, attributes: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new entity instance with validation.

        Args:
            entity_type: The entity type ID
            attributes: The entity attributes

        Returns:
            The created entity dictionary

        Raises:
            ValueError: If validation fails
        """
        # Validate first
        is_valid, errors = self.validate_entity(entity_type, attributes)
        if not is_valid:
            raise ValueError(f"Entity validation failed: {'; '.join(errors)}")

        # Generate ID
        entity_id = self._generate_entity_id(entity_type, attributes)

        # Create entity
        now = datetime.utcnow().isoformat()
        entity = {
            "id": entity_id,
            "entity_type": entity_type,
            "attributes": attributes.copy(),
            "created_at": now,
            "updated_at": now,
        }

        return entity

    def get_entity_relationships(self, entity_type: str) -> dict[str, dict[str, Any]]:
        """
        Get all relationships for an entity type.

        Args:
            entity_type: The entity type ID

        Returns:
            Dictionary mapping relationship names to relationship info
        """
        entity_schema = self._entities.get(entity_type)
        if not entity_schema:
            return {}

        relationships = {}
        for rel_id, rel_schema in entity_schema.relationships_dict.items():
            relationships[rel_id] = {
                "name": rel_schema.name,
                "target_entity": rel_schema.target_entity,
                "cardinality": rel_schema.cardinality,
                "inverse_name": rel_schema.inverse_name,
            }

        return relationships

    def validate_relationship(self, source_entity_type: str, relationship_name: str, target_entity_type: str) -> bool:
        """
        Validate if a relationship is valid between entity types.

        Args:
            source_entity_type: The source entity type
            relationship_name: The relationship name
            target_entity_type: The target entity type

        Returns:
            True if valid, False otherwise
        """
        # Check source entity exists
        source_schema = self._entities.get(source_entity_type)
        if not source_schema:
            return False

        # Check relationship exists
        rel_schema = source_schema.relationships_dict.get(relationship_name)
        if not rel_schema:
            return False

        # Check target entity type matches
        if rel_schema.target_entity != target_entity_type:
            return False

        # Check target entity exists
        return target_entity_type in self._entities

    def get_inverse_relationship(self, source_entity_type: str, relationship_name: str) -> dict[str, Any] | None:
        """
        Get the inverse relationship information.

        Args:
            source_entity_type: The source entity type
            relationship_name: The relationship name

        Returns:
            Dictionary with inverse relationship info or None
        """
        # Get source relationship
        source_schema = self._entities.get(source_entity_type)
        if not source_schema:
            return None

        rel_schema = source_schema.relationships_dict.get(relationship_name)
        if not rel_schema:
            return None

        # Get target entity
        target_schema = self._entities.get(rel_schema.target_entity)
        if not target_schema:
            return None

        # Find inverse relationship
        for _inv_rel_id, inv_rel_schema in target_schema.relationships_dict.items():
            if inv_rel_schema.target_entity == source_entity_type and inv_rel_schema.inverse_name == relationship_name:
                return {
                    "name": inv_rel_schema.name,
                    "cardinality": inv_rel_schema.cardinality,
                    "target_entity": inv_rel_schema.target_entity,
                }

        return None

    def _generate_entity_id(self, entity_type: str, attributes: dict[str, Any]) -> str:
        """
        Generate a unique ID for an entity.

        Args:
            entity_type: The entity type
            attributes: The entity attributes

        Returns:
            A unique entity ID
        """
        # Use UUID for uniqueness
        unique_part = str(uuid.uuid4())[:8]
        return f"{entity_type}-{unique_part}"

    def register_entity(self, entity: Any) -> Any:
        """
        Register an entity instance.

        Args:
            entity: The entity to register (must have id, type, and attributes)

        Returns:
            The registered entity

        Raises:
            ValueError: If entity type is unknown or validation fails
        """
        # Check entity type exists
        if entity.type not in self._entities:
            raise ValueError(f"Unknown entity type: {entity.type}")

        # Generate ID if not provided
        if not entity.id:
            entity.id = self._generate_entity_id(entity.type, entity.attributes)

        # Validate entity attributes
        is_valid, errors = self.validate_entity(entity.type, entity.attributes)
        if not is_valid:
            raise ValueError(f"Entity validation failed: {'; '.join(errors)}")

        # Store entity
        if entity.type not in self.entities:
            self.entities[entity.type] = {}
        self.entities[entity.type][entity.id] = entity

        return entity

    def get_canonical_entity(self, entity_type: str, entity_id: str) -> Any | None:
        """
        Get a canonical entity by type and ID.

        Args:
            entity_type: The entity type
            entity_id: The entity ID

        Returns:
            The entity or None if not found
        """
        return self.entities.get(entity_type, {}).get(entity_id)

    def find_similar_entities(
        self, name: str, threshold: float = 0.8, entity_type: str = None, limit: int = None
    ) -> list[tuple[Any, float]]:
        """
        Find entities with similar names, optionally filtered by type.

        Args:
            name: The name to search for
            threshold: Similarity threshold (0-1)
            entity_type: Optional entity type to filter by ("person", "project", "team")
            limit: Optional limit on number of results

        Returns:
            List of tuples (entity, similarity_score) sorted by similarity descending
        """
        similar: list[tuple[Any, float]] = []
        name_casefolded: str = name.casefold()

        # Determine which entity types to search
        types_to_search: list[str] = [entity_type] if entity_type else list(self.entities.keys())

        for search_type in types_to_search:
            if search_type not in self.entities:
                continue

            entities = self.entities[search_type]
            for entity in entities.values():
                entity_name_to_compare: str | None = None

                # Get canonical_name if available and not None
                if hasattr(entity, "canonical_name") and entity.canonical_name is not None:
                    entity_name_to_compare = entity.canonical_name.casefold()
                # Fallback to name attribute if available and not None
                elif hasattr(entity, "name") and entity.name is not None:
                    entity_name_to_compare = entity.name.casefold()

                # Only calculate similarity if we have a valid name to compare
                if entity_name_to_compare is not None:
                    ratio: float = self._calculate_similarity(name_casefolded, entity_name_to_compare)
                    if ratio >= threshold:
                        similar.append((entity, ratio))

        # Sort by similarity score (descending)
        similar.sort(key=lambda x: x[1], reverse=True)

        # Apply limit if specified
        if limit is not None:
            similar = similar[:limit]

        return similar

    def get_all_entities(self) -> dict[str, dict[str, Any]]:
        """
        Get all entities organized by type.

        Returns:
            Dictionary mapping entity types to their entities
        """
        return self.entities.copy()

    def validate_entity_against_domain(self, entity_type: str, attributes: dict[str, Any]) -> bool:
        """
        Validate entity attributes against domain schema.

        Args:
            entity_type: The entity type
            attributes: The attributes to validate

        Returns:
            True if valid, False otherwise
        """
        is_valid, _ = self.validate_entity(entity_type, attributes)
        return is_valid

    def register_person(
        self,
        canonical_name: str,
        aliases: list[str] = None,
        titles: list[str] = None,
        email: str = None,
        phone: str = None,
        departments: list[str] = None,
        confidence: float = 1.0,
    ) -> str:
        """Register a new person entity.

        Args:
            canonical_name: The canonical name of the person
            aliases: List of aliases (optional)
            titles: List of titles (optional)
            email: Email address (optional)
            phone: Phone number (optional)
            departments: List of departments (optional)
            confidence: Confidence level (optional)

        Returns:
            Entity ID of the registered person
        """
        # Generate entity ID
        entity_id = self._normalize_name_to_id(canonical_name)

        # Create person attributes dictionary
        attributes = {
            "name": canonical_name,  # Required field by domain schema
            "canonical_name": canonical_name,
            "titles": titles or [],
            "email": email,
            "phone": phone,
            "departments": departments or [],
            "confidence": confidence,
        }

        # Add aliases to attributes if provided
        if aliases:
            attributes["aliases"] = aliases

        # Create entity using the existing create_entity method if person type exists
        try:
            self.create_entity("person", attributes)

            # Store in entities registry if person type exists
            if "person" not in self.entities:
                self.entities["person"] = {}

            # Create a simple person object for storage
            person = type(
                "Person",
                (),
                {
                    "id": entity_id,
                    "canonical_name": canonical_name,
                    "titles": titles or [],
                    "email": email,
                    "phone": phone,
                    "departments": departments or [],
                    "confidence": confidence,
                    "name": canonical_name,  # Add name attribute for find_similar_entities
                },
            )()

            self.entities["person"][entity_id] = person

            logger.info(f"Registered person entity: {entity_id} ({canonical_name})")
            return entity_id

        except Exception as e:
            # Fallback: just store in entities dict without validation
            logger.warning(f"Could not validate person entity, storing without validation: {e}")

            if "person" not in self.entities:
                self.entities["person"] = {}

            # Create a simple person object for storage
            person = type(
                "Person",
                (),
                {
                    "id": entity_id,
                    "canonical_name": canonical_name,
                    "titles": titles or [],
                    "email": email,
                    "phone": phone,
                    "departments": departments or [],
                    "confidence": confidence,
                    "name": canonical_name,  # Add name attribute for find_similar_entities
                },
            )()

            self.entities["person"][entity_id] = person

            logger.info(f"Registered person entity (fallback): {entity_id} ({canonical_name})")
            return entity_id

    def _normalize_name_to_id(self, name: str) -> str:
        """Convert name to normalized entity ID for person entities.

        Args:
            name: Person's name

        Returns:
            Normalized ID like 'person-john-doe'
        """
        # Remove common titles first
        titles = r"\b(Dr\.?|Mr\.?|Mrs\.?|Ms\.?|Prof\.?|Professor)\s+"
        normalized_name = re.sub(titles, "", name, flags=re.IGNORECASE)

        # Handle "Last, First" format - remove comma but keep original order
        if "," in normalized_name:
            normalized_name = normalized_name.replace(",", "")

        # Clean up extra spaces
        normalized_name = " ".join(normalized_name.split())

        # Convert to lowercase and replace spaces with hyphens
        id_base = normalized_name.lower().replace(" ", "-")

        # Remove special characters
        id_base = re.sub(r"[^\w\-]", "", id_base)

        # Add entity type prefix
        return f"person-{id_base}"

    def find_entity(self, name: str, entity_type: str | None = None) -> Any | None:
        """
        Find an exact match entity by name.

        Args:
            name: The name to search for
            entity_type: Optional entity type to filter by

        Returns:
            The entity if found, None otherwise
        """
        # Use find_similar_entities with threshold=1.0 for case-insensitive exact match
        name = name.strip()
        results = self.find_similar_entities(
            name,
            threshold=1.0,
            entity_type=entity_type,
            limit=1,
        )
        return results[0][0] if results else None

    def search_entities(
        self,
        query: str,
        entity_type: str | None = None,
        limit: int | None = None,
    ) -> list[tuple[Any, float]]:
        """
        Search for entities by name with fuzzy matching.

        Args:
            query: The search query
            entity_type: Optional entity type to filter by
            limit: Optional limit on number of results

        Returns:
            List of (entity, similarity_score) tuples sorted by similarity descending
        """
        # Use find_similar_entities with threshold=0.8 for fuzzy search
        query = query.strip()
        return self.find_similar_entities(
            query,
            threshold=0.8,
            entity_type=entity_type,
            limit=limit,
        )

    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """Calculate similarity ratio between two strings."""
        from difflib import SequenceMatcher

        return SequenceMatcher(None, s1, s2).ratio()


# Singleton instance
_entity_registry = None


def get_entity_registry() -> EntityRegistry:
    """
    DEPRECATED: Use get_entity_repository() instead.

    Tenant-scoped accessor (Phase 4.1): returns the current tenant's entity
    registry. In single-tenant mode the container returns the ``EntityRegistry``
    process singleton (its ``__new__`` is intentionally retained — production
    code constructs ``EntityRegistry()`` directly in several places), so this is
    identical to the previous global instance.
    """
    from app.core.tenancy.context import current_tenant

    return current_tenant().entity_registry


def get_entity_repository():
    """Get or create the entity repository instance."""
    # Forward to the canonical factory
    return get_entity_registry()
