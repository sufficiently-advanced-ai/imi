"""
Graceful Shutdown and Lifecycle Management - Issue #398

Provides:
- Graceful shutdown handling
- Database connection pooling
- Background task management
- Resource cleanup
- Health status during shutdown
"""

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class LifecycleState(str, Enum):
    """Application lifecycle states"""
    STARTING = "starting"
    RUNNING = "running"
    SHUTTING_DOWN = "shutting_down"
    SHUTDOWN = "shutdown"


@dataclass
class ShutdownHandler:
    """Handler for graceful shutdown"""
    name: str
    handler: Callable[[], Awaitable[None]]
    timeout: float = 30.0  # Default 30 second timeout
    priority: int = 0  # Higher priority runs first


class LifecycleManager:
    """
    Manages application lifecycle and graceful shutdown
    """

    def __init__(self, shutdown_timeout: float = 60.0):
        self.shutdown_timeout = shutdown_timeout
        self.state = LifecycleState.STARTING

        # Shutdown handlers
        self.shutdown_handlers: list[ShutdownHandler] = []

        # Background tasks
        self.background_tasks: list[asyncio.Task] = []

        # Database connections and pools
        self.database_pools: list[Any] = []

        # Startup/shutdown events
        self._shutdown_event = asyncio.Event()
        self._startup_complete = asyncio.Event()

        logger.info(f"Lifecycle manager initialized with {shutdown_timeout}s shutdown timeout")

    def add_shutdown_handler(
        self,
        name: str,
        handler: Callable[[], Awaitable[None]],
        timeout: float = 30.0,
        priority: int = 0
    ) -> None:
        """
        Add a graceful shutdown handler

        Args:
            name: Handler name for logging
            handler: Async function to call during shutdown
            timeout: Timeout for this handler
            priority: Priority (higher values run first)
        """
        shutdown_handler = ShutdownHandler(
            name=name,
            handler=handler,
            timeout=timeout,
            priority=priority
        )

        self.shutdown_handlers.append(shutdown_handler)
        self.shutdown_handlers.sort(key=lambda x: x.priority, reverse=True)

        logger.info(f"Added shutdown handler: {name} (priority: {priority}, timeout: {timeout}s)")

    def add_background_task(self, task: asyncio.Task, name: str | None = None) -> None:
        """Add a background task to be managed"""
        self.background_tasks.append(task)
        if name:
            task.set_name(name)

        logger.info(f"Added background task: {name or 'unnamed'}")

    def register_database_pool(self, pool: Any) -> None:
        """Register a database connection pool for cleanup"""
        self.database_pools.append(pool)
        logger.info("Registered database pool for cleanup")

    async def startup(self) -> None:
        """Run startup sequence"""
        logger.info("Starting application lifecycle...")

        # Initialize signal handlers
        self._setup_signal_handlers()

        # Mark as running
        self.state = LifecycleState.RUNNING
        self._startup_complete.set()

        logger.info("Application startup complete - ready to serve requests")

    def _setup_signal_handlers(self) -> None:
        """Setup graceful shutdown signal handlers"""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum} - initiating graceful shutdown")
            asyncio.create_task(self.shutdown())

        # Handle SIGTERM and SIGINT
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        logger.info("Signal handlers configured for graceful shutdown")

    async def shutdown(self) -> None:
        """Perform graceful shutdown"""
        if self.state != LifecycleState.RUNNING:
            logger.info("Shutdown already in progress or complete")
            return

        logger.info("Initiating graceful shutdown...")
        self.state = LifecycleState.SHUTTING_DOWN
        self._shutdown_event.set()

        shutdown_start = time.time()

        try:
            # Run shutdown handlers in priority order
            for handler in self.shutdown_handlers:
                logger.info(f"Running shutdown handler: {handler.name}")
                try:
                    await asyncio.wait_for(handler.handler(), timeout=handler.timeout)
                    logger.info(f"Shutdown handler '{handler.name}' completed successfully")
                except TimeoutError:
                    logger.warning(f"Shutdown handler '{handler.name}' timed out after {handler.timeout}s")
                except Exception as e:
                    logger.error(f"Shutdown handler '{handler.name}' failed: {e}")

            # Cancel background tasks
            logger.info(f"Cancelling {len(self.background_tasks)} background tasks...")
            for task in self.background_tasks:
                if not task.done():
                    task.cancel()

            # Wait for tasks to complete
            if self.background_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*self.background_tasks, return_exceptions=True),
                        timeout=10.0
                    )
                    logger.info("All background tasks cancelled successfully")
                except TimeoutError:
                    logger.warning("Some background tasks did not complete within timeout")

            # Close database connections
            await self._close_database_connections()

            # Final cleanup
            shutdown_duration = time.time() - shutdown_start
            logger.info(f"Graceful shutdown completed in {shutdown_duration:.2f}s")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        finally:
            self.state = LifecycleState.SHUTDOWN

    async def _close_database_connections(self) -> None:
        """Close all registered database connections"""
        if not self.database_pools:
            return

        logger.info(f"Closing {len(self.database_pools)} database connection pools...")

        for i, pool in enumerate(self.database_pools):
            try:
                if hasattr(pool, 'close'):
                    await pool.close()
                elif hasattr(pool, 'disconnect'):
                    await pool.disconnect()
                logger.info(f"Database pool {i+1} closed successfully")
            except Exception as e:
                logger.error(f"Failed to close database pool {i+1}: {e}")

        logger.info("Database connection cleanup complete")

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown signal"""
        await self._shutdown_event.wait()

    def is_shutting_down(self) -> bool:
        """Check if application is shutting down"""
        return self.state in [LifecycleState.SHUTTING_DOWN, LifecycleState.SHUTDOWN]

    def get_status(self) -> dict[str, Any]:
        """Get current lifecycle status"""
        return {
            "state": self.state.value,
            "shutdown_handlers": len(self.shutdown_handlers),
            "background_tasks": len(self.background_tasks),
            "active_background_tasks": len([t for t in self.background_tasks if not t.done()]),
            "database_pools": len(self.database_pools),
            "startup_complete": self._startup_complete.is_set(),
            "shutdown_requested": self._shutdown_event.is_set()
        }


# Database connection pool manager
class DatabasePoolManager:
    """
    Manages database connection pools with lifecycle integration
    """

    def __init__(self, lifecycle_manager: LifecycleManager):
        self.lifecycle_manager = lifecycle_manager
        self.pools: dict[str, Any] = {}
        self.connection_stats: dict[str, dict[str, Any]] = {}

    async def create_pool(
        self,
        name: str,
        database_url: str,
        min_connections: int = 1,
        max_connections: int = 10,
        **pool_kwargs
    ) -> Any:
        """
        Create a database connection pool

        Args:
            name: Pool identifier
            database_url: Database connection URL
            min_connections: Minimum pool size
            max_connections: Maximum pool size
            **pool_kwargs: Additional pool configuration

        Returns:
            Database pool instance
        """
        try:
            # This is a placeholder - actual implementation would depend on database type
            # For SQLAlchemy async pools:
            from sqlalchemy.ext.asyncio import create_async_engine

            engine = create_async_engine(
                database_url,
                pool_size=min_connections,
                max_overflow=max_connections - min_connections,
                **pool_kwargs
            )

            self.pools[name] = engine
            self.connection_stats[name] = {
                "created_at": time.time(),
                "min_connections": min_connections,
                "max_connections": max_connections,
                "active_connections": 0,
                "total_connections": 0
            }

            # Register with lifecycle manager
            self.lifecycle_manager.register_database_pool(engine)

            logger.info(f"Created database pool '{name}': {min_connections}-{max_connections} connections")
            return engine

        except Exception as e:
            logger.error(f"Failed to create database pool '{name}': {e}")
            raise

    def get_pool(self, name: str) -> Any | None:
        """Get a database pool by name"""
        return self.pools.get(name)

    def get_pool_stats(self) -> dict[str, dict[str, Any]]:
        """Get statistics for all pools"""
        stats = {}

        for name, pool in self.pools.items():
            base_stats = self.connection_stats.get(name, {})

            # Try to get live stats from pool
            live_stats = {}
            try:
                if hasattr(pool, 'pool'):
                    # SQLAlchemy pool stats
                    pool_obj = pool.pool
                    live_stats = {
                        "size": getattr(pool_obj, 'size', lambda: 0)(),
                        "checked_in": getattr(pool_obj, 'checkedin', lambda: 0)(),
                        "checked_out": getattr(pool_obj, 'checkedout', lambda: 0)(),
                        "overflow": getattr(pool_obj, 'overflow', lambda: 0)()
                    }
            except Exception as e:
                logger.debug(f"Could not get live stats for pool '{name}': {e}")

            stats[name] = {**base_stats, **live_stats}

        return stats


# Global lifecycle manager instance
_lifecycle_manager: LifecycleManager | None = None


def get_lifecycle_manager() -> LifecycleManager:
    """Get global lifecycle manager instance"""
    global _lifecycle_manager
    if _lifecycle_manager is None:
        _lifecycle_manager = LifecycleManager()
    return _lifecycle_manager


@asynccontextmanager
async def managed_lifecycle():
    """Context manager for application lifecycle"""
    manager = get_lifecycle_manager()

    try:
        await manager.startup()
        yield manager
    finally:
        await manager.shutdown()


# Health check integration
async def lifecycle_health_check() -> dict[str, Any]:
    """Health check that considers lifecycle state"""
    manager = get_lifecycle_manager()
    status = manager.get_status()

    # Determine health based on lifecycle state
    is_healthy = status["state"] == LifecycleState.RUNNING

    return {
        "healthy": is_healthy,
        "state": status["state"],
        "details": status
    }
