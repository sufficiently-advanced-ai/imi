"""Regression tests for SecureUserService lookups with non-numeric IDs.

The user-lookup WHERE clause used the pattern
``or_(User.external_id == user_id, User.id == int(user_id) if
user_id.isdigit() else 0)``. For any non-numeric ID the literal ``0``
reaches ``or_()`` and SQLAlchemy raises ``ArgumentError`` at
statement-build time; the service's blanket ``except Exception`` then
swallows it, so every lookup silently returns None/[]/False for
external-style IDs (PR #5 review finding).
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.services import secure_user_service as sus_module
from app.services.secure_user_service import SecureUserService
from app.user_models.db_models import User


@pytest_asyncio.fixture
async def service_env(monkeypatch):
    """SecureUserService wired to a fresh in-memory SQLite database."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def fake_get_database_session():
        async with maker() as session:
            yield session

    monkeypatch.setattr(sus_module, "get_database_session", fake_get_database_session)
    # Low iteration count: these tests exercise lookups, not hash strength.
    yield SecureUserService(hash_iterations=1_000), maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_user_with_non_numeric_id_creates_and_returns_user(service_env):
    service, _ = service_env
    result = await service.get_user("user_abc123")
    assert result is not None
    assert result.id == "user_abc123"


@pytest.mark.asyncio
async def test_get_user_finds_existing_user_by_internal_numeric_id(service_env):
    service, maker = service_env
    async with maker() as session:
        user = User(
            external_id="ext_xyz",
            email="internal-id@example.com",
            first_name="Inner",
            last_name="Id",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        internal_id = user.id

    result = await service.get_user(str(internal_id))
    assert result is not None
    assert result.email == "internal-id@example.com"


@pytest.mark.asyncio
async def test_session_lifecycle_with_non_numeric_user_id(service_env):
    service, _ = service_env
    assert await service.get_user("user_abc") is not None

    created = await service.create_session("user_abc", "token-123")
    assert created is not None

    assert await service.terminate_session("user_abc", str(created.id)) is True


@pytest.mark.asyncio
async def test_terminate_session_with_non_numeric_session_id_returns_false(service_env):
    service, _ = service_env
    assert await service.get_user("user_abc") is not None
    assert await service.terminate_session("user_abc", "not-a-number") is False
