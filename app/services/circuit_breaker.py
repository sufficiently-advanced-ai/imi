"""
Enhanced Circuit Breaker implementation - Issue #398

Production-ready circuit breaker with:
- Service-specific configurations
- Async support with timeouts
- Comprehensive metrics tracking
- Service registry for centralized management
- Integration with health checks
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CircuitBreakerState(str, Enum):
    """Circuit breaker states"""
    CLOSED = "closed"        # Normal operation
    OPEN = "open"           # Failing, reject all calls
    HALF_OPEN = "half_open" # Testing recovery


class CircuitBreakerError(Exception):
    """Base exception for circuit breaker errors"""
    pass


class CircuitBreakerOpenError(CircuitBreakerError):
    """Raised when circuit breaker is open and rejecting calls"""
    pass


@dataclass
class CircuitBreakerMetrics:
    """Metrics tracked by circuit breaker"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: float | None = None
    last_success_time: float | None = None

    @property
    def failure_rate(self) -> float:
        """Calculate failure rate as percentage"""
        if self.total_calls == 0:
            return 0.0
        return (self.failed_calls / self.total_calls) * 100


class CircuitBreaker:
    """
    Production-ready circuit breaker implementation

    Features:
    - Async support with configurable timeouts
    - Comprehensive metrics tracking
    - Service-specific configuration
    - State transition notifications
    - Health status reporting
    """

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        timeout: float = 30.0,
    ):
        # Validation
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if recovery_timeout < 0:
            raise ValueError("recovery_timeout cannot be negative")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.timeout = timeout

        # State tracking
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self._last_failure_time: float | None = None

        # Metrics
        self.metrics = CircuitBreakerMetrics()

        # Logging
        self._logger = logger.getChild(f"circuit_breaker.{service_name}")

    async def call(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        """
        Execute an operation through the circuit breaker

        Args:
            operation: Async callable to execute

        Returns:
            Result of the operation

        Raises:
            CircuitBreakerOpenError: If circuit is open
            asyncio.TimeoutError: If operation times out
            Exception: Any exception from the operation
        """
        # Check if circuit is open
        if self.state == CircuitBreakerState.OPEN:
            if self._should_attempt_recovery():
                self.state = CircuitBreakerState.HALF_OPEN
                self._logger.info(f"Circuit breaker transitioning to HALF_OPEN for {self.service_name}")
            else:
                self.metrics.rejected_calls += 1
                raise CircuitBreakerOpenError(f"Circuit breaker is OPEN for service: {self.service_name}")

        # Execute operation with timeout
        time.time()
        self.metrics.total_calls += 1

        try:
            # Apply timeout to operation
            result = await asyncio.wait_for(operation(), timeout=self.timeout)

            # Success - record metrics and potentially close circuit
            self.metrics.successful_calls += 1
            self.metrics.last_success_time = time.time()

            if self.state == CircuitBreakerState.HALF_OPEN:
                # Recovery successful - close circuit
                self.state = CircuitBreakerState.CLOSED
                self.failure_count = 0
                self._logger.info(f"Circuit breaker CLOSED for {self.service_name} after successful recovery")

            return result

        except TimeoutError as e:
            # Timeout counts as failure
            self._record_failure()
            self._logger.warning(f"Operation timeout for {self.service_name}: {self.timeout}s")
            raise e

        except Exception as e:
            # Operation failed - record failure
            self._record_failure()
            self._logger.warning(f"Operation failed for {self.service_name}: {str(e)}")
            raise e

    def _should_attempt_recovery(self) -> bool:
        """Check if enough time has passed to attempt recovery"""
        if self._last_failure_time is None:
            return True
        return (time.time() - self._last_failure_time) >= self.recovery_timeout

    def _record_failure(self) -> None:
        """Record a failure and potentially open the circuit"""
        self.failure_count += 1
        self.metrics.failed_calls += 1
        self._last_failure_time = time.time()
        self.metrics.last_failure_time = self._last_failure_time

        # Check if we should open the circuit
        if self.failure_count >= self.failure_threshold:
            old_state = self.state
            self.state = CircuitBreakerState.OPEN

            if old_state != CircuitBreakerState.OPEN:
                self._logger.error(
                    f"Circuit breaker OPENED for {self.service_name} "
                    f"(failures: {self.failure_count}/{self.failure_threshold})"
                )

    def get_health_status(self) -> dict[str, Any]:
        """Get current health status and metrics"""
        return {
            "service_name": self.service_name,
            "state": self.state,
            "failure_count": self.failure_count,
            "total_calls": self.metrics.total_calls,
            "successful_calls": self.metrics.successful_calls,
            "failed_calls": self.metrics.failed_calls,
            "rejected_calls": self.metrics.rejected_calls,
            "last_failure_time": self.metrics.last_failure_time,
            "failure_rate": self.metrics.failure_rate,
            "configuration": {
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "timeout": self.timeout
            }
        }

    def reset(self) -> None:
        """Manually reset the circuit breaker"""
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self._last_failure_time = None
        self._logger.info(f"Circuit breaker manually reset for {self.service_name}")


class ServiceRegistry:
    """
    Registry for managing service-specific circuit breakers

    Provides centralized configuration and management of circuit breakers
    for different services with service-specific settings.
    """

    # Default configurations for known services
    DEFAULT_CONFIGURATIONS = {
        "claude_api": {
            "failure_threshold": 5,
            "recovery_timeout": 60,
            "timeout": 30
        },
        "github_api": {
            "failure_threshold": 5,
            "recovery_timeout": 60,
            "timeout": 15
        },
        "database": {
            "failure_threshold": 3,
            "recovery_timeout": 30,
            "timeout": 10
        },
        "git_operations": {
            "failure_threshold": 5,
            "recovery_timeout": 60,
            "timeout": 20
        }
    }

    def __init__(self, configurations: dict[str, dict[str, Any]] | None = None):
        self._configurations = configurations or self.DEFAULT_CONFIGURATIONS.copy()
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._logger = logger.getChild("service_registry")

        self._logger.info(f"Service registry initialized with {len(self._configurations)} service configurations")

    def get_circuit_breaker(self, service_name: str) -> CircuitBreaker:
        """
        Get or create a circuit breaker for the specified service

        Args:
            service_name: Name of the service

        Returns:
            Circuit breaker instance for the service
        """
        if service_name not in self._circuit_breakers:
            # Get configuration for this service
            config = self._configurations.get(service_name, {
                "failure_threshold": 5,
                "recovery_timeout": 60,
                "timeout": 30
            })

            # Create new circuit breaker
            self._circuit_breakers[service_name] = CircuitBreaker(
                service_name=service_name,
                **config
            )

            self._logger.info(f"Created circuit breaker for service: {service_name}")

        return self._circuit_breakers[service_name]

    def get_health_status(self) -> dict[str, dict[str, Any]]:
        """Get health status for all registered circuit breakers"""
        return {
            service_name: breaker.get_health_status()
            for service_name, breaker in self._circuit_breakers.items()
        }

    def add_service_configuration(self, service_name: str, config: dict[str, Any]) -> None:
        """Add or update configuration for a service"""
        self._configurations[service_name] = config

        # If circuit breaker already exists, it will keep existing instance
        # New configuration applies to future instances
        self._logger.info(f"Updated configuration for service: {service_name}")

    def reset_circuit_breaker(self, service_name: str) -> bool:
        """Reset a specific circuit breaker"""
        if service_name in self._circuit_breakers:
            self._circuit_breakers[service_name].reset()
            return True
        return False

    def reset_all_circuit_breakers(self) -> None:
        """Reset all circuit breakers"""
        for breaker in self._circuit_breakers.values():
            breaker.reset()
        self._logger.info("All circuit breakers reset")


# Global service registry instance
_service_registry: ServiceRegistry | None = None


def get_service_registry() -> ServiceRegistry:
    """Get the global service registry instance"""
    global _service_registry
    if _service_registry is None:
        _service_registry = ServiceRegistry()
    return _service_registry


# Decorator for easy circuit breaker application
def circuit_breaker(service_name: str):
    """
    Decorator to apply circuit breaker to async functions

    Usage:
        @circuit_breaker("my_service")
        async def my_operation():
            # Your async operation here
            pass
    """
    def decorator(func: Callable[[], Awaitable[Any]]) -> Callable[[], Awaitable[Any]]:
        async def wrapper(*args, **kwargs) -> Any:
            registry = get_service_registry()
            breaker = registry.get_circuit_breaker(service_name)

            async def operation():
                return await func(*args, **kwargs)

            return await breaker.call(operation)

        return wrapper
    return decorator
