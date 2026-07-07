import asyncio
import json
import sys
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError
from anthropic.types import MessageParam

from ..config import settings
from .inference import InferenceRetryableError

if TYPE_CHECKING:
    from .error_tracker import ErrorTracker
else:
    # Import at runtime to avoid circular imports
    pass

# OpenTelemetry imports - wrapped in try/except for graceful degradation
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    from traceloop.sdk import Traceloop

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    print(
        "Warning: OpenTelemetry not available, continuing without tracing",
        file=sys.stderr,
    )


class ClaudeRateLimiter:
    """Rate limiter for Claude API to prevent exceeding API rate limits.

    This class implements a token bucket algorithm for rate limiting,
    with separate buckets for requests per minute and tokens per minute.
    """

    def __init__(
        self,
        requests_per_minute: int = 100,  # Increased for interval-based processing
        tokens_per_minute: int = 100000,  # Default conservative TPM limit
        max_retry_attempts: int = 5,
        initial_retry_delay: float = 1.0,
        max_retry_delay: float = 60.0,
    ):
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.max_retry_attempts = max_retry_attempts
        self.initial_retry_delay = initial_retry_delay
        self.max_retry_delay = max_retry_delay

        # Token buckets
        self.request_tokens = requests_per_minute
        self.prompt_tokens = tokens_per_minute

        # Lock for thread safety
        self.lock = asyncio.Lock()

        # Last refill time
        self.last_refill = time.time()

        # Usage stats
        self.total_requests = 0
        self.total_tokens = 0
        self.rate_limited_requests = 0

        # Start the refill task only if there's a running event loop
        self._refill_task = None
        try:
            self._refill_task = asyncio.create_task(self._refill_buckets_periodically())
        except RuntimeError:
            # No event loop running, we'll start the task later
            pass

    async def _refill_buckets_periodically(self):
        """Periodically refill the token buckets."""
        try:
            while True:
                # Refill every 2 seconds
                await asyncio.sleep(2)
                await self._refill_buckets()
        except asyncio.CancelledError:
            # Task was cancelled, clean up
            pass

    def _ensure_refill_task(self):
        """Ensure the refill task is running."""
        if self._refill_task is None:
            try:
                self._refill_task = asyncio.create_task(
                    self._refill_buckets_periodically()
                )
            except RuntimeError:
                # Still no event loop, will try again later
                pass

    async def _refill_buckets(self):
        """Refill rate limit token buckets based on elapsed time."""
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_refill

            # Only refill if some time has passed
            if elapsed <= 0:
                return

            # Calculate tokens to add based on elapsed time (as a fraction of a minute)
            request_tokens_to_add = self.requests_per_minute * (elapsed / 60)
            prompt_tokens_to_add = self.tokens_per_minute * (elapsed / 60)

            # Add tokens up to the maximum
            self.request_tokens = min(
                self.requests_per_minute, self.request_tokens + request_tokens_to_add
            )
            self.prompt_tokens = min(
                self.tokens_per_minute, self.prompt_tokens + prompt_tokens_to_add
            )

            # Update last refill time
            self.last_refill = now

    async def wait_for_capacity(
        self, prompt_tokens: int = 1, max_tokens: int = 1
    ) -> bool:
        """Wait until there's capacity for the request or timeout.

        Args:
            prompt_tokens: Number of input tokens in the request
            max_tokens: Maximum number of output tokens in the request

        Returns:
            True if capacity is available, False if timeout
        """
        # Estimated total tokens for this request
        estimated_tokens = prompt_tokens + max_tokens

        # Try for up to 2 minutes
        timeout = time.time() + 120

        while time.time() < timeout:
            async with self.lock:
                if self.request_tokens >= 1 and self.prompt_tokens >= estimated_tokens:
                    # We have capacity, decrement tokens
                    self.request_tokens -= 1
                    self.prompt_tokens -= estimated_tokens
                    self.total_requests += 1
                    self.total_tokens += estimated_tokens
                    return True

            # No capacity, wait a bit and try again after refill
            self.rate_limited_requests += 1
            await asyncio.sleep(1)
            await self._refill_buckets()

        # Timeout reached
        return False

    def get_stats(self) -> dict[str, Any]:
        """Get current rate limiter statistics."""
        return {
            "total_requests": self.total_requests,
            "total_tokens": self.total_tokens,
            "rate_limited_requests": self.rate_limited_requests,
            "current_request_tokens": self.request_tokens,
            "current_prompt_tokens": self.prompt_tokens,
            "requests_per_minute": self.requests_per_minute,
            "tokens_per_minute": self.tokens_per_minute,
        }

    def stop(self):
        """Stop the refill task."""
        if self._refill_task:
            self._refill_task.cancel()


