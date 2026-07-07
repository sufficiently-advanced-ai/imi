"""
ask_kb — natural-language intent tool.

The "tell me what you want" meta-tool that prompted the MCP surface
re-audit. Takes a natural-language intent, runs an internal Claude
sub-agent loop with access to the read-only Tier-1 CRUD tools, and
returns a synthesized answer plus a structured trace of which tools
were called.

Phase E3 of the MCP cleanup. Designed to be the *complement* to the
sharpened CRUD surface, not a replacement. External callers that
already know what they want should call the specific CRUD tool
directly (cheaper, deterministic). External callers with fuzzy or
multi-step intents should use ask_kb (more expensive, but handles
ambiguity).

Scope choices in this initial implementation:
- Read-only by default. Mutations are gated behind `allow_mutations`
  to avoid unintended state changes from natural-language intents.
- Step budget is bounded (`max_steps`, default 8) so a confused
  sub-agent can't loop forever.
- The trace records tool name + a short summary per call, not the
  full input/output, to keep the response shape compact.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# Anthropic-compatible tool schemas for the read-only Tier-1 CRUD set.
# These are *separate* from the MCP TOOL_DEFS shape because Anthropic
# expects a slightly different schema layout (no top-level `name` key
# duplication; `input_schema` instead of `inputSchema`). Built from
# TOOL_DEFS at call time so descriptions stay in sync.
def _build_anthropic_tools(read_only: bool) -> list[dict[str, Any]]:
    from app.services.mcp_tool_definitions import TOOL_DEFS

    read_only_tools = {
        "search_knowledge_graph",
        "list_entities",
        "get_entity_by_name",
        "find_related_entities",
        "search_signals",
        "list_entity_profiles",
        "read_document",
        "extract_entities",
        "list_meetings",
        "get_meeting_transcript",
    }
    write_tools = {"update_signal", "delete_signal"}

    available = read_only_tools | (write_tools if not read_only else set())

    return [
        {
            "name": td["name"],
            "description": td["description"],
            "input_schema": td["inputSchema"],
        }
        for tool_name, td in TOOL_DEFS.items()
        if tool_name in available
    ]


# Mapping from canonical tool name to the chat_tools.py function call.
# Each value is a coroutine factory: given the args dict, return a coroutine.
def _make_tool_dispatcher():
    """Build a dispatcher dict for tools the sub-agent can invoke."""

    async def _search_knowledge_graph(args):
        from app.services.chat_tools import search_knowledge_graph
        return await search_knowledge_graph(**args)

    async def _list_entities(args):
        from app.services.chat_tools import list_entities
        return await list_entities(**args)

    async def _get_entity_by_name(args):
        from app.services.chat_tools import get_entity_by_name
        return await get_entity_by_name(**args)

    async def _find_related_entities(args):
        # Honor the same mode-dispatch as the MCP-layer wrapper.
        mode = args.pop("mode", "neighbors")
        if mode == "types_only":
            from app.services.chat_tools import get_entity_relationships
            return await get_entity_relationships(args["entity_id"])
        from app.services.chat_tools import find_related_entities
        return await find_related_entities(**args)

    async def _search_signals(args):
        from app.services.chat_tools import search_signals
        # Accept legacy 'limit' as max_results alias.
        if "limit" in args and "max_results" not in args:
            args["max_results"] = args.pop("limit")
        return await search_signals(**args)

    async def _list_entity_profiles(args):
        from app.services.chat_tools import list_entity_profiles
        return await list_entity_profiles(**args)

    async def _read_document(args):
        from app.services.chat_tools import read_document
        return await read_document(**args)

    async def _extract_entities(args):
        from app.services.chat_tools import extract_entities
        return await extract_entities(**args)

    async def _list_meetings(args):
        from app.services.chat_tools import list_meetings
        if "limit" in args and "max_results" not in args:
            args["max_results"] = args.pop("limit")
        return await list_meetings(**args)

    async def _get_meeting_transcript(args):
        from app.services.chat_tools import get_meeting_transcript
        return await get_meeting_transcript(**args)

    async def _update_signal(args):
        from app.services.chat_tools import update_signal
        return await update_signal(**args)

    async def _delete_signal(args):
        from app.services.chat_tools import delete_signal
        return await delete_signal(**args)

    return {
        "search_knowledge_graph": _search_knowledge_graph,
        "list_entities": _list_entities,
        "get_entity_by_name": _get_entity_by_name,
        "find_related_entities": _find_related_entities,
        "search_signals": _search_signals,
        "list_entity_profiles": _list_entity_profiles,
        "read_document": _read_document,
        "extract_entities": _extract_entities,
        "list_meetings": _list_meetings,
        "get_meeting_transcript": _get_meeting_transcript,
        "update_signal": _update_signal,
        "delete_signal": _delete_signal,
    }


def _summarize_result(tool_name: str, result: Any) -> str:
    """Build a short trace summary for a tool result.

    Compact by design — the trace is a quick audit of what happened,
    not the raw data (which lives in the agent loop's context).
    """
    if isinstance(result, list):
        return f"{len(result)} items"
    if isinstance(result, dict):
        if "error" in result:
            return f"error: {result['error']}"
        if "meetings" in result:
            return f"{result.get('count', len(result.get('meetings', [])))} meetings"
        if "rows" in result:
            return f"{result.get('count', 0)} rows"
        if "id" in result and "name" in result:
            return f"{result.get('name', '?')} ({result.get('id', '?')})"
        return f"{len(result)}-field dict"
    return str(type(result).__name__)


_SYSTEM_PROMPT = """You are a knowledge-base sub-agent invoked via the ask_kb tool.

The user has given you a natural-language intent — your job is to use the
available CRUD tools to find the answer. Prefer the most-specific tool for
each step (get_entity_by_name for exact lookups, list_entities for type-scoped
retrieval, find_related_entities for graph traversal, search_knowledge_graph
only when you don't have an entity name).

Rules:
- Call tools to gather data; do not guess from prior knowledge.
- Stop calling tools as soon as you have enough to answer.
- Once you have the answer, respond with prose — no more tool calls.
- Cite specific entities and documents you used.
"""


async def ask_kb(
    intent: str,
    entity_context: list[str] | None = None,
    allow_mutations: bool = False,
    max_steps: int = 8,
) -> dict[str, Any]:
    """Run a sub-agent loop that uses CRUD tools to answer a natural-language intent.

    Args:
        intent: Free-form natural-language query (e.g. "Who on the west
            coast focuses on healthcare?").
        entity_context: Optional list of entity IDs to ground the
            sub-agent. Surfaced in the system prompt as context.
        allow_mutations: When True, gives the sub-agent access to
            update_signal / delete_signal in addition to the read-only
            CRUD set. Default False.
        max_steps: Hard cap on tool-call iterations (default 8).

    Returns:
        Dict with keys:
            answer: synthesized prose answer from the sub-agent.
            trace: list of {tool, args_summary, result_summary} entries.
            steps_used: int.
            stopped_reason: 'answered' | 'max_steps' | 'error'.
    """
    from app.services.claude_client import get_claude_client

    if not intent or not intent.strip():
        return {
            "answer": "",
            "trace": [],
            "steps_used": 0,
            "stopped_reason": "error",
            "error": "intent must be a non-empty string",
        }

    tools = _build_anthropic_tools(read_only=not allow_mutations)
    dispatcher = _make_tool_dispatcher()

    user_prompt = intent
    if entity_context:
        user_prompt += "\n\nRelevant entity IDs for context: " + ", ".join(entity_context)

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_prompt},
    ]
    trace: list[dict[str, Any]] = []
    stopped_reason = "max_steps"
    answer = ""

    client_wrapper = get_claude_client()
    raw_client = getattr(client_wrapper, "client", None) or getattr(client_wrapper, "_client", None)
    if raw_client is None:
        return {
            "answer": "",
            "trace": [],
            "steps_used": 0,
            "stopped_reason": "error",
            "error": "Anthropic client unavailable on the server",
        }

    model = getattr(client_wrapper, "model", None) or "claude-sonnet-4-6"

    for step in range(max_steps):
        try:
            response = await raw_client.messages.create(
                model=model,
                max_tokens=2048,
                system=_SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )
        except Exception as e:
            logger.exception("ask_kb: Claude API call failed")
            return {
                "answer": answer,
                "trace": trace,
                "steps_used": step,
                "stopped_reason": "error",
                "error": f"claude_api: {e}",
            }

        # Extract text and tool_use blocks from the response.
        content_blocks = response.content if hasattr(response, "content") else []
        tool_calls = []
        text_parts = []
        for block in content_blocks:
            block_type = getattr(block, "type", None)
            if block_type == "tool_use":
                tool_calls.append(block)
            elif block_type == "text":
                text_parts.append(getattr(block, "text", ""))

        if not tool_calls:
            # Sub-agent has stopped calling tools — synthesize answer.
            answer = "\n".join(t for t in text_parts if t).strip()
            stopped_reason = "answered"
            break

        # Append the assistant message verbatim (with tool_use blocks)
        # to maintain conversation continuity.
        messages.append({"role": "assistant", "content": content_blocks})

        # Execute each tool call and feed results back.
        tool_results = []
        for tc in tool_calls:
            tool_name = getattr(tc, "name", "")
            tool_args = getattr(tc, "input", {}) or {}
            tool_id = getattr(tc, "id", "")

            handler = dispatcher.get(tool_name)
            if handler is None:
                result = {"error": f"unknown_tool: {tool_name}"}
            else:
                try:
                    result = await handler(dict(tool_args))
                except Exception as e:
                    logger.exception(f"ask_kb: tool {tool_name} failed")
                    result = {"error": str(e)}

            trace.append({
                "tool": tool_name,
                "args_summary": ", ".join(f"{k}={str(v)[:40]}" for k, v in tool_args.items()),
                "result_summary": _summarize_result(tool_name, result),
            })

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": json.dumps(result, default=str)[:8000],
            })

        messages.append({"role": "user", "content": tool_results})

    return {
        "answer": answer,
        "trace": trace,
        "steps_used": min(step + 1, max_steps),
        "stopped_reason": stopped_reason,
    }
