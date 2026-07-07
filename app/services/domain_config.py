"""Domain Configuration Service - Issue #156"""

import json
from pathlib import Path
from threading import RLock

import yaml

from app.core.domain_config import DomainConfigService
from app.model_schemas.domain_config import DomainConfiguration


class DomainConfigLoader:
    """Service for loading and managing domain configurations."""

    _lock = RLock()

    def __init__(self):
        self._active_domain: DomainConfiguration | None = None
        self._loaded_domains: dict[str, DomainConfiguration] = {}

    def load_from_file(self, file_path: Path) -> DomainConfiguration:
        """Load domain configuration from YAML or JSON file."""
        if not file_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")

        # Read file content
        content = file_path.read_text()

        # Parse based on file extension
        try:
            if file_path.suffix.lower() in [".yaml", ".yml"]:
                data = yaml.safe_load(content)
            elif file_path.suffix.lower() == ".json":
                data = json.loads(content)
            else:
                raise ValueError(f"Unsupported file format: {file_path.suffix}")
        except (yaml.YAMLError, json.JSONDecodeError) as e:
            raise ValueError(f"Invalid configuration: {str(e)}")

        # Extract domain configuration
        if "domain" in data:
            config_data = data["domain"]
        else:
            config_data = data

        # Validate and create configuration
        try:
            config = DomainConfiguration(**config_data)
            with self._lock:
                self._loaded_domains[config.id] = config
            return config
        except Exception as e:
            raise ValueError(f"Invalid configuration: {str(e)}")

    def set_active_domain(self, config: DomainConfiguration) -> None:
        """Set the active domain configuration."""
        with self._lock:
            self._active_domain = config

    def get_active_domain(self) -> DomainConfiguration | None:
        """Get the currently active domain configuration."""
        with self._lock:
            return self._active_domain

    def get_loaded_domains(self) -> dict[str, DomainConfiguration]:
        """Get all loaded domain configurations."""
        with self._lock:
            return self._loaded_domains.copy()

    def clear_cache(self) -> None:
        """Clear all loaded configurations except active."""
        with self._lock:
            active_id = self._active_domain.id if self._active_domain else None
            self._loaded_domains = {
                k: v for k, v in self._loaded_domains.items() if k == active_id
            }


def get_domain_config_loader() -> DomainConfigService:
    """Get the singleton DomainConfigService instance from the service registry.

    IMPORTANT: Uses the service registry pattern to ensure a single instance
    is used across the entire application, regardless of import path.
    This prevents the issue where different parts of the app create separate
    DomainConfigService instances that don't share state.

    Note: Returns DomainConfigService (not DomainConfigLoader) as that's the
    unified service class that provides all domain configuration functionality.
    """
    from app.core.service_registry import get_registry

    registry = get_registry()

    if not registry.has("domain_config"):
        def factory():
            return DomainConfigService()

        registry.register_factory("domain_config", factory)

    return registry.get("domain_config")
