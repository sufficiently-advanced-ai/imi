"""
Telemetry Monitoring and Alerting Routes
Provides endpoints for monitoring telemetry health, performance, and statistics
"""

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..config import get_config
from ..services.telemetry_manager import get_telemetry_manager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/telemetry/health")
async def telemetry_health_check(verbose: bool = Query(False, description="Include detailed statistics")):
    """
    Health check endpoint for telemetry system.
    Returns telemetry status, configuration, and performance metrics.
    """
    try:
        telemetry_manager = get_telemetry_manager()
        config = get_config().telemetry

        health_status = {
            "status": "healthy" if telemetry_manager._initialized else "not_initialized",
            "timestamp": time.time(),
            "enabled": config.enabled,
            "environment": config.environment,
            "service_name": config.service_name,
            "sampling_enabled": config.sampling_enabled,
        }

        if verbose:
            # Add detailed statistics
            detailed_stats = telemetry_manager.get_telemetry_stats()
            health_status.update(detailed_stats)

        return health_status

    except Exception as e:
        logger.error(f"Telemetry health check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Telemetry health check failed: {e}")


@router.get("/api/telemetry/stats")
async def get_telemetry_statistics():
    """
    Get comprehensive telemetry statistics including sampling and performance metrics.
    """
    try:
        telemetry_manager = get_telemetry_manager()
        return telemetry_manager.get_telemetry_stats()

    except Exception as e:
        logger.error(f"Failed to get telemetry statistics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get telemetry statistics: {e}")


@router.get("/api/telemetry/config")
async def get_telemetry_configuration():
    """
    Get current telemetry configuration (non-sensitive values only).
    """
    try:
        config = get_config().telemetry

        # Return non-sensitive configuration values
        return {
            "enabled": config.enabled,
            "service_name": config.service_name,
            "service_version": config.service_version,
            "environment": config.environment,
            "client_name": config.client_name,
            "sampling": {
                "enabled": config.sampling_enabled,
                "default_rate": config.default_sample_rate,
                "error_rate": config.error_sample_rate,
                "llm_rate": config.llm_sample_rate,
                "webhook_rate": config.webhook_sample_rate,
            },
            "pii_protection": {
                "enabled": config.pii_scrubbing_enabled,
                "max_attribute_length": config.max_attribute_length,
                "max_span_attributes": config.max_span_attributes,
                "scrub_user_data": config.scrub_user_data,
            },
            "performance": {
                "async_export": config.async_export,
                "export_interval": config.export_interval,
                "max_batch_size": config.max_export_batch_size,
                "overhead_limit": config.performance_overhead_limit,
            },
            "endpoint": config.endpoint.replace("localhost", "[LOCAL]"),  # Mask internal endpoints
        }

    except Exception as e:
        logger.error(f"Failed to get telemetry configuration: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get telemetry configuration: {e}")


@router.get("/api/telemetry/sampling/stats")
async def get_sampling_statistics():
    """
    Get detailed sampling statistics by operation type.
    """
    try:
        telemetry_manager = get_telemetry_manager()

        if not telemetry_manager._sampler:
            return {"error": "Sampling not initialized"}

        stats = telemetry_manager._sampler.get_stats()

        # Add sampling rate analysis
        config = get_config().telemetry
        sampling_analysis = {
            "configured_rates": {
                "default": config.default_sample_rate,
                "error": config.error_sample_rate,
                "llm": config.llm_sample_rate,
                "webhook": config.webhook_sample_rate,
                "high_priority": config.high_priority_sample_rate,
            },
            "effective_rates": {},
            "data_volume_reduction": 0.0,
        }

        # Calculate effective rates and data reduction
        if stats["total_requests"] > 0:
            for operation, count in stats["operation_stats"].items():
                effective_rate = count / stats["total_requests"]
                sampling_analysis["effective_rates"][operation] = effective_rate

            # Calculate overall data volume reduction
            sampling_analysis["data_volume_reduction"] = 1.0 - stats["sample_rate"]

        return {
            "sampling_stats": stats,
            "sampling_analysis": sampling_analysis,
        }

    except Exception as e:
        logger.error(f"Failed to get sampling statistics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get sampling statistics: {e}")


@router.get("/api/telemetry/performance")
async def get_performance_metrics():
    """
    Get telemetry performance impact metrics.
    """
    try:
        telemetry_manager = get_telemetry_manager()
        stats = telemetry_manager.get_telemetry_stats()

        performance_data = stats.get("performance", {})
        config = get_config().telemetry

        # Add performance analysis
        performance_analysis = {
            "within_overhead_limit": performance_data.get("within_limit", True),
            "overhead_limit_ms": config.performance_overhead_limit * 1000,
            "overhead_status": "optimal" if performance_data.get("within_limit", True) else "elevated",
        }

        return {
            "performance_metrics": performance_data,
            "performance_analysis": performance_analysis,
            "recommendations": _generate_performance_recommendations(performance_data, config),
        }

    except Exception as e:
        logger.error(f"Failed to get performance metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get performance metrics: {e}")


