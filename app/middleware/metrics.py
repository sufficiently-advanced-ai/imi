"""
HTTP metrics middleware for FastAPI.
Records request duration, count, and in-progress requests.
"""

import os
import re
import time
from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.metrics import setup_metrics


class HTTPMetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to record HTTP request metrics."""

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        # Instruments are created lazily on the first request (see
        # _ensure_metrics). The middleware must be registered at app
        # construction time — before startup — but the exporting MeterProvider
        # is only set up during the startup event (initialize_telemetry +
        # setup_metrics). Deferring instrument creation until the first request
        # guarantees we bind to the production meter, not a no-op provider.
        self._metrics_ready = False
        self.request_duration_histogram = None
        self.request_counter = None
        self.in_progress_gauge = None

    def _ensure_metrics(self) -> None:
        """Create the OTEL instruments once, on first use (degrade gracefully)."""
        if self._metrics_ready:
            return
        # Mark ready up-front so a failure doesn't retry on every request.
        self._metrics_ready = True
        try:
            setup_metrics()
            from app.metrics import meter  # type: ignore
        except Exception:
            meter = None

        if meter:
            self.request_duration_histogram = meter.create_histogram(
                "http_request_duration_seconds",
                unit="s",
                description="HTTP request duration in seconds",
            )

            self.request_counter = meter.create_counter(
                "http_requests_total",
                unit="requests",
                description="Total HTTP requests",
            )

            self.in_progress_gauge = meter.create_up_down_counter(
                "http_requests_in_progress",
                unit="requests",
                description="Number of HTTP requests currently being processed",
            )

    def _normalize_route(self, path: str) -> str:
        """
        Normalize route paths by converting dynamic segments to patterns.

        Examples:
        - /users/123 -> /users/{id}
        - /api/documents/456/sections/789 -> /api/documents/{id}/sections/{id}
        - /health -> /health (static routes unchanged)
        """
        # Handle static routes and health checks
        if path in ["/health", "/metrics", "/docs", "/openapi.json"]:
            return path

        # Simple numeric ID pattern replacement
        # This covers most common cases like /users/123, /documents/456/sections/789
        normalized = re.sub(r"/\d+", "/{id}", path)

        # Handle UUID patterns (common in APIs)
        normalized = re.sub(
            r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            "/{uuid}",
            normalized,
        )

        # Handle other common patterns if needed
        # For more complex route matching, FastAPI's router could be used
        # but this simple approach covers most use cases for metrics

        return normalized

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process the request and record metrics."""
        # Lazily bind instruments to the production meter on first request.
        self._ensure_metrics()

        # Extract labels
        method = request.method
        route = self._normalize_route(request.url.path)
        client_name = os.getenv("CLIENT_NAME", "unknown")

        # Track in-progress requests
        if self.in_progress_gauge:
            self.in_progress_gauge.add(1, {"client_name": client_name})

        # Record start time
        start_time = time.perf_counter()
        status_code = "500"

        try:
            response = await call_next(request)
            status_code = str(response.status_code)

        finally:
            # Calculate duration
            duration = time.perf_counter() - start_time

            # Decrement in-progress gauge (only once, in finally block)
            if self.in_progress_gauge:
                self.in_progress_gauge.add(-1, {"client_name": client_name})

            # Record metrics
            labels = {
                "method": method,
                "route": route,
                "status_code": status_code,
                "client_name": client_name,
            }

            if self.request_duration_histogram:
                self.request_duration_histogram.record(duration, labels)

            if self.request_counter:
                self.request_counter.add(1, labels)

        return response
