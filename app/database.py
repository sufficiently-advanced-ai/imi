"""
Database configuration and connection management for SQLite with SQLAlchemy.

This module provides async database connection management, session handling,
and initialization for the monocontainer deployment.
"""

import logging
import os
from collections.abc import AsyncGenerator

from pydantic import BaseModel, Field, validator
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import StaticPool

from app.config import get_settings

# Configure logger for database operations
logger = logging.getLogger(__name__)


class DatabaseConfig(BaseModel):
    """Database configuration settings."""

    database_url: str = Field(..., description="SQLAlchemy database URL")
    database_path: str = Field(..., description="Path to SQLite database file")
    connection_pool_size: int = Field(default=5, ge=1, le=20, description="Connection pool size")
    max_overflow: int = Field(default=10, ge=0, le=50, description="Maximum pool overflow")
    pool_timeout: float = Field(default=30.0, ge=1.0, le=300.0, description="Pool timeout in seconds")
    pool_recycle: int = Field(default=3600, ge=600, le=86400, description="Pool recycle time in seconds")

    @validator('database_url')
    def validate_database_url(cls, v):
        """Validate database URL format."""
        if not v or not isinstance(v, str):
            raise ValueError("Database URL must be a non-empty string")
        if not (v.startswith('sqlite') or v.startswith('sqlite+aiosqlite')):
            raise ValueError("Database URL must be for SQLite or async SQLite")
        return v

    @validator('database_path')
    def validate_database_path(cls, v):
        """Validate database path."""
        if not v or not isinstance(v, str):
            raise ValueError("Database path must be a non-empty string")
        if not v.endswith('.db'):
            raise ValueError("Database path must end with .db extension")
        return v


# Global database components
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# SQLAlchemy declarative base
Base = declarative_base()


def get_database_config() -> DatabaseConfig:
    """Get database configuration from app settings."""
    try:
        settings = get_settings()
        logger.debug("Loading database configuration from settings")

        # Use settings for database configuration
        db_path = settings.DATABASE_PATH

        # Ensure database directory exists
        db_dir = os.path.dirname(db_path)
        try:
            os.makedirs(db_dir, exist_ok=True)
            logger.debug(f"Database directory ensured: {db_dir}")
        except OSError as e:
            logger.error(f"Failed to create database directory {db_dir}: {e}")
            raise RuntimeError(f"Cannot create database directory: {e}")

        # Use provided URL or construct one
        database_url = settings.DATABASE_URL or f"sqlite+aiosqlite:///{db_path}"

        # Create and validate configuration
        config = DatabaseConfig(
            database_url=database_url,
            database_path=db_path,
            connection_pool_size=settings.DATABASE_POOL_SIZE,
            max_overflow=settings.DATABASE_MAX_OVERFLOW,
            pool_timeout=settings.DATABASE_POOL_TIMEOUT,
            pool_recycle=settings.DATABASE_POOL_RECYCLE,
        )

        logger.info(f"Database configuration loaded: {db_path}")
        return config

    except Exception as e:
        logger.error(f"Failed to load database configuration: {e}")
        raise RuntimeError(f"Database configuration error: {e}")


def get_database_url(db_path: str) -> str:
    """Generate SQLite database URL for given path."""
    return f"sqlite+aiosqlite:///{db_path}"


def get_database_engine(config: DatabaseConfig) -> AsyncEngine:
    """Create and configure SQLAlchemy async engine."""
    global _engine

    if _engine is not None:
        logger.debug("Returning existing database engine")
        return _engine

    try:
        logger.info(f"Creating database engine for: {config.database_path}")

        # SQLite-specific engine configuration optimized for single file database
        _engine = create_async_engine(
            config.database_url,
            # SQLite with aiosqlite configuration
            poolclass=StaticPool,  # Optimal for SQLite - reuses single connection
            pool_pre_ping=True,    # Verify connections before use
            pool_recycle=config.pool_recycle,
            # Note: pool_timeout is not valid for SQLite with StaticPool
            # SQLite settings
            connect_args={
                "check_same_thread": False,  # Allow SQLite to be used across threads
                "timeout": 20.0,             # SQLite busy timeout
            },
            echo=False,  # Set to True for SQL logging in development
        )

        # Enforce SQLite foreign keys on every connection
        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.close()

        logger.info("Database engine created successfully")
        return _engine

    except Exception as e:
        logger.error(f"Failed to create database engine: {e}")
        raise RuntimeError(f"Database engine creation failed: {e}")