class ClaudeClient:
    """Async client for Anthropic's Claude API with rate limiting, retries, and semaphore."""

    def __init__(self, max_concurrent_requests: int = 3, error_tracker: Optional['ErrorTracker'] = None):
        """Initialize the Claude client.

        Args:
            max_concurrent_requests: Maximum number of concurrent requests allowed
            error_tracker: Optional ErrorTracker instance for dependency injection
        """
        # Initialize OpenTelemetry for LLM monitoring if available
        if OTEL_AVAILABLE and not getattr(ClaudeClient, "_traceloop_initialized", False):
            try:
                Traceloop.init(
                    app_name="imi",
                    disable_batch=True,  # Real-time tracing for development
                )
                ClaudeClient._traceloop_initialized = True
                self._log_claude_request("otel_init", {"status": "success"})
            except Exception as e:
                self._log_claude_request(
                    "otel_init", {"status": "failed", "error": str(e)}
                )

        # Inference endpoint registry — maps model-tier/operation to a concrete
        # endpoint (Anthropic, OpenAI-compatible self-hosted, or Bedrock). With
        # no config/inference.yaml this resolves everything to Anthropic.
        from .inference import get_inference_registry

        self.registry = get_inference_registry()

        # Default Anthropic client (covers the no-config / default-endpoint case
        # and any external callers that reach through to .client). Per-endpoint
        # Anthropic clients with a distinct base_url/api_key are cached lazily.
        self.client = self._build_anthropic_client(
            api_key=settings.ANTHROPIC_API_KEY,
            base_url=getattr(settings, "ANTHROPIC_BASE_URL", "") or None,
        )
        self._anthropic_clients: dict[tuple, Anthropic] = {
            (settings.ANTHROPIC_API_KEY, getattr(settings, "ANTHROPIC_BASE_URL", "") or None): self.client
        }

        # Rate limiter for API limits
        self.rate_limiter = ClaudeRateLimiter()

        # Semaphore for limiting concurrent requests
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)

        # Counter for request IDs
        self.request_counter = 0

        # Error tracker for centralized error monitoring
        if error_tracker is None:
            from .error_tracker import ErrorTracker
            self.error_tracker = ErrorTracker()
        else:
            self.error_tracker = error_tracker

        # OpenTelemetry tracer for manual span creation
        self.tracer = None
        if OTEL_AVAILABLE:
            self.tracer = trace.get_tracer("claude_client")

    @staticmethod
    def _build_anthropic_client(
        api_key: str | None, base_url: str | None
    ) -> Anthropic:
        """Construct a native Anthropic client, honoring an optional base_url."""
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": 60.0,
            "max_retries": 1,  # we handle retries with exponential backoff
        }
        if base_url:
            kwargs["base_url"] = base_url
        return Anthropic(**kwargs)

    def _anthropic_client_for(self, ep) -> Anthropic:
        """Return (caching) the Anthropic client for a resolved endpoint."""
        key = (ep.api_key, ep.api_base)
        client = self._anthropic_clients.get(key)
        if client is None:
            client = self._build_anthropic_client(api_key=ep.api_key, base_url=ep.api_base)
            self._anthropic_clients[key] = client
        return client

    def _dispatch(self, ep, api_kwargs: dict[str, Any]) -> Any:
        """Execute one inference call against a resolved endpoint.

        Anthropic endpoints use the native SDK (returns an Anthropic ``Message``);
        everything else goes through LiteLLM and is translated back to an
        Anthropic-shaped response so callers are unaffected. Runs on a worker
        thread via the caller's ``asyncio.to_thread``.
        """
        if ep.is_anthropic:
            client = self._anthropic_client_for(ep)
            return client.messages.create(**{**api_kwargs, "model": ep.model})

        # Non-Anthropic → LiteLLM (lazy import: the dependency is only needed
        # when a deployment actually routes off Anthropic).
        import litellm

        from .inference import to_anthropic_response, to_openai_messages

        litellm.telemetry = False
        litellm.drop_params = True  # let heterogeneous backends ignore unknown params

        completion_kwargs: dict[str, Any] = {
            "model": ep.model,
            "messages": to_openai_messages(
                api_kwargs.get("messages", []), api_kwargs.get("system")
            ),
            "max_tokens": api_kwargs.get("max_tokens"),
            "temperature": api_kwargs.get("temperature"),
        }
        if ep.api_base:
            completion_kwargs["api_base"] = ep.api_base
        if ep.api_key:
            completion_kwargs["api_key"] = ep.api_key
        completion_kwargs.update(ep.extra or {})

        # Map transient LiteLLM errors to InferenceRetryableError so the caller's
        # retry loop backs off and retries the SAME endpoint (never falls back to
        # Anthropic). Non-transient errors (auth, bad request) propagate as-is.
        transient = tuple(
            exc
            for name in (
                "RateLimitError",
                "Timeout",
                "APIConnectionError",
                "ServiceUnavailableError",
                "InternalServerError",
            )
            if (exc := getattr(litellm.exceptions, name, None)) is not None
        )
        try:
            raw = litellm.completion(**completion_kwargs)
        except transient as e:  # type: ignore[misc]
            raise InferenceRetryableError(f"{type(e).__name__}: {e}") from e

        response = to_anthropic_response(raw)
        # Stash the raw LiteLLM response so cost can be computed from it.
        response._litellm_raw = raw  # type: ignore[attr-defined]
        return response

    async def generate_messages_batch(
        self, requests: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Generate multiple Claude messages in parallel while respecting rate limits.

        Args:
            requests: List of request configurations, each containing:
                - messages: List of message objects
                - model: Claude model to use
                - max_tokens: Maximum tokens to generate
                - temperature: Sampling temperature
                - request_id: Optional request ID
                - estimate_token_count: Optional token estimate

        Returns:
            List of responses in the same order as requests
        """
        # Create tasks for each request
        tasks = []
        for req in requests:
            task = asyncio.create_task(
                self.generate_message(
                    messages=req.get("messages", []),
                    model=req.get("model", settings.CLAUDE_SONNET_MODEL),
                    max_tokens=req.get("max_tokens", 1024),
                    temperature=req.get("temperature", 0.7),
                    operation=req.get("operation", "chat"),
                    request_id=req.get("request_id"),
                    estimate_token_count=req.get("estimate_token_count"),
                    conversation_id=req.get("conversation_id"),
                    turn_number=req.get("turn_number"),
                )
            )
            tasks.append(task)

        # Wait for all tasks to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self._log_claude_request(
                    "batch_request_failed",
                    {
                        "request_id": requests[i].get("request_id", f"batch_{i}"),
                        "error": str(result),
                        "error_type": type(result).__name__,
                    },
                )
                processed_results.append({"error": str(result)})
            else:
                processed_results.append(result)

        return processed_results

    async def generate_message(
        self,
        messages: list[MessageParam],
        model: str = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        operation: str = "chat",
        request_id: str = None,
        estimate_token_count: int | None = None,
        conversation_id: str | None = None,
        turn_number: int | None = None,
    ) -> Any:
        """Generate a message using Claude with rate limiting and retries.

        Args:
            messages: List of message objects to send to Claude API
            model: Claude model to use, defaults to settings.CLAUDE_SONNET_MODEL
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            system: Optional system prompt
            tools: Optional list of tools for function calling
            operation: Operation label for metrics (default: "chat")
            request_id: Optional request ID for tracking
            estimate_token_count: Optional token count estimate to avoid counting tokens
            conversation_id: Optional conversation ID for tracking
            turn_number: Optional turn number within conversation

        Returns:
            The Claude API response

        Raises:
            Exception: If the request fails after all retries
        """
        # Generate request ID if not provided
        if not request_id:
            self.request_counter += 1
            request_id = f"req_{self.request_counter}_{int(time.time())}"

        # Use model from settings if not specified
        if not model:
            model = settings.CLAUDE_SONNET_MODEL

        # Resolve which inference endpoint this (model-tier, operation) routes to.
        endpoint = self.registry.resolve(model, operation)
        # Fail closed: tool-use is only routed to Anthropic endpoints. We do not
        # translate tools to a non-Anthropic backend (plain-generation only).
        if tools and not endpoint.allow_tools:
            raise ValueError(
                f"Tool use is not permitted on inference endpoint '{endpoint.name}' "
                f"(operation={operation!r}); route tool-using calls to an Anthropic endpoint."
            )

        # Estimate token count for rate limiting (simple heuristic if not provided)
        if not estimate_token_count:
            # Simple and conservative estimation: 4 characters ≈ 1 token
            prompt_text = ""
            for msg in messages:
                if isinstance(msg.get("content"), str):
                    prompt_text += msg.get("content", "")
                elif isinstance(msg.get("content"), list):
                    for content_item in msg.get("content", []):
                        if (
                            isinstance(content_item, dict)
                            and content_item.get("type") == "text"
                        ):
                            prompt_text += content_item.get("text", "")

            estimate_token_count = len(prompt_text) // 4

        # Log the request attempt
        self._log_claude_request(
            "attempt",
            {
                "request_id": request_id,
                "model": model,
                "estimated_tokens": estimate_token_count,
                "max_tokens": max_tokens,
            },
        )

        # Wait for rate limiter capacity
        if not await self.rate_limiter.wait_for_capacity(
            estimate_token_count, max_tokens
        ):
            self._log_claude_request(
                "rate_limit_timeout", {"request_id": request_id, "model": model}
            )
            raise Exception(
                f"Rate limit timeout waiting for capacity - request {request_id}"
            )

        # Use semaphore to limit concurrent requests
        async with self.semaphore:
            # Try with retries and exponential backoff
            retry_attempts = 0
            max_retries = self.rate_limiter.max_retry_attempts
            retry_delay = self.rate_limiter.initial_retry_delay

            while retry_attempts <= max_retries:
                try:
                    self._log_claude_request(
                        "sending",
                        {
                            "request_id": request_id,
                            "model": model,
                            "retry": retry_attempts,
                        },
                    )

                    # Make the API call with manual span
                    start_time = time.time()
                    api_kwargs = {
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    }

                    # Add optional parameters
                    if system is not None:
                        api_kwargs["system"] = system
                    if tools is not None:
                        api_kwargs["tools"] = tools

                    # Create manual span for testing and conversation context
                    if self.tracer:
                        with self.tracer.start_as_current_span("claude_generate_message") as span:
                            # Add conversation context to span
                            if conversation_id:
                                span.set_attribute("conversation.id", conversation_id)
                            if turn_number:
                                span.set_attribute("conversation.turn", turn_number)

                            # Add other useful attributes
                            span.set_attribute("model", model)
                            span.set_attribute("request_id", request_id)
                            span.set_attribute("operation", operation)

                            response = await asyncio.to_thread(
                                self._dispatch, endpoint, api_kwargs
                            )
                            # Add token usage attributes
                            span.set_attribute("llm.input_tokens", response.usage.input_tokens)
                            span.set_attribute("llm.output_tokens", response.usage.output_tokens)
                            span.set_attribute("llm.total_tokens", response.usage.input_tokens + response.usage.output_tokens)
                            # Mark success
                            span.set_status(Status(StatusCode.OK))
                    else:
                        response = await asyncio.to_thread(
                            self._dispatch, endpoint, api_kwargs
                        )

                    end_time = time.time()

                    self._log_claude_request(
                        "success",
                        {
                            "request_id": request_id,
                            "model": model,
                            "duration": round(end_time - start_time, 2),
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens,
                            "retry": retry_attempts,
                        },
                    )

                    # Record metrics - wrapped in try/except to ensure it doesn't break the main flow
                    try:
                        from ..metrics import record_llm_usage

                        cost = self._cost_for(endpoint, response)
                        record_llm_usage(
                            model=endpoint.model,
                            operation=operation,  # Now uses the parameter
                            input_tokens=response.usage.input_tokens,
                            output_tokens=response.usage.output_tokens,
                            cost=cost,
                        )
                    except Exception as metrics_error:
                        # Log the error but don't fail the request
                        import logging

                        logger = logging.getLogger(__name__)
                        logger.error(
                            f"Failed to record metrics for request {request_id}: {str(metrics_error)}"
                        )

                    return response

                except RateLimitError as e:
                    retry_attempts += 1

                    # Track rate limit error
                    self.error_tracker.track_error(
                        error=e,
                        context={
                            "operation": operation,
                            "model": model,
                            "request_id": request_id,
                            "retry_attempt": retry_attempts,
                            "max_retries": max_retries,
                        },
                        source="claude_client"
                    )

                    self._log_claude_request(
                        "rate_limited",
                        {
                            "request_id": request_id,
                            "model": model,
                            "retry": retry_attempts,
                            "max_retries": max_retries,
                            "delay": retry_delay,
                            "error": str(e),
                        },
                    )

                    if retry_attempts > max_retries:
                        raise

                    # Wait with exponential backoff
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(
                        retry_delay * 2, self.rate_limiter.max_retry_delay
                    )

                except (APIConnectionError, APIStatusError, InferenceRetryableError) as e:
                    retry_attempts += 1

                    # Track API error
                    self.error_tracker.track_error(
                        error=e,
                        context={
                            "operation": operation,
                            "model": model,
                            "request_id": request_id,
                            "retry_attempt": retry_attempts,
                            "max_retries": max_retries,
                            "error_type": type(e).__name__,
                        },
                        source="claude_client"
                    )

                    self._log_claude_request(
                        "api_error",
                        {
                            "request_id": request_id,
                            "model": model,
                            "retry": retry_attempts,
                            "max_retries": max_retries,
                            "delay": retry_delay,
                            "error": str(e),
                            "error_type": type(e).__name__,
                        },
                    )

                    if retry_attempts > max_retries:
                        raise

                    # Wait with exponential backoff
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(
                        retry_delay * 2, self.rate_limiter.max_retry_delay
                    )

                except Exception as e:
                    # Track unexpected error
                    self.error_tracker.track_error(
                        error=e,
                        context={
                            "operation": operation,
                            "model": model,
                            "request_id": request_id,
                            "error_type": type(e).__name__,
                        },
                        source="claude_client"
                    )

                    # Log unexpected errors
                    self._log_claude_request(
                        "unexpected_error",
                        {
                            "request_id": request_id,
                            "model": model,
                            "error": str(e),
                            "error_type": type(e).__name__,
                        },
                    )
                    raise

            # Should not reach here, but just in case
            raise Exception(
                f"Failed after {max_retries} retries - request {request_id}"
            )

    def _cost_for(self, endpoint, response: Any) -> float:
        """Compute call cost for the resolved endpoint.

        Anthropic endpoints use the built-in pricing table. Non-Anthropic
        endpoints use the endpoint's configured per-MTok ``pricing`` if present,
        else LiteLLM's own cost estimate, else 0.0 (self-hosted with no price).
        """
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        if endpoint.is_anthropic:
            return self._calculate_cost(input_tokens, output_tokens, endpoint.model)

        if endpoint.pricing:
            return (
                (input_tokens / 1_000_000) * float(endpoint.pricing.get("input", 0.0))
                + (output_tokens / 1_000_000) * float(endpoint.pricing.get("output", 0.0))
            )

        raw = getattr(response, "_litellm_raw", None)
        if raw is not None:
            try:
                import litellm

                return float(litellm.completion_cost(completion_response=raw) or 0.0)
            except Exception:
                pass  # self-hosted / unknown model — no price available
        return 0.0

    def _calculate_cost(
        self, input_tokens: int, output_tokens: int, model: str
    ) -> float:
        """Calculate the cost of a Claude API call based on token usage.

        Pricing as of January 2025:
        - Claude 3 Opus: $15/$75 per million tokens (input/output)
        - Claude 3.5 Opus: $15/$75 per million tokens (input/output) - placeholder
        - Claude 3.5 Sonnet: $3/$15 per million tokens (input/output)
        - Claude 3 Sonnet: $3/$15 per million tokens (input/output)
        - Claude 3.5 Haiku: $0.80/$4 per million tokens (input/output)
        - Claude 3 Haiku: $0.25/$1.25 per million tokens (input/output)
        - Claude Sonnet 4: $3/$15 per million tokens (input/output)
        - Claude Sonnet 4.5: $3/$15 per million tokens (input/output)
        - Claude Haiku 4.5: $1/$5 per million tokens (input/output)

        Note: Claude 3.5 Opus is not yet released, using Claude 3 Opus pricing as placeholder
        """
        # Define pricing per million tokens - updated January 2025
        pricing = {
            "claude-3-opus": {"input": 15.0, "output": 75.0},
            "claude-3-5-opus": {"input": 15.0, "output": 75.0},  # Placeholder pricing
            "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
            "claude-3-sonnet": {"input": 3.0, "output": 15.0},
            "claude-3-5-haiku": {"input": 0.80, "output": 4.0},
            "claude-3-haiku": {"input": 0.25, "output": 1.25},
            "claude-sonnet-4": {"input": 3.0, "output": 15.0},  # Sonnet 4 pricing
            "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},  # Sonnet 4.5 pricing
            "claude-haiku-4-5": {"input": 1.0, "output": 5.0},  # Haiku 4.5 pricing
        }

        # Normalize model name for matching
        model_lower = model.lower().replace(".", "-")  # Handle both . and - separators

        # Default to sonnet pricing if model not found
        model_key = None
        for key in pricing.keys():
            if key in model_lower or model_lower.replace("-", ".") in key:
                model_key = key
                break

        if not model_key:
            # Additional fallback patterns
            if "sonnet-4-5" in model_lower or "20250929" in model_lower:
                model_key = "claude-sonnet-4-5"
            elif "sonnet-4" in model_lower:
                model_key = "claude-sonnet-4"
            elif "sonnet" in model_lower:
                model_key = (
                    "claude-3-5-sonnet"
                    if "3-5" in model_lower or "3.5" in model_lower
                    else "claude-3-sonnet"
                )
            elif "haiku" in model_lower:
                # Check for specific haiku model with date
                if "haiku-4-5" in model_lower or "20251001" in model_lower:
                    model_key = "claude-haiku-4-5"
                elif "20241022" in model_lower:
                    model_key = "claude-3-5-haiku"
                else:
                    model_key = (
                        "claude-3-5-haiku"
                        if "3-5" in model_lower or "3.5" in model_lower
                        else "claude-3-haiku"
                    )
            elif "opus" in model_lower:
                model_key = (
                    "claude-3-5-opus"
                    if "3-5" in model_lower or "3.5" in model_lower
                    else "claude-3-opus"
                )
            else:
                model_key = "claude-3-5-sonnet"  # Default to latest Sonnet

        # Calculate cost (price per million tokens)
        input_cost = (input_tokens / 1_000_000) * pricing[model_key]["input"]
        output_cost = (output_tokens / 1_000_000) * pricing[model_key]["output"]

        return input_cost + output_cost

    def _log_claude_request(self, status: str, details: dict[str, Any]):
        """Log Claude API request with structured output."""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "component": "claude_client",
            "status": status,
            "details": details,
        }
        print(json.dumps(log_entry), file=sys.stderr)

    async def generate_display(self, prompt: str) -> str:
        """Generate display content for meeting state.

        Args:
            prompt: The prompt for display generation

        Returns:
            JSON string with display sections
        """
        messages = [{"role": "user", "content": prompt}]

        response = await self.generate_message(
            messages=messages,
            model=settings.CLAUDE_HAIKU_MODEL,
            max_tokens=1024,
            temperature=0.7,
            operation="display_generation",
        )

        # Extract text content from response object
        if response:
            # Handle Anthropic Message object
            if hasattr(response, "content") and response.content:
                # content is a list of content blocks
                if len(response.content) > 0 and hasattr(response.content[0], "text"):
                    text_content = response.content[0].text
                    # Log empty responses
                    try:
                        parsed = json.loads(text_content)
                        if not parsed.get("sections"):
                            self._log_claude_request(
                                "warning",
                                {
                                    "operation": "display_generation",
                                    "issue": "empty_sections_array",
                                    "prompt_length": len(prompt),
                                    "response": text_content[:200],
                                },
                            )
                    except Exception:
                        pass
                    return text_content
            # Handle dict response (legacy format)
            elif isinstance(response, dict) and "content" in response:
                if (
                    isinstance(response["content"], list)
                    and len(response["content"]) > 0
                ):
                    text_content = response["content"][0].get(
                        "text", '{"sections": []}'
                    )
                    return text_content
                elif isinstance(response["content"], str):
                    return response["content"]

        # Log when we have to use fallback
        self._log_claude_request(
            "warning",
            {
                "operation": "display_generation",
                "issue": "no_valid_response",
                "response_type": type(response).__name__ if response else "None",
                "has_content": hasattr(response, "content") if response else False,
            },
        )
        return '{"sections": []}'

    def get_stats(self) -> dict[str, Any]:
        """Get client statistics."""
        return {
            "rate_limiter_stats": self.rate_limiter.get_stats(),
            "max_concurrent_requests": self.semaphore._value,
            "current_semaphore_value": self.semaphore._value,
        }

    async def shutdown(self):
        """Shutdown the client and clean up resources."""
        self.rate_limiter.stop()


# Global instance - lazy initialization
_claude_client_instance = None


def get_claude_client() -> ClaudeClient:
    """Get the global Claude client instance, creating it if necessary."""
    global _claude_client_instance
    if _claude_client_instance is None:
        _claude_client_instance = ClaudeClient(
            max_concurrent_requests=settings.CLAUDE_MAX_CONCURRENCY
            if hasattr(settings, "CLAUDE_MAX_CONCURRENCY")
            else 3
        )
    return _claude_client_instance


# For backward compatibility
claude_client = None  # Will be set by get_claude_client() when needed
