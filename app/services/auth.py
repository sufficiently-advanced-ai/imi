"""Authentication service for the community edition.

Two modes, selected by AUTH_MODE:
  - "none" (default): every request is treated as the demo user; no cookie
    required. Suitable for local single-user deployments.
  - "demo": a session cookie is required; /auth/login sets it. Provides a
    minimal login/logout flow without any external identity provider.
"""

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "session"


@dataclass
class AuthResult:
    """Result of session authentication.

    Attributes:
        user: User profile dict if authenticated, None otherwise.
        sealed_session: Present for API compatibility; always None in the
                        community edition (no external session refresh).
    """

    user: dict[str, Any] | None
    sealed_session: str | None = None


class AuthService:
    """Session authentication for the community edition (demo/none modes)."""

    def __init__(self):
        self.mode = settings.AUTH_MODE
        logger.info("AUTH_MODE=%s — using the %s auth provider", self.mode, self.mode)

    @staticmethod
    def _demo_user() -> dict:
        """Return the canonical demo/anonymous user dict."""
        return {
            "id": "demo-user-001",
            "email": "demo@example.com",
            "first_name": "Demo",
            "last_name": "User",
        }

    async def get_user_from_session(self, request: Request) -> AuthResult:
        """Extract and validate the user from the session cookie.

        Args:
            request: FastAPI request object

        Returns:
            AuthResult with the user profile (or None if unauthenticated).
        """
        # none mode — return demo user unconditionally (no cookie required)
        if self.mode == "none":
            return AuthResult(user=self._demo_user())

        # demo mode — require the session cookie set by /auth/login
        session = request.cookies.get(SESSION_COOKIE)
        if session == "demo_user":
            return AuthResult(user=self._demo_user())
        return AuthResult(user=None)

    async def logout_user(self, session_token: str | None = None) -> bool:
        """Log out the user. Always succeeds in the community edition."""
        return True

    def get_logout_url(self, session_token: str | None = None) -> str:
        """Return the post-logout redirect target."""
        return "/"

    def create_session_cookie(
        self, user_data: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        """Create secure session cookie configuration.

        Args:
            user_data: User profile data
            request: FastAPI request object

        Returns:
            Cookie configuration dict
        """
        is_secure = request.url.scheme == "https"

        return {
            "httponly": True,
            "samesite": "lax",
            "secure": is_secure,
            "path": "/",
            "max_age": 86400,  # 24 hours
        }


# Singleton instance with lazy initialization
_auth_service: AuthService | None = None


def get_auth_service() -> AuthService:
    """Get or create the singleton auth service instance.

    Uses lazy initialization to avoid circular imports.
    """
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service


# Dependency functions for FastAPI
async def get_current_user(request: Request) -> dict[str, Any]:
    """Extract and validate user from the session.

    This is a FastAPI dependency that can be used to protect routes.
    Raises HTTPException(401) if not authenticated.

    Usage:
        @router.get("/protected")
        async def protected_route(user: dict = Depends(get_current_user)):
            return {"message": f"Hello {user['email']}"}
    """
    try:
        auth = get_auth_service()
        auth_result = await auth.get_user_from_session(request)
        if not auth_result.user:
            raise HTTPException(
                status_code=401,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return auth_result.user
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth validation error: {e}")
        raise HTTPException(
            status_code=401,
            detail="Invalid session",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_optional_user(request: Request) -> dict | None:
    """Extract user from the session if available, otherwise return None.

    This dependency allows optional authentication - useful for endpoints
    that have different behavior for authenticated vs anonymous users.

    Usage:
        @router.get("/public")
        async def public_route(user: dict | None = Depends(get_optional_user)):
            if user:
                return {"message": f"Hello {user['email']}"}
            return {"message": "Hello anonymous"}
    """
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


# List of public endpoints that don't require authentication
PUBLIC_ENDPOINTS = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",  # This checks auth internally
    "/auth/status",  # Auth routes without /api prefix
    "/auth/login",
    "/auth/logout",
    "/auth/me",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/_next",  # Next.js assets
    "/static",  # Static files
    "/favicon.ico",
    # Webhook endpoints (use webhook secret validation instead of user auth)
    "/api/webhook/github",
}
