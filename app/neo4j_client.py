"""
Neo4j Client - Async driver with connection pooling.

Singleton via service registry, matching existing patterns (domain_config_service.py).
"""

import asyncio
import logging

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession

from .config import settings

logger = logging.getLogger(__name__)

# Module-level singleton
_neo4j_client: "Neo4jClient | None" = None


class Neo4jClient:
    """Async Neo4j driver wrapper with connection pooling and health checks."""

    def __init__(
        self,
        uri: str = "",
        username: str = "",
        password: str = "",
    ):
        self._uri = uri or settings.NEO4J_URI
        self._username = username or settings.NEO4J_USERNAME
        self._password = password or settings.NEO4J_PASSWORD.get_secret_value()
        self._driver: AsyncDriver | None = None

    async def initialize(self) -> None:
        """Connect to Neo4j and verify connectivity."""
        logger.info(f"Connecting to Neo4j at {self._uri}")
        self._driver = AsyncGraphDatabase.driver(
            self._uri,
            auth=(self._username, self._password),
            max_connection_pool_size=25,
            connection_acquisition_timeout=30.0,
        )
        # Verify the connection works
        try:
            await self._driver.verify_connectivity()
            server_info = await self._driver.get_server_info()
            logger.info(
                f"Neo4j connected: {server_info.agent} "
                f"(protocol {server_info.protocol_version})"
            )
        except Exception as e:
            logger.exception(f"Neo4j connection failed: {e}")
            await self.close()
            raise

    @property
    def is_initialized(self) -> bool:
        """Whether the driver has been created and is ready for use."""
        return self._driver is not None

    @property
    def driver(self) -> AsyncDriver:
        """Get the underlying driver (raises if not initialized)."""
        if self._driver is None:
            raise RuntimeError("Neo4j client not initialized. Call initialize() first.")
        return self._driver

    def session(self, **kwargs) -> AsyncSession:
        """Get an async session (use as async context manager).

        Example:
            async with client.session() as session:
                result = await session.run("MATCH (n) RETURN count(n)")
        """
        return self.driver.session(**kwargs)

    async def execute_write(self, query: str, parameters: dict | None = None) -> list:
        """Execute a write transaction with automatic retries and return results as list of dicts."""
        async with self.session() as session:
            async def tx_func(tx):
                result = await tx.run(query, parameters or {})
                return [record.data() async for record in result]
            return await session.execute_write(tx_func)

    async def execute_read(self, query: str, parameters: dict | None = None) -> list:
        """Execute a read transaction with automatic retries and return results as list of dicts."""
        async with self.session() as session:
            async def tx_func(tx):
                result = await tx.run(query, parameters or {})
                return [record.data() async for record in result]
            return await session.execute_read(tx_func)

    async def execute_many(self, statements: list[str]) -> dict:
        """Execute multiple Cypher statements sequentially in one session.

        Returns a summary dict with succeeded/failed counts and any errors.
        """
        results = {"succeeded": 0, "failed": 0, "errors": []}
        async with self.session() as session:
            for stmt in statements:
                try:
                    async def tx_func(tx, s=stmt):
                        result = await tx.run(s)
                        await result.consume()
                    await session.execute_write(tx_func)
                    logger.debug(f"Executed: {stmt[:80]}...")
                    results["succeeded"] += 1
                except Exception as e:
                    logger.warning(f"Statement failed (non-fatal): {stmt[:80]}... — {e}")
                    results["failed"] += 1
                    results["errors"].append(str(e))
        return results

    async def health_check(self) -> dict:
        """Check Neo4j connectivity and return status."""
        if self._driver is None:
            return {"status": "not_initialized", "healthy": False}
        try:
            await self._driver.verify_connectivity()
            async with self.session() as session:
                result = await session.run("RETURN 1 AS ok")
                record = await result.single()
                return {
                    "status": "healthy",
                    "healthy": True,
                    "result": record["ok"] if record else None,
                }
        except Exception as e:
            return {"status": "unhealthy", "healthy": False, "error": str(e)}

    async def close(self) -> None:
        """Close the driver and release all connections."""
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
            logger.info("Neo4j connection closed")


def get_neo4j_client() -> Neo4jClient:
    """Get the global Neo4j client singleton.

    Note: The client must be initialized via initialize_neo4j() before use.
    Calling methods on an uninitialized client will raise RuntimeError.
    """
    global _neo4j_client
    if _neo4j_client is None:
        _neo4j_client = Neo4jClient()
    return _neo4j_client


async def initialize_neo4j(max_retries: int = 5, base_delay: float = 2.0) -> Neo4jClient:
    """Initialize the global Neo4j client (call during app startup).

    Retries with exponential backoff when Neo4j is temporarily unavailable,
    which commonly occurs when the uvicorn reloader restarts the worker
    process before Neo4j is fully ready.
    """
    if max_retries < 1:
        raise ValueError("max_retries must be >= 1")
    if base_delay <= 0:
        raise ValueError("base_delay must be > 0")

    client = get_neo4j_client()
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            await client.initialize()
            if attempt > 1:
                logger.info(f"Neo4j connected on attempt {attempt}")
            return client
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))  # 2s, 4s, 8s, 16s
                logger.warning(
                    f"Neo4j connection attempt {attempt}/{max_retries} failed: {e}. "
                    f"Retrying in {delay:.0f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.exception(
                    f"Neo4j connection failed after {max_retries} attempts"
                )

    if last_error is None:
        raise RuntimeError("Neo4j initialization failed without capturing an error")
    raise last_error


async def close_neo4j() -> None:
    """Close the global Neo4j client (call during app shutdown)."""
    global _neo4j_client
    if _neo4j_client is not None:
        await _neo4j_client.close()
        _neo4j_client = None
