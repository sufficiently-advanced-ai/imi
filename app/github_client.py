import json
import re
import sys
import time
from datetime import datetime

from fastapi import HTTPException
from github import Github

from .utils.retry import retry

# OpenTelemetry imports for telemetry instrumentation - Issue #524
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None

# Import metrics and error tracking for telemetry integration
try:
    from .metrics import record_github_api_request, record_github_repository_operation
    from .services.error_tracker import ErrorTracker

    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False
    record_github_api_request = None
    record_github_repository_operation = None
    ErrorTracker = None



class GitHubClient:
    def __init__(self, token: str, repo_name: str, webhook_secret: str):
        """Initialize GitHub client with token and repo name.

        Args:
            token: GitHub access token
            repo_name: Repository name in format "owner/repo"
            webhook_secret: Secret for validating webhooks
        """
        self.github = Github(token)
        self.repo_name = repo_name
        self.webhook_secret = webhook_secret

        # Initialize telemetry components - Issue #524
        self._error_tracker: ErrorTracker | None = None
        self._initialize_error_tracker()

        # Initialize repository access with telemetry instrumentation
        try:
            # Apply retry decorator to get_repo
            @retry(max_attempts=3, delay=2.0)
            def _get_repo():
                return self.github.get_repo(repo_name)

            self.repo = self._get_repository_with_telemetry(_get_repo)
        except (ImportError, AttributeError, RuntimeError, ValueError) as e:
            # Record failed GitHub API request in initialization error path
            if METRICS_AVAILABLE and record_github_api_request:
                # Extract status code from exception if available
                status_code = getattr(e, "status", 500) if hasattr(e, "status") else 500
                record_github_api_request(
                    f"/repos/{repo_name}",
                    "GET",
                    status_code,
                    0.0  # Duration not available in this initialization path
                )

            # Record failed repository operation
            if METRICS_AVAILABLE and record_github_repository_operation:
                record_github_repository_operation("get_repo", "error")

            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize GitHub client after retries: {e!s}",
            ) from e

    def _initialize_error_tracker(self) -> None:
        """Initialize error tracker with graceful degradation."""
        if ErrorTracker and METRICS_AVAILABLE:
            try:
                self._error_tracker = ErrorTracker()
            except (ImportError, AttributeError, ValueError, RuntimeError) as e:
                import logging
                logging.warning(f"Failed to initialize error tracker: {e}")
                self._error_tracker = None

    def _sanitize_token_for_telemetry(self, token: str) -> str:
        """Sanitize GitHub token for telemetry attributes."""
        if not token:
            return "[REDACTED]"
        if token.startswith('ghp_') or token.startswith('github_pat_'):
            return token[:6] + "***"
        return "[REDACTED]"

    def _sanitize_telemetry_attributes(self, attributes: dict) -> dict:
        """Sanitize telemetry attributes to remove sensitive data."""
        if not attributes:
            return {}

        sanitized = {}
        sensitive_keywords = {'token', 'secret', 'password', 'key', 'credential'}

        for key, value in attributes.items():
            key_lower = key.lower()
            # Check if key contains sensitive keywords
            if any(keyword in key_lower for keyword in sensitive_keywords):
                if 'token' in key_lower:
                    sanitized[key] = self._sanitize_token_for_telemetry(str(value))
                else:
                    sanitized[key] = "[REDACTED]"
            elif isinstance(value, str) and any(keyword in value.lower() for keyword in ['ghp_', 'github_pat_']):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = value

        return sanitized

    def _classify_error_for_telemetry(self, error: Exception) -> str:
        """Classify error for telemetry attributes."""
        error_str = str(error).lower()

        if "authentication" in error_str or "401" in error_str or "bad credentials" in error_str:
            return "authentication"
        elif "not found" in error_str or "404" in error_str:
            return "not_found"
        elif "rate limit" in error_str or "429" in error_str:
            return "rate_limit"
        elif "network" in error_str or "timeout" in error_str or "connection" in error_str:
            return "network"
        elif "validation" in error_str or "invalid" in error_str:
            return "validation_error"
        elif "server error" in error_str or "500" in error_str:
            return "server_error"
        else:
            return "unknown"

    def _get_repository_with_telemetry(self, get_repo_func):
        """Execute repository access with telemetry instrumentation."""
        # Create span if OpenTelemetry is available
        if OTEL_AVAILABLE and trace:
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span("github_client.get_repository") as span:
                try:
                    # Set basic span attributes
                    span.set_attribute("github.method", "get_repository")
                    span.set_attribute("github.repository", self.repo_name)

                    # Execute the repository access
                    repo = get_repo_func()

                    # Set success attributes
                    if hasattr(repo, 'default_branch'):
                        span.set_attribute("github.default_branch", repo.default_branch)
                    if hasattr(repo, 'private'):
                        span.set_attribute("github.repo_private", repo.private)

                    span.set_status(Status(StatusCode.OK))

                    # Record metrics
                    if METRICS_AVAILABLE and record_github_repository_operation:
                        record_github_repository_operation("get_repository", self.repo_name)

                    return repo

                except (ConnectionError, TimeoutError, ValueError, AttributeError) as e:
                    # Set error attributes
                    span.set_attribute("github.error_type", "repository_access_error")
                    span.set_attribute("github.error_category", self._classify_error_for_telemetry(e))
                    span.set_status(Status(StatusCode.ERROR, str(e)))

                    # Track error if error tracker is available
                    if self._error_tracker:
                        try:
                            self._error_tracker.track_error(
                                e,
                                source="github_client",
                                context={
                                    "operation": "repository_access",
                                    "repository": self.repo_name,
                                    "token_redacted": self._sanitize_token_for_telemetry("token_present")
                                }
                            )
                        except (ImportError, AttributeError, ValueError):
                            # Log but don't fail the application if error tracking fails
                            import logging
                            logging.debug("Failed to track error", exc_info=True)

                    raise
        else:
            # Fallback when telemetry is not available
            try:
                repo = get_repo_func()
                if METRICS_AVAILABLE and record_github_repository_operation:
                    record_github_repository_operation("get_repository", self.repo_name)
                return repo
            except (ConnectionError, TimeoutError, ValueError, AttributeError) as e:
                # Record GitHub API request metric for error path
                if METRICS_AVAILABLE and record_github_api_request:
                    # Extract status code from exception if available
                    status_code = getattr(e, "status", 500) if hasattr(e, "status") else 500
                    duration = 0.0  # Duration not available in this fallback path
                    record_github_api_request(
                        f"/repos/{self.repo_name}",
                        "GET",
                        str(status_code),
                        duration
                    )

                if self._error_tracker:
                    try:
                        self._error_tracker.track_error(
                            e,
                            source="github_client",
                            context={
                                "operation": "repository_access",
                                "repository": self.repo_name
                            }
                        )
                    except (ImportError, AttributeError, ValueError):
                        import logging
                        logging.debug("Failed to track error", exc_info=True)
                raise

    def validate_webhook_payload(self, payload: dict, event: str) -> dict:
        """Validate webhook payload.

        Args:
            payload: Webhook payload as dictionary
            event: X-GitHub-Event header value

        Returns:
            Validated payload dictionary

        Raises:
            HTTPException: If validation fails
        """
        start_time = time.perf_counter()

        # Create span if OpenTelemetry is available
        if OTEL_AVAILABLE and trace:
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span("github_client.validate_webhook_payload") as span:
                try:
                    # Set basic span attributes
                    span.set_attribute("github.method", "validate_webhook_payload")
                    span.set_attribute("github.event_type", event)
                    span.set_attribute("github.repository", self.repo_name)

                    # Extract payload information
                    commit_count = len(payload.get("commits", []))
                    span.set_attribute("github.commit_count", commit_count)

                    # Verify event type
                    if event != "push":
                        span.set_attribute("github.error_type", "validation_error")
                        span.set_attribute("github.error_reason", "unsupported_event_type")
                        span.set_status(Status(StatusCode.ERROR, "Unsupported event type"))

                        # Record failed request metric
                        duration = time.perf_counter() - start_time
                        if METRICS_AVAILABLE and record_github_api_request:
                            record_github_api_request("validate_webhook_payload", event, "error", duration)

                        raise HTTPException(
                            status_code=400, detail="Only push events are supported"
                        )

                    span.set_status(Status(StatusCode.OK))

                    # Record successful request metric
                    duration = time.perf_counter() - start_time
                    if METRICS_AVAILABLE and record_github_api_request:
                        record_github_api_request("validate_webhook_payload", event, "success", duration)

                    return payload

                except HTTPException:
                    raise  # Re-raise HTTP exceptions as-is
                except (ValueError, KeyError, TypeError, AttributeError) as e:
                    # Set error attributes
                    span.set_attribute("github.error_type", "validation_error")
                    span.set_attribute("github.error_category", self._classify_error_for_telemetry(e))
                    span.set_status(Status(StatusCode.ERROR, str(e)))

                    # Record failed request metric
                    duration = time.perf_counter() - start_time
                    if METRICS_AVAILABLE and record_github_api_request:
                        record_github_api_request("validate_webhook_payload", event, "error", duration)

                    raise HTTPException(
                        status_code=400, detail=f"Invalid webhook payload: {e!s}"
                    ) from e
        else:
            # Fallback when telemetry is not available
            try:
                # Verify event type
                if event != "push":
                    # Record failed request metric
                    duration = time.perf_counter() - start_time
                    if METRICS_AVAILABLE and record_github_api_request:
                        record_github_api_request("validate_webhook_payload", event, "error", duration)

                    raise HTTPException(
                        status_code=400, detail="Only push events are supported"
                    )

                # Record successful request metric
                duration = time.perf_counter() - start_time
                if METRICS_AVAILABLE and record_github_api_request:
                    record_github_api_request("validate_webhook_payload", event, "success", duration)

                return payload
            except HTTPException:
                raise
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                # Record failed request metric
                duration = time.perf_counter() - start_time
                if METRICS_AVAILABLE and record_github_api_request:
                    record_github_api_request("validate_webhook_payload", event, "error", duration)

                raise HTTPException(
                    status_code=400, detail=f"Invalid webhook payload: {e!s}"
                ) from e

    def log_webhook_payload(self, payload: dict) -> None:
        """Log webhook payload information for debugging.

        Args:
            payload: GitHub webhook payload dictionary
        """
        start_time = time.perf_counter()

        # Create span if OpenTelemetry is available
        if OTEL_AVAILABLE and trace:
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span("github_client.log_webhook_payload") as span:
                try:
                    # Set basic span attributes
                    span.set_attribute("github.method", "log_webhook_payload")

                    # Extract and sanitize payload information for telemetry
                    commit_count = len(payload.get("commits", []))
                    payload_size = len(str(payload))

                    span.set_attribute("github.payload_size", payload_size)
                    span.set_attribute("github.commit_count", commit_count)

                    # Analyze file changes
                    has_added_files = False
                    has_modified_files = False
                    has_removed_files = False

                    for commit in payload.get("commits", []):
                        if commit.get("added"):
                            has_added_files = True
                        if commit.get("modified"):
                            has_modified_files = True
                        if commit.get("removed"):
                            has_removed_files = True

                    span.set_attribute("github.has_added_files", has_added_files)
                    span.set_attribute("github.has_modified_files", has_modified_files)
                    span.set_attribute("github.has_removed_files", has_removed_files)

                    # Sanitize sensitive data in commit messages for telemetry
                    if payload.get("head_commit", {}).get("message"):
                        original_message = payload["head_commit"]["message"]
                        sanitized_message = self._sanitize_commit_message(original_message)
                        span.set_attribute("github.head_commit_message", sanitized_message[:100])  # Limit length

                    # Avoid logging raw payload; log only sanitized metadata
                    # If needed, gate full payload behind an explicit debug flag and sanitize first.

                    # Log payload metadata (existing functionality)
                    print(
                        json.dumps(
                            {
                                "timestamp": datetime.utcnow().isoformat(),
                                "component": "github_client",
                                "operation": "webhook_payload",
                                "payload": {
                                    "commits": commit_count,
                                    "ref": payload.get("ref"),
                                    "repository": payload.get("repository", {}).get("full_name"),
                                },
                            }
                        ),
                        file=sys.stderr,
                    )

                    span.set_status(Status(StatusCode.OK))

                    # Record successful request metric
                    duration = time.perf_counter() - start_time
                    if METRICS_AVAILABLE and record_github_api_request:
                        record_github_api_request("log_webhook_payload", "unknown", "success", duration)

                except (ValueError, KeyError, TypeError, AttributeError) as e:
                    # Set error attributes
                    span.set_attribute("github.error_type", "logging_error")
                    span.set_attribute("github.error_category", self._classify_error_for_telemetry(e))
                    span.set_status(Status(StatusCode.ERROR, str(e)))

                    # Log error details (existing functionality)
                    error_details = {
                        "timestamp": datetime.utcnow().isoformat(),
                        "component": "github_client",
                        "operation": "webhook_payload",
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "payload_size": len(str(payload)),
                    }
                    print(json.dumps(error_details), file=sys.stderr)

                    # Record failed request metric
                    duration = time.perf_counter() - start_time
                    if METRICS_AVAILABLE and record_github_api_request:
                        record_github_api_request("log_webhook_payload", "unknown", "error", duration)
        else:
            # Fallback when telemetry is not available - existing functionality
            try:
                # Avoid logging raw payload; log only sanitized metadata
                # If needed, gate full payload behind an explicit debug flag and sanitize first.

                # Log payload metadata
                print(
                    json.dumps(
                        {
                            "timestamp": datetime.utcnow().isoformat(),
                            "component": "github_client",
                            "operation": "webhook_payload",
                            "payload": {
                                "commits": len(payload.get("commits", [])),
                                "ref": payload.get("ref"),
                                "repository": payload.get("repository", {}).get("full_name"),
                            },
                        }
                    ),
                    file=sys.stderr,
                )

                # Record successful request metric
                duration = time.perf_counter() - start_time
                if METRICS_AVAILABLE and record_github_api_request:
                    record_github_api_request("log_webhook_payload", "unknown", "success", duration)

            except (ValueError, KeyError, TypeError, AttributeError) as e:
                error_details = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "component": "github_client",
                    "operation": "webhook_payload",
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "payload_size": len(str(payload)),
                }
                print(json.dumps(error_details), file=sys.stderr)

                # Record failed request metric
                duration = time.perf_counter() - start_time
                if METRICS_AVAILABLE and record_github_api_request:
                    record_github_api_request("log_webhook_payload", "unknown", "error", duration)

    def _sanitize_commit_message(self, message: str) -> str:
        """Sanitize commit message to remove sensitive information."""
        if not message:
            return message

        # Pattern to match common sensitive data patterns
        sensitive_patterns = [
            r'(api[_\-]?key|token|password|secret|credential|auth)[=:\s]+[^\s,;]+',
            r'ghp_[a-zA-Z0-9_]+',
            r'github_pat_[a-zA-Z0-9_]+',
            r'(sk-[a-zA-Z0-9]+)',  # OpenAI API keys
            r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',  # Email addresses
            r'(postgres://[^@]+:[^@]+@[^\s]+)',  # Database connection strings
        ]

        sanitized = message
        for pattern in sensitive_patterns:
            sanitized = re.sub(pattern, '[REDACTED]', sanitized, flags=re.IGNORECASE)

        return sanitized
