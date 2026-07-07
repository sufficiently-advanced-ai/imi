"""
Base Orchestrator Abstract Class

Provides common patterns and infrastructure for all orchestrator implementations.
Handles error management, structured logging, telemetry integration, and
dependency injection patterns consistently across all orchestrators.

Design Philosophy:
- Orchestrators coordinate business logic, never handle HTTP directly
- Common error handling and logging patterns
- OpenTelemetry integration for observability
- Structured dependency injection
- Consistent async/await patterns
"""

import json
import logging
import traceback
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class BaseOrchestrator(ABC):
    """
    Abstract base class for all orchestrator implementations.

    Provides:
    - Structured logging with _log_operation method
    - Error handling patterns
    - OpenTelemetry integration
    - Common initialization patterns
    """

    def __init__(self):
        """Initialize base orchestrator with common dependencies."""
        self._telemetry_available = self._check_telemetry_availability()
        self._metrics_available = self._check_metrics_availability()

    def _check_telemetry_availability(self) -> bool:
        """Check if OpenTelemetry is available for tracing."""
        try:
            from opentelemetry import trace
            from opentelemetry.trace import Status, StatusCode
            self._trace = trace
            self._StatusCode = StatusCode
            self._Status = Status
            return True
        except ImportError:
            self._trace = None
            self._StatusCode = None
            self._Status = None
            return False

    def _check_metrics_availability(self) -> bool:
        """Check if metrics recording is available."""
        try:
            from ...metrics import record_github_commit_analysis, record_github_webhook_processing
            self._record_webhook_processing = record_github_webhook_processing
            self._record_commit_analysis = record_github_commit_analysis
            return True
        except ImportError:
            self._record_webhook_processing = None
            self._record_commit_analysis = None
            return False

    def _log_operation(
        self,
        operation: str,
        details: dict[str, Any],
        error: Exception | None = None
    ) -> None:
        """
        Log orchestrator operations with structured format.

        Maintains compatibility with existing _log_webhook pattern while
        being generic enough for all orchestrator types.

        Args:
            operation: Name of the operation being performed
            details: Structured details about the operation
            error: Optional exception if operation failed
        """
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "component": "orchestrator",
            "orchestrator": self.__class__.__name__,
            "operation": operation,
            "status": "error" if error else "success",
            "details": details,
        }
        if error:
            log_entry["error"] = str(error)
            log_entry["error_type"] = type(error).__name__

        if error:
            logger.error(json.dumps(log_entry))
        else:
            logger.info(json.dumps(log_entry))

    def _create_telemetry_span(self, span_name: str):
        """
        Create telemetry span if OpenTelemetry is available.

        Args:
            span_name: Name for the telemetry span

        Returns:
            Telemetry span or None if not available
        """
        if self._telemetry_available and self._trace:
            tracer = self._trace.get_tracer(__name__)
            return tracer.start_as_current_span(span_name)
        return None

    def _set_span_attributes(self, span, attributes: dict[str, Any]) -> None:
        """
        Set attributes on telemetry span if available.

        Args:
            span: Telemetry span (can be None)
            attributes: Key-value pairs to set as span attributes
        """
        if span and self._telemetry_available:
            for key, value in attributes.items():
                span.set_attribute(key, value)

    def _set_span_success(self, span) -> None:
        """Mark span as successful if telemetry is available."""
        if span and self._telemetry_available and self._Status and self._StatusCode:
            span.set_status(self._Status(self._StatusCode.OK))

    def _set_span_error(self, span, error: Exception) -> None:
        """Mark span as failed if telemetry is available."""
        if span and self._telemetry_available and self._Status and self._StatusCode:
            span.set_attribute("error_type", type(error).__name__)
            span.set_status(self._Status(self._StatusCode.ERROR, str(error)))

    def _record_metrics(self, metric_name: str, *args, **kwargs) -> None:
        """
        Record metrics if metrics system is available.

        Args:
            metric_name: Name of the metric to record
            *args, **kwargs: Arguments passed to metric recording function
        """
        if not self._metrics_available:
            return

        if metric_name == "webhook_processing" and self._record_webhook_processing:
            self._record_webhook_processing(*args, **kwargs)
        elif metric_name == "commit_analysis" and self._record_commit_analysis:
            self._record_commit_analysis(*args, **kwargs)

    async def _handle_orchestrator_error(
        self,
        operation: str,
        error: Exception,
        context: dict[str, Any] | None = None,
        span = None
    ) -> None:
        """
        Handle orchestrator errors consistently.

        Args:
            operation: Name of the operation that failed
            error: The exception that occurred
            context: Additional context for logging
            span: Optional telemetry span to mark as failed
        """
        error_details = {
            "operation": operation,
            "error": str(error),
            "error_type": type(error).__name__,
            "stacktrace": traceback.format_exc(),
        }
        if context:
            error_details.update(context)

        self._log_operation(f"{operation}_failed", error_details, error)
        self._set_span_error(span, error)

    @abstractmethod
    async def process(self, *args, **kwargs) -> Any:
        """
        Main processing method that each orchestrator must implement.

        This method should contain the core business logic for the orchestrator.
        It should NOT handle HTTP concerns like request parsing or response formatting.
        """
        pass

    def get_orchestrator_name(self) -> str:
        """Get the name of this orchestrator for logging and telemetry."""
        return self.__class__.__name__

    def get_orchestrator_version(self) -> str:
        """Get version information for this orchestrator."""
        return "1.0.0"
