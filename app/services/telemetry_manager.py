"""
QA/Build Telemetry Manager for imi
Implements full telemetry collection for comprehensive QA coverage and bug remediation
Sampling is disabled to ensure complete observability during development and testing
"""

import hashlib
import logging
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult
from opentelemetry.trace.status import Status, StatusCode

from ..config import get_config

logger = logging.getLogger(__name__)


@dataclass
class SamplingStats:
    """Statistics for sampling operations."""
    total_requests: int = 0
    sampled_requests: int = 0
    error_requests: int = 0
    rejected_requests: int = 0
    operation_stats: dict[str, int] = field(default_factory=dict)
    last_reset: float = field(default_factory=time.time)


@dataclass
class PerformanceMetrics:
    """Performance impact tracking."""
    total_overhead_ns: int = 0
    sample_count: int = 0
    max_overhead_ns: int = 0
    avg_overhead_ns: float = 0.0
    overhead_percentile_95: float = 0.0
    recent_overheads: deque = field(default_factory=lambda: deque(maxlen=1000))


class IntelligentSampler(Sampler):
    """
    Intelligent sampler with error-first priority and operation-aware sampling.

    Implements:
    - Error-first sampling (100% error traces)
    - Operation-specific sampling rates
    - Adaptive sampling based on load
    - Consistent trace-based sampling for related spans
    """

    def __init__(self, telemetry_config):
        self.config = telemetry_config
        self._stats = SamplingStats()
        self._stats_lock = Lock()
        self._trace_decisions = {}  # Cache for consistent decisions
        self._cache_cleanup_threshold = 10000

    def get_description(self) -> str:
        return f"IntelligentSampler(default_rate={self.config.default_sample_rate})"

    def should_sample(
        self,
        parent_context: trace.Context | None,
        trace_id: int,
        name: str,
        kind: trace.SpanKind = None,
        attributes: dict[str, Any] = None,
        links: list = None,
        trace_state: trace.TraceState = None,
    ) -> SamplingResult:
        """Make sampling decision based on intelligent rules."""

        with self._stats_lock:
            self._stats.total_requests += 1

        # Check if we have a cached decision for this trace
        trace_id_str = f"{trace_id:032x}"
        if trace_id_str in self._trace_decisions:
            decision = self._trace_decisions[trace_id_str]
            if decision == Decision.RECORD_AND_SAMPLE:
                with self._stats_lock:
                    self._stats.sampled_requests += 1
            return SamplingResult(decision)

        attributes = attributes or {}

        # Determine operation type from span name and attributes
        operation_type = self._extract_operation_type(name, attributes)

        # Check for error indicators
        is_error = self._is_error_span(attributes)
        if is_error:
            with self._stats_lock:
                self._stats.error_requests += 1

        # Get sampling rate for this operation
        sample_rate = self.config.get_sample_rate_for_operation(operation_type, is_error)

        # Make sampling decision
        decision = self._make_sampling_decision(trace_id, sample_rate)

        # Cache the decision for consistency across trace
        if len(self._trace_decisions) > self._cache_cleanup_threshold:
            self._cleanup_cache()
        self._trace_decisions[trace_id_str] = decision

        # Update statistics
        with self._stats_lock:
            if decision == Decision.RECORD_AND_SAMPLE:
                self._stats.sampled_requests += 1
                self._stats.operation_stats[operation_type] = \
                    self._stats.operation_stats.get(operation_type, 0) + 1
            else:
                self._stats.rejected_requests += 1

        return SamplingResult(decision)

    def _extract_operation_type(self, span_name: str, attributes: dict[str, Any]) -> str:
        """Extract operation type from span name and attributes."""
        span_name_lower = span_name.lower()

        # Check for specific operation patterns
        if 'claude' in span_name_lower or 'anthropic' in span_name_lower:
            return 'llm'
        elif 'webhook' in span_name_lower or 'github' in span_name_lower:
            return 'webhook'
        elif any(keyword in span_name_lower for keyword in ['critical', 'important', 'priority']):
            return 'high_priority'
        elif 'http' in span_name_lower and attributes.get('http.route', '').startswith('/api/'):
            return 'api'
        else:
            return 'default'

    def _is_error_span(self, attributes: dict[str, Any]) -> bool:
        """Check if span indicates an error condition."""
        # Check for HTTP error status codes
        http_status = attributes.get('http.status_code')
        if http_status and int(http_status) >= 400:
            return True

        # Check for error-related attributes
        error_indicators = ['error', 'exception', 'failed', 'timeout']
        for key, value in attributes.items():
            if any(indicator in key.lower() for indicator in error_indicators):
                return True
            if isinstance(value, str) and any(indicator in value.lower() for indicator in error_indicators):
                return True

        return False

    def _make_sampling_decision(self, trace_id: int, sample_rate: float) -> Decision:
        """Make consistent sampling decision for a trace ID."""
        if sample_rate >= 1.0:
            return Decision.RECORD_AND_SAMPLE
        elif sample_rate <= 0.0:
            return Decision.DROP

        # Use trace ID for consistent sampling decision
        trace_hash = hashlib.sha256(f"{trace_id}".encode()).hexdigest()
        hash_value = int(trace_hash[:8], 16) / 0xFFFFFFFF

        if hash_value < sample_rate:
            return Decision.RECORD_AND_SAMPLE
        else:
            return Decision.DROP

    def _cleanup_cache(self):
        """Clean up old trace decisions to prevent memory leaks."""
        # Keep only the most recent decisions
        keep_size = self._cache_cleanup_threshold // 2
        items = list(self._trace_decisions.items())[-keep_size:]
        self._trace_decisions = dict(items)

    def get_stats(self) -> dict[str, Any]:
        """Get sampling statistics."""
        with self._stats_lock:
            sample_rate = (
                self._stats.sampled_requests / max(1, self._stats.total_requests)
            )
            error_rate = (
                self._stats.error_requests / max(1, self._stats.total_requests)
            )

            return {
                "total_requests": self._stats.total_requests,
                "sampled_requests": self._stats.sampled_requests,
                "error_requests": self._stats.error_requests,
                "rejected_requests": self._stats.rejected_requests,
                "sample_rate": sample_rate,
                "error_rate": error_rate,
                "operation_stats": dict(self._stats.operation_stats),
                "cache_size": len(self._trace_decisions),
            }


