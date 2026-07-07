import asyncio
import functools
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class DegradedModeError(Exception):
    """Raised when operating in degraded mode"""

    pass


class PartialSuccess:
    """Tracks partial success/failure of operations"""

    def __init__(self):
        self.successes: dict[str, Any] = {}
        self.failures: dict[str, Exception] = {}

    def add_success(self, key: str, result: Any) -> None:
        """Record a successful operation"""
        self.successes[key] = result

    def add_failure(self, key: str, exception: Exception) -> None:
        """Record a failed operation"""
        self.failures[key] = exception

    def is_complete_success(self) -> bool:
        """Check if all operations succeeded"""
        return len(self.failures) == 0 and len(self.successes) > 0

    def is_complete_failure(self) -> bool:
        """Check if all operations failed"""
        return len(self.successes) == 0 and len(self.failures) > 0

    def is_partial_success(self) -> bool:
        """Check if some operations succeeded and some failed"""
        return len(self.successes) > 0 and len(self.failures) > 0

    def get_summary(self) -> dict[str, Any]:
        """Get summary of results"""
        return {
            "total": len(self.successes) + len(self.failures),
            "succeeded": len(self.successes),
            "failed": len(self.failures),
            "successes": self.successes,
            "failures": {k: str(v) for k, v in self.failures.items()},
        }


class ErrorCollector:
    """Context manager for collecting errors"""

    def __init__(self):
        self.errors: list[Exception] = []

    def add(self, error: Exception) -> None:
        """Add an error to the collection"""
        self.errors.append(error)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Don't suppress exceptions
        return False

    def __len__(self):
        return len(self.errors)

    def __iter__(self):
        return iter(self.errors)


def partial_success() -> PartialSuccess:
    """Create a new PartialSuccess tracker"""
    return PartialSuccess()


def collect_errors() -> ErrorCollector:
    """Create a new error collector context manager"""
    return ErrorCollector()


def with_fallback(
    default: Any | Callable[[], Any],
    log_errors: bool = True,
    mark_degraded: bool = False,
    preserve_context: bool = False,
) -> Callable:
    """
    Decorator to provide fallback values on exception.

    Args:
        default: Default value or callable that returns default
        log_errors: Whether to log errors
        mark_degraded: Whether to mark function as operating in degraded mode
        preserve_context: Whether to pass exception to default callable

    Returns:
        Decorated function with fallback behavior
    """

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    result = await func(*args, **kwargs)
                    if mark_degraded and hasattr(async_wrapper, "_is_degraded"):
                        async_wrapper._is_degraded = False
                    return result
                except Exception as e:
                    if log_errors:
                        logger.warning(
                            f"Falling back to default for {func.__name__}: {str(e)}"
                        )

                    if mark_degraded:
                        async_wrapper._is_degraded = True

                    if callable(default):
                        if preserve_context:
                            return default(e)
                        else:
                            return default()
                    else:
                        return default

            # Add degraded mode tracking
            if mark_degraded:
                async_wrapper._is_degraded = False

            return async_wrapper
        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    result = func(*args, **kwargs)
                    if mark_degraded and hasattr(sync_wrapper, "_is_degraded"):
                        sync_wrapper._is_degraded = False
                    return result
                except Exception as e:
                    if log_errors:
                        logger.warning(
                            f"Falling back to default for {func.__name__}: {str(e)}"
                        )

                    if mark_degraded:
                        sync_wrapper._is_degraded = True

                    if callable(default):
                        if preserve_context:
                            return default(e)
                        else:
                            return default()
                    else:
                        return default

            # Add degraded mode tracking
            if mark_degraded:
                sync_wrapper._is_degraded = False

            return sync_wrapper

    return decorator
