"""
Rate Limiting Middleware - Issue #398

Token bucket rate limiter with:
- Per-client tracking (IP-based)
- Configurable limits and windows
- Redis-like behavior using in-memory storage
- Headers for rate limit status
- Integration with FastAPI middleware
"""

import logging
import time
from dataclasses import dataclass, field

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


@dataclass
class TokenBucket:
    """Token bucket for rate limiting"""
    capacity: int
    tokens: float
    refill_rate: float  # tokens per second
    last_refill: float = field(default_factory=time.time)

    def consume_tokens(self, tokens: int = 1) -> bool:
        """
        Try to consume tokens from the bucket

        Returns:
            True if tokens were consumed, False if rate limit exceeded
        """
        # Refill tokens based on elapsed time
        now = time.time()
        elapsed = now - self.last_refill

        # Add tokens based on refill rate
        tokens_to_add = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill = now

        # Check if we have enough tokens
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True

        return False

    def get_reset_time(self) -> float:
        """Get time when bucket will be full again"""
        if self.tokens >= self.capacity:
            return 0

        time_to_full = (self.capacity - self.tokens) / self.refill_rate
        return time.time() + time_to_full


class RateLimiter:
    """
    Token bucket rate limiter with per-client tracking
    """

    def __init__(
        self,
        requests_per_second: float = 10.0,
        burst_capacity: int = 20,
        cleanup_interval: float = 300.0  # 5 minutes
    ):
        self.requests_per_second = requests_per_second
        self.burst_capacity = burst_capacity
        self.cleanup_interval = cleanup_interval

        # Client tracking
        self._buckets: dict[str, TokenBucket] = {}
        self._last_cleanup = time.time()

        # Stats
        self.total_requests = 0
        self.rate_limited_requests = 0

        logger.info(
            f"Rate limiter initialized: {requests_per_second} req/s, "
            f"burst: {burst_capacity}, cleanup: {cleanup_interval}s"
        )

    def _get_client_key(self, request: Request) -> str:
        """Get client identifier from request"""
        # Try to get real IP from headers (for proxy setups)
        client_ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip() or
            request.headers.get("x-real-ip") or
            getattr(request.client, "host", "unknown")
        )

        # Include user agent for better distinction
        user_agent = request.headers.get("user-agent", "")[:50]  # Truncate

        return f"{client_ip}:{hash(user_agent) % 10000}"

    def _cleanup_expired_buckets(self) -> None:
        """Remove old, unused buckets to prevent memory leaks"""
        now = time.time()
        if (now - self._last_cleanup) < self.cleanup_interval:
            return

        # Remove buckets that haven't been used recently
        cutoff_time = now - self.cleanup_interval
        expired_keys = [
            key for key, bucket in self._buckets.items()
            if bucket.last_refill < cutoff_time
        ]

        for key in expired_keys:
            del self._buckets[key]

        self._last_cleanup = now

        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired rate limit buckets")

    def is_allowed(self, request: Request) -> tuple[bool, dict[str, str]]:
        """
        Check if request is allowed through rate limiter

        Returns:
            (allowed, headers) - headers contain rate limit info
        """
        self.total_requests += 1

        # Periodic cleanup
        self._cleanup_expired_buckets()

        # Get or create bucket for client
        client_key = self._get_client_key(request)

        if client_key not in self._buckets:
            self._buckets[client_key] = TokenBucket(
                capacity=self.burst_capacity,
                tokens=self.burst_capacity,
                refill_rate=self.requests_per_second
            )

        bucket = self._buckets[client_key]

        # Try to consume a token
        allowed = bucket.consume_tokens(1)

        if not allowed:
            self.rate_limited_requests += 1

        # Prepare headers
        reset_time = bucket.get_reset_time()
        headers = {
            "X-RateLimit-Limit": str(int(self.requests_per_second * 60)),  # per minute
            "X-RateLimit-Remaining": str(int(bucket.tokens)),
            "X-RateLimit-Reset": str(int(reset_time)),
            "X-RateLimit-Policy": f"{self.requests_per_second} req/s, burst {self.burst_capacity}"
        }

        if not allowed:
            headers["Retry-After"] = str(int(1 / self.requests_per_second))  # Seconds

        return allowed, headers

    def get_stats(self) -> dict[str, any]:
        """Get rate limiter statistics"""
        return {
            "total_requests": self.total_requests,
            "rate_limited_requests": self.rate_limited_requests,
            "active_clients": len(self._buckets),
            "rate_limit_percentage": (
                (self.rate_limited_requests / max(1, self.total_requests)) * 100
            ),
            "configuration": {
                "requests_per_second": self.requests_per_second,
                "burst_capacity": self.burst_capacity,
                "cleanup_interval": self.cleanup_interval
            }
        }


class RateLimitingMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for rate limiting
    """

    def __init__(
        self,
        app,
        requests_per_second: float = 10.0,
        burst_capacity: int = 20,
        exclude_paths: list | None = None
    ):
        super().__init__(app)
        self.rate_limiter = RateLimiter(
            requests_per_second=requests_per_second,
            burst_capacity=burst_capacity
        )

        # Paths to exclude from rate limiting (e.g., health checks)
        self.exclude_paths = exclude_paths or [
            "/health",
            "/health/ready",
            "/metrics"
        ]

        logger.info(f"Rate limiting middleware enabled: {requests_per_second} req/s")

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request through rate limiter"""

        # Skip rate limiting for excluded paths
        if any(request.url.path.startswith(path) for path in self.exclude_paths):
            return await call_next(request)

        # Check rate limit
        allowed, headers = self.rate_limiter.is_allowed(request)

        if not allowed:
            # Rate limit exceeded
            logger.warning(
                f"Rate limit exceeded for {request.client.host if request.client else 'unknown'} "
                f"on {request.method} {request.url.path}"
            )

            # Return 429 Too Many Requests
            response = JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": "Too many requests. Please slow down.",
                    "retry_after": headers.get("Retry-After")
                }
            )

            # Add rate limit headers
            for key, value in headers.items():
                response.headers[key] = value

            return response

        # Process request normally
        response = await call_next(request)

        # Add rate limit headers to successful responses
        for key, value in headers.items():
            response.headers[key] = value

        return response


# Global rate limiter instance for direct access
_global_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Get global rate limiter instance"""
    global _global_rate_limiter
    if _global_rate_limiter is None:
        _global_rate_limiter = RateLimiter()
    return _global_rate_limiter


# API endpoint for rate limit status
async def rate_limit_status() -> dict[str, any]:
    """Get current rate limit statistics"""
    limiter = get_rate_limiter()
    return limiter.get_stats()