class PIIProtectionSpanProcessor(BatchSpanProcessor):
    """Span processor with PII protection and data sanitization."""

    def __init__(self, span_exporter: SpanExporter, telemetry_config):
        self.config = telemetry_config
        super().__init__(
            span_exporter,
            max_queue_size=telemetry_config.batch_size * 4,
            schedule_delay_millis=telemetry_config.export_interval,
            export_timeout_millis=telemetry_config.export_timeout * 1000,
            max_export_batch_size=telemetry_config.max_export_batch_size,
        )

    def on_end(self, span: Span) -> None:
        """Process span before adding to batch, applying PII protection."""
        if not span or not span.context:
            return

        # Apply PII protection and sanitization
        if self.config.pii_scrubbing_enabled:
            self._sanitize_span(span)

        # Limit attribute count
        if hasattr(span, '_attributes') and len(span._attributes) > self.config.max_span_attributes:
            # Keep only the most important attributes
            important_attrs = self._select_important_attributes(span._attributes)
            span._attributes = important_attrs

        super().on_end(span)

    def _sanitize_span(self, span: Span):
        """Sanitize span data for PII protection."""
        if not hasattr(span, '_attributes'):
            return

        sanitized_attrs = {}
        for key, value in span._attributes.items():
            if self.config.should_scrub_attribute(key, value):
                sanitized_attrs[key] = "[REDACTED]"
            else:
                sanitized_attrs[key] = self.config.sanitize_attribute_value(value)

        span._attributes = sanitized_attrs

        # Sanitize span name if needed
        if hasattr(span, '_name') and isinstance(span._name, str):
            span._name = self.config.sanitize_attribute_value(span._name)

    def _select_important_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Select most important attributes when limit is exceeded."""
        # Priority order for attributes
        priority_patterns = [
            'http.method', 'http.status_code', 'http.route',
            'operation.name', 'service.name', 'error',
            'duration', 'user.id', 'request.id'
        ]

        important_attrs = {}
        remaining_slots = self.config.max_span_attributes

        # First, add high-priority attributes
        for pattern in priority_patterns:
            for key, value in attributes.items():
                if pattern in key.lower() and remaining_slots > 0:
                    important_attrs[key] = value
                    remaining_slots -= 1

        # Fill remaining slots with other attributes
        for key, value in attributes.items():
            if key not in important_attrs and remaining_slots > 0:
                important_attrs[key] = value
                remaining_slots -= 1

        return important_attrs


class HTTPNoiseFilterProcessor(PIIProtectionSpanProcessor):
    """Extends PII protection with HTTP noise span filtering.

    Drops noisy ASGI sub-spans (http send/receive) that clutter trace views
    while preserving all PII sanitization from the parent class.
    """

    NOISE_NAMES = {"http send", "http receive"}

    def on_end(self, span: Span) -> None:
        if span and span.name in self.NOISE_NAMES:
            return  # Drop noisy spans silently
        super().on_end(span)


class TelemetryManager:
    """
    QA/Build telemetry manager with complete data collection and PII protection.

    Features:
    - Full telemetry collection (no sampling) for comprehensive QA coverage
    - PII protection with configurable sanitization levels
    - Complete observability for bug detection and remediation
    - Optimized for development and testing environments
    """

    def __init__(self):
        self.config = get_config().telemetry
        self._initialized = False
        self._performance_metrics = PerformanceMetrics()
        self._sampler = None
        self._tracer_provider = None
        self._meter_provider = None
        self.logger = logging.getLogger(self.__class__.__name__)

    def initialize(self):
        """Initialize telemetry with production configuration."""
        if self._initialized or not self.config.enabled:
            return

        try:
            self.logger.info(f"Initializing production telemetry (env: {self.config.environment})")

            # Set up resource attributes
            resource = Resource.create(self.config.get_resource_attributes())

            # Initialize intelligent sampler
            self._sampler = IntelligentSampler(self.config)

            # Set up tracing
            self._setup_tracing(resource)

            # Set up metrics
            self._setup_metrics(resource)

            self._initialized = True
            self.logger.info("Production telemetry initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize telemetry: {e}")
            # Don't fail the application if telemetry setup fails

    def _resolve_otlp_endpoint(self, base: str, signal_path: str) -> str | None:
        """Build a full OTLP endpoint URL, or None if export should be disabled.

        Returns None when no usable endpoint is configured. The OTLP *HTTP*
        exporters only support http/https; a configured endpoint that is empty,
        scheme-less, or uses some other scheme (grpc://, ftp://, …) would make
        the exporter raise on every export and flood the logs. The compose files
        default OTEL_EXPORTER_OTLP_ENDPOINT to "" (empty), which overrides the
        settings default, so this guard is what keeps a bare dev stack quiet.
        """
        endpoint = (base or "").strip()
        if not endpoint:
            return None
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        # Tolerate a trailing slash so we don't double up the signal path
        # (e.g. ".../v1/traces/" must not become ".../v1/traces/v1/traces").
        endpoint = endpoint.rstrip('/')
        if not endpoint.endswith(signal_path):
            endpoint += signal_path
        return endpoint

    def _setup_tracing(self, resource: Resource):
        """Set up OpenTelemetry tracing with intelligent sampling."""

        # Create tracer provider with intelligent sampler
        self._tracer_provider = TracerProvider(
            resource=resource,
            sampler=self._sampler
        )

        # Set up OTLP span exporter
        if self.config.traces_exporter == 'otlp':
            endpoint = self._resolve_otlp_endpoint(self.config.endpoint, '/v1/traces')
            if endpoint is None:
                self.logger.info(
                    "OTLP traces exporter disabled: OTEL_EXPORTER_OTLP_ENDPOINT is "
                    "unset or not an http(s) URL (expected an absolute URL like "
                    "http://host:4318)"
                )
            else:
                span_exporter = OTLPSpanExporter(
                    endpoint=endpoint,
                    headers=self.config.headers
                )

                # Add span processor with PII protection + HTTP noise filtering
                span_processor = HTTPNoiseFilterProcessor(span_exporter, self.config)
                self._tracer_provider.add_span_processor(span_processor)

        # Set global tracer provider
        trace.set_tracer_provider(self._tracer_provider)

    def _setup_metrics(self, resource: Resource):
        """Set up OpenTelemetry metrics."""
        if self.config.metrics_exporter == 'otlp':
            endpoint = self._resolve_otlp_endpoint(self.config.endpoint, '/v1/metrics')
            if endpoint is None:
                self.logger.info(
                    "OTLP metrics exporter disabled: OTEL_EXPORTER_OTLP_ENDPOINT is "
                    "unset or not an http(s) URL (expected an absolute URL like "
                    "http://host:4318)"
                )
                return

            metric_exporter = OTLPMetricExporter(
                endpoint=endpoint,
                headers=self.config.headers
            )

            metric_reader = PeriodicExportingMetricReader(
                exporter=metric_exporter,
                export_interval_millis=self.config.export_interval,
            )

            self._meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[metric_reader]
            )

            metrics.set_meter_provider(self._meter_provider)

    @contextmanager
    def performance_tracking(self, operation_name: str):
        """Context manager for tracking telemetry performance overhead."""
        start_time = time.perf_counter_ns()
        try:
            yield
        finally:
            end_time = time.perf_counter_ns()
            overhead_ns = end_time - start_time
            self._record_performance_overhead(operation_name, overhead_ns)

    def _record_performance_overhead(self, operation: str, overhead_ns: int):
        """Record performance overhead for monitoring."""
        self._performance_metrics.total_overhead_ns += overhead_ns
        self._performance_metrics.sample_count += 1
        self._performance_metrics.max_overhead_ns = max(
            self._performance_metrics.max_overhead_ns, overhead_ns
        )

        # Update running averages
        self._performance_metrics.avg_overhead_ns = (
            self._performance_metrics.total_overhead_ns /
            self._performance_metrics.sample_count
        )

        # Track recent overheads for percentile calculation
        self._performance_metrics.recent_overheads.append(overhead_ns)

        # Update 95th percentile
        if len(self._performance_metrics.recent_overheads) >= 20:
            sorted_overheads = sorted(self._performance_metrics.recent_overheads)
            p95_index = int(0.95 * len(sorted_overheads))
            self._performance_metrics.overhead_percentile_95 = sorted_overheads[p95_index]

    def get_telemetry_stats(self) -> dict[str, Any]:
        """Get comprehensive telemetry statistics."""
        stats = {
            "enabled": self.config.enabled,
            "initialized": self._initialized,
            "environment": self.config.environment,
            "service_name": self.config.service_name,
            "sampling_enabled": self.config.sampling_enabled,
        }

        if self._sampler:
            stats["sampling"] = self._sampler.get_stats()

        # Performance metrics
        if self._performance_metrics.sample_count > 0:
            avg_overhead_ms = self._performance_metrics.avg_overhead_ns / 1_000_000
            max_overhead_ms = self._performance_metrics.max_overhead_ns / 1_000_000
            p95_overhead_ms = self._performance_metrics.overhead_percentile_95 / 1_000_000

            stats["performance"] = {
                "avg_overhead_ms": avg_overhead_ms,
                "max_overhead_ms": max_overhead_ms,
                "p95_overhead_ms": p95_overhead_ms,
                "sample_count": self._performance_metrics.sample_count,
                "within_limit": avg_overhead_ms < (self.config.performance_overhead_limit * 1000)
            }

        return stats

    def should_sample_operation(self, operation_type: str, is_error: bool = False) -> bool:
        """Check if an operation should be sampled (for manual instrumentation).

        Returns True for all operations during QA/build to ensure complete telemetry coverage.
        """
        if not self._initialized or not self.config.enabled:
            return False

        # Always sample operations for QA and bug remediation
        return True

    def create_span(self, name: str, operation_type: str = "default", attributes: dict[str, Any] = None) -> trace.Span:
        """Create a span with automatic sampling decision."""
        if not self._initialized:
            return trace.NonRecordingSpan(trace.INVALID_SPAN_CONTEXT)

        tracer = trace.get_tracer(self.config.service_name)

        # Apply PII protection to attributes
        if attributes and self.config.pii_scrubbing_enabled:
            protected_attributes = {}
            for key, value in attributes.items():
                if self.config.should_scrub_attribute(key, value):
                    protected_attributes[key] = "[REDACTED]"
                else:
                    protected_attributes[key] = self.config.sanitize_attribute_value(value)
            attributes = protected_attributes

        return tracer.start_span(name, attributes=attributes)


# Global telemetry manager instance
telemetry_manager = TelemetryManager()


def get_telemetry_manager() -> TelemetryManager:
    """Get global telemetry manager instance."""
    return telemetry_manager


def initialize_telemetry():
    """Initialize the global telemetry manager."""
    telemetry_manager.initialize()


# Convenience functions for common operations
def sample_operation(operation_type: str, is_error: bool = False) -> bool:
    """Check if operation should be sampled."""
    return telemetry_manager.should_sample_operation(operation_type, is_error)


def create_span(name: str, operation_type: str = "default", attributes: dict[str, Any] = None) -> trace.Span:
    """Create a telemetry span with sampling and PII protection."""
    return telemetry_manager.create_span(name, operation_type, attributes)


@contextmanager
def trace_operation(name: str, operation_type: str = "default", attributes: dict[str, Any] = None):
    """Context manager for tracing operations with automatic cleanup."""
    span = create_span(name, operation_type, attributes)
    try:
        with telemetry_manager.performance_tracking(operation_type):
            yield span
    except Exception as e:
        span.set_status(Status(StatusCode.ERROR, str(e)))
        raise
    finally:
        span.end()
