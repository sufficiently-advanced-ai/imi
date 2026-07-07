"""
Production Monitoring API endpoints - Issue #398

Provides API endpoints for:
- Rate limiter statistics
- Request validation metrics
- Performance monitoring reports
- Circuit breaker status
- Lifecycle health status
"""

import logging
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/monitoring/rate-limits")
async def get_rate_limit_stats() -> dict[str, Any]:
    """Get rate limiting statistics"""
    try:
        from ..core.middleware.rate_limiter import get_rate_limiter
        limiter = get_rate_limiter()
        return limiter.get_stats()
    except Exception as e:
        logger.error(f"Failed to get rate limit stats: {e}")
        return {"error": str(e)}


@router.get("/api/monitoring/request-validation")
async def get_request_validation_stats() -> dict[str, Any]:
    """Get request validation statistics"""
    try:
        from ..core.middleware.request_validator import get_request_validator
        validator = get_request_validator()
        return validator.get_stats()
    except Exception as e:
        logger.error(f"Failed to get request validation stats: {e}")
        return {"error": str(e)}


@router.get("/api/monitoring/performance")
async def get_performance_report() -> dict[str, Any]:
    """Get performance monitoring report"""
    try:
        from ..core.middleware.performance_monitor import get_performance_monitor
        monitor = get_performance_monitor()
        return monitor.get_performance_report()
    except Exception as e:
        logger.error(f"Failed to get performance report: {e}")
        return {"error": str(e)}


@router.get("/api/monitoring/circuit-breakers")
async def get_circuit_breaker_status() -> dict[str, Any]:
    """Get circuit breaker status for all services"""
    try:
        from ..services.circuit_breaker import get_service_registry
        registry = get_service_registry()
        return registry.get_health_status()
    except Exception as e:
        logger.error(f"Failed to get circuit breaker status: {e}")
        return {"error": str(e)}


@router.get("/api/monitoring/lifecycle")
async def get_lifecycle_status() -> dict[str, Any]:
    """Get application lifecycle status"""
    try:
        from ..core.lifecycle import get_lifecycle_manager
        manager = get_lifecycle_manager()
        return manager.get_status()
    except Exception as e:
        logger.error(f"Failed to get lifecycle status: {e}")
        return {"error": str(e)}


@router.get("/api/monitoring/overview")
async def get_monitoring_overview() -> dict[str, Any]:
    """Get comprehensive monitoring overview"""
    try:
        # Collect all monitoring data
        overview = {}

        # Rate limiting
        try:
            from ..core.middleware.rate_limiter import get_rate_limiter
            limiter = get_rate_limiter()
            overview["rate_limiting"] = limiter.get_stats()
        except Exception as e:
            overview["rate_limiting"] = {"error": str(e)}

        # Request validation
        try:
            from ..core.middleware.request_validator import get_request_validator
            validator = get_request_validator()
            overview["request_validation"] = validator.get_stats()
        except Exception as e:
            overview["request_validation"] = {"error": str(e)}

        # Performance
        try:
            from ..core.middleware.performance_monitor import get_performance_monitor
            monitor = get_performance_monitor()
            perf_report = monitor.get_performance_report()
            overview["performance"] = {
                "summary": perf_report.get("summary", {}),
                "memory": perf_report.get("memory", {}),
                "slow_requests": perf_report.get("summary", {}).get("slow_requests", 0)
            }
        except Exception as e:
            overview["performance"] = {"error": str(e)}

        # Circuit breakers
        try:
            from ..services.circuit_breaker import get_service_registry
            registry = get_service_registry()
            cb_status = registry.get_health_status()
            overview["circuit_breakers"] = {
                "total_services": len(cb_status),
                "open_circuits": len([
                    name for name, status in cb_status.items()
                    if status.get("state") == "open"
                ]),
                "services": list(cb_status.keys())
            }
        except Exception as e:
            overview["circuit_breakers"] = {"error": str(e)}

        # Lifecycle
        try:
            from ..core.lifecycle import get_lifecycle_manager
            manager = get_lifecycle_manager()
            lifecycle_status = manager.get_status()
            overview["lifecycle"] = {
                "state": lifecycle_status.get("state"),
                "background_tasks": lifecycle_status.get("background_tasks", 0),
                "active_tasks": lifecycle_status.get("active_background_tasks", 0)
            }
        except Exception as e:
            overview["lifecycle"] = {"error": str(e)}

        return overview

    except Exception as e:
        logger.error(f"Failed to get monitoring overview: {e}")
        return {"error": str(e)}


@router.post("/api/monitoring/circuit-breakers/{service_name}/reset")
async def reset_circuit_breaker(service_name: str) -> dict[str, Any]:
    """Reset a specific circuit breaker"""
    try:
        from ..services.circuit_breaker import get_service_registry
        registry = get_service_registry()

        success = registry.reset_circuit_breaker(service_name)

        if success:
            return {
                "status": "success",
                "message": f"Circuit breaker for {service_name} has been reset"
            }
        else:
            return {
                "status": "error",
                "message": f"Circuit breaker for {service_name} not found"
            }
    except Exception as e:
        logger.error(f"Failed to reset circuit breaker {service_name}: {e}")
        return {"error": str(e)}


@router.post("/api/monitoring/circuit-breakers/reset-all")
async def reset_all_circuit_breakers() -> dict[str, Any]:
    """Reset all circuit breakers"""
    try:
        from ..services.circuit_breaker import get_service_registry
        registry = get_service_registry()

        registry.reset_all_circuit_breakers()

        return {
            "status": "success",
            "message": "All circuit breakers have been reset"
        }
    except Exception as e:
        logger.error(f"Failed to reset all circuit breakers: {e}")
        return {"error": str(e)}
