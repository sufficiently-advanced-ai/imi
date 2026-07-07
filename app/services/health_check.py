"""
Health check service for production monitoring - Issue #398

Provides comprehensive health checking for:
- Application liveness (basic health)
- Readiness checks with dependency validation
- Performance metrics and caching
- Integration with monitoring systems
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class DependencyStatus(str, Enum):
    """Health check status values"""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"


@dataclass
class HealthCheckResult:
    """Result of a health check operation"""
    status: DependencyStatus
    checks: dict[str, Any]
    timestamp: datetime
    performance_metrics: dict[str, Any]
    uptime_seconds: float
    version: str = "1.0.0"


class HealthCheckCache:
    """Simple in-memory cache for health check results"""

    def __init__(self, ttl_seconds: int = 30):
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, Any] = {}
        self._timestamps: dict[str, datetime] = {}

    def get(self, key: str) -> HealthCheckResult | None:
        """Get cached result if not expired"""
        if key not in self._cache or key not in self._timestamps:
            return None

        # Check if expired
        age = datetime.utcnow() - self._timestamps[key]
        if age.total_seconds() > self.ttl_seconds:
            # Remove expired entry
            del self._cache[key]
            del self._timestamps[key]
            return None

        return self._cache[key]

    def set(self, key: str, result: HealthCheckResult) -> None:
        """Cache a health check result"""
        self._cache[key] = result
        self._timestamps[key] = datetime.utcnow()

    def clear(self, key: str | None = None) -> None:
        """Clear cache entry or all entries"""
        if key:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)
        else:
            self._cache.clear()
            self._timestamps.clear()


class HealthCheckService:
    """Comprehensive health check service"""

    def __init__(
        self,
        dependency_timeout: float = 5.0,
        cache_ttl: int = 30
    ):
        if dependency_timeout <= 0:
            raise ValueError("dependency_timeout must be positive")
        if cache_ttl < 0:
            raise ValueError("cache_ttl cannot be negative")

        self.dependency_timeout = dependency_timeout
        self.cache = HealthCheckCache(ttl_seconds=cache_ttl)
        self._logger = logger
        self._start_time = time.time()
        self._status_change_listeners: list[Callable] = []
        self._last_status: DependencyStatus | None = None

    async def liveness_check(self) -> HealthCheckResult:
        """Basic liveness check - application is running"""
        start_time = time.time()

        try:
            # Basic application health
            uptime = time.time() - self._start_time

            checks = {
                "application": {
                    "status": DependencyStatus.HEALTHY,
                    "uptime_seconds": uptime,
                    "message": "Application is running"
                }
            }

            performance_metrics = {
                "check_duration_ms": (time.time() - start_time) * 1000,
                "dependency_check_times": {"application": 0},
                "cache_hit_ratio": 0.0
            }

            result = HealthCheckResult(
                status=DependencyStatus.HEALTHY,
                checks=checks,
                timestamp=datetime.utcnow(),
                performance_metrics=performance_metrics,
                uptime_seconds=uptime
            )

            return result

        except Exception as e:
            self._logger.error(f"Liveness check failed: {e}")
            checks = {
                "application": {
                    "status": DependencyStatus.UNHEALTHY,
                    "error": str(e)
                }
            }

            return HealthCheckResult(
                status=DependencyStatus.UNHEALTHY,
                checks=checks,
                timestamp=datetime.utcnow(),
                performance_metrics={},
                uptime_seconds=0
            )

    async def readiness_check(self) -> HealthCheckResult:
        """Comprehensive readiness check with dependency validation"""
        # Check cache first
        cached = self.cache.get("readiness")
        if cached:
            # Update cache hit metrics
            if "performance_metrics" in cached.__dict__:
                cached.performance_metrics["cache_hit_ratio"] = 1.0
            return cached

        start_time = time.time()
        checks = {}
        dependency_times = {}
        uptime = time.time() - self._start_time

        # Check each dependency
        overall_status = DependencyStatus.HEALTHY

        # 1. Git Operations
        git_start = time.time()
        try:
            git_check = await asyncio.wait_for(
                self._check_git_operations(),
                timeout=self.dependency_timeout
            )
            checks["git_operations"] = git_check
            if git_check["status"] != DependencyStatus.HEALTHY:
                overall_status = DependencyStatus.DEGRADED
        except TimeoutError:
            checks["git_operations"] = {
                "status": DependencyStatus.UNHEALTHY,
                "error": "timeout",
                "message": "Git operations check timed out"
            }
            overall_status = DependencyStatus.UNHEALTHY
        except Exception as e:
            checks["git_operations"] = {
                "status": DependencyStatus.UNHEALTHY,
                "error": str(e)
            }
            overall_status = DependencyStatus.DEGRADED

        dependency_times["git_operations"] = (time.time() - git_start) * 1000

        # 2. Database
        db_start = time.time()
        try:
            db_check = await asyncio.wait_for(
                self._check_database(),
                timeout=self.dependency_timeout
            )
            checks["database"] = db_check
            if db_check["status"] != DependencyStatus.HEALTHY:
                overall_status = DependencyStatus.DEGRADED
        except TimeoutError:
            checks["database"] = {
                "status": DependencyStatus.UNHEALTHY,
                "error": "timeout",
                "message": "Database check timed out"
            }
            overall_status = DependencyStatus.UNHEALTHY
        except Exception as e:
            checks["database"] = {
                "status": DependencyStatus.UNHEALTHY,
                "error": str(e)
            }
            overall_status = DependencyStatus.DEGRADED

        dependency_times["database"] = (time.time() - db_start) * 1000

        # 3. External APIs
        api_start = time.time()
        try:
            api_check = await asyncio.wait_for(
                self._check_external_apis(),
                timeout=self.dependency_timeout
            )
            checks["external_apis"] = api_check
            if any(
                api_status != DependencyStatus.HEALTHY
                for api_status in api_check.values()
                if isinstance(api_status, dict) and "status" in api_status
            ):
                overall_status = DependencyStatus.DEGRADED
        except TimeoutError:
            checks["external_apis"] = {
                "status": DependencyStatus.UNHEALTHY,
                "error": "timeout"
            }
            overall_status = DependencyStatus.UNHEALTHY
        except Exception as e:
            checks["external_apis"] = {
                "status": DependencyStatus.UNHEALTHY,
                "error": str(e)
            }
            overall_status = DependencyStatus.DEGRADED

        dependency_times["external_apis"] = (time.time() - api_start) * 1000

        # Create performance metrics
        total_duration = (time.time() - start_time) * 1000
        performance_metrics = {
            "check_duration_ms": total_duration,
            "dependency_check_times": dependency_times,
            "cache_hit_ratio": 0.0  # This was a fresh check
        }

        result = HealthCheckResult(
            status=overall_status,
            checks=checks,
            timestamp=datetime.utcnow(),
            performance_metrics=performance_metrics,
            uptime_seconds=uptime
        )

        # Cache the result
        self.cache.set("readiness", result)

        # Notify status change listeners
        self._notify_status_change(overall_status)

        return result

    async def _check_git_operations(self) -> dict[str, Any]:
        """Check git operations health"""
        try:
            from ..git_ops import git_ops

            # Check if git is initialized
            is_initialized = await git_ops.is_initialized()
            if not is_initialized:
                return {
                    "status": DependencyStatus.UNHEALTHY,
                    "details": {"initialized": False},
                    "message": "Git repository not initialized"
                }

            # Check git status
            status = await git_ops.get_status()

            return {
                "status": DependencyStatus.HEALTHY,
                "details": {
                    "initialized": True,
                    "repository_status": status
                },
                "message": "Git operations functional"
            }

        except Exception as e:
            return {
                "status": DependencyStatus.UNHEALTHY,
                "error": str(e),
                "message": "Git operations failed"
            }

    async def _check_database(self) -> dict[str, Any]:
        """Check database connectivity"""
        try:
            from ..database import get_database_engine

            engine = get_database_engine()

            # Test connection with simple query
            async with engine.begin() as conn:
                from sqlalchemy import text
                result = await conn.execute(text("SELECT 1"))
                await result.fetchone()

            return {
                "status": DependencyStatus.HEALTHY,
                "details": {
                    "connection_pool": "active",
                    "query_test": "passed"
                },
                "message": "Database connection healthy"
            }

        except Exception as e:
            return {
                "status": DependencyStatus.UNHEALTHY,
                "error": str(e),
                "message": "Database connection failed"
            }

    async def _check_external_apis(self) -> dict[str, Any]:
        """Check external API connectivity"""
        apis = {}

        # Claude API
        try:
            from ..services.claude_client import ClaudeClient
            client = ClaudeClient()
            # Don't actually make a call, just check if client is properly configured
            if hasattr(client, '_client') and client._client:
                apis["claude_api"] = {
                    "status": DependencyStatus.HEALTHY,
                    "message": "Claude client configured"
                }
            else:
                apis["claude_api"] = {
                    "status": DependencyStatus.UNHEALTHY,
                    "message": "Claude client not configured"
                }
        except Exception as e:
            apis["claude_api"] = {
                "status": DependencyStatus.UNHEALTHY,
                "error": str(e)
            }

        # GitHub API
        try:
            from ..github_client import GitHubClient
            client = GitHubClient()
            # Check if configured
            apis["github_api"] = {
                "status": DependencyStatus.HEALTHY,
                "message": "GitHub client available"
            }
        except Exception as e:
            apis["github_api"] = {
                "status": DependencyStatus.UNHEALTHY,
                "error": str(e)
            }

        return apis

    def add_status_change_listener(self, callback: Callable) -> None:
        """Add callback for status change notifications"""
        self._status_change_listeners.append(callback)

    def _notify_status_change(self, new_status: DependencyStatus) -> None:
        """Notify listeners of status changes"""
        if self._last_status != new_status:
            old_status = self._last_status
            self._last_status = new_status

            for listener in self._status_change_listeners:
                try:
                    listener(old_status, new_status)
                except Exception as e:
                    self._logger.error(f"Status change listener failed: {e}")

    def invalidate_cache(self, key: str | None = None) -> None:
        """Invalidate health check cache"""
        self.cache.clear(key)
        self._logger.info(f"Health check cache invalidated: {key or 'all'}")

    def _record_metrics(self, operation: str, duration: float) -> None:
        """Record metrics for health check operations"""
        # Integration point for metrics system
        pass


# Global service instance
_health_service: HealthCheckService | None = None


def get_health_service() -> HealthCheckService:
    """Get the global health service instance"""
    global _health_service
    if _health_service is None:
        _health_service = HealthCheckService()
    return _health_service
