"""User service layer for managing users, preferences, and sessions."""

import logging
from typing import Any

from app.services.secure_user_service import get_secure_user_service
from app.user_models import UserActivity, UserPreferences, UserResponse, UserSession

logger = logging.getLogger(__name__)
logger.debug("UserService module loaded")


class UserService:
    """Service for user management operations."""

    def __init__(self):
        """Initialize the user service."""
        # Use the secure database-backed service
        self._secure_service = get_secure_user_service()

    async def get_user(self, user_id: str) -> UserResponse | None:
        """Get user by ID."""
        return await self._secure_service.get_user(user_id)

    async def update_user(
        self, user_id: str, update_data: dict[str, Any]
    ) -> UserResponse | None:
        """Update user data."""
        return await self._secure_service.update_user(user_id, update_data)

    async def get_preferences(self, user_id: str) -> UserPreferences | None:
        """Get user preferences."""
        return await self._secure_service.get_preferences(user_id)

    async def update_preferences(
        self, user_id: str, update_data: dict[str, Any]
    ) -> UserPreferences | None:
        """Update user preferences."""
        return await self._secure_service.update_preferences(user_id, update_data)

    async def get_user_sessions(
        self, user_id: str, limit: int | None = None, offset: int | None = None
    ) -> list[UserSession]:
        """Get user sessions with pagination."""
        return await self._secure_service.get_user_sessions(user_id, limit, offset)

    async def terminate_session(self, user_id: str, session_id: str) -> bool:
        """Terminate a user session."""
        return await self._secure_service.terminate_session(user_id, session_id)

    async def get_user_activity(self, user_id: str) -> UserActivity | None:
        """Get user activity summary."""
        return await self._secure_service.get_user_activity(user_id)


# Global service instance for dependency injection
_user_service: UserService | None = None


def get_user_service() -> UserService:
    """Get or create the user service instance."""
    global _user_service
    if _user_service is None:
        _user_service = UserService()
    return _user_service
