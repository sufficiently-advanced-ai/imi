"""
Consolidated EntityRepository - Issue #395

This module consolidates functionality from:
- entity_registry.py (singleton, domain-aware) - 94 references
- entity_registry_canonical.py (storage-based, hardcoded types)
- entity_registry_dynamic.py (thread-safe, LRU cache)

Features consolidated:
- Singleton pattern with thread safety (RLock)
- Domain configuration support
- LRU cache with configurable size limits
- JSON persistence to registry.json
- Alias indexing for fast lookups
- Entity validation against domain schemas
- Thread-safe operations
"""

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.model_schemas.domain_config import DomainConfiguration, DomainEntity

logger = logging.getLogger(__name__)

# Configuration constants
MAX_CACHE_SIZE = 10000  # Maximum number of cached entities
DEFAULT_TTL = 3600  # Default TTL for cache entries in seconds


class EntityRepository:
    """
    Unified entity repository with caching, persistence, and thread safety.

    Consolidates functionality from multiple registry implementations into
    a single, comprehensive service.
    """

    _instance = None
    _lock = threading.RLock()  # Class-level lock for singleton

    # Mapping from plural accessor names to entity type IDs for backward compatibility
    _PLURAL_TO_ENTITY_TYPE = {
        "people": "person",
        "persons": "person",
        "projects": "project",
        "teams": "team",
        "accounts": "account",
        "organizations": "organization",
        "companies": "company",
    }

    def __new__(cls, *args, **kwargs):
        """Ensure singleton instance with thread safety."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    # Store the initialization args for later use
                    cls._instance._init_args = args
                    cls._instance._init_kwargs = kwargs
        return cls._instance

    def __init__(self, storage_path: Path | None = None):
        """
        Initialize the unified entity repository.

        Args:
            storage_path: Path to JSON file for persistence
        """
        # Only initialize once (singleton pattern)
        if hasattr(self, '_initialized'):
            return

        # Use provided storage_path or stored kwargs, defaulting to registry.json
        if storage_path is None and hasattr(self, '_init_kwargs'):
            storage_path = self._init_kwargs.get('storage_path')

        self.storage_path = storage_path or Path("registry.json")
        self.MAX_CACHE_SIZE = MAX_CACHE_SIZE

        # Domain configuration state
        self._entities: dict[str, DomainEntity] = {}
        self._domain_config: DomainConfiguration | None = None
        self._current_domain_id: str | None = None

        # Entity storage
        self._entity_storage: dict[str, dict[str, Any]] = {}

        # Caching layer
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_ttl: dict[str, datetime] = {}

        # Alias indexing
        self._aliases: dict[str, str] = {}  # alias -> entity_id

        # Thread safety
        self._instance_lock = threading.RLock()

        # Change notification handlers
        self._change_handlers: list[Callable[[str, str, Any], None]] = []

        # Cached methods
        self.get_entity_schema = lru_cache(maxsize=128)(self._get_entity_schema_impl)

        # Schedule loading after initialization completes
        # Don't create task in __init__ as event loop may not exist
        if self.storage_path.exists():
            logger.info(f"Registry file exists at {self.storage_path}, will load on first access")

        self._initialized = True
        logger.info("EntityRepository initialized")

    def __getattr__(self, name: str) -> dict[str, Any]:
        """
        Dynamic attribute access for entity type collections.

        Allows accessing entities via plural names like `registry.people`
        which maps to entity_storage["person"].

        This provides backward compatibility with code that expects
        `registry.people.values()`, `registry.projects.clear()`, etc.

        Args:
            name: Attribute name (e.g., "people", "projects")

        Returns:
            Dict of entities for that type

        Raises:
            AttributeError: If name doesn't map to a valid entity type
        """
        # Avoid infinite recursion for internal attributes
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

        # Check if it's a known plural form
        entity_type = self._PLURAL_TO_ENTITY_TYPE.get(name)

        # Or check if the name itself is a valid entity type in domain config
        if entity_type is None and hasattr(self, "_domain_config") and self._domain_config:
            if name in self._domain_config.entities:
                entity_type = name

        # Or check _entity_storage directly (for types without domain config)
        if entity_type is None and hasattr(self, "_entity_storage") and name in self._entity_storage:
            entity_type = name

        if entity_type is not None:
            # Ensure storage dict exists for this type
            if entity_type not in self._entity_storage:
                self._entity_storage[entity_type] = {}
            return self._entity_storage[entity_type]

        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    # Backward-compatible register methods for graph builder
    def register_person(self, canonical_name: str, titles: list = None) -> str:
        """Register a person entity (backward compatibility for graph builder).

        Args:
            canonical_name: The canonical name for the person
            titles: Optional list of titles/roles

        Returns:
            The entity ID for the registered person
        """
        entity_id = f"person-{canonical_name.lower().replace(' ', '-')}"
        with self._instance_lock:
            if "person" not in self._entity_storage:
                self._entity_storage["person"] = {}
            self._entity_storage["person"][entity_id] = {
                "id": entity_id,
                "canonical_name": canonical_name,
                "titles": titles or [],
                "type": "person"
            }
        logger.debug(f"Registered person entity: {entity_id}")
        return entity_id

    def register_project(self, canonical_name: str = None, name: str = None, status: str = "active") -> str:
        """Register a project entity (backward compatibility for graph builder).

        Args:
            canonical_name: The canonical name for the project (preferred)
            name: Alternative name parameter
            status: Project status (default: active)

        Returns:
            The entity ID for the registered project
        """
        project_name = canonical_name or name
        if not project_name:
            raise ValueError("Either canonical_name or name must be provided")
        entity_id = f"project-{project_name.lower().replace(' ', '-')}"
        with self._instance_lock:
            if "project" not in self._entity_storage:
                self._entity_storage["project"] = {}
            self._entity_storage["project"][entity_id] = {
                "id": entity_id,
                "canonical_name": project_name,
                "name": project_name,
                "status": status,
                "type": "project"
            }
        logger.debug(f"Registered project entity: {entity_id}")
        return entity_id

    def register_team(self, name: str) -> str:
        """Register a team entity (backward compatibility for graph builder).

        Args:
            name: The name of the team

        Returns:
            The entity ID for the registered team
        """
        entity_id = f"team-{name.lower().replace(' ', '-')}"
        with self._instance_lock:
            if "team" not in self._entity_storage:
                self._entity_storage["team"] = {}
            self._entity_storage["team"][entity_id] = {
                "id": entity_id,
                "name": name,
                "type": "team"
            }
        logger.debug(f"Registered team entity: {entity_id}")
        return entity_id

    async def initialize(self) -> None:
        """
        Explicitly initialize the repository with async operations.

        This method should be called after the repository is created
        to safely perform async initialization like loading from storage.
        """
        if hasattr(self, '_async_initialized'):
            return

        if self.storage_path.exists():
            await self.load_from_json(str(self.storage_path))

        self._async_initialized = True
        logger.info("EntityRepository async initialization completed")

    async def register_domain(self, domain_config: DomainConfiguration) -> None:
        """
        Register a domain configuration.

        Args:
            domain_config: The domain configuration to register
        """
        logger.debug(f"register_domain() called for domain '{domain_config.id}'")
        with self._instance_lock:
            self._domain_config = domain_config
            self._entities = domain_config.entities.copy()
            self._current_domain_id = domain_config.id

            logger.debug(f"Set _entities keys: {list(self._entities.keys())[:3]}...")

            # Clear caches when domain changes
            self.get_entity_schema.cache_clear()
            self._cache.clear()
            self._cache_ttl.clear()

            # Initialize entity storage for each type
            for entity_type in domain_config.entities.keys():
                if entity_type not in self._entity_storage:
                    self._entity_storage[entity_type] = {}

            logger.info(
                f"Registered domain '{domain_config.id}' with {len(self._entities)} entity types"
            )

            # Notify change handlers
            await self._notify_handlers("domain_registered", domain_config.id, domain_config)

            logger.debug(f"Completed register_domain() for '{domain_config.id}'")

    def clear(self) -> None:
        """Clear all registered entities and domain configuration."""
        with self._instance_lock:
            self._entities.clear()
            self._domain_config = None
            self._current_domain_id = None
            self._entity_storage.clear()
            self._cache.clear()
            self._cache_ttl.clear()
            self._aliases.clear()
            self.get_entity_schema.cache_clear()
            logger.info("Cleared entity registry")

    def get_entity_types(self) -> list[str]:
        """Get all registered entity type IDs."""
        with self._instance_lock:
            return list(self._entities.keys())

    def _get_entity_schema_impl(self, entity_type: str) -> DomainEntity | None:
        """Internal implementation for cached entity schema retrieval."""
        return self._entities.get(entity_type)

    def get_entity_schema(self, entity_type: str) -> DomainEntity | None:
        """Get the schema for a specific entity type (cached)."""
        return self._get_entity_schema_impl(entity_type)

    def validate_entity(
        self, entity_type: str, attributes: dict[str, Any]
    ) -> tuple[bool, list[str]]:
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

        # Skip validation if schema has no attributes defined
        if not hasattr(entity_schema, 'attributes_dict') or not entity_schema.attributes_dict:
            return True, []

        # Check required fields
        for attr_id, attr_schema in entity_schema.attributes_dict.items():
            if hasattr(attr_schema, 'required') and attr_schema.required and attr_id not in attributes:
                errors.append(f"Required field '{attr_id}' is missing")

        # Validate each provided attribute
        for attr_id, value in attributes.items():
            if attr_id not in entity_schema.attributes_dict:
                # Allow unknown attributes but skip validation
                continue

            attr_schema = entity_schema.attributes_dict[attr_id]

            # Skip None values for optional fields
            if value is None and (not hasattr(attr_schema, 'required') or not attr_schema.required):
                continue

            # Type validation based on schema
            if hasattr(attr_schema, 'type'):
                if attr_schema.type == "string" and not isinstance(value, str):
                    errors.append(f"Field '{attr_id}' must be a string")
                elif attr_schema.type == "number" and not isinstance(value, (int, float)):
                    errors.append(f"Field '{attr_id}' must be a number")
                elif attr_schema.type == "boolean" and not isinstance(value, bool):
                    errors.append(f"Field '{attr_id}' must be a boolean")

        return len(errors) == 0, errors

    async def store_entity(self, entity_id: str, entity_data: dict[str, Any]) -> None:
        """
        Store an entity in the repository.

        Args:
            entity_id: Unique entity identifier
            entity_data: Entity data to store
        """
        with self._instance_lock:
            entity_type = entity_data.get("type", entity_id.split("-")[0])

            if entity_type not in self._entity_storage:
                self._entity_storage[entity_type] = {}

            # Add metadata
            entity_data["stored_at"] = datetime.utcnow().isoformat()
            entity_data["id"] = entity_id

            # Store entity
            self._entity_storage[entity_type][entity_id] = entity_data

            # Update cache
            await self._cache_entity(entity_id, entity_data)

            logger.debug(f"Stored entity: {entity_id}")

            # Notify handlers
            await self._notify_handlers("entity_stored", entity_id, entity_data)

    async def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """
        Get an entity from the repository.

        Args:
            entity_id: Entity identifier

        Returns:
            Entity data if found, None otherwise
        """
        # Check cache first
        cached = await self._get_cached_entity(entity_id)
        if cached is not None:
            return cached

        # Search storage
        with self._instance_lock:
            for _, entities in self._entity_storage.items():
                if entity_id in entities:
                    entity_data = entities[entity_id]
                    await self._cache_entity(entity_id, entity_data)
                    return entity_data

        return None

    async def update_entity(self, entity_id: str, entity_data: dict[str, Any]) -> None:
        """
        Update an existing entity.

        Args:
            entity_id: Entity identifier
            entity_data: Updated entity data
        """
        with self._instance_lock:
            entity_type = entity_data.get("type", entity_id.split("-")[0])

            if entity_type in self._entity_storage and entity_id in self._entity_storage[entity_type]:
                # Update metadata
                entity_data["updated_at"] = datetime.utcnow().isoformat()
                entity_data["id"] = entity_id

                # Update storage
                self._entity_storage[entity_type][entity_id] = entity_data

                # Invalidate and update cache
                await self._invalidate_cache(entity_id)
                await self._cache_entity(entity_id, entity_data)

                logger.debug(f"Updated entity: {entity_id}")

                # Notify handlers
                await self._notify_handlers("entity_updated", entity_id, entity_data)

    async def delete_entity(self, entity_id: str) -> bool:
        """
        Delete an entity from the repository.

        Args:
            entity_id: Entity identifier

        Returns:
            True if deleted, False if not found
        """
        with self._instance_lock:
            # Find and remove from storage
            for _, entities in self._entity_storage.items():
                if entity_id in entities:
                    del entities[entity_id]

                    # Remove from cache
                    await self._invalidate_cache(entity_id)

                    # Remove aliases
                    self._remove_entity_aliases(entity_id)

                    logger.debug(f"Deleted entity: {entity_id}")

                    # Notify handlers
                    await self._notify_handlers("entity_deleted", entity_id, None)
                    return True

        return False

    async def list_entities_by_type(self, entity_type: str) -> list[dict[str, Any]]:
        """
        List all entities of a specific type.

        Args:
            entity_type: Type of entities to list

        Returns:
            List of entity data dictionaries
        """
        with self._instance_lock:
            entities = self._entity_storage.get(entity_type, {})
            return list(entities.values())

    async def create_entity(self, entity_type: str, attributes: dict[str, Any]) -> dict[str, Any] | None:
        """
        Create a new entity with validation.

        Args:
            entity_type: The entity type ID
            attributes: The entity attributes

        Returns:
            The created entity dictionary or None if validation failed
        """
        # Validate first
        is_valid, errors = self.validate_entity(entity_type, attributes)
        if not is_valid:
            logger.error(f"Entity validation failed: {'; '.join(errors)}")
            return None

        # Generate ID
        entity_id = self._generate_entity_id(entity_type, attributes)

        # Create entity data
        now = datetime.utcnow().isoformat()
        entity_data = {
            "id": entity_id,
            "type": entity_type,
            "attributes": attributes.copy(),
            "created_at": now,
            "updated_at": now,
        }

        # Store entity
        await self.store_entity(entity_id, entity_data)

        return entity_data

    def _generate_entity_id(self, entity_type: str, attributes: dict[str, Any]) -> str:
        """Generate entity ID from type and attributes."""
        # Try to find a name field
        name_fields = ["name", "title", "canonical_name", "display_name"]
        name = None

        for field in name_fields:
            if field in attributes and attributes[field]:
                name = str(attributes[field])
                break

        if not name:
            # Fallback to timestamp-based ID
            timestamp = str(int(datetime.utcnow().timestamp()))
            return f"{entity_type}-entity-{timestamp}"

        # Normalize name for ID
        normalized = name.lower().replace(" ", "-")
        normalized = "".join(c for c in normalized if c.isalnum() or c == "-")

        return f"{entity_type}-{normalized}"

    # Cache management methods
    async def _cache_entity(self, entity_id: str, entity_data: dict[str, Any]) -> None:
        """Cache an entity with TTL."""
        with self._instance_lock:
            # Enforce cache size limit
            if len(self._cache) >= self.MAX_CACHE_SIZE:
                await self._evict_oldest_cached()

            self._cache[entity_id] = entity_data
            self._cache_ttl[entity_id] = datetime.utcnow() + timedelta(seconds=DEFAULT_TTL)

    async def _get_cached_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Get entity from cache if not expired."""
        with self._instance_lock:
            if entity_id not in self._cache:
                return None

            # Check TTL
            if datetime.utcnow() > self._cache_ttl.get(entity_id, datetime.utcnow()):
                await self._invalidate_cache(entity_id)
                return None

            return self._cache[entity_id]

    async def _invalidate_cache(self, entity_id: str) -> None:
        """Remove entity from cache."""
        with self._instance_lock:
            self._cache.pop(entity_id, None)
            self._cache_ttl.pop(entity_id, None)

    async def _evict_oldest_cached(self) -> None:
        """Evict the oldest cached entity to make space."""
        with self._instance_lock:
            if not self._cache_ttl:
                return

            # Find oldest entry
            oldest_id = min(self._cache_ttl.keys(), key=lambda k: self._cache_ttl[k])
            await self._invalidate_cache(oldest_id)

    # Alias management
    def register_alias(self, alias: str, entity_id: str) -> None:
        """Register an alias for an entity."""
        with self._instance_lock:
            self._aliases[alias.lower()] = entity_id
            logger.debug(f"Registered alias '{alias}' for {entity_id}")

    async def get_entity_by_alias(self, alias: str) -> dict[str, Any] | None:
        """Get entity by alias."""
        with self._instance_lock:
            entity_id = self._aliases.get(alias.lower())
            if entity_id:
                return await self.get_entity(entity_id)
        return None

    def get_aliases(self, entity_id: str) -> list[str]:
        """Get all aliases for an entity."""
        with self._instance_lock:
            return [alias for alias, eid in self._aliases.items() if eid == entity_id]

    def remove_alias(self, alias: str) -> None:
        """Remove an alias."""
        with self._instance_lock:
            self._aliases.pop(alias.lower(), None)

    def _remove_entity_aliases(self, entity_id: str) -> None:
        """Remove all aliases for an entity."""
        with self._instance_lock:
            aliases_to_remove = [alias for alias, eid in self._aliases.items() if eid == entity_id]
            for alias in aliases_to_remove:
                self._aliases.pop(alias, None)

    # Persistence
    async def save_to_json(self, file_path: str) -> None:
        """Save registry data to JSON file."""
        data = {
            "domain_id": self._current_domain_id,
            "entities": self._entity_storage,
            "aliases": self._aliases,
            "saved_at": datetime.utcnow().isoformat()
        }

        try:
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            logger.info(f"Registry saved to {file_path}")
        except Exception as e:
            logger.error(f"Failed to save registry: {e}")

    async def load_from_json(self, file_path: str) -> None:
        """Load registry data from JSON file."""
        try:
            with open(file_path) as f:
                data = json.load(f)

            with self._instance_lock:
                self._current_domain_id = data.get("domain_id")
                self._entity_storage = data.get("entities", {})
                self._aliases = data.get("aliases", {})

                # Populate cache with recently accessed entities
                total_entities = sum(len(entities) for entities in self._entity_storage.values())
                logger.info(f"Loaded {total_entities} entities from {file_path}")

        except Exception as e:
            logger.error(f"Failed to load registry: {e}")

    # Relationship validation
    def validate_relationship(
        self, source_entity_type: str, relationship_name: str, target_entity_type: str
    ) -> bool:
        """Validate if a relationship is valid between entity types."""
        source_schema = self._entities.get(source_entity_type)
        if not source_schema:
            return False

        # Check if schema has relationships
        if not hasattr(source_schema, 'relationships') or not source_schema.relationships:
            return False

        # Check relationship exists - use relationships_dict property, not the list
        if not hasattr(source_schema, 'relationships_dict'):
            return False
        rel_schema = source_schema.relationships_dict.get(relationship_name)
        if not rel_schema:
            return False

        # Check target entity type matches
        if hasattr(rel_schema, 'target') and rel_schema.target != target_entity_type:
            return False

        # Check target entity exists
        return target_entity_type in self._entities

    def get_entity_relationships(self, entity_type: str) -> dict[str, dict[str, Any]]:
        """Get all relationships for an entity type."""
        entity_schema = self._entities.get(entity_type)
        if not entity_schema or not hasattr(entity_schema, 'relationships_dict'):
            return {}

        relationships = {}
        for rel_id, rel_schema in entity_schema.relationships_dict.items():
            relationships[rel_id] = {
                "name": getattr(rel_schema, 'name', rel_id),
                "target_entity": getattr(rel_schema, 'target_entity', 'unknown'),
                "cardinality": getattr(rel_schema, 'cardinality', 'many'),
            }

        return relationships

    def get_inverse_relationship(
        self, source_entity_type: str, relationship_name: str
    ) -> dict[str, Any] | None:
        """Get inverse relationship information."""
        source_schema = self._entities.get(source_entity_type)
        if not source_schema or not hasattr(source_schema, 'relationships_dict'):
            return None

        rel_schema = source_schema.relationships_dict.get(relationship_name)
        if not rel_schema or not hasattr(rel_schema, 'inverse_name'):
            return None

        return {
            "name": rel_schema.inverse_name,
            "type": getattr(rel_schema, 'target_entity', 'unknown')
        }

    # Property accessors for backward compatibility
    @property
    def domain_config(self) -> DomainConfiguration | None:
        """Get the current domain configuration."""
        return self._domain_config

    # Change notification system
    def register_change_handler(self, handler: Callable[[str, str, Any], None]) -> None:
        """Register a change notification handler."""
        with self._instance_lock:
            if handler not in self._change_handlers:
                self._change_handlers.append(handler)

    def unregister_change_handler(self, handler: Callable[[str, str, Any], None]) -> None:
        """Unregister a change notification handler."""
        with self._instance_lock:
            if handler in self._change_handlers:
                self._change_handlers.remove(handler)

    async def _notify_handlers(self, event: str, entity_id: str, data: Any) -> None:
        """Notify all registered change handlers."""
        for handler in self._change_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event, entity_id, data)
                else:
                    handler(event, entity_id, data)
            except Exception as e:
                logger.error(f"Error in change handler: {e}")

    # Statistics and monitoring
    async def get_stats(self) -> dict[str, Any]:
        """Get repository statistics."""
        with self._instance_lock:
            total_entities = sum(len(entities) for entities in self._entity_storage.values())

            return {
                "total_entities": total_entities,
                "entity_types": len(self._entities),
                "cached_entities": len(self._cache),
                "aliases": len(self._aliases),
                "domain_id": self._current_domain_id,
                "storage_path": str(self.storage_path)
            }

    def get_cache_info(self) -> dict[str, Any]:
        """Get cache information."""
        with self._instance_lock:
            return {
                "size": len(self._cache),
                "max_size": self.MAX_CACHE_SIZE,
                "hit_ratio": getattr(self.get_entity_schema, 'cache_info', lambda: {})()
            }

    # Backward compatibility methods
    def get_canonical_entity(self, entity_id: str, entity_type: str = None) -> dict[str, Any] | None:
        """
        Get a canonical entity by ID, optionally filtered by type.

        Backward-compatible method that searches across all entity types.
        For async context, prefer using get_entity() instead.

        Args:
            entity_id: The entity ID to find
            entity_type: Optional entity type to filter search

        Returns:
            The entity data if found, None otherwise
        """
        with self._instance_lock:
            # If entity_type is provided, search only that type
            if entity_type:
                entities = self._entity_storage.get(entity_type, {})
                return entities.get(entity_id)

            # Otherwise, search across all entity types
            for _, entities in self._entity_storage.items():
                if entity_id in entities:
                    return entities[entity_id]

            # Also check cache as fallback
            return self._cache.get(entity_id)

    def load_domain_config(self, domain_config: DomainConfiguration) -> None:
        """
        Load a domain configuration synchronously (compatibility alias for register_domain).

        This method performs synchronous domain registration to ensure domain config
        is immediately available after the call returns. This is critical for services
        that need to access entity schemas right after domain loading.
        """
        with self._instance_lock:
            self._domain_config = domain_config
            self._entities = domain_config.entities.copy()
            self._current_domain_id = domain_config.id

            # Clear caches when domain changes
            self.get_entity_schema.cache_clear()
            self._cache.clear()
            self._cache_ttl.clear()

            # Initialize entity storage for each type
            for entity_type in domain_config.entities.keys():
                if entity_type not in self._entity_storage:
                    self._entity_storage[entity_type] = {}

            logger.info(
                f"Loaded domain '{domain_config.id}' with {len(self._entities)} entity types (sync)"
            )

    def create_entity_sync(self, entity_type: str, attributes: dict[str, Any]) -> dict[str, Any]:
        """Create entity (sync wrapper for compatibility)."""
        try:
            asyncio.get_running_loop()
            # Cannot call sync method from async context
            raise RuntimeError(
                "Cannot call create_entity_sync from async context. "
                "Use 'await create_entity()' instead."
            )
        except RuntimeError as e:
            if "no running event loop" not in str(e).lower():
                raise
            # No event loop, safe to run synchronously
            return asyncio.run(self.create_entity(entity_type, attributes))


# Global instance for backward compatibility
_entity_repository: EntityRepository | None = None
_singleton_lock = threading.RLock()


def get_entity_repository() -> EntityRepository:
    """Get or create the singleton EntityRepository instance."""
    global _entity_repository
    if _entity_repository is None:
        with _singleton_lock:
            if _entity_repository is None:
                _entity_repository = EntityRepository()
    return _entity_repository
