"""
Consolidated DomainConfigService - Issue #395

This module consolidates functionality from:
- domain_config.py (basic loader)
- domain_config_loader.py (extended loader)
- domain_config_manager.py (manager pattern)
- domain_config_cache.py (caching layer)

Features consolidated:
- Layered architecture: loader → manager → cache
- TTL-based caching (3600s default)
- Change notification system
- Thread-safe operations
- File loading (YAML/JSON)
- Domain validation and management
- Backup and migration utilities
"""

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from app.model_schemas.domain_config import DomainConfiguration

logger = logging.getLogger(__name__)

# Configuration constants
DEFAULT_TTL = 3600  # Default TTL for cache entries in seconds
MAX_CACHE_SIZE = 100  # Maximum number of cached configurations


class DomainConfigLoader:
    """Domain configuration loader with file system support."""

    def __init__(self):
        """Initialize the domain loader."""
        self._cache = {}

    async def load_domain(self, domain_id: str) -> DomainConfiguration | None:
        """
        Load domain configuration by ID.

        Args:
            domain_id: Domain configuration ID

        Returns:
            DomainConfiguration if found, None otherwise
        """
        try:
            # Validate and constrain domain_id
            import re
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", domain_id):
                logger.warning("Invalid domain_id supplied: %s", domain_id)
                return None

            # Search only under whitelisted roots
            allowed_roots = [Path("config/domains"), Path("domains")]
            extensions = (".yaml", ".yml", ".json")
            for root in allowed_roots:
                base = root.resolve()
                for ext in extensions:
                    candidate = base / f"{domain_id}{ext}"
                    # Ensure resolved path is still under the allowed root
                    try:
                        candidate_resolved = candidate.resolve()
                        if candidate_resolved.is_relative_to(base) and candidate_resolved.exists():
                            return await self.load_from_file(candidate_resolved)
                    except (OSError, ValueError):
                        # Skip invalid paths
                        continue

            logger.warning(f"Domain configuration not found: {domain_id}")
            return None

        except Exception as e:
            logger.error(f"Error loading domain {domain_id}: {e}")
            return None

    async def load_from_file(self, file_path: Path) -> DomainConfiguration | None:
        """Load domain configuration from YAML or JSON file."""
        try:
            if not file_path.exists():
                logger.error(f"Configuration file not found: {file_path}")
                return None

            # Read file content
            content = file_path.read_text()

            # Parse based on file extension
            try:
                if file_path.suffix.lower() in [".yaml", ".yml"]:
                    data = yaml.safe_load(content)
                elif file_path.suffix.lower() == ".json":
                    data = json.loads(content)
                else:
                    logger.error(f"Unsupported file format: {file_path.suffix}")
                    return None
            except (yaml.YAMLError, json.JSONDecodeError) as e:
                logger.error(f"Invalid configuration: {str(e)}")
                return None

            # Extract domain configuration
            if "domain" in data:
                config_data = data["domain"]
            else:
                config_data = data

            # Validate and create configuration
            try:
                config = DomainConfiguration(**config_data)
                return config
            except Exception as e:
                logger.error(f"Invalid configuration: {str(e)}")
                return None

        except Exception as e:
            logger.error(f"Error loading file {file_path}: {e}")
            return None

    async def load_from_directory(self, directory_path: Path) -> list[DomainConfiguration]:
        """Load all domain configurations from a directory."""
        configs = []

        if not directory_path.exists() or not directory_path.is_dir():
            return configs

        # Supported file extensions
        extensions = [".yaml", ".yml", ".json"]

        for file_path in directory_path.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in extensions:
                config = await self.load_from_file(file_path)
                if config:
                    configs.append(config)

        return configs


