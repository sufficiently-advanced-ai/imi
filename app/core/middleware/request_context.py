"""
Request Context Middleware

Provides request context propagation with correlation IDs,
user context, and request metadata for tracing and debugging.
"""

import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Context variables for request-scoped data
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_context_var: ContextVar[dict[str, Any] | None] = ContextVar("user_context", default=None)
request_metadata_var: ContextVar[dict[str, Any] | None] = ContextVar("request_metadata", default=None)

# Tenant scoping (multi-tenancy core primitive — Phase 4.1).
# Defaults to "default" so code running OUTSIDE a request (scripts, background
# tasks, tests) resolves to the single default tenant container with no
# middleware involved. This default is what keeps single-tenant behavior — and
# the existing test suite — unchanged. Set per-request by TenantContextMiddleware
# (app/core/tenancy/middleware.py). Re-exported from app/core/tenancy/context.py.
DEFAULT_TENANT_ID = "default"
current_tenant_id: ContextVar[str] = ContextVar("current_tenant_id", default=DEFAULT_TENANT_ID)


def ambient_tenant_id() -> str | None:
    """Return the current tenant id, or None when running single-tenant.

    Used to stamp tenant scope onto records at persistence chokepoints
    (issue #953). Returning None for the default tenant keeps single-tenant
    data byte-identical to before multi-tenancy existed.
    """
    tenant_id = current_tenant_id.get()
    return tenant_id if tenant_id != DEFAULT_TENANT_ID else None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware for managing request context throughout the request lifecycle.

    Features:
    - Generates or extracts request IDs for tracing
    - Propagates user context from authentication
    - Tracks request metadata (timing, path, method)
    - Provides context variables accessible throughout the request
    """

    def __init__(self, app, service_name: str = "imi"):
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next):
        """Process request with context management."""
        # Generate or extract request ID
        request_id = self._get_or_generate_request_id(request)

        # Set up request metadata
        request_metadata = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query) if request.url.query else None,
            "service": self.service_name,
            "start_time": time.time()
        }

        # Extract user context if available
        user_context = self._extract_user_context(request)

        # Set context variables
        request_id_var.set(request_id)
        user_context_var.set(user_context)
        request_metadata_var.set(request_metadata)

        # Store in request state for other middleware
        request.state.request_id = request_id
        request.state.user_context = user_context
        request.state.request_metadata = request_metadata

        # Log request start
        logger.info(
            f"Request started: {request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "user_id": user_context.get("id") if user_context else None,
                "method": request.method,
                "path": request.url.path
            }
        )

        try:
            # Process request
            response = await call_next(request)

            # Add headers to response
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Service-Name"] = self.service_name

            # Calculate duration
            duration = time.time() - request_metadata["start_time"]
            response.headers["X-Response-Time"] = f"{duration:.3f}"

            # Log request completion
            logger.info(
                f"Request completed: {request.method} {request.url.path} "
                f"[{response.status_code}] in {duration:.3f}s",
                extra={
                    "request_id": request_id,
                    "status_code": response.status_code,
                    "duration": duration
                }
            )

            return response

        except Exception as e:
            # Calculate duration for error case
            duration = time.time() - request_metadata["start_time"]

            # Log request failure
            logger.error(
                f"Request failed: {request.method} {request.url.path} "
                f"after {duration:.3f}s - {str(e)}",
                extra={
                    "request_id": request_id,
                    "duration": duration,
                    "error": str(e)
                },
                exc_info=True
            )

            # Re-raise for error handler middleware
            raise

    def _get_or_generate_request_id(self, request: Request) -> str:
        """Get request ID from headers or generate new one."""
        # Check standard headers
        request_id = (
            request.headers.get("X-Request-ID") or
            request.headers.get("X-Correlation-ID") or
            request.headers.get("X-Trace-ID")
        )

        # Generate if not provided
        if not request_id:
            request_id = str(uuid.uuid4())

        return request_id

    def _extract_user_context(self, request: Request) -> dict[str, Any] | None:
        """Extract user context from request."""
        # Check if auth middleware has set user
        if hasattr(request.state, "user"):
            return request.state.user

        # Check for user in headers (for service-to-service calls)
        user_id = request.headers.get("X-User-ID")
        if user_id:
            return {
                "id": user_id,
                "source": "header"
            }

        return None


class LoggingContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enhance logging with request context.

    Adds request ID and user context to all log messages
    within the request scope.
    """

    async def dispatch(self, request: Request, call_next):
        """Process request with enhanced logging context."""
        # Get context from request state
        request_id = getattr(request.state, "request_id", None)
        user_context = getattr(request.state, "user_context", None)

        # Create logging adapter with context
        adapter = logging.LoggerAdapter(
            logger,
            {
                "request_id": request_id,
                "user_id": user_context.get("id") if user_context else None
            }
        )

        # Store adapter in request state for use in routes
        request.state.logger = adapter

        # Process request
        return await call_next(request)