def create_database_session(config: DatabaseConfig) -> async_sessionmaker[AsyncSession]:
    """Create database session factory."""
    global _session_factory

    if _session_factory is not None:
        logger.debug("Returning existing session factory")
        return _session_factory

    try:
        logger.debug("Creating database session factory")
        engine = get_database_engine(config)

        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,  # Keep objects usable after commit
            autoflush=True,         # Auto-flush before queries
            autocommit=False,       # Manual transaction control
        )

        logger.debug("Database session factory created successfully")
        return _session_factory

    except Exception as e:
        logger.error(f"Failed to create database session factory: {e}")
        raise RuntimeError(f"Database session factory creation failed: {e}")


async def get_database_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions (tenant-scoped, Phase 4.1).

    The session factory is obtained from the current tenant's container. In
    single-tenant mode the ``RelationalBackend`` returns this module's
    ``_session_factory`` global, so behavior is unchanged.
    """
    from app.core.tenancy.context import current_tenant

    session_factory = current_tenant().session_factory()
    if session_factory is None:
        # This should not happen in normal operation if initialization is correct
        logger.error("Database session factory is not initialized.")
        raise RuntimeError("Database session factory is not initialized.")

    try:
        async with session_factory() as session:
            logger.debug("Database session created and yielded")
            yield session
    except SQLAlchemyError as e:
        logger.error(f"Database session error: {e}", exc_info=True)
        # The `async with` block will handle rollback on exception
        raise RuntimeError(f"Database session error: {e}") from e
    finally:
        logger.debug("Database session context closed")


async def initialize_database(config: DatabaseConfig | None = None) -> None:
    """Initialize database tables and run migrations."""
    try:
        if config is None:
            config = get_database_config()

        logger.info(f"Initializing database: {config.database_path}")

        # Ensure database directory exists
        db_dir = os.path.dirname(config.database_path)
        try:
            os.makedirs(db_dir, exist_ok=True)
            logger.debug(f"Database directory ensured: {db_dir}")
        except OSError as e:
            logger.error(f"Failed to create database directory {db_dir}: {e}")
            raise RuntimeError(f"Cannot create database directory: {e}")

        # Create database engine
        engine = get_database_engine(config)

        # Test database connection and create initial structure
        try:
            async with engine.begin() as conn:
                # Create a simple test to ensure database file is created and accessible
                from sqlalchemy import text

                logger.debug("Testing database connection and creating initial structure")
                await conn.execute(
                    text("CREATE TABLE IF NOT EXISTS _db_init_test (id INTEGER PRIMARY KEY)")
                )
                # Verify we can query the test table
                await conn.execute(text("SELECT 1 FROM _db_init_test LIMIT 1"))
                # Clean up test table
                await conn.execute(text("DROP TABLE IF EXISTS _db_init_test"))

                logger.debug("Database connection test completed successfully")

                # Import all model modules to register them with Base.metadata
                import app.user_models.db_models  # noqa: F401

                # Create user tables if they don't exist
                logger.debug("Creating user management tables")
                await conn.run_sync(Base.metadata.create_all)
                logger.debug("User management tables created/verified")

        except SQLAlchemyError as e:
            logger.error(f"Database connection test failed: {e}")
            if "unable to open database file" in str(e).lower():
                raise RuntimeError(f"Database initialization failed - invalid database path: {config.database_path}. {e}")
            else:
                raise RuntimeError(f"Database initialization failed - connection error: {e}")

        # Set proper file permissions for security (readable/writable by owner only)
        if os.path.exists(config.database_path):
            try:
                os.chmod(config.database_path, 0o600)
                logger.debug(f"Set secure file permissions (600) on {config.database_path}")
            except OSError as e:
                logger.warning(f"Failed to set secure permissions on database file: {e}")

        # Create the session factory after engine is ready
        create_database_session(config)
        logger.info("Database session factory created")

        logger.info("Database initialization completed successfully")

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise RuntimeError(f"Failed to initialize database: {e}")


async def close_database_connections() -> None:
    """Close all database connections and dispose engine."""
    global _engine, _session_factory

    try:
        logger.info("Closing database connections")

        if _engine is not None:
            logger.debug("Disposing database engine")
            await _engine.dispose()
            _engine = None
            logger.debug("Database engine disposed successfully")

        _session_factory = None
        logger.info("Database connections closed successfully")

    except Exception as e:
        logger.error(f"Error closing database connections: {e}")
        # Reset globals even if disposal failed to avoid stale references
        _engine = None
        _session_factory = None
        raise RuntimeError(f"Failed to close database connections: {e}")
