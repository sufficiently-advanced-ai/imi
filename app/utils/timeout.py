import asyncio
import builtins
import functools
import threading
from collections.abc import Callable
from typing import Any


class TimeoutError(Exception):
    """Raised when a function execution exceeds the timeout"""

    def __init__(self, func_name: str, seconds: float):
        super().__init__(f"Function '{func_name}' timed out after {seconds} seconds")


def timeout(seconds: float) -> Callable:
    """
    Decorator to add timeout functionality to functions.

    Args:
        seconds: Maximum execution time in seconds

    Returns:
        Decorated function with timeout
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            result = [TimeoutError(func.__name__, seconds)]
            exception = [None]

            def target():
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:
                    exception[0] = e

            thread = threading.Thread(target=target)
            thread.daemon = True
            thread.start()
            thread.join(timeout=seconds)

            if thread.is_alive():
                # Thread is still running, timeout occurred
                raise TimeoutError(func.__name__, seconds)

            if exception[0]:
                raise exception[0]

            if isinstance(result[0], TimeoutError):
                raise result[0]

            return result[0]

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)
            except builtins.TimeoutError:
                raise TimeoutError(func.__name__, seconds)

        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator
