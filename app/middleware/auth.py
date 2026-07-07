"""Authentication middleware for protecting routes."""

import logging
import os
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.services.auth import PUBLIC_ENDPOINTS, get_auth_service

logger = logging.getLogger(__name__)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce authentication on protected routes."""

    async def dispatch(self, request: Request, call_next: Callable) -> JSONResponse:
        """Check authentication for protected routes.

        Args:
            request: The incoming request
            call_next: The next middleware or route handler

        Returns:
            Response from the route or 401 if unauthorized
        """
        # Check if path is public
        path = request.url.path

        # Check exact matches and prefixes
        is_public = any(
            path == endpoint
            or (endpoint.endswith("/") and path.startswith(endpoint))
            or path.startswith(endpoint + "/")
            for endpoint in PUBLIC_ENDPOINTS
        )

        # If public endpoint, proceed without auth check
        if is_public:
            response = await call_next(request)
            return response

        # For protected endpoints, verify authentication
        try:
            auth_service = get_auth_service()

            # Debug: Log request details only when auth debug is enabled
            if os.getenv("AUTH_DEBUG", "0") == "1":
                logger.warning(f"AUTH DEBUG - Path: {path}")
                logger.warning(f"AUTH DEBUG - Cookies: {list(request.cookies.keys())}")
                logger.warning(f"AUTH DEBUG - User-Agent: {request.headers.get('user-agent', 'unknown')}")

            auth_result = await auth_service.get_user_from_session(request)

            if not auth_result.user:
                if os.getenv("AUTH_DEBUG", "0") == "1":
                    logger.warning(f"AUTH DEBUG - No user found for path {path}")
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Not authenticated"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if os.getenv("AUTH_DEBUG", "0") == "1":
                logger.warning(f"AUTH DEBUG - User authenticated: {auth_result.user.get('email', 'unknown')}")

            # Add user to request state for downstream use
            request.state.user = auth_result.user

        except Exception as e:
            logger.error(f"Authentication middleware error: {e}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication error"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Proceed with authenticated request
        return await call_next(request)
