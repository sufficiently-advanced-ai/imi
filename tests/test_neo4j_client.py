"""Tests for Neo4j Client — Lifecycle, query execution, health checks.

Source: app/neo4j_client.py

All Neo4j driver interactions are mocked via AsyncMock.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.neo4j_client import Neo4jClient


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """Create a Neo4jClient without connecting."""
    return Neo4jClient(uri="bolt://test:7687", username="neo4j", password="test")


@pytest.fixture
def mock_session():
    """Create a mock async session with execute_read/write support."""
    session = AsyncMock()
    session.execute_read = AsyncMock(return_value=[])
    session.execute_write = AsyncMock(return_value=[])
    session.run = AsyncMock()
    return session


@pytest.fixture
def mock_driver(mock_session):
    """Create a mock AsyncDriver with session support.

    driver.session() is synchronous in the real Neo4j driver — it returns
    an AsyncSession object that supports `async with`. We use MagicMock
    for the driver and configure session() to return an async context
    manager wrapping our mock_session.
    """
    driver = MagicMock()
    driver.verify_connectivity = AsyncMock()
    driver.close = AsyncMock()

    server_info = MagicMock()
    server_info.agent = "Neo4j/5.0.0"
    server_info.protocol_version = (5, 0)
    driver.get_server_info = AsyncMock(return_value=server_info)

    # driver.session() is sync and returns an async context manager
    session_cm = AsyncMock()
    session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    driver.session.return_value = session_cm

    return driver


@pytest.fixture
def initialized_client(client, mock_driver):
    """Client with a pre-set mock driver (simulating post-initialize state)."""
    client._driver = mock_driver
    return client


# ──────────────────────────────────────────────────────────────
# Driver Property
# ──────────────────────────────────────────────────────────────


class TestDriverProperty:
    def test_raises_before_init(self, client):
        """Accessing .driver before initialize() raises RuntimeError."""
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = client.driver

    def test_returns_driver_after_init(self, initialized_client, mock_driver):
        assert initialized_client.driver is mock_driver


# ──────────────────────────────────────────────────────────────
# Initialize
# ──────────────────────────────────────────────────────────────


class TestInitialize:
    @pytest.mark.asyncio
    async def test_initialize_creates_driver(self, client):
        """initialize() should create a driver and verify connectivity."""
        mock_driver = AsyncMock()
        server_info = MagicMock()
        server_info.agent = "Neo4j/5.0.0"
        server_info.protocol_version = (5, 0)
        mock_driver.get_server_info = AsyncMock(return_value=server_info)
        mock_driver.verify_connectivity = AsyncMock()

        with patch(
            "app.neo4j_client.AsyncGraphDatabase.driver",
            return_value=mock_driver,
        ):
            await client.initialize()

        assert client._driver is mock_driver
        mock_driver.verify_connectivity.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_initialize_closes_on_failure(self, client):
        """If verify_connectivity fails, driver should be closed."""
        mock_driver = AsyncMock()
        mock_driver.verify_connectivity = AsyncMock(
            side_effect=ConnectionError("refused")
        )
        mock_driver.close = AsyncMock()

        with (
            patch(
                "app.neo4j_client.AsyncGraphDatabase.driver",
                return_value=mock_driver,
            ),
            pytest.raises(ConnectionError),
        ):
            await client.initialize()

        mock_driver.close.assert_awaited_once()


# ──────────────────────────────────────────────────────────────
# Query Execution
# ──────────────────────────────────────────────────────────────


class TestExecuteWrite:
    @pytest.mark.asyncio
    async def test_calls_session_execute_write(self, initialized_client, mock_session):
        """execute_write should open a session and call session.execute_write."""
        mock_session.execute_write = AsyncMock(return_value=[{"count": 1}])

        result = await initialized_client.execute_write(
            "CREATE (n:Test) RETURN count(n) AS count"
        )

        mock_session.execute_write.assert_awaited_once()
        assert result == [{"count": 1}]


class TestExecuteRead:
    @pytest.mark.asyncio
    async def test_calls_session_execute_read(self, initialized_client, mock_session):
        """execute_read should open a session and call session.execute_read."""
        mock_session.execute_read = AsyncMock(
            return_value=[{"id": "person-tom", "name": "Tom"}]
        )

        result = await initialized_client.execute_read(
            "MATCH (n:Person) RETURN n.id AS id, n.name AS name"
        )

        mock_session.execute_read.assert_awaited_once()
        assert result == [{"id": "person-tom", "name": "Tom"}]


class TestExecuteMany:
    @pytest.mark.asyncio
    async def test_returns_summary(self, initialized_client, mock_session):
        """execute_many should return succeeded/failed counts."""
        statements = ["CREATE CONSTRAINT c1 IF NOT EXISTS FOR (n:A) REQUIRE n.id IS UNIQUE",
                       "CREATE CONSTRAINT c2 IF NOT EXISTS FOR (n:B) REQUIRE n.id IS UNIQUE"]

        result = await initialized_client.execute_many(statements)

        assert result["succeeded"] == 2
        assert result["failed"] == 0
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_handles_partial_failure(self, initialized_client, mock_session):
        """When one statement fails, it's counted but doesn't stop execution."""
        call_count = 0

        async def side_effect(func):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("constraint already exists")
            return await func(AsyncMock())

        mock_session.execute_write = AsyncMock(side_effect=side_effect)

        statements = ["stmt1", "stmt2", "stmt3"]
        result = await initialized_client.execute_many(statements)

        assert result["succeeded"] == 2
        assert result["failed"] == 1
        assert len(result["errors"]) == 1


# ──────────────────────────────────────────────────────────────
# Health Check
# ──────────────────────────────────────────────────────────────


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self, initialized_client, mock_session, mock_driver):
        """When connected, health_check returns healthy: True."""
        mock_record = {"ok": 1}
        mock_result = AsyncMock()
        mock_result.single = AsyncMock(return_value=mock_record)
        mock_session.run = AsyncMock(return_value=mock_result)

        result = await initialized_client.health_check()
        assert result["healthy"] is True
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_not_initialized(self, client):
        """Before initialization, health_check returns healthy: False."""
        result = await client.health_check()
        assert result["healthy"] is False
        assert result["status"] == "not_initialized"

    @pytest.mark.asyncio
    async def test_connection_error(self, initialized_client, mock_driver):
        """If verify_connectivity fails, health_check returns error info."""
        mock_driver.verify_connectivity = AsyncMock(
            side_effect=ConnectionError("network unreachable")
        )

        result = await initialized_client.health_check()
        assert result["healthy"] is False
        assert "network unreachable" in result["error"]


# ──────────────────────────────────────────────────────────────
# Close
# ──────────────────────────────────────────────────────────────


class TestClose:
    @pytest.mark.asyncio
    async def test_closes_driver(self, initialized_client, mock_driver):
        await initialized_client.close()
        mock_driver.close.assert_awaited_once()
        assert initialized_client._driver is None

    @pytest.mark.asyncio
    async def test_close_idempotent(self, client):
        """Closing when already closed doesn't raise."""
        await client.close()  # no-op, should not raise


# ──────────────────────────────────────────────────────────────
# Singleton / Module Functions
# ──────────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_neo4j_client_returns_instance(self):
        """get_neo4j_client should return a Neo4jClient."""
        import app.neo4j_client as mod

        original = mod._neo4j_client
        try:
            mod._neo4j_client = None
            client = mod.get_neo4j_client()
            assert isinstance(client, Neo4jClient)
        finally:
            mod._neo4j_client = original

    def test_get_neo4j_client_returns_cached(self):
        """Second call should return the same instance."""
        import app.neo4j_client as mod

        original = mod._neo4j_client
        try:
            mod._neo4j_client = None
            c1 = mod.get_neo4j_client()
            c2 = mod.get_neo4j_client()
            assert c1 is c2
        finally:
            mod._neo4j_client = original
