"""Shared types for the inference layer.

``AnthropicLikeResponse`` is the **compatibility contract**: every caller of
``ClaudeClient.generate_message`` reads ``response.content[0].text`` and
``response.usage.input_tokens`` / ``output_tokens``. When a non-Anthropic
endpoint answers (via LiteLLM, which returns an OpenAI-shaped object), we build
one of these so callers never have to know the difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class InferenceConfigError(Exception):
    """Raised when the inference registry/config is invalid or references an
    unknown endpoint. Surfacing this (rather than silently routing elsewhere) is
    what keeps endpoint selection fail-closed."""


class InferenceRetryableError(Exception):
    """Wraps a transient LiteLLM error (rate limit, timeout, connection,
    overload) so ``ClaudeClient``'s retry loop can back off and retry the *same*
    endpoint. Non-transient errors (auth, bad request) are not wrapped — they
    propagate and fail closed without retry or fallback."""


@dataclass
class ContentBlock:
    """Mimics an Anthropic content block. Only text is supported — non-Anthropic
    endpoints are plain-generation only (see package docstring)."""

    text: str
    type: str = "text"


@dataclass
class Usage:
    """Mimics ``anthropic.types.Usage`` for the fields callers read."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class AnthropicLikeResponse:
    """Duck-typed stand-in for ``anthropic.types.Message``.

    Exposes exactly the attributes the codebase accesses on a Claude response:
    ``.content`` (list of blocks with ``.type`` / ``.text``), ``.usage``
    (``.input_tokens`` / ``.output_tokens``), ``.stop_reason``, and ``.model``.
    """

    content: list[ContentBlock] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str | None = None
    model: str | None = None

    @property
    def text(self) -> str:
        """Convenience: concatenated text of all text blocks."""
        return "".join(b.text for b in self.content if getattr(b, "type", None) == "text")