# Context accessor functions
def get_request_id() -> str | None:
    """Get current request ID from context."""
    return request_id_var.get()


def get_user_context() -> dict[str, Any] | None:
    """Get current user context."""
    return user_context_var.get()


def get_request_metadata() -> dict[str, Any] | None:
    """Get current request metadata."""
    return request_metadata_var.get()


def get_request_logger() -> logging.Logger:
    """Get logger with request context."""
    request_id = get_request_id()
    user_context = get_user_context()

    return logging.LoggerAdapter(
        logger,
        {
            "request_id": request_id,
            "user_id": user_context.get("id") if user_context else None
        }
    )


# Decorators for context injection
def with_request_context(func):
    """
    Decorator to inject request context into function.

    Example:
        @with_request_context
        async def process_data(data, request_id=None, user_context=None):
            logger.info(f"Processing for request {request_id}")
    """
    async def wrapper(*args, **kwargs):
        # Inject context if not provided
        if "request_id" not in kwargs:
            kwargs["request_id"] = get_request_id()
        if "user_context" not in kwargs:
            kwargs["user_context"] = get_user_context()

        return await func(*args, **kwargs)

    return wrapper


def log_with_context(level: str = "info"):
    """
    Decorator to log function entry/exit with context.

    Args:
        level: Log level (debug, info, warning, error)

    Example:
        @log_with_context("debug")
        async def calculate_something(value):
            return value * 2
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            logger_func = getattr(logger, level)
            request_id = get_request_id()

            # Log entry
            logger_func(
                f"Entering {func.__name__}",
                extra={
                    "request_id": request_id,
                    "function": func.__name__,
                    "args": str(args)[:100],  # Truncate for safety
                    "kwargs": str(kwargs)[:100]
                }
            )

            start_time = time.time()

            try:
                # Execute function
                result = await func(*args, **kwargs)

                # Log success
                duration = time.time() - start_time
                logger_func(
                    f"Exiting {func.__name__} successfully",
                    extra={
                        "request_id": request_id,
                        "function": func.__name__,
                        "duration": duration
                    }
                )

                return result

            except Exception as e:
                # Log failure
                duration = time.time() - start_time
                logger.error(
                    f"Failed in {func.__name__}: {str(e)}",
                    extra={
                        "request_id": request_id,
                        "function": func.__name__,
                        "duration": duration,
                        "error": str(e)
                    },
                    exc_info=True
                )
                raise

        return wrapper

    return decorator


def configure_request_context(app, service_name: str = "imi"):
    """
    Configure request context handling for the application.

    Args:
        app: FastAPI application instance
        service_name: Name of the service for identification
    """
    # Add request context middleware
    app.add_middleware(RequestContextMiddleware, service_name=service_name)

    # Add logging context middleware
    app.add_middleware(LoggingContextMiddleware)

    logger.info(f"Request context configured for service: {service_name}")
