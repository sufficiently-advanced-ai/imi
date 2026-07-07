"""
OpenTelemetry Metrics Setup for imi
Implements custom metrics required by Grafana dashboards
"""

import os

# Type imports
from urllib.parse import urlsplit, urlunsplit

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import Counter, Histogram, Meter, MeterProvider, UpDownCounter
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

# Global metric instances
llm_tokens_counter: Counter | None = None
llm_cost_counter: Counter | None = None
documents_counter: Counter | None = None
entities_counter: Counter | None = None
active_connections_gauge: UpDownCounter | None = None
error_counter: Counter | None = None
meter: Meter | None = None
_metrics_initialized: bool = False

# Conversation tracking metrics - Issue #525
llm_conversation_length_histogram: Histogram | None = None
llm_conversation_duration_histogram: Histogram | None = None
llm_conversation_rapid_questions_counter: Counter | None = None
llm_conversation_abandoned_counter: Counter | None = None


def setup_metrics():
    """Initialize OpenTelemetry metrics with OTLP export"""
    global \
        llm_tokens_counter, \
        llm_cost_counter, \
        documents_counter, \
        entities_counter, \
        active_connections_gauge, \
        error_counter, \
        meter, \
        _metrics_initialized, \
        llm_conversation_length_histogram, \
        llm_conversation_duration_histogram, \
        llm_conversation_rapid_questions_counter, \
        llm_conversation_abandoned_counter

    # Prevent multiple initializations
    if _metrics_initialized:
        return

    # Check if production telemetry manager is handling metrics
    try:
        from .services.telemetry_manager import get_telemetry_manager
        telemetry_manager = get_telemetry_manager()
        if telemetry_manager._initialized and telemetry_manager.config.enabled:
            print("Using production telemetry manager for metrics")
            # Use the meter from telemetry manager. `metrics` is the module-level
            # import (top of file); re-importing it here would rebind it as a
            # function-local, making every other `metrics.*` reference in this
            # function raise UnboundLocalError on paths that skip this branch.
            meter = metrics.get_meter("imi", version="1.0.0")
            _setup_legacy_metrics_with_production_meter()
            return
    except ImportError:
        pass

    # Skip if metrics are disabled
    if os.getenv("OTEL_METRICS_EXPORTER", "none") == "none":
        print("Metrics exporter disabled, skipping metrics setup")
        return

    try:
        # Create resource with required attributes
        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "imi"),
                "service.instance.id": os.getenv("OTEL_SERVICE_INSTANCE_ID", "unknown"),
                "client.name": os.getenv("CLIENT_NAME", "unknown"),
                "deployment.environment": os.getenv("DEPLOY_ENV", "development"),
                "service.namespace": "imi",
                "service.version": os.getenv("OTEL_SERVICE_VERSION", "1.0.0"),
            }
        )

        # Setup metrics provider with OTLP exporter
        if os.getenv("OTEL_METRICS_EXPORTER") == "otlp":
            # An explicitly-empty OTEL_EXPORTER_OTLP_ENDPOINT (as the compose
            # files pass by default) overrides the os.getenv default. The OTLP
            # HTTP exporter only supports http/https; an empty or otherwise
            # invalid endpoint would make it raise MissingSchema on every
            # export, flooding the logs. Guard before constructing it.
            endpoint = os.getenv(
                "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"
            ).strip()
            parsed = urlsplit(endpoint)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                print(
                    "OTLP metrics exporter disabled: OTEL_EXPORTER_OTLP_ENDPOINT is "
                    "unset or not an http(s) URL (expected an absolute URL like "
                    "http://host:4318)"
                )
            else:
                # Ensure endpoint has the metrics path (tolerate a trailing slash
                # so we don't double up the path: ".../v1/metrics/" must not
                # become ".../v1/metrics/v1/metrics").
                endpoint = endpoint.rstrip("/")
                if not endpoint.endswith("/v1/metrics"):
                    endpoint += "/v1/metrics"

                metric_reader = PeriodicExportingMetricReader(
                    exporter=OTLPMetricExporter(endpoint=endpoint, headers={}),
                    export_interval_millis=int(
                        os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "10000")
                    ),
                )
                metrics.set_meter_provider(
                    MeterProvider(resource=resource, metric_readers=[metric_reader])
                )
                # Log only scheme/host/port/path — never userinfo or query params,
                # which can carry credentials when set via OTEL_EXPORTER_OTLP_ENDPOINT.
                safe = urlsplit(endpoint)
                safe_netloc = safe.hostname or ""
                if safe.port:
                    safe_netloc += f":{safe.port}"
                redacted = urlunsplit((safe.scheme, safe_netloc, safe.path, "", ""))
                print(f"Metrics configured with OTLP export to {redacted}")

        # Get meter instance
        meter = metrics.get_meter("imi", version="1.0.0")

        # Create metrics required by Grafana dashboards
        llm_tokens_counter = meter.create_counter(
            "llm_tokens_used_total", unit="tokens", description="Total LLM tokens used"
        )

        llm_cost_counter = meter.create_counter(
            "llm_token_cost_total", unit="USD", description="Total cost of LLM tokens"
        )

        documents_counter = meter.create_counter(
            "documents_processed_total",
            unit="documents",
            description="Total documents processed",
        )

        entities_counter = meter.create_counter(
            "entities_discovered_total",
            unit="entities",
            description="Total entities discovered",
        )

        active_connections_gauge = meter.create_up_down_counter(
            "active_sse_connections",
            unit="connections",
            description="Active SSE connections",
        )

        error_counter = meter.create_counter(
            "errors_total",
            unit="errors",
            description="Total errors by type and source",
        )

        # Conversation tracking metrics - Issue #525
        llm_conversation_length_histogram = meter.create_histogram(
            "llm_conversation_length_turns",
            unit="turns",
            description="Distribution of conversation lengths in turns",
        )

        llm_conversation_duration_histogram = meter.create_histogram(
            "llm_conversation_duration_seconds",
            unit="seconds",
            description="Duration of completed conversations",
        )

        llm_conversation_rapid_questions_counter = meter.create_counter(
            "llm_conversation_rapid_questions_total",
            unit="conversations",
            description="Conversations with rapid-fire question patterns",
        )

        llm_conversation_abandoned_counter = meter.create_counter(
            "llm_conversation_abandoned_total",
            unit="conversations",
            description="Conversations that were abandoned without completion",
        )

        print("OpenTelemetry metrics initialized successfully")
        _metrics_initialized = True

    except (ImportError, AttributeError, ValueError, RuntimeError) as e:
        print(f"Failed to initialize OpenTelemetry metrics: {e}")
        # Don't fail the application if metrics setup fails


