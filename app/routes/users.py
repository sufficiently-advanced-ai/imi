"""User API endpoints for profile management, preferences, and sessions."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.services.auth import get_auth_service
from app.services.user_service import UserService, get_user_service
from app.user_models import (
    UserActivity,
    UserPreferences,
    UserPreferencesUpdate,
    UserResponse,
    UserSession,
    UserSessionTerminate,
    UserUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])


async def get_current_user(request: Request) -> dict[str, Any]:
    """Dependency to get the current authenticated user."""
    auth_service = get_auth_service()
    user = await auth_service.get_user_from_session(request)

    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return user


# =============================================================================
# GET /api/users/me - Current User Profile
# =============================================================================


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: dict[str, Any] = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
):
    """Get the current authenticated user's profile."""
    try:
        user_id = current_user.get("id", current_user.get("email", "unknown"))
        user_profile = await user_service.get_user(user_id)

        if not user_profile:
            raise HTTPException(status_code=404, detail="User profile not found")

        return user_profile

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting current user profile: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# PUT /api/users/me - Update Current User Profile
# =============================================================================


@router.put("/me", response_model=UserResponse)
async def update_current_user_profile(
    update_data: UserUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
):
    """Update the current authenticated user's profile."""
    try:
        user_id = current_user.get("id", current_user.get("email", "unknown"))

        # Convert Pydantic model to dict, excluding None values
        update_dict = update_data.model_dump(exclude_unset=True)

        updated_user = await user_service.update_user(user_id, update_dict)

        if not updated_user:
            raise HTTPException(status_code=500, detail="Failed to update user profile")

        return updated_user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user profile: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# GET /api/users/preferences - User Preferences
# =============================================================================


@router.get("/preferences", response_model=UserPreferences)
async def get_user_preferences(
    current_user: dict[str, Any] = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
):
    """Get the current user's preferences."""
    try:
        user_id = current_user.get("id", current_user.get("email", "unknown"))
        preferences = await user_service.get_preferences(user_id)

        if not preferences:
            raise HTTPException(status_code=404, detail="User preferences not found")

        return preferences

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user preferences: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# PUT /api/users/preferences - Update User Preferences
# =============================================================================


@router.put("/preferences", response_model=UserPreferences)
async def update_user_preferences(
    update_data: UserPreferencesUpdate,
    current_user: dict[str, Any] = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
):
    """Update the current user's preferences."""
    try:
        user_id = current_user.get("id", current_user.get("email", "unknown"))

        # Convert Pydantic model to dict, excluding None values
        update_dict = update_data.model_dump(exclude_unset=True)

        updated_preferences = await user_service.update_preferences(
            user_id, update_dict
        )

        if not updated_preferences:
            raise HTTPException(
                status_code=500, detail="Failed to update user preferences"
            )

        return updated_preferences

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user preferences: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# GET /api/users/sessions - Active Sessions
# =============================================================================


@router.get("/sessions", response_model=list[UserSession])
async def get_user_sessions(
    limit: int | None = Query(
        None, ge=1, le=100, description="Limit number of sessions returned"
    ),
    offset: int | None = Query(None, ge=0, description="Number of sessions to skip"),
    current_user: dict[str, Any] = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
):
    """Get the current user's active sessions with pagination."""
    try:
        user_id = current_user.get("id", current_user.get("email", "unknown"))
        sessions = await user_service.get_user_sessions(
            user_id, limit=limit, offset=offset
        )

        # Remove sensitive token information from response
        safe_sessions = []
        for session in sessions:
            session_dict = session.model_dump()
            # Remove token field if it exists
            session_dict.pop("token", None)
            safe_sessions.append(UserSession(**session_dict))

        return safe_sessions

    except Exception as e:
        logger.error(f"Error getting user sessions: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# DELETE /api/users/sessions/{id} - Terminate Session
# =============================================================================


@router.delete("/sessions/{session_id}", response_model=UserSessionTerminate)
async def terminate_user_session(
    session_id: str = Path(..., description="Session ID to terminate"),
    current_user: dict[str, Any] = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
):
    """Terminate a specific user session."""
    try:
        user_id = current_user.get("id", current_user.get("email", "unknown"))

        success = await user_service.terminate_session(user_id, session_id)

        if not success:
            raise HTTPException(status_code=404, detail="Session not found")

        return UserSessionTerminate()

    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(
            status_code=403, detail="Cannot delete other user's session"
        )
    except Exception as e:
        logger.error(f"Error terminating session {session_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# GET /api/users/activity - User Activity Summary
# =============================================================================


@router.get("/activity", response_model=UserActivity)
async def get_user_activity(
    current_user: dict[str, Any] = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
):
    """Get the current user's activity summary."""
    try:
        user_id = current_user.get("id", current_user.get("email", "unknown"))
        activity = await user_service.get_user_activity(user_id)

        if not activity:
            raise HTTPException(status_code=404, detail="User activity not found")

        return activity

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user activity: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
