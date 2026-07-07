"""
Service Registry Pattern Implementation

Provides centralized service management with lazy initialization,
singleton patterns, and circular dependency resolution.
"""

import logging
from collections.abc import Callable
from threading import Lock
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ServiceRegistry:
    """
    Central registry for managing service instances.

    Features:
    - Singleton pattern for services
    - Lazy initialization
    - Circular dependency resolution
    - Thread-safe operations
    """

    _instance: Optional['ServiceRegistry'] = None
    _lock = Lock()

    def __new__(cls) -> 'ServiceRegistry':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._services: dict[str, Any] = {}
            self._factories: dict[str, Callable[[], Any]] = {}
            self._initializing: set = set()
            self._initialized = True
            logger.info("ServiceRegistry initialized")

    def register_factory(
        self,
        service_name: str,
        factory: Callable[[], Any],
        override: bool = False
    ) -> None:
        """
        Register a factory function for lazy service initialization.

        Args:
            service_name: Unique identifier for the service
            factory: Function that creates and returns the service instance
            override: Whether to override existing registration
        """
        if service_name in self._factories and not override:
            logger.warning(f"Service '{service_name}' already registered")
            return

        self._factories[service_name] = factory
        logger.debug(f"Registered factory for service: {service_name}")

    def register_instance(
        self,
        service_name: str,
        instance: Any,
        override: bool = False
    ) -> None:
        """
        Register a pre-initialized service instance.

        Args:
            service_name: Unique identifier for the service
            instance: The service instance
            override: Whether to override existing instance
        """
        if service_name in self._services and not override:
            logger.warning(f"Service instance '{service_name}' already exists")
            return

        self._services[service_name] = instance
        logger.debug(f"Registered instance for service: {service_name}")

    def get(self, service_name: str) -> Any:
        """
        Get a service instance, initializing if necessary.

        Args:
            service_name: The service identifier

        Returns:
            The service instance

        Raises:
            ValueError: If service is not registered
            RuntimeError: If circular dependency detected
        """
        # Return existing instance if available
        if service_name in self._services:
            return self._services[service_name]

        # Check for circular dependency
        if service_name in self._initializing:
            raise RuntimeError(
                f"Circular dependency detected for service: {service_name}"
            )

        # Get factory and initialize
        if service_name not in self._factories:
            raise ValueError(f"Service '{service_name}' not registered")

        self._initializing.add(service_name)
        try:
            logger.debug(f"Initializing service: {service_name}")
            instance = self._factories[service_name]()
            self._services[service_name] = instance
            logger.info(f"Service initialized: {service_name}")
            return instance
        finally:
            self._initializing.discard(service_name)

    def get_optional(self, service_name: str) -> Any | None:
        """
        Get a service instance if registered, otherwise return None.

        Args:
            service_name: The service identifier

        Returns:
            The service instance or None
        """
        try:
            return self.get(service_name)
        except (ValueError, RuntimeError):
            return None

    def has(self, service_name: str) -> bool:
        """
        Check if a service is registered.

        Args:
            service_name: The service identifier

        Returns:
            True if service is registered
        """
        return (
            service_name in self._services or
            service_name in self._factories
        )

    def clear(self) -> None:
        """Clear all registered services and factories."""
        self._services.clear()
        self._factories.clear()
        self._initializing.clear()
        logger.info("ServiceRegistry cleared")

    def get_registered_services(self) -> dict[str, str]:
        """
        Get information about all registered services.

        Returns:
            Dictionary mapping service names to their status
        """
        result = {}

        for name in self._services:
            result[name] = "initialized"

        for name in self._factories:
            if name not in self._services:
                result[name] = "registered"

        return result


# Global registry instance
_registry = ServiceRegistry()


def get_registry() -> ServiceRegistry:
    """Get the global service registry instance."""
    return _registry


def register_factory(
    service_name: str,
    factory: Callable[[], Any],
    override: bool = False
) -> None:
    """
    Convenience function to register a service factory.

    Args:
        service_name: Unique identifier for the service
        factory: Function that creates the service
        override: Whether to override existing registration
    """
    get_registry().register_factory(service_name, factory, override)


def register_instance(
    service_name: str,
    instance: Any,
    override: bool = False
) -> None:
    """
    Convenience function to register a service instance.

    Args:
        service_name: Unique identifier for the service
        instance: The service instance
        override: Whether to override existing instance
    """
    get_registry().register_instance(service_name, instance, override)


def get_service(service_name: str) -> Any:
    """
    Convenience function to get a service.

    Args:
        service_name: The service identifier

    Returns:
        The service instance
    """
    return get_registry().get(service_name)


def get_optional_service(service_name: str) -> Any | None:
    """
    Convenience function to get an optional service.

    Args:
        service_name: The service identifier

    Returns:
        The service instance or None
    """
    return get_registry().get_optional(service_name)


# Decorator for automatic service registration
def service(name: str | None = None):
    """
    Decorator to automatically register a service class.

    Args:
        name: Optional service name (uses class name if not provided)

    Example:
        @service("claude_client")
        class ClaudeClient:
            pass
    """
    def decorator(cls: type) -> type:
        service_name = name or cls.__name__

        # Register factory that creates instance
        def factory():
            return cls()

        register_factory(service_name, factory)

        # Add convenience method to get instance
        @classmethod
        def get_instance(cls):
            return get_service(service_name)

        cls.get_instance = get_instance

        return cls

    return decorator
