"""
Core Middleware

Shared middleware components for cross-cutting concerns.
"""

import logging
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for request/response logging."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        # Log request
        logger.info(f"Request: {request.method} {request.url}")

        # Process request
        response = await call_next(request)

        # Log response
        process_time = time.time() - start_time
        logger.info(f"Response: {response.status_code} ({process_time:.3f}s)")

        return response


class CORSMiddleware:
    """CORS configuration for frontend integration."""

    @staticmethod
    def setup_cors(app):
        """Set up CORS middleware on the app."""
        from fastapi.middleware.cors import CORSMiddleware as FastAPICORSMiddleware

        app.add_middleware(
            FastAPICORSMiddleware,
            allow_origins=["*"],  # Configure appropriately for production
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
