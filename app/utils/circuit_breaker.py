import functools
import time
from collections.abc import Callable


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open"""

    pass


class CircuitBreaker:
    """
    Circuit breaker pattern implementation to prevent cascading failures.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: type[Exception] = Exception,
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Time in seconds before attempting to close circuit
            expected_exception: Exception type to count as failure
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self._failure_count = 0
        self._last_failure_time = None
        self._state = "closed"  # closed, open, half-open

    def __call__(self, func: Callable) -> Callable:
        """Decorator to apply circuit breaker to a function"""

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if self._state == "open":
                if (
                    self._last_failure_time
                    and time.time() - self._last_failure_time > self.recovery_timeout
                ):
                    # Try to recover
                    self._state = "half-open"
                else:
                    func_name = getattr(func, "__name__", "unknown")
                    raise CircuitBreakerError(
                        f"Circuit breaker is open for {func_name}"
                    )

            try:
                result = func(*args, **kwargs)
                # Success - reset failure count
                if self._state == "half-open":
                    self._state = "closed"
                self._failure_count = 0
                return result

            except self.expected_exception as e:
                self._failure_count += 1
                self._last_failure_time = time.time()

                if self._failure_count >= self.failure_threshold:
                    self._state = "open"

                raise e

        return wrapper

    def reset(self):
        """Manually reset the circuit breaker"""
        self._failure_count = 0
        self._last_failure_time = None
        self._state = "closed"
