"""
Alembic environment configuration for async SQLAlchemy migrations.
"""

import asyncio
import os
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata

# Import your models here so they're available for autogenerate
try:
    from app.database import Base
    target_metadata = Base.metadata
except ImportError:
    target_metadata = None

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_database_url():
    """Get database URL from environment or config."""
    # Try environment variable first
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    
    # Try to construct from database path setting
    db_path = os.getenv("DATABASE_PATH", "/app/data/imi.db")
    if db_path:
        return f"sqlite+aiosqlite:///{db_path}"
    
    # Fall back to config
    config_url = config.get_main_option("sqlalchemy.url")
    if config_url:
        return config_url
        
    # Final fallback to default
    return "sqlite+aiosqlite:///data/imi.db"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Handle case where we're already in an asyncio event loop (e.g., pytest-asyncio)
    try:
        # Check if we're already in an asyncio context
        asyncio.get_running_loop()
        # If we get here, there's already a running loop, so run in a thread
        import threading
        
        result = [None]
        exception = [None]
        
        def run_in_thread():
            try:
                result[0] = asyncio.run(run_async_migrations())
            except Exception as e:
                exception[0] = e
        
        thread = threading.Thread(target=run_in_thread)
        thread.start()
        thread.join()
        
        if exception[0]:
            raise exception[0]
            
    except RuntimeError:
        # No event loop running, safe to use asyncio.run
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()