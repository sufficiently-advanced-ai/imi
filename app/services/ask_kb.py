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
- Inference follows ``config/inference.yaml`` (operation ``ask_kb``), never a
  raw SDK client: the API key may be a placeholder on deployments that run on
  subscription auth. ``agent_sdk`` endpoints refuse tools in
  ``generate_message``, so they get their own loop, whose only tools are the
  in-process ones built here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.config import settings

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
def _make_tool_dispatcher(actor: str | None = None):
    """Build a dispatcher dict for tools the sub-agent can invoke.

    ``actor`` is the server-stamped identity of the MCP caller (ADR-009);
    ``update_signal`` records it and enforces ``MCP_REVIEW_ACTIONS``.
    """
    from app.services.review_authority import MCP_ACTOR

    stamped_actor = actor or MCP_ACTOR

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
        # ask_kb is an MCP tool: same actor stamping and review gate as the
        # MCP update_signal tool (the sub-agent's "actor" is only a claim).
        from app.services.review_authority import mcp_update_signal
        return await mcp_update_signal(args, actor=stamped_actor)

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


async def _execute_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    dispatcher: dict[str, Any],
    trace: list[dict[str, Any]],
) -> str:
    """Run one sub-agent tool call, record it in the trace, return the result text."""
    handler = dispatcher.get(tool_name)
    if handler is None:
        result: Any = {"error": f"unknown_tool: {tool_name}"}
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
    return json.dumps(result, default=str)[:8000]


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


# Inference routing label (config/inference.yaml).
_OPERATION = "ask_kb"
_SDK_SERVER_NAME = "kb"
# Whole-loop cap for the Agent SDK path. Each step is a CLI model turn; the
# message loop is bounded per call by the client's own timeout instead.
_AGENT_SDK_TIMEOUT_SECONDS = 180.0


def _error_result(
    error: str,
    answer: str = "",
    trace: list[dict[str, Any]] | None = None,
    steps_used: int = 0,
) -> dict[str, Any]:
    return {
        "answer": answer,
        "trace": trace or [],
        "steps_used": steps_used,
        "stopped_reason": "error",
        "error": error,
    }


async def ask_kb(
    intent: str,
    entity_context: list[str] | None = None,
    allow_mutations: bool = False,
    max_steps: int = 8,
    actor: str | None = None,
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
        actor: Server-stamped identity of the caller, recorded on audit rows
            for mutations (default ``mcp``; ADR-009).

    Returns:
        Dict with keys:
            answer: synthesized prose answer from the sub-agent.
            trace: list of {tool, args_summary, result_summary} entries.
            steps_used: int.
            stopped_reason: 'answered' | 'max_steps' | 'error'.
    """
    from app.services.claude_client import get_claude_client

    if not intent or not intent.strip():
        return _error_result("intent must be a non-empty string")

    tools = _build_anthropic_tools(read_only=not allow_mutations)
    dispatcher = _make_tool_dispatcher(actor)

    user_prompt = intent
    if entity_context:
        user_prompt += "\n\nRelevant entity IDs for context: " + ", ".join(entity_context)

    client = get_claude_client()
    model = settings.CLAUDE_SONNET_MODEL
    try:
        endpoint = client.registry.resolve(model, _OPERATION)
    except Exception as e:
        logger.exception("ask_kb: inference endpoint resolution failed")
        return _error_result(f"inference: {e}")

    if getattr(endpoint, "is_agent_sdk", False):
        return await _run_agent_sdk_loop(
            user_prompt, tools, dispatcher, endpoint.model, max_steps
        )
    return await _run_message_loop(client, user_prompt, tools, dispatcher, model, max_steps)


async def _run_message_loop(
    client: Any,
    user_prompt: str,
    tools: list[dict[str, Any]],
    dispatcher: dict[str, Any],
    model: str,
    max_steps: int,
) -> dict[str, Any]:
    """Tool loop over ``generate_message``, for endpoints that allow tools.

    An endpoint that doesn't is refused by ``generate_message`` (fail closed)
    and comes back here as a ``stopped_reason: error`` result.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    trace: list[dict[str, Any]] = []
    answer = ""
    steps_used = 0

    for step in range(max_steps):
        try:
            response = await client.generate_message(
                messages=messages,
                model=model,
                max_tokens=2048,
                system=_SYSTEM_PROMPT,
                tools=tools,
                operation=_OPERATION,
            )
        except Exception as e:
            logger.exception("ask_kb: Claude API call failed")
            return _error_result(f"claude_api: {e}", answer, trace, step)

        steps_used = step + 1

        # Extract text and tool_use blocks from the response.
        tool_calls = []
        text_parts = []
        content_blocks = response.content if hasattr(response, "content") else []
        for block in content_blocks:
            block_type = getattr(block, "type", None)
            if block_type == "tool_use":
                tool_calls.append(block)
            elif block_type == "text":
                text_parts.append(getattr(block, "text", ""))

        if not tool_calls:
            # Sub-agent has stopped calling tools — synthesize answer.
            answer = "\n".join(t for t in text_parts if t).strip()
            return {
                "answer": answer,
                "trace": trace,
                "steps_used": steps_used,
                "stopped_reason": "answered",
            }

        # Append the assistant message verbatim (with tool_use blocks)
        # to maintain conversation continuity.
        messages.append({"role": "assistant", "content": content_blocks})

        # Execute each tool call and feed results back.
        tool_results = []
        for tc in tool_calls:
            content = await _execute_tool(
                getattr(tc, "name", ""), getattr(tc, "input", {}) or {}, dispatcher, trace
            )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": getattr(tc, "id", ""),
                "content": content,
            })

        messages.append({"role": "user", "content": tool_results})

    return {
        "answer": answer,
        "trace": trace,
        "steps_used": steps_used,
        "stopped_reason": "max_steps",
    }


