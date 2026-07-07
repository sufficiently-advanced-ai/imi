"""
Health check routes for production monitoring - Issue #398
"""

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services.health_check import DependencyStatus, get_health_service

logger = logging.getLogger(__name__)
router = APIRouter()


class LivenessResponse(BaseModel):
    status: str
    timestamp: str
    version: str
    uptime_seconds: float
    hot_reload: bool = False


class ReadinessResponse(BaseModel):
    status: str
    timestamp: str
    dependencies: dict[str, Any]
    performance_metrics: dict[str, Any]
    version: str
    uptime_seconds: float


class MetricsResponse(BaseModel):
    health_score: int
    dependency_count: int
    healthy_dependencies: int
    degraded_dependencies: int
    unhealthy_dependencies: int
    uptime_seconds: float
    last_check_duration_ms: float
    cache_hit_ratio: float
    timestamp: str
    error: str | None = None


@router.get("/health", response_model=LivenessResponse)
async def liveness_check() -> Response:
    """
    Basic liveness probe - checks if application is running

    Used by Kubernetes liveness probes and load balancers.
    Should be fast and lightweight.
    """
    start_time = time.time()

    try:
        health_service = get_health_service()
        result = await health_service.liveness_check()

        # Add response timing header
        duration_ms = (time.time() - start_time) * 1000

        response_data = LivenessResponse(
            status=result.status,
            timestamp=result.timestamp.isoformat(),
            version=result.version,
            uptime_seconds=result.uptime_seconds,
            hot_reload=True,
        ).model_dump()

        response = JSONResponse(content=response_data)
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

        return response

    except Exception as e:
        logger.error(f"Liveness check failed: {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "error": str(e),
                "timestamp": time.time()
            }
        )


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness_check() -> Response:
    """
    Comprehensive readiness probe - checks all dependencies

    Used by Kubernetes readiness probes and deployment systems.
    Includes detailed dependency status and performance metrics.
    """
    start_time = time.time()

    try:
        health_service = get_health_service()
        result = await health_service.readiness_check()

        # Add response timing header
        duration_ms = (time.time() - start_time) * 1000

        # Determine HTTP status code based on health
        status_code = 200
        if result.status == DependencyStatus.UNHEALTHY:
            status_code = 503
        elif result.status == DependencyStatus.DEGRADED:
            status_code = 200  # Still ready, but degraded

        response_data = ReadinessResponse(
            status=result.status,
            timestamp=result.timestamp.isoformat(),
            dependencies=result.checks,
            performance_metrics=result.performance_metrics,
            version=result.version,
            uptime_seconds=result.uptime_seconds,
        ).model_dump()

        if status_code == 503:
            response = JSONResponse(content=response_data, status_code=503)
        else:
            response = JSONResponse(content=response_data)

        # Add cache and timing headers
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
        response.headers["Cache-Control"] = f"max-age={health_service.cache.ttl_seconds}"

        return response

    except Exception as e:
        logger.error(f"Readiness check failed: {e}")

        error_response = {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": time.time()
        }

        response = JSONResponse(content=error_response, status_code=503)
        response.headers["Cache-Control"] = "no-cache"

        return response


@router.get("/health/metrics", response_model=MetricsResponse)
async def health_metrics() -> dict[str, Any]:
    """
    Health check metrics endpoint for monitoring systems
    """
    try:
        health_service = get_health_service()

        # Get current health status
        result = await health_service.readiness_check()

        # Calculate health score (0-100)
        health_score = 100
        degraded_count = 0
        unhealthy_count = 0

        for _check_name, check_data in result.checks.items():
            if isinstance(check_data, dict) and "status" in check_data:
                status = check_data["status"]
                if status == DependencyStatus.UNHEALTHY:
                    unhealthy_count += 1
                    health_score -= 30
                elif status == DependencyStatus.DEGRADED:
                    degraded_count += 1
                    health_score -= 10

        health_score = max(0, health_score)  # Don't go below 0

        return MetricsResponse(
            health_score=health_score,
            dependency_count=len(result.checks),
            healthy_dependencies=len(result.checks) - degraded_count - unhealthy_count,
            degraded_dependencies=degraded_count,
            unhealthy_dependencies=unhealthy_count,
            uptime_seconds=result.uptime_seconds,
            last_check_duration_ms=result.performance_metrics.get("check_duration_ms", 0),
            cache_hit_ratio=result.performance_metrics.get("cache_hit_ratio", 0),
            timestamp=result.timestamp.isoformat(),
        ).model_dump()

    except Exception as e:
        logger.exception("Health metrics failed: %s", e)
        return MetricsResponse(
            health_score=0,
            dependency_count=0,
            healthy_dependencies=0,
            degraded_dependencies=0,
            unhealthy_dependencies=0,
            uptime_seconds=0,
            last_check_duration_ms=0,
            cache_hit_ratio=0,
            timestamp=str(time.time()),
            error=str(e),
        ).model_dump()


@router.post("/health/cache/invalidate")
async def invalidate_health_cache() -> dict[str, str]:
    """
    Invalidate health check cache - useful for forced re-checks
    """
    try:
        health_service = get_health_service()
        health_service.invalidate_cache()

        return {
            "status": "success",
            "message": "Health check cache invalidated"
        }

    except Exception as e:
        logger.error(f"Cache invalidation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "message": str(e)
            }
        )