@router.post("/api/telemetry/test/sample")
async def test_sampling_decision(
    operation_type: str = Query(..., description="Operation type to test"),
    is_error: bool = Query(False, description="Whether this is an error scenario")
):
    """
    Test sampling decision for a specific operation type.
    Useful for debugging sampling configuration.
    """
    try:
        telemetry_manager = get_telemetry_manager()
        config = get_config().telemetry

        # Get configured sampling rate
        configured_rate = config.get_sample_rate_for_operation(operation_type, is_error)

        # Test actual sampling decision
        should_sample = telemetry_manager.should_sample_operation(operation_type, is_error)

        return {
            "operation_type": operation_type,
            "is_error": is_error,
            "configured_sample_rate": configured_rate,
            "should_sample": should_sample,
            "sampling_enabled": config.sampling_enabled,
            "explanation": _explain_sampling_decision(operation_type, is_error, configured_rate, should_sample),
        }

    except Exception as e:
        logger.error(f"Failed to test sampling decision: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to test sampling decision: {e}")


def _generate_performance_recommendations(performance_data: dict[str, Any], config) -> list[str]:
    """Generate performance optimization recommendations."""
    recommendations = []

    avg_overhead = performance_data.get("avg_overhead_ms", 0)
    max_overhead = performance_data.get("max_overhead_ms", 0)
    limit_ms = config.performance_overhead_limit * 1000

    if avg_overhead > limit_ms:
        recommendations.append(f"Average overhead ({avg_overhead:.2f}ms) exceeds limit ({limit_ms}ms). Consider reducing sampling rates.")

    if max_overhead > limit_ms * 5:
        recommendations.append(f"Maximum overhead ({max_overhead:.2f}ms) is very high. Check for blocking operations in telemetry code.")

    if performance_data.get("sample_count", 0) < 100:
        recommendations.append("Insufficient performance samples for accurate analysis. Continue monitoring.")

    if not config.async_export:
        recommendations.append("Consider enabling async export (TELEMETRY_ASYNC_EXPORT=true) for better performance.")

    if config.export_interval < 1000:
        recommendations.append("Export interval is very short. Consider increasing TELEMETRY_EXPORT_INTERVAL for better performance.")

    if not recommendations:
        recommendations.append("Performance metrics are within acceptable limits.")

    return recommendations


def _explain_sampling_decision(operation_type: str, is_error: bool, configured_rate: float, should_sample: bool) -> str:
    """Provide human-readable explanation of sampling decision."""
    if not should_sample:
        return "Request rejected due to telemetry being disabled."

    if is_error:
        return f"Error scenario - sampled at {configured_rate*100}% rate (error-first sampling)."

    if configured_rate >= 1.0:
        return f"Operation type '{operation_type}' configured for 100% sampling."

    if configured_rate <= 0.0:
        return f"Operation type '{operation_type}' configured to reject all samples."

    return f"Operation type '{operation_type}' sampled at {configured_rate*100}% rate."


@router.get("/api/telemetry/alerts")
async def get_telemetry_alerts():
    """
    Get current telemetry alerts and warnings.
    """
    try:
        telemetry_manager = get_telemetry_manager()
        config = get_config().telemetry
        stats = telemetry_manager.get_telemetry_stats()

        alerts = []
        warnings = []

        # Check if telemetry is disabled
        if not config.enabled:
            warnings.append({
                "type": "telemetry_disabled",
                "message": "Telemetry is disabled",
                "severity": "warning",
                "timestamp": time.time(),
            })

        # Check if telemetry failed to initialize
        if config.enabled and not telemetry_manager._initialized:
            alerts.append({
                "type": "initialization_failed",
                "message": "Telemetry failed to initialize",
                "severity": "error",
                "timestamp": time.time(),
            })

        # Check performance overhead
        performance_data = stats.get("performance", {})
        if performance_data.get("avg_overhead_ms", 0) > config.performance_overhead_limit * 1000:
            alerts.append({
                "type": "high_overhead",
                "message": f"Telemetry overhead ({performance_data['avg_overhead_ms']:.2f}ms) exceeds limit",
                "severity": "warning",
                "timestamp": time.time(),
            })

        # Check sampling effectiveness
        sampling_stats = stats.get("sampling", {})
        if sampling_stats and sampling_stats.get("sample_rate", 0) > 0.5:
            warnings.append({
                "type": "high_sampling_rate",
                "message": f"High sampling rate ({sampling_stats['sample_rate']*100:.1f}%) may impact performance",
                "severity": "info",
                "timestamp": time.time(),
            })

        # Check error rate
        error_rate = sampling_stats.get("error_rate", 0)
        if error_rate > 0.1:
            alerts.append({
                "type": "high_error_rate",
                "message": f"High error rate ({error_rate*100:.1f}%) detected in telemetry sampling",
                "severity": "warning",
                "timestamp": time.time(),
            })

        return {
            "alerts": alerts,
            "warnings": warnings,
            "alert_count": len(alerts),
            "warning_count": len(warnings),
            "last_check": time.time(),
        }

    except Exception as e:
        logger.error(f"Failed to get telemetry alerts: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get telemetry alerts: {e}")