def _setup_legacy_metrics_with_production_meter():
    """Setup legacy metrics using production telemetry meter."""
    global \
        llm_tokens_counter, \
        llm_cost_counter, \
        documents_counter, \
        entities_counter, \
        active_connections_gauge, \
        error_counter, \
        meter, \
        _metrics_initialized, \
        llm_conversation_length_histogram, \
        llm_conversation_duration_histogram, \
        llm_conversation_rapid_questions_counter, \
        llm_conversation_abandoned_counter

    try:
        from opentelemetry import metrics
        meter = metrics.get_meter("imi", version="1.0.0")

        # Create metrics required by legacy code
        llm_tokens_counter = meter.create_counter(
            "llm_tokens_used_total", unit="tokens", description="Total LLM tokens used"
        )

        llm_cost_counter = meter.create_counter(
            "llm_token_cost_total", unit="USD", description="Total cost of LLM tokens"
        )

        documents_counter = meter.create_counter(
            "documents_processed_total",
            unit="documents",
            description="Total documents processed",
        )

        entities_counter = meter.create_counter(
            "entities_discovered_total",
            unit="entities",
            description="Total entities discovered",
        )

        active_connections_gauge = meter.create_up_down_counter(
            "active_sse_connections",
            unit="connections",
            description="Active SSE connections",
        )

        error_counter = meter.create_counter(
            "errors_total",
            unit="errors",
            description="Total errors by type and source",
        )

        # Conversation tracking metrics - Issue #525
        llm_conversation_length_histogram = meter.create_histogram(
            "llm_conversation_length_turns",
            unit="turns",
            description="Distribution of conversation lengths in turns",
        )

        llm_conversation_duration_histogram = meter.create_histogram(
            "llm_conversation_duration_seconds",
            unit="seconds",
            description="Duration of completed conversations",
        )

        llm_conversation_rapid_questions_counter = meter.create_counter(
            "llm_conversation_rapid_questions_total",
            unit="conversations",
            description="Conversations with rapid-fire question patterns",
        )

        llm_conversation_abandoned_counter = meter.create_counter(
            "llm_conversation_abandoned_total",
            unit="conversations",
            description="Conversations that were abandoned without completion",
        )

        print("Legacy metrics integrated with production telemetry")
        _metrics_initialized = True

    except (ImportError, AttributeError, ValueError, RuntimeError) as e:
        print(f"Failed to setup legacy metrics with production telemetry: {e}")


