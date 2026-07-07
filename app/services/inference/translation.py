"""Translate between Anthropic-shaped calls and the OpenAI/LiteLLM wire format.

Only used on the **non-Anthropic** path. Anthropic endpoints keep using the
native SDK, so no translation runs for them.

Scope is plain generation (prompt-in / text-out): we flatten Anthropic content
blocks to text and ignore tool/image blocks — tool-use is never routed to a
non-Anthropic endpoint (the caller guards that and fails closed).
"""

from __future__ import annotations

import logging
from typing import Any

from .base import AnthropicLikeResponse, ContentBlock, Usage

logger = logging.getLogger(__name__)

# OpenAI finish_reason -> Anthropic stop_reason
_STOP_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
}


def _block_to_text(content: Any) -> str:
    """Flatten an Anthropic message ``content`` value to a plain string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                # Anthropic text block: {"type": "text", "text": "..."}
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                # tolerate a bare {"text": "..."} block
                elif "text" in item and isinstance(item["text"], str):
                    parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    # Unexpected shape (not None/str/list) — tolerate it but surface for debugging.
    logger.debug("to_openai_messages: unexpected content type %s; coercing with str()", type(content).__name__)
    return str(content)


def to_openai_messages(
    messages: list[dict[str, Any]], system: str | None = None
) -> list[dict[str, str]]:
    """Convert Anthropic-style messages (+ separate system prompt) into the
    OpenAI Chat Completions ``messages`` list LiteLLM expects."""
    if messages is not None and not isinstance(messages, list):
        raise TypeError(
            f"messages must be a list (or None), got {type(messages).__name__}"
        )
    out: list[dict[str, str]] = []
    if system:
        out.append({"role": "system", "content": system})
    for msg in messages or []:
        role = msg.get("role", "user")
        out.append({"role": role, "content": _block_to_text(msg.get("content"))})
    return out


def to_anthropic_response(resp: Any) -> AnthropicLikeResponse:
    """Convert a LiteLLM ``ModelResponse`` (OpenAI shape) into an
    ``AnthropicLikeResponse`` the rest of the codebase can read unchanged."""
    text = ""
    finish_reason = None
    model = getattr(resp, "model", None)

    choices = getattr(resp, "choices", None) or []
    if choices:
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is not None:
            text = getattr(message, "content", None) or ""
        elif isinstance(choice, dict):  # defensive: dict-shaped choice
            logger.debug("to_anthropic_response: dict-shaped choice; using fallback access")
            text = (choice.get("message") or {}).get("content") or ""
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason is None and isinstance(choice, dict):
            finish_reason = choice.get("finish_reason")
    else:
        logger.warning(
            "to_anthropic_response: response has no choices (model=%s) — returning empty text",
            getattr(resp, "model", None),
        )

    usage_obj = getattr(resp, "usage", None)
    if usage_obj is None:
        logger.warning("to_anthropic_response: response missing usage (model=%s)", model)
    input_tokens = getattr(usage_obj, "prompt_tokens", 0) or 0
    output_tokens = getattr(usage_obj, "completion_tokens", 0) or 0

    stop_reason = _STOP_REASON_MAP.get(finish_reason, finish_reason) if finish_reason else finish_reason
    return AnthropicLikeResponse(
        content=[ContentBlock(text=text or "")],
        usage=Usage(input_tokens=int(input_tokens), output_tokens=int(output_tokens)),
        stop_reason=stop_reason,
        model=model,
    )
