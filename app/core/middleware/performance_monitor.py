"""
Performance Monitoring Middleware - Issue #398

Monitors request performance with:
- X-Process-Time header for response timing
- Slow request logging
- Performance metrics collection
- Memory usage tracking
- Request/response size monitoring
"""

import logging
import time

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


@dataclass
class RequestMetrics:
    """Metrics for a single request"""
    path: str
    method: str
    duration_ms: float
    status_code: int
    request_size: int
    response_size: int
    timestamp: float
    memory_usage_mb: float


@dataclass
class PerformanceStats:
    """Aggregated performance statistics"""
    total_requests: int = 0
    total_duration_ms: float = 0
    slow_requests: int = 0
    avg_duration_ms: float = 0
    min_duration_ms: float = float('inf')
    max_duration_ms: float = 0
    p95_duration_ms: float = 0
    p99_duration_ms: float = 0

    # Memory stats
    avg_memory_mb: float = 0
    max_memory_mb: float = 0

    # Size stats
    total_request_size: int = 0
    total_response_size: int = 0
    avg_request_size: float = 0
    avg_response_size: float = 0

    # By status code
    status_codes: dict[int, int] = field(default_factory=dict)

    # By endpoint
    endpoint_stats: dict[str, dict[str, Any]] = field(default_factory=dict)


class PerformanceMonitor:
    """
    Performance monitoring with metrics collection
    """

    def __init__(
        self,
        slow_request_threshold_ms: float = 1000.0,  # 1 second
        max_stored_requests: int = 1000,
        enable_memory_tracking: bool = True
    ):
        self.slow_request_threshold_ms = slow_request_threshold_ms
        self.max_stored_requests = max_stored_requests
        self.enable_memory_tracking = enable_memory_tracking

        # Store recent requests for percentile calculations
        self.recent_requests: deque = deque(maxlen=max_stored_requests)

        # Aggregated stats
        self.stats = PerformanceStats()

        # Process info for memory tracking
        self.process = (
            psutil.Process()
            if enable_memory_tracking and PSUTIL_AVAILABLE
            else None
        )

        logger.info(
            f"Performance monitor initialized: slow_threshold={slow_request_threshold_ms}ms, "
            f"max_stored={max_stored_requests}, memory_tracking={enable_memory_tracking}"
        )

    def record_request(self, metrics: RequestMetrics) -> None:
        """Record metrics for a completed request"""

        # Store request for percentile calculations
        self.recent_requests.append(metrics)

        # Update aggregated stats
        self.stats.total_requests += 1
        self.stats.total_duration_ms += metrics.duration_ms

        # Check if slow request
        if metrics.duration_ms > self.slow_request_threshold_ms:
            self.stats.slow_requests += 1
            logger.warning(
                f"Slow request: {metrics.method} {metrics.path} took {metrics.duration_ms:.2f}ms "
                f"(threshold: {self.slow_request_threshold_ms}ms)"
            )

        # Update duration stats
        self.stats.avg_duration_ms = self.stats.total_duration_ms / self.stats.total_requests
        self.stats.min_duration_ms = min(self.stats.min_duration_ms, metrics.duration_ms)
        self.stats.max_duration_ms = max(self.stats.max_duration_ms, metrics.duration_ms)

        # Update memory stats
        if self.enable_memory_tracking:
            self.stats.avg_memory_mb = (
                (self.stats.avg_memory_mb * (self.stats.total_requests - 1) + metrics.memory_usage_mb)
                / self.stats.total_requests
            )
            self.stats.max_memory_mb = max(self.stats.max_memory_mb, metrics.memory_usage_mb)

        # Update size stats
        self.stats.total_request_size += metrics.request_size
        self.stats.total_response_size += metrics.response_size
        self.stats.avg_request_size = self.stats.total_request_size / self.stats.total_requests
        self.stats.avg_response_size = self.stats.total_response_size / self.stats.total_requests

        # Update status code stats
        self.stats.status_codes[metrics.status_code] = (
            self.stats.status_codes.get(metrics.status_code, 0) + 1
        )

        # Update endpoint-specific stats
        endpoint_key = f"{metrics.method} {metrics.path}"
        if endpoint_key not in self.stats.endpoint_stats:
            self.stats.endpoint_stats[endpoint_key] = {
                "count": 0,
                "total_duration": 0,
                "avg_duration": 0,
                "max_duration": 0,
                "slow_count": 0
            }

        endpoint_stat = self.stats.endpoint_stats[endpoint_key]
        endpoint_stat["count"] += 1
        endpoint_stat["total_duration"] += metrics.duration_ms
        endpoint_stat["avg_duration"] = endpoint_stat["total_duration"] / endpoint_stat["count"]
        endpoint_stat["max_duration"] = max(endpoint_stat["max_duration"], metrics.duration_ms)

        if metrics.duration_ms > self.slow_request_threshold_ms:
            endpoint_stat["slow_count"] += 1

        # Update percentiles (expensive, so only do occasionally)
        if self.stats.total_requests % 100 == 0:  # Every 100 requests
            self._update_percentiles()

    def _update_percentiles(self) -> None:
        """Calculate percentile statistics from recent requests"""
        if not self.recent_requests:
            return

        durations = sorted([req.duration_ms for req in self.recent_requests])
        n = len(durations)

        if n > 0:
            p95_idx = min(int(n * 0.95), n - 1)
            p99_idx = min(int(n * 0.99), n - 1)

            self.stats.p95_duration_ms = durations[p95_idx]
            self.stats.p99_duration_ms = durations[p99_idx]

    def get_memory_usage(self) -> float:
        """Get current memory usage in MB"""
        if not self.process:
            return 0.0

        try:
            # Get memory info
            memory_info = self.process.memory_info()
            return memory_info.rss / 1024 / 1024  # Convert to MB
        except Exception:  # Handle any psutil exception or if psutil is not available
            return 0.0

    def get_performance_report(self) -> dict[str, Any]:
        """Get comprehensive performance report"""
        # Ensure percentiles are current
        self._update_percentiles()

        # Get top slow endpoints
        slow_endpoints = sorted(
            [
                {
                    "endpoint": endpoint,
                    "avg_duration": stats["avg_duration"],
                    "max_duration": stats["max_duration"],
                    "slow_count": stats["slow_count"],
                    "total_count": stats["count"]
                }
                for endpoint, stats in self.stats.endpoint_stats.items()
                if stats["slow_count"] > 0
            ],
            key=lambda x: x["avg_duration"],
            reverse=True
        )[:10]  # Top 10

        return {
            "summary": {
                "total_requests": self.stats.total_requests,
                "avg_duration_ms": round(self.stats.avg_duration_ms, 2),
                "min_duration_ms": round(self.stats.min_duration_ms, 2),
                "max_duration_ms": round(self.stats.max_duration_ms, 2),
                "p95_duration_ms": round(self.stats.p95_duration_ms, 2),
                "p99_duration_ms": round(self.stats.p99_duration_ms, 2),
                "slow_requests": self.stats.slow_requests,
                "slow_request_percentage": round(
                    (self.stats.slow_requests / max(1, self.stats.total_requests)) * 100, 2
                )
            },
            "memory": {
                "current_mb": round(self.get_memory_usage(), 2),
                "avg_mb": round(self.stats.avg_memory_mb, 2),
                "max_mb": round(self.stats.max_memory_mb, 2)
            },
            "traffic": {
                "avg_request_size_bytes": round(self.stats.avg_request_size, 2),
                "avg_response_size_bytes": round(self.stats.avg_response_size, 2),
                "total_request_size_mb": round(self.stats.total_request_size / 1024 / 1024, 2),
                "total_response_size_mb": round(self.stats.total_response_size / 1024 / 1024, 2)
            },
            "status_codes": dict(self.stats.status_codes),
            "slow_endpoints": slow_endpoints,
            "configuration": {
                "slow_threshold_ms": self.slow_request_threshold_ms,
                "max_stored_requests": self.max_stored_requests,
                "memory_tracking": self.enable_memory_tracking
            }
        }


class PerformanceMonitoringMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for performance monitoring
    """

    def __init__(
        self,
        app,
        slow_request_threshold_ms: float = 1000.0,
        exclude_paths: list[str] | None = None
    ):
        super().__init__(app)
        self.monitor = PerformanceMonitor(
            slow_request_threshold_ms=slow_request_threshold_ms
        )

        # Paths to exclude from detailed monitoring
        self.exclude_paths = exclude_paths or [
            "/metrics",
            "/health",
            "/docs",
            "/redoc"
        ]

        logger.info(f"Performance monitoring middleware enabled: threshold={slow_request_threshold_ms}ms")

    async def dispatch(self, request: Request, call_next) -> Response:
        """Monitor request performance"""

        start_time = time.time()
        start_memory = self.monitor.get_memory_usage()

        # Get request size
        request_size = 0
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                request_size = int(content_length)
            except ValueError:
                pass

        # Process request
        response = await call_next(request)

        # Calculate timing
        duration_ms = (time.time() - start_time) * 1000

        # Add timing header
        response.headers["X-Process-Time"] = f"{duration_ms:.2f}ms"

        # Skip detailed monitoring for excluded paths
        if any(request.url.path.startswith(path) for path in self.exclude_paths):
            return response

        # Get response size
        response_size = 0
        if hasattr(response, 'body') and response.body:
            if isinstance(response.body, bytes):
                response_size = len(response.body)
            elif isinstance(response.body, str):
                response_size = len(response.body.encode('utf-8'))

        # Record metrics
        metrics = RequestMetrics(
            path=request.url.path,
            method=request.method,
            duration_ms=duration_ms,
            status_code=response.status_code,
            request_size=request_size,
            response_size=response_size,
            timestamp=start_time,
            memory_usage_mb=start_memory
        )

        self.monitor.record_request(metrics)

        return response


# Global monitor instance
_global_monitor: PerformanceMonitor | None = None


def get_performance_monitor() -> PerformanceMonitor:
    """Get global performance monitor instance"""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = PerformanceMonitor()
    return _global_monitor


# API endpoint for performance report
async def performance_report() -> dict[str, Any]:
    """Get current performance report"""
    monitor = get_performance_monitor()
    return monitor.get_performance_report()
