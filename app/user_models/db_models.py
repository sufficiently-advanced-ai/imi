"""
SQLAlchemy database models for user management.

These models represent the database schema for users, preferences, and sessions.
They are separate from the Pydantic models which handle API validation.
"""

import datetime as dt
from datetime import timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func

from app.database import Base


def _current_tenant_id_default() -> str:
    """Python-side default for tenant_id: the request's current tenant.

    In single-tenant mode this is ``"default"`` (the ContextVar default), so
    SQLite deployments are unaffected. In hosted Postgres it is the active
    tenant, so a new row's ``tenant_id`` matches the RLS ``WITH CHECK`` GUC — a
    hardcoded ``"default"`` here would make every non-default-tenant INSERT fail
    RLS.
    """
    from app.core.middleware.request_context import current_tenant_id

    return current_tenant_id.get()


class TenantScopedMixin:
    """Adds a ``tenant_id`` column to tenant-scoped tables (Phase 4.2).

    The Python-side default resolves the current tenant from context (defaults to
    ``"default"`` outside a request). ``server_default="default"`` backfills
    existing rows and the SQLite single-tenant path. On hosted Postgres this
    column is the partition key the RLS policies key off (see
    ``app/core/tenancy/backends/postgres.py`` and migration 003). Indexed for
    tenant-filtered queries.
    """

    tenant_id = Column(
        String(255),
        nullable=False,
        server_default="default",
        default=_current_tenant_id_default,
        index=True,
    )


class User(TenantScopedMixin, Base):
    """
    User model representing authenticated users.

    Stores user identity information along with local
    application-specific data.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(String(255), unique=True, nullable=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)

    # Status and timestamps
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_login = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    preferences = relationship(
        "UserPreference",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    sessions = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )

    def __init__(self, **kwargs: Any) -> None:
        # Validate required fields
        required_fields = ["email", "first_name", "last_name"]
        for field in required_fields:
            if field not in kwargs or not kwargs[field]:
                raise ValueError(f"Missing required field: {field}")

        # Set default values for fields if not provided
        if "is_active" not in kwargs:
            kwargs["is_active"] = True
        if "created_at" not in kwargs:
            kwargs["created_at"] = dt.datetime.now(dt.UTC)
        if "updated_at" not in kwargs:
            kwargs["updated_at"] = dt.datetime.now(dt.UTC)

        super().__init__(**kwargs)

    @validates("email")
    def validate_email(self, key: str, email: str) -> str:
        """Validate email format"""
        if not email or "@" not in email:
            raise ValueError("Invalid email format")
        return email.lower()

    @property
    def full_name(self) -> str:
        """Get user's full name"""
        return f"{self.first_name} {self.last_name}".strip()

    def __repr__(self) -> str:
        return (
            f"<User(id={self.id}, email='{self.email}')>"
        )


class UserPreference(Base):
    """
    User preferences model for storing UI and application settings.

    This model stores user-specific preferences like theme, display settings,
    and notification preferences.
    """

    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)

    # Theme and display preferences
    theme = Column(String(50), default="light", nullable=False)
    display_settings = Column(JSON, nullable=False)
    notifications = Column(JSON, nullable=False)

    # Timestamps
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationship
    user = relationship("User", back_populates="preferences")

    def __init__(self, **kwargs: Any) -> None:
        # Set default values for JSON fields if not provided
        if "display_settings" not in kwargs:
            kwargs["display_settings"] = {
                "sidebar_collapsed": False,
                "items_per_page": 20,
                "compact_view": False,
                "show_avatars": True,
            }

        if "notifications" not in kwargs:
            kwargs["notifications"] = {
                "email_digest": True,
                "meeting_alerts": True,
                "entity_updates": False,
                "email": True,
                "push": False,
            }

        super().__init__(**kwargs)

    def update_preferences(self, preferences_data: dict[str, Any]) -> None:
        """Update user preferences with new data"""
        for key, value in preferences_data.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def __repr__(self) -> str:
        return f"<UserPreference(id={self.id}, user_id={self.user_id}, theme='{self.theme}')>"


class UserSession(TenantScopedMixin, Base):
    """
    User session model for tracking active user sessions with secure token storage.

    This model stores session tokens and metadata for authenticated users,
    implementing secure token hashing with PBKDF2-HMAC-SHA256.
    """

    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Secure token storage
    # token field is deprecated - kept for migration compatibility, will be removed
    token = Column(
        String(255), unique=True, nullable=True, index=True
    )  # Deprecated - for migration only
    token_hash = Column(
        String(255), nullable=True, index=True
    )  # PBKDF2-HMAC-SHA256 hash of token
    token_salt = Column(
        String(32), nullable=True
    )  # Per-token salt for additional security

    # Session metadata
    ip_address = Column(String(45), nullable=True)  # IPv6 support
    user_agent = Column(Text, nullable=True)

    # Session timing
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_accessed = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationship
    user = relationship("User", back_populates="sessions")

    def __init__(self, **kwargs: Any) -> None:
        # Set default expiration if not provided (24 hours)
        if "expires_at" not in kwargs:
            kwargs["expires_at"] = dt.datetime.now(dt.UTC) + timedelta(hours=24)

        super().__init__(**kwargs)

    def is_expired(self) -> bool:
        """Check if session is expired"""
        return dt.datetime.now(dt.UTC) > self.expires_at

    def extend_session(self, hours: int = 24) -> None:
        """Extend session expiration time"""
        self.expires_at = dt.datetime.now(dt.UTC) + timedelta(hours=hours)

    def __repr__(self) -> str:
        # Note: Token is not included in repr for security reasons
        return f"<UserSession(id={self.id}, user_id={self.user_id}, expires_at='{self.expires_at}')>"












