"""
Secure user service layer with database backing and token hashing.

This module provides async database-backed user management with secure token storage
using PBKDF2-HMAC-SHA256 for token hashing and constant-time verification.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import selectinload

from app.database import get_database_session
from app.user_models import UserActivity, UserPreferences, UserResponse
from app.user_models import UserSession as PydanticUserSession
from app.user_models.db_models import User, UserPreference, UserSession

logger = logging.getLogger(__name__)


def _user_lookup_clause(user_id: str):
    """Match a user by external_id, or by internal id for numeric input.

    ``or_()`` only accepts SQL expressions — a bare ``0`` fallback for
    non-numeric IDs raises ArgumentError at statement-build time.
    """
    if user_id.isdigit():
        return or_(User.external_id == user_id, User.id == int(user_id))
    return User.external_id == user_id


class SecureUserService:
    """Secure user service with database backing and token hashing."""

    def __init__(self, hash_iterations: int = 100_000) -> None:
        """Initialize the secure user service."""
        # Use PBKDF2 with SHA-256 for secure token hashing
        self.hash_iterations: int = hash_iterations  # OWASP recommended minimum

    def _generate_token_salt(self) -> str:
        """Generate a cryptographically secure random salt for token hashing."""
        return secrets.token_hex(16)  # 32 character hex string

    def _hash_token(self, token: str, salt: str) -> str:
        """
        Hash a token using PBKDF2 with SHA-256.

        Args:
            token: The plaintext token to hash
            salt: The salt to use for hashing

        Returns:
            The hashed token as a hex string
        """
        token_bytes = token.encode("utf-8")
        # `salt` is stored as hex; use raw bytes for PBKDF2
        salt_bytes = bytes.fromhex(salt)
        hash_bytes = hashlib.pbkdf2_hmac(
            "sha256", token_bytes, salt_bytes, self.hash_iterations
        )
        return hash_bytes.hex()

    def _verify_token(self, token: str, token_hash: str, salt: str) -> bool:
        """
        Verify a token against its hash using constant-time comparison.

        Args:
            token: The plaintext token to verify
            token_hash: The stored hash to compare against
            salt: The salt used for hashing

        Returns:
            True if the token matches, False otherwise
        """
        if not token or not token_hash or not salt:
            return False

        # Compute hash of provided token
        computed_hash = self._hash_token(token, salt)

        # Use constant-time comparison to prevent timing attacks
        return hmac.compare_digest(computed_hash, token_hash)

    async def get_user(self, user_id: str) -> UserResponse | None:
        """Get user by ID (external_id or internal id)."""
        try:
            async for session in get_database_session():
                # Try to find by external_id first, then by internal id
                stmt = select(User).where(
                    _user_lookup_clause(user_id)
                )
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()

                if not user:
                    # Create a basic user record for users not yet in system
                    user = User(
                        external_id=user_id,
                        email=f"user-{user_id}@example.com",
                        first_name="User",
                        last_name=user_id,
                        is_active=True,
                        last_login=datetime.now(UTC),
                    )
                    session.add(user)
                    await session.commit()
                    await session.refresh(user)
                    logger.info(f"Created new user record for {user_id}")

                # Convert to Pydantic model
                return UserResponse(
                    id=user.external_id or str(user.id),
                    email=user.email,
                    name=user.full_name,
                    created_at=user.created_at,
                    last_login=user.last_login,
                    is_active=user.is_active,
                )

        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            return None

    async def update_user(
        self, user_id: str, update_data: dict[str, Any]
    ) -> UserResponse | None:
        """Update user data."""
        try:
            async for session in get_database_session():
                # Find user
                stmt = select(User).where(
                    _user_lookup_clause(user_id)
                )
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()

                if not user:
                    # Create user if doesn't exist
                    user = await self.get_user(user_id)
                    if not user:
                        return None
                    # Re-fetch from DB
                    result = await session.execute(stmt)
                    user = result.scalar_one_or_none()

                # Update fields
                for field, value in update_data.items():
                    if value is not None and hasattr(user, field):
                        setattr(user, field, value)

                user.updated_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(user)
                logger.info(f"Updated user {user_id}")

                # Convert to Pydantic model
                return UserResponse(
                    id=user.external_id or str(user.id),
                    email=user.email,
                    name=user.full_name,
                    created_at=user.created_at,
                    last_login=user.last_login,
                    is_active=user.is_active,
                )

        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}")
            return None

    async def get_preferences(self, user_id: str) -> UserPreferences | None:
        """Get user preferences."""
        try:
            async for session in get_database_session():
                # Find user
                stmt = select(User).where(
                    _user_lookup_clause(user_id)
                ).options(selectinload(User.preferences))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()

                if not user:
                    return None

                # Get or create preferences
                if not user.preferences:
                    prefs = UserPreference(
                        user_id=user.id,
                        theme="light",
                        display_settings={"compact_view": False, "show_avatars": True},
                        notifications={"email": True, "push": False},
                    )
                    session.add(prefs)
                    await session.commit()
                    await session.refresh(prefs)
                    logger.info(f"Created default preferences for user {user_id}")
                else:
                    prefs = user.preferences

                # Convert to Pydantic model
                return UserPreferences(
                    user_id=user.external_id or str(user.id),
                    theme=prefs.theme,
                    display_settings=prefs.display_settings,
                    notifications=prefs.notifications,
                )

        except Exception as e:
            logger.error(f"Error getting preferences for user {user_id}: {e}")
            return None

    async def update_preferences(
        self, user_id: str, update_data: dict[str, Any]
    ) -> UserPreferences | None:
        """Update user preferences."""
        try:
            async for session in get_database_session():
                # Find user and preferences
                stmt = select(User).where(
                    _user_lookup_clause(user_id)
                ).options(selectinload(User.preferences))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()

                if not user:
                    return None

                # Get or create preferences in-situ to avoid nested session
                if not user.preferences:
                    prefs = UserPreference(
                        user_id=user.id,
                        theme="light",
                        display_settings={"compact_view": False, "show_avatars": True},
                        notifications={"email": True, "push": False},
                    )
                    session.add(prefs)
                    user.preferences = prefs
                else:
                    prefs = user.preferences

                # Update only allowed preference fields
                allowed_fields = {"theme", "display_settings", "notifications"}
                for field, value in update_data.items():
                    if field in allowed_fields and value is not None:
                        setattr(prefs, field, value)
                prefs.updated_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(prefs)
                logger.info(f"Updated preferences for user {user_id}")

                # Convert to Pydantic model
                return UserPreferences(
                    user_id=user.external_id or str(user.id),
                    theme=prefs.theme,
                    display_settings=prefs.display_settings,
                    notifications=prefs.notifications,
                )

        except Exception as e:
            logger.error(f"Error updating preferences for user {user_id}: {e}")
            return None

    async def create_session(
        self,
        user_id: str,
        token: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> UserSession | None:
        """
        Create a new user session with secure token storage.

        Args:
            user_id: User ID (external_id or internal id)
            token: The session token to store securely
            ip_address: Client IP address
            user_agent: Client user agent

        Returns:
            Created UserSession or None on error
        """
        try:
            async for session in get_database_session():
                # Find user
                stmt = select(User).where(
                    _user_lookup_clause(user_id)
                )
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()

                if not user:
                    logger.error(f"User {user_id} not found")
                    return None

                # Generate salt and hash token
                salt = self._generate_token_salt()
                token_hash = self._hash_token(token, salt)

                # Create session with hashed token
                session_obj = UserSession(
                    user_id=user.id,
                    token=None,  # Never store plaintext
                    token_hash=token_hash,
                    token_salt=salt,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    expires_at=datetime.now(UTC) + timedelta(hours=24),
                )

                session.add(session_obj)
                await session.commit()
                await session.refresh(session_obj)

                logger.info(f"Created secure session for user {user_id} (session_id: {session_obj.id})")
                return session_obj

        except Exception as e:
            logger.error(f"Error creating session for user {user_id}: {e}")
            return None

    async def validate_session(
        self, token: str, user_id: str | None = None
    ) -> UserSession | None:
        """
        Validate and return active session by token using secure verification.

        Args:
            token: Session token to validate
            user_id: Optional user ID to narrow the search

        Returns:
            UserSession or None if invalid/expired
        """
        try:
            async for session in get_database_session():
                current_time = datetime.now(UTC)

                # Build query with database-level filtering
                stmt = select(UserSession).where(
                    and_(
                        UserSession.token_hash.isnot(None),
                        UserSession.token_salt.isnot(None),
                        UserSession.expires_at > current_time,
                    )
                )

                # Narrow by user if provided
                if user_id:
                    user_stmt = select(User).where(
                        _user_lookup_clause(user_id)
                    )
                    user_result = await session.execute(user_stmt)
                    user = user_result.scalar_one_or_none()
                    if user:
                        stmt = stmt.where(UserSession.user_id == user.id)

                result = await session.execute(stmt)
                active_sessions = result.scalars().all()

                for session_obj in active_sessions:
                    # Use constant-time comparison
                    if self._verify_token(token, session_obj.token_hash, session_obj.token_salt):
                        # Update last accessed
                        session_obj.last_accessed = datetime.now(UTC)
                        await session.commit()
                        await session.refresh(session_obj)
                        return session_obj

                return None

        except Exception as e:
            logger.error(f"Error validating session: {e}")
            return None

    async def get_user_sessions(
        self, user_id: str, limit: int | None = None, offset: int | None = None
    ) -> list[PydanticUserSession]:
        """Get user sessions with pagination."""
        try:
            async for session in get_database_session():
                # Find user
                stmt = select(User).where(
                    _user_lookup_clause(user_id)
                )
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()

                if not user:
                    return []

                # Get sessions
                stmt = select(UserSession).where(UserSession.user_id == user.id)

                if offset is not None:
                    stmt = stmt.offset(offset)
                if limit is not None:
                    stmt = stmt.limit(limit)

                result = await session.execute(stmt)
                sessions = result.scalars().all()

                # Convert to Pydantic models
                return [
                    PydanticUserSession(
                        id=str(s.id),
                        user_id=user.external_id or str(user.id),
                        created_at=s.created_at,
                        expires_at=s.expires_at,
                        ip_address=s.ip_address,
                        is_active=not s.is_expired(),
                    )
                    for s in sessions
                ]

        except Exception as e:
            logger.error(f"Error getting sessions for user {user_id}: {e}")
            return []

    async def terminate_session(self, user_id: str, session_id: str) -> bool:
        """Terminate a user session."""
        if not session_id.isdigit():
            return False
        try:
            async for session in get_database_session():
                # Find user
                user_stmt = select(User).where(
                    _user_lookup_clause(user_id)
                )
                user_result = await session.execute(user_stmt)
                user = user_result.scalar_one_or_none()

                if not user:
                    return False

                # Find and delete session
                stmt = select(UserSession).where(
                    and_(
                        UserSession.id == int(session_id),
                        UserSession.user_id == user.id,
                    )
                )
                result = await session.execute(stmt)
                session_obj = result.scalar_one_or_none()

                if session_obj:
                    await session.delete(session_obj)
                    await session.commit()
                    logger.info(f"Terminated session {session_id} for user {user_id}")
                    return True

                return False

        except Exception as e:
            logger.error(f"Error terminating session {session_id} for user {user_id}: {e}")
            return False

    async def get_user_activity(self, user_id: str) -> UserActivity | None:
        """Get user activity summary."""
        try:
            async for session in get_database_session():
                # Find user with sessions
                stmt = select(User).where(
                    _user_lookup_clause(user_id)
                ).options(selectinload(User.sessions))
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()

                if not user:
                    return None

                # Calculate activity metrics
                login_count = len(user.sessions)
                session_durations = []

                for s in user.sessions:
                    if s.last_accessed and s.created_at:
                        duration = (s.last_accessed - s.created_at).total_seconds()
                        session_durations.append(duration)

                avg_duration = int(sum(session_durations) / len(session_durations)) if session_durations else 3600

                # Create activity summary
                return UserActivity(
                    user_id=user.external_id or str(user.id),
                    login_count=login_count,
                    last_login=user.last_login,
                    session_duration_avg=avg_duration,
                    features_used=["meetings", "chat", "search"],
                    activity_timeline=[
                        {
                            "action": "login",
                            "timestamp": datetime.now(UTC),
                            "ip": "127.0.0.1",
                        }
                    ],
                )

        except Exception as e:
            logger.error(f"Error getting activity for user {user_id}: {e}")
            return None

    async def migrate_existing_sessions(self) -> int:
        """
        Migrate existing sessions with plaintext tokens to secure storage.

        Returns:
            Number of sessions migrated
        """
        migrated_count = 0
        try:
            async for session in get_database_session():
                # Find sessions with plaintext tokens
                stmt = select(UserSession).where(
                    and_(
                        UserSession.token.isnot(None),
                        UserSession.token_hash.is_(None),
                    )
                )
                result = await session.execute(stmt)
                legacy_sessions = result.scalars().all()

                for session_obj in legacy_sessions:
                    # Generate salt and hash
                    salt = self._generate_token_salt()
                    token_hash = self._hash_token(session_obj.token, salt)

                    # Update with secure storage
                    session_obj.token_salt = salt
                    session_obj.token_hash = token_hash
                    session_obj.token = None  # Clear plaintext

                    migrated_count += 1

                if migrated_count > 0:
                    await session.commit()
                    logger.info(f"Migrated {migrated_count} sessions to secure storage")

        except Exception as e:
            logger.error(f"Error migrating sessions: {e}")

        return migrated_count

    async def cleanup_expired_sessions(self) -> int:
        """
        Remove expired sessions from database.

        Returns:
            Number of sessions cleaned up
        """
        try:
            async for session in get_database_session():
                current_time = datetime.now(UTC)

                # Delete expired sessions
                stmt = delete(UserSession).where(UserSession.expires_at < current_time)
                result = await session.execute(stmt)
                count = result.rowcount

                if count > 0:
                    await session.commit()
                    logger.info(f"Cleaned up {count} expired sessions")

                return count

        except Exception as e:
            logger.error(f"Error cleaning up expired sessions: {e}")
            return 0


# Global service instance for dependency injection
_secure_user_service: SecureUserService | None = None


def get_secure_user_service() -> SecureUserService:
    """Get or create the secure user service instance."""
    global _secure_user_service
    if _secure_user_service is None:
        _secure_user_service = SecureUserService()
    return _secure_user_service
