"""
Request Validation Middleware - Issue #398

Validates incoming requests for:
- Size limits (10MB default)
- Content-type validation
- Body parsing safety
- Security headers
- Input sanitization
"""

import json
import logging
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class RequestValidationError(Exception):
    """Exception raised for request validation failures"""
    pass


class RequestValidator:
    """
    Request validation logic
    """

    # Default size limit: 10MB
    DEFAULT_MAX_SIZE = 10 * 1024 * 1024  # 10MB in bytes

    # Allowed content types for different endpoints
    ALLOWED_CONTENT_TYPES = {
        "application/json",
        "application/x-www-form-urlencoded",
        "multipart/form-data",
        "text/plain",
        "text/markdown",
        "text/csv"
    }

    # Dangerous patterns to check in request bodies
    DANGEROUS_PATTERNS = [
        "<script",
        "javascript:",
        "vbscript:",
        "onload=",
        "onerror=",
        "onclick=",
        "eval(",
        "exec(",
        "import(",
        "__import__"
    ]

    def __init__(
        self,
        max_size: int = DEFAULT_MAX_SIZE,
        validate_content_type: bool = True,
        validate_size: bool = True,
        sanitize_input: bool = True
    ):
        self.max_size = max_size
        self.validate_content_type = validate_content_type
        self.validate_size = validate_size
        self.sanitize_input = sanitize_input

        # Statistics
        self.total_requests = 0
        self.rejected_requests = 0
        self.size_violations = 0
        self.content_type_violations = 0
        self.sanitization_violations = 0

        logger.info(
            f"Request validator initialized: max_size={max_size/1024/1024:.1f}MB, "
            f"validate_ct={validate_content_type}, sanitize={sanitize_input}"
        )

    def validate_request_size(self, content_length: int | None, path: str) -> None:
        """
        Validate request size

        Args:
            content_length: Content-Length header value
            path: Request path for logging

        Raises:
            RequestValidationError: If request is too large
        """
        if not self.validate_size:
            return

        if content_length is None:
            return  # No content-length header, will be checked during body read

        if content_length > self.max_size:
            self.size_violations += 1
            error_msg = (
                f"Request too large: {content_length} bytes "
                f"(max: {self.max_size} bytes) for {path}"
            )
            logger.warning(error_msg)
            raise RequestValidationError(error_msg)

    def validate_request_content_type(self, content_type: str | None, path: str) -> None:
        """
        Validate content type

        Args:
            content_type: Content-Type header value
            path: Request path for logging

        Raises:
            RequestValidationError: If content type is not allowed
        """
        if not self.validate_content_type:
            return

        # Skip validation for GET requests and some paths
        if not content_type:
            return  # No content type (likely GET request)

        # Extract main content type (ignore charset, boundary, etc.)
        main_type = content_type.split(";")[0].strip().lower()

        if main_type not in self.ALLOWED_CONTENT_TYPES:
            self.content_type_violations += 1
            error_msg = (
                f"Unsupported content type: {content_type} for {path}. "
                f"Allowed types: {', '.join(self.ALLOWED_CONTENT_TYPES)}"
            )
            logger.warning(error_msg)
            raise RequestValidationError(error_msg)

    def sanitize_body(self, body: bytes, path: str) -> bytes:
        """
        Sanitize request body for dangerous patterns

        Args:
            body: Request body as bytes
            path: Request path for logging

        Returns:
            Sanitized body

        Raises:
            RequestValidationError: If dangerous patterns are found
        """
        if not self.sanitize_input:
            return body

        if not body:
            return body

        try:
            # Decode body as string for pattern checking
            body_str = body.decode('utf-8', errors='ignore').lower()

            # Check for dangerous patterns
            found_patterns = [
                pattern for pattern in self.DANGEROUS_PATTERNS
                if pattern in body_str
            ]

            if found_patterns:
                self.sanitization_violations += 1
                error_msg = (
                    f"Potentially dangerous content detected in {path}: "
                    f"{', '.join(found_patterns)}"
                )
                logger.warning(error_msg)
                raise RequestValidationError(error_msg)

        except UnicodeDecodeError:
            # Binary content - skip text-based sanitization
            logger.debug(f"Skipping sanitization for binary content in {path}")

        return body

    def validate_json(self, body: bytes, path: str) -> None:
        """
        Validate JSON body structure

        Args:
            body: Request body as bytes
            path: Request path for logging

        Raises:
            RequestValidationError: If JSON is invalid
        """
        if not body:
            return

        try:
            json.loads(body.decode('utf-8'))
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON in request to {path}: {str(e)}"
            logger.warning(error_msg)
            raise RequestValidationError(error_msg)
        except UnicodeDecodeError as e:
            error_msg = f"Invalid UTF-8 encoding in request to {path}: {str(e)}"
            logger.warning(error_msg)
            raise RequestValidationError(error_msg)

    def get_stats(self) -> dict[str, Any]:
        """Get validation statistics"""
        return {
            "total_requests": self.total_requests,
            "rejected_requests": self.rejected_requests,
            "rejection_rate": (
                (self.rejected_requests / max(1, self.total_requests)) * 100
            ),
            "violations": {
                "size_violations": self.size_violations,
                "content_type_violations": self.content_type_violations,
                "sanitization_violations": self.sanitization_violations
            },
            "configuration": {
                "max_size_mb": self.max_size / 1024 / 1024,
                "validate_content_type": self.validate_content_type,
                "validate_size": self.validate_size,
                "sanitize_input": self.sanitize_input
            }
        }


class RequestValidationMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for request validation
    """

    def __init__(
        self,
        app,
        max_size: int = RequestValidator.DEFAULT_MAX_SIZE,
        exclude_paths: list[str] | None = None
    ):
        super().__init__(app)
        self.validator = RequestValidator(max_size=max_size)

        # Paths to exclude from validation
        self.exclude_paths = exclude_paths or [
            "/health",
            "/health/ready",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json"
        ]

        logger.info(f"Request validation middleware enabled: max_size={max_size/1024/1024:.1f}MB")

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request through validator"""

        self.validator.total_requests += 1

        # Skip validation for excluded paths
        if any(request.url.path.startswith(path) for path in self.exclude_paths):
            return await call_next(request)

        try:
            # Validate content length
            content_length = request.headers.get("content-length")
            if content_length:
                content_length = int(content_length)
                self.validator.validate_request_size(content_length, request.url.path)

            # Validate content type
            content_type = request.headers.get("content-type")
            self.validator.validate_request_content_type(content_type, request.url.path)

            # For requests with bodies, validate body
            if request.method in ["POST", "PUT", "PATCH"]:
                # Read body (this replaces the stream, so we need to store it)
                body = await request.body()

                # Validate body size if content-length wasn't present
                if len(body) > self.validator.max_size:
                    self.validator.size_violations += 1
                    raise RequestValidationError(
                        f"Request body too large: {len(body)} bytes "
                        f"(max: {self.validator.max_size} bytes)"
                    )

                # Sanitize body and capture the return value
                body = self.validator.sanitize_body(body, request.url.path)

                # Validate JSON structure if content type is JSON
                if content_type and "application/json" in content_type:
                    self.validator.validate_json(body, request.url.path)

                # Re-inject body for downstream handlers
                # Setting _body is sufficient - Starlette will use it instead of reading from stream
                request._body = body

            # Process request normally
            response = await call_next(request)

            # Add security headers
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-XSS-Protection"] = "1; mode=block"

            return response

        except RequestValidationError as e:
            self.validator.rejected_requests += 1

            logger.warning(
                f"Request validation failed for {request.method} {request.url.path}: {str(e)}"
            )

            # Return 400 Bad Request
            return JSONResponse(
                status_code=400,
                content={
                    "error": "request_validation_failed",
                    "message": str(e),
                    "path": request.url.path
                }
            )

        except ValueError as e:
            # Handle invalid content-length header
            self.validator.rejected_requests += 1

            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "message": f"Invalid request headers: {str(e)}"
                }
            )

        except Exception as e:
            # Unexpected error during validation
            logger.error(f"Unexpected error in request validation: {str(e)}")

            # Allow request to proceed (fail open for unexpected errors)
            return await call_next(request)


# Global validator instance
_global_validator: RequestValidator | None = None


def get_request_validator() -> RequestValidator:
    """Get global request validator instance"""
    global _global_validator
    if _global_validator is None:
        _global_validator = RequestValidator()
    return _global_validator


# API endpoint for validation stats
async def request_validation_status() -> dict[str, Any]:
    """Get current request validation statistics"""
    validator = get_request_validator()
    return validator.get_stats()
