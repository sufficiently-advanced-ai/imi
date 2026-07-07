"""
Centralized Error Tracking Service - Issue #536
Provides comprehensive error tracking with telemetry integration.
"""

import logging
import time
from typing import Any, ClassVar

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.services.error_classifier import ErrorClassifier

logger = logging.getLogger(__name__)

# Module-level functions for easier testing and mocking
def get_tracer():
    """Get OpenTelemetry tracer instance."""
    return trace.get_tracer(__name__)

# This will be imported from metrics module
error_counter = None


class ErrorTracker:
    """
    Centralized error tracking service with telemetry integration.

    Features:
    - Error classification using ErrorClassifier
    - OpenTelemetry span creation and error recording
    - Metrics integration for error counters
    - Context sanitization to remove sensitive data
    - Graceful degradation when telemetry is unavailable
    - Thread-safe operation with minimal performance overhead
    """

    # Sensitive field names to exclude from telemetry
    SENSITIVE_FIELDS: ClassVar[set[str]] = {
        "api_key", "password", "authorization", "session_token",
        "auth_token", "secret", "token", "credential", "key"
    }

    def __init__(self, classifier: ErrorClassifier | None = None):
        """
        Initialize ErrorTracker with dependencies.

        Args:
            classifier: ErrorClassifier instance, creates new one if None
        """
        self._classifier = classifier or ErrorClassifier()
        self._tracer = None
        self._error_counter = None

        # Initialize telemetry components with graceful degradation
        self._initialize_telemetry()
        self._initialize_metrics()

    def _initialize_telemetry(self) -> None:
        """Initialize OpenTelemetry tracer with error handling."""
        try:
            self._tracer = get_tracer()
        except Exception:
            logger.exception("Failed to initialize telemetry tracer")
            self._tracer = None

    def _initialize_metrics(self) -> None:
        """Initialize metrics counter with error handling."""
        try:
            # Import here to avoid circular imports
            global error_counter
            from app.metrics import error_counter as metrics_error_counter
            error_counter = metrics_error_counter
            self._error_counter = error_counter
        except (ImportError, AttributeError) as e:
            logger.warning(f"Failed to initialize error metrics: {e}")
            self._error_counter = None

    def track_error(
        self,
        error: Exception,
        context: dict[str, Any],
        source: str
    ) -> dict[str, Any]:
        """
        Track an error with classification, telemetry, and metrics.

        Args:
            error: The exception to track
            context: Additional context about the error
            source: Source of the error (e.g., 'claude_client', 'api_endpoint')

        Returns:
            Dictionary containing error classification and tracking results
        """
        start_time = time.perf_counter()

        try:
            # Classify the error
            classification = self._classifier.classify(error)

            # Sanitize context to remove sensitive data
            sanitized_context = self._sanitize_context(context)

            # Create telemetry span
            self._create_telemetry_span(
                error, classification, sanitized_context, source
            )

            # Record metrics
            self._record_error_metrics(classification, source)

            # Add performance tracking
            duration_ms = (time.perf_counter() - start_time) * 1000
            if duration_ms > 1.0:  # Log if over 1ms
                logger.warning(f"Error tracking took {duration_ms:.2f}ms - exceeds 1ms target")

            return classification

        except Exception:
            # Never fail the application due to error tracking issues
            logger.exception("Error tracking failed")

            # Return minimal classification to prevent application failure
            return {
                "category": "unknown",
                "retryable": True,
                "recovery_strategy": "exponential_backoff"
            }

    def _sanitize_context(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        Remove sensitive data from context.

        Args:
            context: Original context dictionary

        Returns:
            Sanitized context with sensitive fields removed
        """
        if not context:
            return {}

        sanitized = {}
        for key, value in context.items():
            # Check if key contains sensitive field names
            key_lower = key.lower()
            is_sensitive = any(
                sensitive_field in key_lower
                for sensitive_field in self.SENSITIVE_FIELDS
            )

            if not is_sensitive:
                # Handle nested dictionaries recursively
                if isinstance(value, dict):
                    sanitized[key] = self._sanitize_context(value)
                elif isinstance(value, (list, tuple)):
                    sanitized[key] = [
                        self._sanitize_context(v) if isinstance(v, dict)
                        else (v if self._is_safe_value(v) else str(type(v).__name__))
                        for v in value
                    ]
                # Handle circular references and large objects
                elif self._is_safe_value(value):
                    sanitized[key] = value
                else:
                    sanitized[key] = str(type(value).__name__)

        return sanitized

    def _is_safe_value(self, value: Any) -> bool:
        """Check if a value is safe to include in telemetry."""
        # Skip very large strings or objects to prevent telemetry issues
        if isinstance(value, str) and len(value) > 1000:
            return False

        # Skip complex objects that might have circular references
        if hasattr(value, '__dict__') and not isinstance(value, (int, float, str, bool, list, tuple)):
            return False

        return True

    def _create_telemetry_span(
        self,
        error: Exception,
        classification: dict[str, Any],
        context: dict[str, Any],
        source: str
    ) -> dict[str, Any] | None:
        """
        Create OpenTelemetry span for error tracking.

        Args:
            error: The exception being tracked
            classification: Error classification results
            context: Sanitized context
            source: Error source

        Returns:
            Span information or None if telemetry unavailable
        """
        if not self._tracer:
            return None

        try:
            span_name = f"error_tracking.{classification.get('category', 'unknown')}"

            with self._tracer.start_as_current_span(span_name) as span:
                # Set error status
                span.set_status(Status(StatusCode.ERROR, str(error)))

                # Record the exception (if present)
                if error:
                    span.record_exception(error)

                # Add classification attributes
                span.set_attribute("error.type", classification.get("category", "unknown"))
                span.set_attribute("error.retryable", classification.get("retryable", False))
                span.set_attribute("error.source", source)
                span.set_attribute("error.recovery_strategy", classification.get("recovery_strategy", ""))

                # Add HTTP status code if available
                if "http_status_code" in classification:
                    span.set_attribute("http.status_code", classification["http_status_code"])

                # Add retry-after for rate limit errors
                if "retry_after" in classification:
                    span.set_attribute("error.retry_after", classification["retry_after"])

                # Add common context keys as error attributes
                if "operation" in context:
                    span.set_attribute("error.operation", context["operation"])

                # Add all context attributes with prefix
                for key, value in context.items():
                    if isinstance(value, (str, int, float, bool)):
                        span.set_attribute(f"context.{key}", value)

                return {
                    "span_id": getattr(span.get_span_context(), 'span_id', None) if hasattr(span, 'get_span_context') else None,
                    "trace_id": getattr(span.get_span_context(), 'trace_id', None) if hasattr(span, 'get_span_context') else None
                }

        except Exception:
            logger.exception("Failed to create telemetry span")
            return None

    def _record_error_metrics(self, classification: dict[str, Any], source: str) -> None:
        """
        Record error metrics for monitoring.

        Args:
            classification: Error classification results
            source: Error source
        """
        if not self._error_counter:
            return

        try:
            labels = {
                "error_type": classification.get("category", "unknown"),
                "source": (source or "unknown"),
                "retryable": str(classification.get("retryable", False)).lower()
            }

            self._error_counter.add(1, labels)

        except Exception:
            logger.exception("Failed to record error metrics")