def record_llm_usage(
    model: str, operation: str, input_tokens: int, output_tokens: int, cost: float
):
    """Record LLM token usage and cost metrics"""
    # Ensure metrics are initialized
    global llm_tokens_counter, llm_cost_counter
    if llm_tokens_counter is None:
        setup_metrics()

    client_name = os.getenv("CLIENT_NAME", "unknown")
    if llm_tokens_counter:
        total_tokens = input_tokens + output_tokens
        llm_tokens_counter.add(
            total_tokens,
            {"model": model, "operation": operation, "client_name": client_name},
        )

    if llm_cost_counter and cost > 0:
        llm_cost_counter.add(
            cost, {"model": model, "operation": operation, "client_name": client_name}
        )


def record_document_processed(doc_type: str):
    """Record a document being processed"""
    global documents_counter
    if documents_counter is None:
        setup_metrics()

    if documents_counter:
        documents_counter.add(
            1, {"type": doc_type, "client_name": os.getenv("CLIENT_NAME", "unknown")}
        )


def record_entities_discovered(entity_type: str, count: int = 1):
    """Record entities being discovered"""
    global entities_counter
    if entities_counter is None:
        setup_metrics()

    if entities_counter:
        entities_counter.add(
            count,
            {"type": entity_type, "client_name": os.getenv("CLIENT_NAME", "unknown")},
        )




def record_background_task_duration(task_name: str, duration: float, status: str):
    """Record background task execution duration"""
    global background_task_duration_histogram, meter
    if background_task_duration_histogram is None:
        setup_metrics()
        if meter:
            background_task_duration_histogram = meter.create_histogram(
                "background_task_duration_seconds",
                unit="seconds",
                description="Duration of background task execution",
            )

    if background_task_duration_histogram:
        background_task_duration_histogram.record(
            duration,
            {
                "task_name": task_name,
                "status": status,
                "client_name": os.getenv("CLIENT_NAME", "unknown"),
            },
        )


def record_background_task_error(task_name: str, error_type: str):
    """Record background task errors"""
    global background_task_error_counter, meter
    if background_task_error_counter is None:
        setup_metrics()
        if meter:
            background_task_error_counter = meter.create_counter(
                "background_task_errors_total",
                unit="errors",
                description="Total background task errors",
            )

    if background_task_error_counter:
        background_task_error_counter.add(
            1,
            {
                "task_name": task_name,
                "error_type": error_type,
                "client_name": os.getenv("CLIENT_NAME", "unknown"),
            },
        )


def record_background_task_operation(task_name: str, operation: str, count: int):
    """Record background task operations"""
    global background_task_operation_counter, meter
    if background_task_operation_counter is None:
        setup_metrics()
        if meter:
            background_task_operation_counter = meter.create_counter(
                "background_task_operations_total",
                unit="operations",
                description="Total background task operations",
            )

    if background_task_operation_counter:
        background_task_operation_counter.add(
            count,
            {
                "task_name": task_name,
                "operation": operation,
                "client_name": os.getenv("CLIENT_NAME", "unknown"),
            },
        )


def record_error_metric(error_type: str, source: str, retryable: bool = False):
    """Record error metrics with consistent labeling"""
    global error_counter
    if error_counter is None:
        setup_metrics()

    if error_counter:
        error_counter.add(
            1,
            {
                "error_type": error_type,
                "source": source,
                "retryable": str(retryable).lower(),
            },
        )


# GitHub specific metrics - Issue #524
github_api_requests_counter = None
github_api_request_duration_histogram = None
github_webhook_processing_counter = None
github_repository_operations_counter = None
github_commit_analysis_counter = None










