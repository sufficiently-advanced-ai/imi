"""Configurable multi-endpoint inference layer.

This package lets a single deployment route single-shot LLM calls to different
inference endpoints — the Anthropic API, an OpenAI-compatible self-hosted server
(vLLM/Ollama/TGI) on a tailnet, or AWS Bedrock in a client DMZ — chosen per
model-tier/operation via a config registry.

Design:
- Anthropic-typed endpoints use the native ``anthropic`` SDK (today's exact
  behaviour, tools included), optionally with a custom ``base_url``.
- Non-Anthropic endpoints go through LiteLLM (``litellm.completion``), which
  normalises ~100 providers to the OpenAI Chat Completions schema. We translate
  that response back into an Anthropic-shaped object so the ~60 existing callers
  of ``ClaudeClient`` are untouched.
- Routing is **fail-closed**: there is no implicit fallback to Anthropic when a
  routed endpoint fails. A misconfigured endpoint name raises.

See ``docs``/the plan and ``config/inference.yaml.example`` for configuration.
"""

from .base import (
    AnthropicLikeResponse,
    ContentBlock,
    InferenceConfigError,
    InferenceRetryableError,
    Usage,
)
from .registry import (
    InferenceRegistry,
    ResolvedEndpoint,
    get_inference_registry,
    reset_inference_registry,
)
from .translation import to_anthropic_response, to_openai_messages

__all__ = [
    "AnthropicLikeResponse",
    "ContentBlock",
    "Usage",
    "InferenceConfigError",
    "InferenceRetryableError",
    "InferenceRegistry",
    "ResolvedEndpoint",
    "get_inference_registry",
    "reset_inference_registry",
    "to_anthropic_response",
    "to_openai_messages",
]
