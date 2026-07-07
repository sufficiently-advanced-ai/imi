"""
Error Classification Service - Issue #380
Centralizes error classification and recovery strategy determination.
"""
import asyncio
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ErrorClassifier:
    """
    Classifies errors and determines appropriate recovery strategies.

    Categories:
    - network: Transient network issues (retryable)
    - authentication: Auth failures (non-retryable, user action needed)
    - rate_limit: Rate limiting (retryable with delay)
    - resource_not_found: Missing resources (non-retryable, cleanup needed)
    - platform_limitation: Platform-specific issues
    - timeout: Operation timeouts (retryable with longer timeout)
    - server_error: Server-side errors (retryable)
    - validation: Input validation errors (non-retryable)
    """

    def __init__(self):
        self.classification_rules = self._initialize_rules()

    def _initialize_rules(self) -> dict[str, dict[str, Any]]:
        """Initialize error classification rules."""
        return {
            "network": {
                "patterns": [
                    r"connection.*refused",
                    r"network.*unreachable",
                    r"connection.*reset",
                    r"broken.*pipe",
                    r"connection.*aborted",
                    r"network.*error",
                ],
                "exception_types": [httpx.NetworkError, httpx.ConnectError, httpx.ReadTimeout],
                "retryable": True,
                "recovery_strategy": "exponential_backoff",
                "max_retries": 3,
                "base_delay": 1.0,
            },
            "authentication": {
                "patterns": [
                    r"invalid.*api.*key",
                    r"authentication.*failed",
                    r"unauthorized",
                    r"forbidden",
                    r"invalid.*token",
                ],
                "status_codes": [401, 403],
                "retryable": False,
                "recovery_strategy": "user_action_required",
                "user_message": "Authentication failed. Please check your API credentials.",
            },
            "rate_limit": {
                "patterns": [r"rate.*limit", r"too.*many.*requests"],
                "status_codes": [429],
                "retryable": True,
                "recovery_strategy": "queue_and_delay",
                "extract_retry_after": True,
            },
            "resource_not_found": {
                "patterns": [
                    r"bot.*not.*found",
                    r"meeting.*not.*found",
                    r"does.*not.*exist",
                ],
                "status_codes": [404],
                "retryable": False,
                "recovery_strategy": "cleanup_and_notify",
            },
            "platform_limitation": {
                "patterns": [
                    r"teams.*not.*support",
                    r"google.*meet.*limit",
                    r"zoom.*ended",
                    r"webex.*unavailable",
                ],
                "retryable": False,
                "recovery_strategy": "platform_specific_handling",
            },
            "timeout": {
                "exception_types": [asyncio.TimeoutError, httpx.TimeoutException],
                "patterns": [r"timeout", r"timed.*out"],
                "retryable": True,
                "recovery_strategy": "retry_with_longer_timeout",
                "max_retries": 2,
                "timeout_multiplier": 2.0,
            },
            "server_error": {
                "status_codes": [500, 502, 503, 504],
                "patterns": [r"internal.*server.*error", r"service.*unavailable"],
                "retryable": True,
                "recovery_strategy": "exponential_backoff",
                "max_retries": 3,
            },
            "validation": {
                "patterns": [
                    r"invalid.*format",
                    r"validation.*error",
                    r"invalid.*parameter",
                ],
                "status_codes": [400, 422],
                "retryable": False,
                "recovery_strategy": "fix_and_retry",
            },
        }

    def classify(self, error: Exception) -> dict[str, Any]:
        """
        Classify an error and return recovery information.

        Args:
            error: The exception to classify

        Returns:
            Dictionary containing:
            - category: Error category
            - retryable: Whether the error is retryable
            - recovery_strategy: Recommended recovery approach
            - Additional context based on error type
        """
        error_str = str(error).lower()

        # Check HTTP status codes if available
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code if error.response else None
            if status_code:
                classification = self._classify_by_status_code(status_code, error)
                if classification:
                    return classification

        # Check exception type
        classification = self._classify_by_exception_type(error)
        if classification:
            return classification

        # Check error message patterns
        classification = self._classify_by_pattern(error_str)
        if classification:
            return classification

        # Default classification for unknown errors
        return {
            "category": "unknown",
            "retryable": True,
            "recovery_strategy": "exponential_backoff",
            "max_retries": 2,
            "error_details": str(error),
        }

    def _classify_by_status_code(
        self, status_code: int, error: httpx.HTTPStatusError
    ) -> dict[str, Any] | None:
        """Classify based on HTTP status code."""
        for category, rules in self.classification_rules.items():
            if "status_codes" in rules and status_code in rules["status_codes"]:
                result = {
                    "category": category,
                    "retryable": rules["retryable"],
                    "recovery_strategy": rules["recovery_strategy"],
                    "http_status_code": status_code,
                }

                # Add specific fields based on category
                if category == "rate_limit" and rules.get("extract_retry_after"):
                    retry_after = error.response.headers.get("Retry-After")
                    if retry_after:
                        result["retry_after"] = int(retry_after)
                    else:
                        result["retry_after"] = 60  # Default to 60 seconds

                if category == "authentication":
                    result["user_message"] = rules.get("user_message")

                if "max_retries" in rules:
                    result["max_retries"] = rules["max_retries"]

                return result

        # Special handling for 5xx errors
        if 500 <= status_code < 600:
            return {
                "category": "server_error",
                "retryable": True,
                "recovery_strategy": "exponential_backoff",
                "max_retries": 3,
                "http_status_code": status_code,
            }

        return None

    def _classify_by_exception_type(self, error: Exception) -> dict[str, Any] | None:
        """Classify based on exception type."""
        for category, rules in self.classification_rules.items():
            if "exception_types" in rules:
                for exc_type in rules["exception_types"]:
                    if isinstance(error, exc_type):
                        result = {
                            "category": category,
                            "retryable": rules["retryable"],
                            "recovery_strategy": rules["recovery_strategy"],
                        }

                        if "max_retries" in rules:
                            result["max_retries"] = rules["max_retries"]

                        if category == "timeout" and "timeout_multiplier" in rules:
                            result["timeout_multiplier"] = rules["timeout_multiplier"]

                        return result

        return None

    def _classify_by_pattern(self, error_str: str) -> dict[str, Any] | None:
        """Classify based on error message patterns."""
        for category, rules in self.classification_rules.items():
            if "patterns" in rules:
                for pattern in rules["patterns"]:
                    if re.search(pattern, error_str):
                        result = {
                            "category": category,
                            "retryable": rules["retryable"],
                            "recovery_strategy": rules["recovery_strategy"],
                        }

                        # Extract platform hint if it's a platform error
                        if category == "platform_limitation":
                            platform_hint = self._extract_platform_hint(error_str)
                            if platform_hint:
                                result["platform_hint"] = platform_hint

                        if "max_retries" in rules:
                            result["max_retries"] = rules["max_retries"]

                        if "user_message" in rules:
                            result["user_message"] = rules["user_message"]

                        return result

        return None

    def _extract_platform_hint(self, error_str: str) -> str | None:
        """Extract platform name from error message."""
        platforms = ["teams", "zoom", "google meet", "webex"]
        for platform in platforms:
            if platform in error_str:
                return platform.replace(" ", "_")
        return None

    def get_user_friendly_message(self, classification: dict[str, Any]) -> str:
        """Get a user-friendly error message based on classification."""
        category = classification.get("category", "unknown")

        messages = {
            "network": "Connection issue. Please check your network and try again.",
            "authentication": "Authentication failed. Please verify your API credentials.",
            "rate_limit": "Too many requests. Please wait a moment and try again.",
            "resource_not_found": "The requested resource was not found.",
            "platform_limitation": "This operation is not supported on the current platform.",
            "timeout": "The operation took too long. Please try again.",
            "server_error": "Server error occurred. Please try again later.",
            "validation": "Invalid input provided. Please check your request.",
            "unknown": "An unexpected error occurred. Please try again.",
        }

        return messages.get(category, messages["unknown"])

    def should_alert_user(self, classification: dict[str, Any]) -> bool:
        """Determine if the error should trigger a user alert."""
        # Non-retryable errors should alert the user
        if not classification.get("retryable", True):
            return True

        # Authentication errors always alert
        if classification.get("category") == "authentication":
            return True

        # Resource not found in user-initiated operations
        if classification.get("category") == "resource_not_found":
            return True

        return False