def setup_github_metrics():
    """Initialize GitHub specific metrics"""
    global github_api_requests_counter, github_api_request_duration_histogram, \
           github_webhook_processing_counter, github_repository_operations_counter, \
           github_commit_analysis_counter, meter

    # Ensure base metrics are initialized first
    if not _metrics_initialized:
        setup_metrics()

    # Skip if metrics are disabled
    if os.getenv("OTEL_METRICS_EXPORTER", "none") == "none" or meter is None:
        return

    try:
        # GitHub API request counter with method, event_type, and status labels
        github_api_requests_counter = meter.create_counter(
            "github_api_requests_total",
            unit="requests",
            description="Total GitHub API requests by method, event type, and status"
        )

        # GitHub API request duration histogram with method labels
        github_api_request_duration_histogram = meter.create_histogram(
            "github_api_request_duration_seconds",
            unit="seconds",
            description="Duration of GitHub API requests by method"
        )

        # GitHub webhook processing counter with event type and status labels
        github_webhook_processing_counter = meter.create_counter(
            "github_webhook_processing_total",
            unit="webhooks",
            description="Total GitHub webhook processing by event type and status"
        )

        # GitHub repository operations counter
        github_repository_operations_counter = meter.create_counter(
            "github_repository_operations_total",
            unit="operations",
            description="Total GitHub repository operations (get_repository, etc.)"
        )

        # GitHub commit analysis counter with operation type
        github_commit_analysis_counter = meter.create_counter(
            "github_commit_analysis_total",
            unit="operations",
            description="Total GitHub commit analysis operations by type"
        )

        print("GitHub specific metrics initialized successfully")

    except (ImportError, AttributeError, ValueError, RuntimeError) as e:
        print(f"Failed to initialize GitHub specific metrics: {e}")


def record_github_api_request(method: str, event_type: str = "unknown", status: str = "success", duration: float = 0.0):
    """Record GitHub API request metrics"""
    global github_api_requests_counter, github_api_request_duration_histogram

    # Initialize if needed
    if github_api_requests_counter is None:
        setup_github_metrics()

    # Record request counter
    if github_api_requests_counter:
        github_api_requests_counter.add(1, {
            "method": method,
            "event_type": event_type,
            "status": status
        })

    # Record duration histogram
    if github_api_request_duration_histogram and duration > 0:
        github_api_request_duration_histogram.record(duration, {
            "method": method
        })


def record_github_webhook_processing(event_type: str, status: str, files_processed: int = 0):
    """Record GitHub webhook processing metrics"""
    global github_webhook_processing_counter

    # Initialize if needed
    if github_webhook_processing_counter is None:
        setup_github_metrics()

    if github_webhook_processing_counter:
        github_webhook_processing_counter.add(1, {
            "event_type": event_type,
            "status": status,
            "files_processed": str(files_processed)
        })


def record_github_repository_operation(operation: str, repository: str):
    """Record GitHub repository operation"""
    global github_repository_operations_counter

    # Initialize if needed
    if github_repository_operations_counter is None:
        setup_github_metrics()

    if github_repository_operations_counter:
        github_repository_operations_counter.add(1, {
            "operation": operation,
            "repository": repository
        })


def record_github_commit_analysis(operation: str, count: int = 1):
    """Record GitHub commit analysis operation"""
    global github_commit_analysis_counter

    # Initialize if needed
    if github_commit_analysis_counter is None:
        setup_github_metrics()

    if github_commit_analysis_counter:
        github_commit_analysis_counter.add(count, {
            "operation": operation
        })


# Conversation tracking metrics functions - Issue #525
def record_conversation_length(conversation_id: str, turn_count: int):
    """Record conversation length in turns"""
    global llm_conversation_length_histogram

    # Initialize if needed
    if llm_conversation_length_histogram is None:
        setup_metrics()

    if llm_conversation_length_histogram:
        llm_conversation_length_histogram.record(turn_count, {
            "conversation_id": conversation_id
        })


def record_conversation_duration(conversation_id: str, duration_seconds: float):
    """Record conversation duration in seconds"""
    global llm_conversation_duration_histogram

    # Initialize if needed
    if llm_conversation_duration_histogram is None:
        setup_metrics()

    if llm_conversation_duration_histogram:
        llm_conversation_duration_histogram.record(duration_seconds, {
            "conversation_id": conversation_id
        })


def record_conversation_rapid_questions():
    """Record a conversation with rapid question pattern"""
    global llm_conversation_rapid_questions_counter

    # Initialize if needed
    if llm_conversation_rapid_questions_counter is None:
        setup_metrics()

    if llm_conversation_rapid_questions_counter:
        llm_conversation_rapid_questions_counter.add(1)


def record_conversation_abandoned(conversation_id: str):
    """Record an abandoned conversation"""
    global llm_conversation_abandoned_counter

    # Initialize if needed
    if llm_conversation_abandoned_counter is None:
        setup_metrics()

    if llm_conversation_abandoned_counter:
        llm_conversation_abandoned_counter.add(1, {
            "conversation_id": conversation_id
        })