class DomainConfigService:
    """
    Unified domain configuration service with caching and management.

    Consolidates functionality from multiple domain config implementations into
    a single, comprehensive service with TTL-based caching and change notifications.
    """

    def __init__(self, default_ttl: int = DEFAULT_TTL):
        """
        Initialize the domain configuration service.

        Args:
            default_ttl: Default TTL for cache entries in seconds
        """
        self.default_ttl = default_ttl

        # Core components
        self._loader = DomainConfigLoader()

        # Cache layer
        self._cache: dict[str, DomainConfiguration] = {}
        self._ttl_cache: dict[str, datetime] = {}

        # Management layer
        self._active_domain: DomainConfiguration | None = None

        # Thread safety
        self._lock = threading.RLock()
        self._auto_load_lock = asyncio.Lock()

        # Change notification system
        self._change_handlers: list[Callable[[str, str, DomainConfiguration | None], None]] = []

        logger.info("DomainConfigService initialized")

        # Note: Auto-loading happens on first access via get_active_domain

    async def ensure_active_domain(self) -> None:
        """No-op — domain is always available via get_domain_config()."""
        pass

    async def _auto_load_initial_domain(self) -> None:
        """No-op — domain is loaded at import time via active_domain.py."""
        pass

    # Core loading functionality
    async def load_domain(self, domain_id: str) -> DomainConfiguration | None:
        """
        Load a domain configuration with caching.

        Args:
            domain_id: Domain configuration ID

        Returns:
            DomainConfiguration if found, None otherwise
        """
        try:
            # Check cache first
            cached = await self.get_cached_domain(domain_id)
            if cached is not None:
                return cached

            # Load from source
            config = await self._loader.load_domain(domain_id)
            if config:
                # Cache the result
                await self._set_cache_entry(domain_id, config, self.default_ttl)

                # Notify handlers
                await self._notify_handlers("domain_loaded", domain_id, config)

                logger.info(f"Loaded domain: {domain_id}")

            return config

        except Exception as e:
            logger.error(f"Error loading domain {domain_id}: {e}")
            return None

    async def get_config_by_id(self, domain_id: str) -> DomainConfiguration | None:
        """
        Get domain configuration by ID (alias for load_domain for backward compatibility).

        Args:
            domain_id: Domain configuration ID

        Returns:
            DomainConfiguration if found, None otherwise
        """
        return await self.load_domain(domain_id)

    async def load_from_file(self, file_path: Path) -> DomainConfiguration | None:
        """Load domain configuration from file."""
        return await self._loader.load_from_file(file_path)

    async def load_from_directory(self, directory_path: Path) -> list[DomainConfiguration]:
        """Load all domain configurations from directory."""
        return await self._loader.load_from_directory(directory_path)

    async def reload_domain(self, domain_id: str) -> DomainConfiguration | None:
        """
        Reload a domain configuration, bypassing cache.

        Args:
            domain_id: Domain configuration ID

        Returns:
            DomainConfiguration if found, None otherwise
        """
        # Remove from cache first
        await self.unload_domain(domain_id)

        # Load fresh copy
        return await self.load_domain(domain_id)

    # Cache management
    async def get_cached_domain(self, domain_id: str) -> DomainConfiguration | None:
        """Get domain from cache if not expired."""
        with self._lock:
            if domain_id not in self._cache:
                return None

            # Check TTL
            if domain_id in self._ttl_cache:
                if datetime.utcnow() > self._ttl_cache[domain_id]:
                    # Expired, remove from cache
                    self._cache.pop(domain_id, None)
                    self._ttl_cache.pop(domain_id, None)
                    return None

            return self._cache.get(domain_id)

    async def _set_cache_entry(
        self, domain_id: str, config: DomainConfiguration, ttl: int = None
    ) -> None:
        """Set cache entry with TTL."""
        if ttl is None:
            ttl = self.default_ttl

        with self._lock:
            # Enforce cache size limit
            if len(self._cache) >= MAX_CACHE_SIZE:
                await self._evict_oldest_cached()

            self._cache[domain_id] = config
            self._ttl_cache[domain_id] = datetime.utcnow() + timedelta(seconds=ttl)

    async def _evict_oldest_cached(self) -> None:
        """Evict the oldest cached domain to make space."""
        with self._lock:
            if not self._ttl_cache:
                return

            # Find oldest entry
            oldest_id = min(self._ttl_cache.keys(), key=lambda k: self._ttl_cache[k])
            self._cache.pop(oldest_id, None)
            self._ttl_cache.pop(oldest_id, None)
            logger.debug(f"Evicted cached domain: {oldest_id}")

    def clear_cache(self) -> None:
        """Clear all cached domains."""
        with self._lock:
            self._cache.clear()
            self._ttl_cache.clear()
            logger.info("Cleared domain config cache")

    def _clear_expired_entries(self) -> None:
        """Remove expired cache entries."""
        with self._lock:
            now = datetime.utcnow()
            expired_keys = [
                key for key, expiry in self._ttl_cache.items()
                if now > expiry
            ]

            for key in expired_keys:
                self._cache.pop(key, None)
                self._ttl_cache.pop(key, None)

            if expired_keys:
                logger.debug(f"Removed {len(expired_keys)} expired cache entries")

    # Domain management
    async def set_active_domain(self, config: DomainConfiguration) -> None:
        """No-op with warning — domain is set at import time via active_domain.py."""
        logger.warning(
            "set_active_domain() is a no-op. Domain is loaded from "
            "ACTIVE_DOMAIN env var at import time."
        )

    def get_active_domain(self) -> DomainConfiguration | None:
        """Delegate to the module-level active domain loader."""
        from .active_domain import get_domain_config
        return get_domain_config()

    def list_loaded_domains(self) -> dict[str, DomainConfiguration]:
        """Get all loaded (cached) domain configurations."""
        # Clean up expired entries first
        self._clear_expired_entries()

        with self._lock:
            return self._cache.copy()

    async def unload_domain(self, domain_id: str) -> None:
        """Unload a domain from cache and deactivate if active."""
        with self._lock:
            # Remove from cache
            self._cache.pop(domain_id, None)
            self._ttl_cache.pop(domain_id, None)

            # Deactivate if it's the active domain
            active = self.get_active_domain()
            if active and active.id == domain_id:
                self._active_domain = None

        # Notify handlers
        await self._notify_handlers("domain_unloaded", domain_id, None)

        logger.info(f"Unloaded domain: {domain_id}")

    async def domain_exists(self, domain_id: str) -> bool:
        """Check if a domain configuration exists."""
        # Check cache first
        if domain_id in self._cache:
            return True

        # Check file system without loading
        possible_paths = [
            Path(f"config/domains/{domain_id}.yaml"),
            Path(f"config/domains/{domain_id}.yml"),
            Path(f"config/domains/{domain_id}.json"),
            Path(f"domains/{domain_id}.yaml"),
            Path(f"domains/{domain_id}.yml"),
            Path(f"domains/{domain_id}.json"),
        ]

        return any(path.exists() for path in possible_paths)

    # Validation
    async def validate_domain_config(self, config_data: dict[str, Any]) -> tuple[bool, list[str]]:
        """
        Validate domain configuration data.

        Args:
            config_data: Raw configuration data

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        try:
            # Try to create DomainConfiguration object
            DomainConfiguration(**config_data)
            return True, []
        except Exception as e:
            errors.append(str(e))
            return False, errors

    async def validate_domain_schema(self, config: DomainConfiguration) -> tuple[bool, list[str]]:
        """
        Validate domain configuration against schema.

        Args:
            config: Domain configuration to validate

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        try:
            # Basic validation - DomainConfiguration should already be validated
            if not config.id:
                errors.append("Domain ID is required")
            if not config.name:
                errors.append("Domain name is required")
            if not config.entities:
                errors.append("Domain must have at least one entity type")

            # Validate entity schemas
            for entity_id, entity_schema in config.entities.items():
                if not entity_schema.id:
                    errors.append(f"Entity {entity_id} missing ID")
                if not entity_schema.name:
                    errors.append(f"Entity {entity_id} missing name")

            return len(errors) == 0, errors

        except Exception as e:
            errors.append(f"Validation error: {str(e)}")
            return False, errors

    # Change notification system
    def register_change_handler(
        self, handler: Callable[[str, str, DomainConfiguration | None], None]
    ) -> None:
        """Register a change notification handler."""
        with self._lock:
            if handler not in self._change_handlers:
                self._change_handlers.append(handler)

    def unregister_change_handler(
        self, handler: Callable[[str, str, DomainConfiguration | None], None]
    ) -> None:
        """Unregister a change notification handler."""
        with self._lock:
            if handler in self._change_handlers:
                self._change_handlers.remove(handler)

    async def _notify_handlers(
        self, event: str, domain_id: str, config: DomainConfiguration | None
    ) -> None:
        """Notify all registered change handlers."""
        for handler in self._change_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event, domain_id, config)
                else:
                    handler(event, domain_id, config)
            except Exception as e:
                logger.error(f"Error in change handler: {e}")

    # Migration and backup
    async def migrate_legacy_config(self, legacy_config: dict[str, Any]) -> DomainConfiguration | None:
        """
        Migrate legacy configuration format to new format.

        Args:
            legacy_config: Legacy configuration data

        Returns:
            Migrated DomainConfiguration or None if migration failed
        """
        try:
            # Map legacy fields to new format
            migrated_data = {}

            # Map domain fields
            if "domain_id" in legacy_config:
                migrated_data["id"] = legacy_config["domain_id"]
            if "domain_name" in legacy_config:
                migrated_data["name"] = legacy_config["domain_name"]
            if "description" in legacy_config:
                migrated_data["description"] = legacy_config["description"]

            # Map entity types (if present)
            if "entity_types" in legacy_config:
                entities = {}
                for entity_type in legacy_config["entity_types"]:
                    entities[entity_type] = {
                        "id": entity_type,
                        "name": entity_type.title(),
                        "description": f"{entity_type.title()} entity",
                        "attributes": {}
                    }
                migrated_data["entities"] = entities

            return DomainConfiguration(**migrated_data)

        except Exception as e:
            logger.error(f"Error migrating legacy config: {e}")
            return None

    async def export_domain_config(
        self, config: DomainConfiguration, file_path: Path, format: str = "yaml"
    ) -> bool:
        """
        Export domain configuration to file.

        Args:
            config: Configuration to export
            file_path: Target file path
            format: Export format ("yaml" or "json")

        Returns:
            True if successful, False otherwise
        """
        try:
            # Write to file
            if format.lower() == "json":
                with open(file_path, 'w') as f:
                    # Use model_dump_json for better serialization
                    f.write(config.model_dump_json(indent=2))
            else:
                # Default to YAML
                config_dict = config.model_dump()
                with open(file_path, 'w') as f:
                    yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)

            logger.info(f"Exported domain config to: {file_path}")
            return True

        except Exception as e:
            logger.error(f"Error exporting config: {e}")
            return False

    async def backup_domains(self, backup_dir: Path) -> bool:
        """
        Backup all loaded domains to directory.

        Args:
            backup_dir: Target backup directory

        Returns:
            True if successful, False otherwise
        """
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)

            domains = await self.list_loaded_domains()

            for domain_id, config in domains.items():
                backup_file = backup_dir / f"{domain_id}.yaml"
                success = await self.export_domain_config(config, backup_file)
                if not success:
                    logger.error(f"Failed to backup domain: {domain_id}")
                    return False

            logger.info(f"Backed up {len(domains)} domains to {backup_dir}")
            return True

        except Exception as e:
            logger.error(f"Error backing up domains: {e}")
            return False

    # Statistics and monitoring
    async def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            return {
                "total_cached": len(self._cache),
                "cache_size": len(self._cache),
                "max_cache_size": MAX_CACHE_SIZE,
                "memory_usage": sum(len(str(config)) for config in self._cache.values()),
                "expired_entries": sum(
                    1 for expiry in self._ttl_cache.values()
                    if datetime.utcnow() > expiry
                )
            }

    async def get_domain_info(self, domain_id: str) -> dict[str, Any] | None:
        """Get detailed information about a domain."""
        config = await self.get_cached_domain(domain_id)
        if not config:
            return None

        with self._lock:
            expiry = self._ttl_cache.get(domain_id)

        return {
            "id": config.id,
            "name": config.name,
            "description": getattr(config, 'description', ''),
            "entity_count": len(config.entities),
            "loaded_at": "unknown",  # Could track this if needed
            "expires_at": expiry.isoformat() if expiry else None,
            "is_active": (active_dom := self.get_active_domain()) is not None and active_dom.id == domain_id
        }

    async def health_check(self) -> dict[str, Any]:
        """Perform service health check."""
        try:
            cache_stats = await self.get_cache_stats()

            # Test basic functionality
            test_passed = True
            try:
                # Try to validate a simple config
                test_config = {"id": "test", "name": "Test", "entities": {}}
                is_valid, _ = await self.validate_domain_config(test_config)
                if not is_valid:
                    test_passed = False
            except Exception:
                test_passed = False

            status = "healthy" if test_passed else "degraded"

            return {
                "status": status,
                "cache_stats": cache_stats,
                "loader_status": "ok",
                "active_domain": self.get_active_domain().id if self.get_active_domain() else None,
                "timestamp": datetime.utcnow().isoformat()
            }

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }


# IMPORTANT: Singleton pattern unified via service registry
# All imports of get_domain_config_service should get the same instance
def get_domain_config_service() -> DomainConfigService:
    """Get the singleton DomainConfigService instance from the service registry.

    IMPORTANT: Uses the service registry pattern to ensure a single instance
    is used across the entire application, regardless of import path.
    This prevents the bug where different import paths create different instances.
    """
    from app.core.service_registry import get_registry

    registry = get_registry()

    if not registry.has("domain_config"):
        def factory():
            return DomainConfigService()

        registry.register_factory("domain_config", factory)

    return registry.get("domain_config")