async def _run_agent_sdk_loop(
    user_prompt: str,
    tools: list[dict[str, Any]],
    dispatcher: dict[str, Any],
    model: str,
    max_steps: int,
) -> dict[str, Any]:
    """Tool loop through the Claude Agent SDK, for subscription-auth endpoints.

    The CLI drives the turns; the tools are served in process. Its only tools
    are those in ``tools``: built-ins, settings files and inherited MCP servers
    are all off, as in ``ClaudeClient._dispatch_agent_sdk``.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        TextBlock,
        create_sdk_mcp_server,
        tool,
    )

    from app.services.claude_client import ClaudeClient

    trace: list[dict[str, Any]] = []

    def _sdk_tool(spec: dict[str, Any]):
        tool_name = spec["name"]

        @tool(tool_name, spec["description"], spec["input_schema"])
        async def _handler(args: dict[str, Any]) -> dict[str, Any]:
            text = await _execute_tool(tool_name, args, dispatcher, trace)
            return {"content": [{"type": "text", "text": text}]}

        return _handler

    server = create_sdk_mcp_server(
        _SDK_SERVER_NAME, tools=[_sdk_tool(spec) for spec in tools]
    )
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        tools=[],                    # no built-ins
        allowed_tools=[f"mcp__{_SDK_SERVER_NAME}__{spec['name']}" for spec in tools],
        mcp_servers={_SDK_SERVER_NAME: server},
        strict_mcp_config=True,      # ignore MCP servers from settings files
        setting_sources=[],          # no user/project/local settings
        permission_mode="dontAsk",   # deny, never prompt a headless CLI
        max_turns=max_steps,
        env=ClaudeClient._dispatch_agent_sdk_env(),
    )

    answer = ""
    result_message = None
    try:
        async with asyncio.timeout(_AGENT_SDK_TIMEOUT_SECONDS):
            async with ClaudeSDKClient(options=options) as sdk_client:
                await sdk_client.query(user_prompt)
                async for message in sdk_client.receive_response():
                    if isinstance(message, AssistantMessage):
                        # Keep the latest turn's text: earlier turns are narration.
                        text = "".join(
                            block.text
                            for block in message.content
                            if isinstance(block, TextBlock)
                        ).strip()
                        answer = text or answer
                    elif isinstance(message, ResultMessage):
                        result_message = message
    except Exception as e:
        # TimeoutError has an empty str(), so name the type.
        logger.exception("ask_kb: Agent SDK call failed")
        return _error_result(f"agent_sdk: {type(e).__name__}: {e}", answer, trace)

    steps_used = min(getattr(result_message, "num_turns", 0) or 0, max_steps)
    subtype = getattr(result_message, "subtype", "")
    if subtype == "error_max_turns":
        return {
            "answer": answer,
            "trace": trace,
            "steps_used": steps_used,
            "stopped_reason": "max_steps",
        }
    if result_message is None or getattr(result_message, "is_error", False):
        detail = getattr(result_message, "result", None) or subtype or "no result"
        return _error_result(f"agent_sdk: {detail}", answer, trace, steps_used)

    return {
        "answer": (getattr(result_message, "result", None) or answer).strip(),
        "trace": trace,
        "steps_used": steps_used,
        "stopped_reason": "answered",
    }
