"""
Compatibility adapter for DomainConfig classes - Issue #395

This module provides backward compatibility for legacy DomainConfig interfaces,
delegating to the new consolidated DomainConfigService.

DEPRECATED: Use app.core.domain_config.domain_config_service.DomainConfigService instead.
"""

import warnings
from pathlib import Path
from typing import Any

# Import the new consolidated service
from app.core.domain_config.domain_config_service import DomainConfigService as NewDomainConfigService

# Issue deprecation warning
warnings.warn(
    "Legacy DomainConfig classes are deprecated. Use app.core.domain_config.domain_config_service.DomainConfigService instead.",
    DeprecationWarning,
    stacklevel=2
)


class DomainConfigLoader:
    """
    DEPRECATED: Compatibility adapter for DomainConfigLoader.

    This class delegates to the new DomainConfigService for backward compatibility.
    Use DomainConfigService directly instead.
    """

    def __init__(self):
        """Initialize with deprecation warning."""
        warnings.warn(
            "DomainConfigLoader is deprecated. Use DomainConfigService instead.",
            DeprecationWarning,
            stacklevel=2
        )
        self._service = NewDomainConfigService()
        self._loaded_domains = {}
        self._active_domain = None

    def load_from_file(self, file_path: Path):
        """Load domain from file (sync compatibility method)."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self._service.load_from_file(file_path))
            if result:
                self._loaded_domains[result.id] = result
            return result
        finally:
            loop.close()

    def load_domain(self, domain_id: str):
        """Load domain by ID (sync compatibility method)."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self._service.load_domain(domain_id))
            if result:
                self._loaded_domains[result.id] = result
            return result
        finally:
            loop.close()

    def set_active_domain(self, config) -> None:
        """Set active domain."""
        self._active_domain = config

    def get_active_domain(self):
        """Get active domain."""
        return self._active_domain

    def get_loaded_domains(self) -> dict:
        """Get loaded domains."""
        return self._loaded_domains.copy()

    def clear_cache(self) -> None:
        """Clear cache."""
        self._loaded_domains.clear()
        self._service.clear_cache()


class DomainConfigManager:
    """
    DEPRECATED: Compatibility adapter for DomainConfigManager.

    This class delegates to the new DomainConfigService for backward compatibility.
    Use DomainConfigService directly instead.
    """

    def __init__(self):
        """Initialize with deprecation warning."""
        warnings.warn(
            "DomainConfigManager is deprecated. Use DomainConfigService instead.",
            DeprecationWarning,
            stacklevel=2
        )
        self._service = NewDomainConfigService()

    def __getattr__(self, name: str) -> Any:
        """Delegate all attribute access to the new service."""
        return getattr(self._service, name)


class DomainConfigCache:
    """
    DEPRECATED: Compatibility adapter for DomainConfigCache.

    This class delegates to the new DomainConfigService for backward compatibility.
    Use DomainConfigService directly instead.
    """

    def __init__(self, ttl: int = 3600):
        """Initialize with deprecation warning."""
        warnings.warn(
            "DomainConfigCache is deprecated. Use DomainConfigService instead.",
            DeprecationWarning,
            stacklevel=2
        )
        self._service = NewDomainConfigService(default_ttl=ttl)

    def __getattr__(self, name: str) -> Any:
        """Delegate all attribute access to the new service."""
        return getattr(self._service, name)


# Compatibility functions
def get_domain_config_loader() -> DomainConfigLoader:
    """Get domain config loader (deprecated)."""
    warnings.warn(
        "get_domain_config_loader() is deprecated. Use get_domain_config_service() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    from app.core.domain_config import DomainConfigService
    return DomainConfigService()
