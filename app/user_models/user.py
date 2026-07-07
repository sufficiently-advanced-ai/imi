"""User data models for the imi system."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, field_validator


def _default_display_settings() -> dict[str, Any]:
    return {"compact_view": False, "show_avatars": True}


def _default_notifications() -> dict[str, bool]:
    return {"email": True, "push": False}


class ThemeChoice(str, Enum):
    """Available theme options."""

    light = "light"
    dark = "dark"
    auto = "auto"


class UserBase(BaseModel):
    """Base user model with common fields."""

    email: str = Field(..., description="User email address")
    name: str = Field(..., min_length=1, max_length=100)
    is_active: bool = True


class UserCreate(UserBase):
    """User creation model."""

    pass


class UserUpdate(BaseModel):
    """User update model with optional fields."""

    name: str | None = Field(None, min_length=1, max_length=100)
    display_name: str | None = Field(None, max_length=50)
    is_active: bool | None = None


class UserResponse(UserBase):
    """User response model."""

    id: str
    created_at: datetime
    last_login: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("created_at", "last_login", mode="before")
    @classmethod
    def _ensure_aware(cls, v):
        """Ensure datetimes are timezone-aware (UTC)."""
        if v is None:
            return v
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


class UserPreferences(BaseModel):
    """User preferences model."""

    user_id: str
    theme: ThemeChoice = ThemeChoice.light
    display_settings: dict[str, Any] = Field(default_factory=_default_display_settings)
    notifications: dict[str, bool] = Field(default_factory=_default_notifications)


class UserPreferencesUpdate(BaseModel):
    """User preferences update model."""

    theme: ThemeChoice | None = None
    display_settings: dict[str, Any] | None = None
    notifications: dict[str, bool] | None = None


class UserSession(BaseModel):
    """User session model."""

    id: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    ip_address: IPvAnyAddress | None = None
    is_active: bool = True

    model_config = ConfigDict(from_attributes=True)

    @field_validator("created_at", "expires_at", mode="before")
    @classmethod
    def _ensure_aware(cls, v):
        """Ensure datetimes are timezone-aware (UTC)."""
        if v is None:
            return v
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


class ActivityEvent(BaseModel):
    """Activity event model for timeline entries."""

    action: str
    timestamp: datetime
    ip: IPvAnyAddress | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ensure_aware(cls, v):
        """Ensure datetimes are timezone-aware (UTC)."""
        if v is None:
            return v
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


MAX_ACTIVITY_EVENTS = 1000


class UserActivity(BaseModel):
    """User activity summary model."""

    user_id: str
    login_count: int
    last_login: datetime | None = None
    session_duration_avg: int  # in seconds
    features_used: list[str] = Field(default_factory=list)
    activity_timeline: list[ActivityEvent] = Field(default_factory=list)

    @field_validator("activity_timeline")
    @classmethod
    def _cap_timeline(cls, v: list[ActivityEvent]) -> list[ActivityEvent]:
        """Cap activity timeline to prevent unbounded growth."""
        if len(v) > MAX_ACTIVITY_EVENTS:
            return v[-MAX_ACTIVITY_EVENTS:]
        return v

    @field_validator("last_login", mode="before")
    @classmethod
    def _ensure_aware(cls, v):
        """Ensure datetimes are timezone-aware (UTC)."""
        if v is None:
            return v
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


class UserSessionTerminate(BaseModel):
    """Response for session termination."""

    message: str = "Session terminated successfully"
