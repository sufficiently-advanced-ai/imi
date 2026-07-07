"""
MCP Tools for ChatAgent — Knowledge graph query & maintenance tools.

Provides Model Context Protocol (MCP) tools that enable the ChatAgent
to query the knowledge base and perform graph maintenance:

Query Tools (13):
- search_knowledge_graph: Search for documents/entities
- read_document: Read document content
- extract_entities: Extract entities from text
- get_entity_by_name: Look up entity by name
- find_related_entities: Graph traversal (mode='neighbors' for connected entities,
    mode='types_only' for relationship-type inventory; consolidates the previous
    get_entity_relationships tool)
- list_entity_profiles: Bulk-fetch profiles for a set of entity IDs
    (renamed from get_entity_profiles for verb consistency with list_entities)
- search_meeting_transcripts: Full-text search across meeting transcripts
- list_meeting_documents: List meeting files with metadata for path discovery
- get_entity_context_summary: Context-aware entity summary with recency data
- query_graph_cypher: Execute read-only Cypher queries against Neo4j
- search_signals: Search meeting signals (decisions, action items, key points,
    insights); graph-first lookup when entity_id is given (consolidates the
    previous query_signals + get_entity_signals tools)

Signal Mutation Tools (2):
- update_signal: Update signal status/content/owner/due_date
- delete_signal: Permanently remove a signal

Graph Maintenance Tools (7):
- graph_add_node: Add a new entity node
- graph_update_node: Update node properties
- graph_delete_node: Delete an entity node
- graph_merge_nodes: Merge duplicate entities
- graph_add_edge: Add a relationship
- graph_update_edge: Update relationship properties
- graph_delete_edge: Delete a relationship
"""

import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from app.services.mcp_tool_definitions import chat_tool_args

# Try to import Claude Agent SDK
try:
    from claude_agent_sdk import McpSdkServerConfig, tool

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    McpSdkServerConfig = None

    # Fallback decorator for environments without the SDK
    def tool(name: str, description: str, schema: dict[str, Any]):
        def decorator(func):
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            wrapper._tool_name = name
            wrapper._tool_description = description
            wrapper._tool_schema = schema
            return wrapper

        return decorator


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-request context via ContextVar (safe for concurrent async requests)
# ---------------------------------------------------------------------------


@dataclass
class ChatToolContext:
    execution_id: str | None = None
    bot_id: str | None = None


_ctx_var: ContextVar[ChatToolContext | None] = ContextVar(
    "chat_tool_ctx", default=None
)


def _get_ctx() -> ChatToolContext:
    return _ctx_var.get() or ChatToolContext()


# ---------------------------------------------------------------------------
# SSE emission helpers
# ---------------------------------------------------------------------------


async def _emit_tool_start(tool_name: str, args: dict) -> None:
    """Emit a tool_start SSE event if streaming is active."""
    _sensitive_keys = {"text", "content", "email", "owner_id"}
    safe_args = {
        k: ("<redacted>" if k in _sensitive_keys else v)
        for k, v in (args or {}).items()
    }
    import sys as _sys

    _sys.stderr.write(
        f"[MCP_TOOL] {tool_name} called | input={json.dumps(safe_args, default=str)[:200]}\n"
    )
    _sys.stderr.flush()
    if not _get_ctx().execution_id:
        return
    try:
        from app.services.sse_manager import sse_manager

        await sse_manager.send_event(
            _get_ctx().execution_id,
            "tool_start",
            {
                "tool_name": tool_name,
                "tool_args": args,
                "tool_id": f"mcp_{tool_name}_{int(time.time() * 1000) % 100000}",
            },
        )
    except Exception as e:
        logger.warning(f"Failed to emit tool_start for {tool_name}: {e}")


async def _emit_tool_complete(tool_name: str, duration: float, summary: str) -> None:
    """Emit a tool_complete SSE event if streaming is active."""
    import sys as _sys

    _sys.stderr.write(
        f"[MCP_TOOL] {tool_name} complete | {duration:.2f}s | {summary[:120]}\n"
    )
    _sys.stderr.flush()
    if not _get_ctx().execution_id:
        return
    try:
        from app.services.sse_manager import sse_manager

        await sse_manager.send_event(
            _get_ctx().execution_id,
            "tool_complete",
            {
                "tool_name": tool_name,
                "duration": duration,
                "result_summary": summary,
                "status": "success",
            },
        )
    except Exception as e:
        logger.warning(f"Failed to emit tool_complete for {tool_name}: {e}")


async def _emit_tool_error(tool_name: str, duration: float, error: str) -> None:
    """Emit a tool_error SSE event if streaming is active."""
    import sys as _sys

    _sys.stderr.write(
        f"[MCP_TOOL] {tool_name} ERROR | {duration:.2f}s | {error[:200]}\n"
    )
    _sys.stderr.flush()
    if not _get_ctx().execution_id:
        return
    try:
        from app.services.sse_manager import sse_manager

        await sse_manager.send_event(
            _get_ctx().execution_id,
            "tool_error",
            {
                "tool_name": tool_name,
                "duration": duration,
                "error_message": error,
                "status": "error",
            },
        )
    except Exception as e:
        logger.warning(f"Failed to emit tool_error for {tool_name}: {e}")


def _ok(text: str) -> dict:
    """Return a standard MCP success content block."""
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    """Return a standard MCP error content block."""
    return {"content": [{"type": "text", "text": f"Error: {text}"}]}


# ============================================================================
# QUERY TOOLS — delegate to app/services/chat_tools.py
#
# Verb taxonomy and parameter conventions are documented in
# docs/mcp_tool_conventions.md. New tools should follow that doc; existing
# inconsistencies are flagged in the Phase C cleanup plan and will be
# resolved there.
# ============================================================================


@tool(*chat_tool_args("search_knowledge_graph"))
async def search_knowledge_graph_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "search_knowledge_graph"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import search_knowledge_graph

        result = await search_knowledge_graph(**args)
        duration = time.time() - start
        count = len(result) if isinstance(result, list) else 0
        await _emit_tool_complete(tool_name, duration, f"Found {count} results")
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(*chat_tool_args("read_document"))
async def read_document_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "read_document"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import read_document

        result = await read_document(**args)
        duration = time.time() - start
        content_len = len(result.get("content", "")) if isinstance(result, dict) else 0
        await _emit_tool_complete(
            tool_name,
            duration,
            f"Read {content_len} chars from {args.get('path', '?')}",
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(*chat_tool_args("list_entities"))
async def list_entities_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "list_entities"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import list_entities

        result = await list_entities(**args)
        duration = time.time() - start
        count = len(result) if isinstance(result, list) else 0
        await _emit_tool_complete(
            tool_name,
            duration,
            f"Listed {count} entities of type '{args.get('entity_type', '?')}'",
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(*chat_tool_args("extract_entities"))
async def extract_entities_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "extract_entities"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import extract_entities

        result = await extract_entities(**args)
        duration = time.time() - start
        total = sum(len(v) for v in result.values()) if isinstance(result, dict) else 0
        await _emit_tool_complete(tool_name, duration, f"Extracted {total} entities")
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(*chat_tool_args("get_entity_by_name"))
async def get_entity_by_name_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "get_entity_by_name"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import get_entity_by_name

        result = await get_entity_by_name(**args)
        duration = time.time() - start
        if result:
            await _emit_tool_complete(
                tool_name,
                duration,
                f"Found: {result.get('name', '?')} ({result.get('id', '?')})",
            )
        else:
            await _emit_tool_complete(
                tool_name, duration, f"No entity found for '{args.get('name')}'"
            )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(*chat_tool_args("find_related_entities"))
async def find_related_entities_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "find_related_entities"
    await _emit_tool_start(tool_name, args)
    try:
        mode = args.get("mode", "neighbors")
        if mode not in {"neighbors", "types_only"}:
            raise ValueError("mode must be 'neighbors' or 'types_only'")
        # Strip 'mode' before delegating; underlying functions don't accept it.
        call_args = {k: v for k, v in args.items() if k != "mode"}

        # The shared MCP contract uses singular 'relationship_type' (one type
        # to traverse), but the underlying chat_tools function still takes a
        # 'relationship_types' list. Translate so callers using the documented
        # parameter don't hit TypeError.
        rel_type = call_args.pop("relationship_type", None)
        if rel_type:
            call_args["relationship_types"] = [rel_type]

        if mode == "types_only":
            from app.services.chat_tools import get_entity_relationships

            # types_only takes entity_id only; ignore other filter args.
            raw = await get_entity_relationships(call_args["entity_id"])
            # Slim the payload to a type inventory; full neighbor lists can balloon
            # MCP responses for high-degree entities (find_related_entities is the
            # right tool when neighbors are needed).
            result = {
                "entity_id": raw.get("entity_id") if isinstance(raw, dict) else None,
                "outgoing_relationship_types": raw.get("available_outgoing_types", [])
                if isinstance(raw, dict)
                else [],
                "incoming_relationship_types": raw.get("available_incoming_types", [])
                if isinstance(raw, dict)
                else [],
                "total_outgoing": raw.get("total_outgoing", 0)
                if isinstance(raw, dict)
                else 0,
                "total_incoming": raw.get("total_incoming", 0)
                if isinstance(raw, dict)
                else 0,
            }
            duration = time.time() - start
            await _emit_tool_complete(
                tool_name,
                duration,
                f"{result['total_outgoing']} outgoing, {result['total_incoming']} incoming relationship types",
            )
            return _ok(json.dumps(result, default=str))

        from app.services.chat_tools import find_related_entities

        result = await find_related_entities(**call_args)
        duration = time.time() - start
        count = len(result) if isinstance(result, list) else 0
        await _emit_tool_complete(
            tool_name, duration, f"Found {count} related entities"
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(*chat_tool_args("list_entity_profiles"))
async def list_entity_profiles_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "list_entity_profiles"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import list_entity_profiles

        result = await list_entity_profiles(**args)
        duration = time.time() - start
        count = len(result) if isinstance(result, list) else 0
        found = (
            sum(1 for p in result if "error" not in p)
            if isinstance(result, list)
            else 0
        )
        await _emit_tool_complete(
            tool_name, duration, f"{found}/{count} entity profiles retrieved"
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))




# ============================================================================
# CYPHER QUERY TOOL — direct Neo4j access for complex queries
# ============================================================================


@tool(
    "mcp__chat__search_meeting_transcripts",
    "Search meeting transcripts for specific text, quotes, or topics. "
    "Use when looking for what someone said, exact quotes, or specific discussion content. "
    "Returns transcript excerpts with speaker attribution. "
    "Unlike search_knowledge_graph (which searches entity names), this searches actual meeting conversation text.",
    {
        "query": str,
        "speaker": str,
        "date_from": str,
        "date_to": str,
        "max_results": int,
    },
)
async def search_meeting_transcripts_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "search_meeting_transcripts"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import search_meeting_transcripts

        result = await search_meeting_transcripts(**args)
        duration = time.time() - start
        count = len(result) if isinstance(result, list) else 0
        await _emit_tool_complete(
            tool_name, duration, f"Found {count} transcript matches"
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(
    "mcp__chat__list_meeting_documents",
    "List meeting documents with metadata. Use to discover meeting file paths "
    "(which are needed for read_document). Can filter by meeting_id, date range, or participant. "
    "Returns file paths, titles, dates, and participants.",
    {
        "meeting_id": str,
        "date_from": str,
        "date_to": str,
        "participant": str,
        "max_results": int,
    },
)
async def list_meeting_documents_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "list_meeting_documents"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import list_meeting_documents

        result = await list_meeting_documents(**args)
        duration = time.time() - start
        count = len(result) if isinstance(result, list) else 0
        await _emit_tool_complete(
            tool_name, duration, f"Found {count} meeting documents"
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(
    "mcp__chat__get_entity_context_summary",
    "Get a context-aware summary for an entity including: last meeting attended, "
    "recent signals, open action items, and related people with their recency data. "
    "Use this instead of just get_entity_relationships when you need to understand "
    "who has the most current context or is best positioned to act.",
    {
        "entity_id": str,
    },
)
async def get_entity_context_summary_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "get_entity_context_summary"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import get_entity_context_summary

        result = await get_entity_context_summary(**args)
        duration = time.time() - start
        if "error" in result and not result.get("entity"):
            await _emit_tool_error(tool_name, duration, result["error"])
        else:
            meetings = result.get("total_meetings_attended", 0)
            items = len(result.get("open_action_items", []))
            await _emit_tool_complete(
                tool_name, duration, f"{meetings} meetings, {items} open items"
            )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(
    "mcp__chat__query_graph_cypher",
    "Execute a read-only Cypher query against Neo4j. "
    "Use for complex queries with filtering, multi-hop traversal, or compound conditions. "
    "All entity nodes have label :Entity plus a type label (e.g. :Member:Entity). "
    "All nodes share: id, name, entity_type, canonical_name. "
    "Example: MATCH (m:Member:Entity)-[:FOCUS_AREAS]->(f:Entity) "
    "WHERE m.geography =~ '.*(CA|WA).*' RETURN m.name, m.geography, f.name "
    "Returns {rows: [...], count: N}. Max 100 rows. Read-only — no CREATE/SET/DELETE. "
    "Falls back gracefully if Neo4j is unavailable.",
    {
        "cypher": str,
        "parameters": dict,
        "limit": int,
    },
)
async def query_graph_cypher_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "query_graph_cypher"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import execute_cypher_query

        # Coerce parameters (agent may send string or None)
        if "parameters" in args:
            params = args["parameters"]
            if isinstance(params, str):
                params = params.strip()
                if not params or params in ("null", "None", "{}", "''", '""'):
                    args.pop("parameters")
                else:
                    try:
                        parsed = json.loads(params)
                        args["parameters"] = parsed if isinstance(parsed, dict) else {}
                    except (json.JSONDecodeError, TypeError):
                        args.pop("parameters")
            elif not isinstance(params, dict):
                args.pop("parameters")

        result = await execute_cypher_query(**args)
        duration = time.time() - start
        if "error" in result:
            await _emit_tool_error(tool_name, duration, result["error"])
        else:
            count = result.get("count", 0)
            await _emit_tool_complete(tool_name, duration, f"Returned {count} rows")
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


# ============================================================================
# SIGNAL QUERY TOOLS (2) — delegate to app/services/chat_tools.py
# ============================================================================


@tool(*chat_tool_args("search_signals"))
async def search_signals_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "search_signals"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import search_signals

        # Accept legacy 'limit' as an alias for 'max_results' so older
        # callers don't break mid-rollout. Always strip 'limit' to avoid
        # forwarding an unexpected kwarg; max_results wins when both are set.
        call_args = dict(args)
        legacy_limit = call_args.pop("limit", None)
        if "max_results" not in call_args and legacy_limit is not None:
            call_args["max_results"] = legacy_limit
        result = await search_signals(**call_args)
        duration = time.time() - start
        count = len(result) if isinstance(result, list) else 0
        await _emit_tool_complete(tool_name, duration, f"Found {count} signals")
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


# ============================================================================
# GENERAL MEMORY TOOLS — delegate to app/services/chat_tools.py (G4 wiring)
# ============================================================================


@tool(*chat_tool_args("capture_thought"))
async def capture_thought_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "capture_thought"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import capture_thought

        result = await capture_thought(**args)
        duration = time.time() - start
        if result.get("success"):
            summary = (
                "Duplicate — returned existing capture"
                if result.get("deduped")
                else f"Captured {result.get('id')}"
            )
            await _emit_tool_complete(tool_name, duration, summary)
        else:
            await _emit_tool_error(
                tool_name, duration, result.get("error", "Unknown error")
            )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(*chat_tool_args("memory_recall"))
async def memory_recall_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "memory_recall"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import memory_recall

        result = await memory_recall(**args)
        duration = time.time() - start
        if result.get("success") is False:
            await _emit_tool_error(
                tool_name, duration, result.get("error", "Unknown error")
            )
        else:
            count = len(result.get("memories", []))
            await _emit_tool_complete(
                tool_name, duration, f"Recalled {count} memories"
            )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


# ============================================================================
# SIGNAL MUTATION TOOLS (2) — delegate to app/services/chat_tools.py
# ============================================================================


@tool(*chat_tool_args("update_signal"))
async def update_signal_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "update_signal"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import update_signal

        result = await update_signal(**args)
        duration = time.time() - start
        if result.get("success"):
            sig = result.get("signal", {})
            await _emit_tool_complete(
                tool_name,
                duration,
                f"Updated {sig.get('type', 'signal')} (neo4j_synced={result.get('neo4j_synced')})",
            )
        else:
            await _emit_tool_error(
                tool_name, duration, result.get("error", "Unknown error")
            )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(*chat_tool_args("delete_signal"))
async def delete_signal_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "delete_signal"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import delete_signal

        result = await delete_signal(**args)
        duration = time.time() - start
        if result.get("success"):
            sig = result.get("deleted_signal", {})
            await _emit_tool_complete(
                tool_name,
                duration,
                f"Deleted {sig.get('type', 'signal')} (neo4j_synced={result.get('neo4j_synced')})",
            )
        else:
            await _emit_tool_error(
                tool_name, duration, result.get("error", "Unknown error")
            )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


_SIGNAL_MUTATION_TOOLS = [
    update_signal_tool,
    delete_signal_tool,
]


# ============================================================================
# DECISION INTELLIGENCE TOOLS (NEW — powered by Semantica)
# ============================================================================


@tool(
    "mcp__chat__find_decision_precedents",
    "Find similar past decisions as precedents using semantic similarity. "
    "Use when the user asks about prior decisions, historical context, or 'have we done this before?'",
    {
        "query": str,
        "category": str,
        "limit": int,
    },
)
async def find_decision_precedents_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "find_decision_precedents"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import find_decision_precedents

        result = await find_decision_precedents(**args)
        duration = time.time() - start
        if (
            isinstance(result, list)
            and result
            and isinstance(result[0], dict)
            and result[0].get("error")
        ):
            error = result[0]["error"]
            await _emit_tool_error(tool_name, duration, error)
            return _err(error)
        count = len(result) if isinstance(result, list) else 0
        await _emit_tool_complete(tool_name, duration, f"Found {count} precedents")
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(
    "mcp__chat__decision_chain",
    "Walk the causal chain around a decision in either direction. "
    "Two modes (set via 'direction'):\n"
    "  - direction='upstream' (default): show the decisions and signals that LED TO this one "
    "(the precedent chain). Replaces the former trace_decision_chain tool.\n"
    "  - direction='downstream': show what FLOWED FROM this decision — follow-up decisions, "
    "action items, and entity changes that trace back to it. Replaces the former "
    "decision_influence tool.\n"
    "Use search_signals (signal_type='decision') first to find the decision ID.",
    {
        "decision_id": str,
        "direction": str,
    },
)
async def decision_chain_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Unified upstream/downstream causal traversal around a decision.

    Consolidates the previous trace_decision_chain (upstream) and
    decision_influence (downstream) tools, which answered different
    halves of the same causal question.
    """
    start = time.time()
    tool_name = "decision_chain"
    await _emit_tool_start(tool_name, args)
    try:
        direction = args.get("direction", "upstream")
        decision_id = args["decision_id"]

        if direction == "downstream":
            from app.services.chat_tools import decision_influence

            result = await decision_influence(decision_id=decision_id)
        else:
            from app.services.chat_tools import trace_decision_chain

            result = await trace_decision_chain(decision_id=decision_id)

        duration = time.time() - start
        if (
            isinstance(result, list)
            and result
            and isinstance(result[0], dict)
            and result[0].get("error")
        ):
            error = result[0]["error"]
            await _emit_tool_error(tool_name, duration, error)
            return _err(error)
        if isinstance(result, dict) and result.get("error"):
            await _emit_tool_error(tool_name, duration, result["error"])
            return _err(result["error"])
        count = len(result) if isinstance(result, list) else 1
        await _emit_tool_complete(
            tool_name, duration, f"Walked {direction} chain ({count} items)"
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(*chat_tool_args("list_decisions"))
async def list_decisions_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "list_decisions"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.decision_view import list_decisions

        result = list_decisions(
            state=args.get("state"),
            owner_id=args.get("owner_id"),
            client_id=args.get("client_id"),
            date_from=args.get("date_from"),
            date_to=args.get("date_to"),
            max_results=args.get("max_results", 50),
        )
        duration = time.time() - start
        total = result.get("total", 0)
        await _emit_tool_complete(tool_name, duration, f"Found {total} decisions")
        return _ok(json.dumps(result, default=str))
    except ValueError as exc:
        await _emit_tool_error(tool_name, time.time() - start, str(exc))
        return _err(str(exc))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(*chat_tool_args("get_decision"))
async def get_decision_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "get_decision"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.decision_view import get_decision

        decision_id = args.get("decision_id")
        if not decision_id:
            await _emit_tool_error(
                tool_name, time.time() - start, "decision_id is required"
            )
            return _err("decision_id is required")
        result = get_decision(decision_id)
        if result is None:
            msg = f"Decision '{decision_id}' not found"
            await _emit_tool_error(tool_name, time.time() - start, msg)
            return _err(msg)
        duration = time.time() - start
        await _emit_tool_complete(
            tool_name, duration, f"Retrieved decision {decision_id}"
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


# Decision-tool registration list defined below, after all the decision tool
# functions have been declared (decision_influence_tool and decision_stats_tool
# live in the temporal section for historical reasons but are semantically
# decision tools — they were moved into _DECISION_TOOLS in Phase A of the
# MCP surface cleanup).


# ============================================================================
# TEMPORAL TOOLS (Issue #864) — powered by Semantica temporal + higher-order queries
# ============================================================================


@tool(
    "mcp__chat__entity_at_time",
    "Get an entity's state at a specific point in time. "
    "Use when the user asks 'what did we know about X at time T?' or needs historical entity data.",
    {
        "entity_id": str,
        "timestamp": str,
    },
)
async def entity_at_time_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "entity_at_time"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import entity_at_time

        result = await entity_at_time(**args)
        duration = time.time() - start
        if isinstance(result, dict) and result.get("error"):
            await _emit_tool_error(tool_name, duration, result["error"])
            return _err(result["error"])
        await _emit_tool_complete(
            tool_name, duration, f"Retrieved state for {args.get('entity_id', '?')}"
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(
    "mcp__chat__active_relationships_at_time",
    "Get relationships that were active for an entity at a specific time. "
    "Use for historical relationship queries.",
    {
        "entity_id": str,
        "timestamp": str,
    },
)
async def active_relationships_at_time_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "active_relationships_at_time"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import active_relationships_at_time

        result = await active_relationships_at_time(**args)
        duration = time.time() - start
        if (
            isinstance(result, list)
            and result
            and isinstance(result[0], dict)
            and result[0].get("error")
        ):
            await _emit_tool_error(tool_name, duration, result[0]["error"])
            return _err(result[0]["error"])
        count = len(result) if isinstance(result, list) else 0
        await _emit_tool_complete(
            tool_name, duration, f"Found {count} active relationships"
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(
    "mcp__chat__get_entity_provenance",
    "Trace where information about an entity came from — which meetings, documents, and signals "
    "originally introduced or modified it, in chronological order. "
    "Use when the user asks 'where did this come from?', 'when was X first mentioned?', or "
    "needs source attribution for a piece of information.",
    {
        "entity_id": str,
    },
)
async def get_entity_provenance_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "get_entity_provenance"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import get_entity_provenance

        result = await get_entity_provenance(**args)
        duration = time.time() - start
        if isinstance(result, dict) and result.get("error"):
            await _emit_tool_error(tool_name, duration, result["error"])
            return _err(result["error"])
        history_count = len(result.get("history", []))
        await _emit_tool_complete(
            tool_name, duration, f"Retrieved {history_count} provenance entries"
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


# decision_influence_tool was consolidated into decision_chain_tool in
# Phase C2 (direction='downstream'). The underlying chat_tools.decision_influence
# function is still called from there.


@tool(
    "mcp__chat__decision_stats",
    "Get aggregate decision statistics and insights across the knowledge base. "
    "Use when the user asks about decision-making patterns or wants an overview.",
    {},
)
async def decision_stats_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "decision_stats"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import decision_stats

        result = await decision_stats()
        duration = time.time() - start
        if isinstance(result, dict) and result.get("error"):
            await _emit_tool_error(tool_name, duration, result["error"])
            return _err(result["error"])
        await _emit_tool_complete(tool_name, duration, "Retrieved decision stats")
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(
    "mcp__chat__what_changed",
    "Show what changed for an entity over a time range. "
    "Diffs the entity's state between two timestamps and lists added/removed/modified fields. "
    "If only date_from is provided, diffs from that time to now. "
    "If both date_from and date_to are provided, diffs between those exact bounds. "
    "Use for 'what changed since last week?', 'what's new with X?', or 'how did X evolve "
    "between date A and date B?'",
    {
        "entity_id": str,
        "date_from": str,
        "date_to": str,
    },
)
async def what_changed_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Unified change-diff tool.

    Consolidates the previous what_changed (open-ended-from-now) and
    what_changed_between (bounded) tools by treating date_to as optional.
    Underlying chat_tools.py functions stay separate; this tool dispatches.
    """
    start = time.time()
    tool_name = "what_changed"
    await _emit_tool_start(tool_name, args)
    try:
        entity_id = args["entity_id"]
        date_from = args.get("date_from") or args.get("since") or args.get("start")
        date_to = args.get("date_to") or args.get("end")

        if date_to:
            from app.services.chat_tools import what_changed_between

            result = await what_changed_between(
                entity_id=entity_id, start=date_from, end=date_to
            )
        else:
            from app.services.chat_tools import what_changed

            result = await what_changed(entity_id=entity_id, since=date_from)

        duration = time.time() - start
        if isinstance(result, dict) and result.get("error"):
            await _emit_tool_error(tool_name, duration, result["error"])
            return _err(result["error"])
        count = len(result.get("changes", []))
        await _emit_tool_complete(tool_name, duration, f"Found {count} changes")
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(
    "mcp__chat__graph_as_of",
    "Reconstruct a subgraph around an entity at a past point in time. Returns the entity plus "
    "the connected entities and relationships as they existed at that timestamp, walking outward "
    "max_depth steps. "
    "Use when you need the entity AND its surrounding context at time T. For just the entity's "
    "own state at time T, use entity_at_time. For the maximum reachable set under temporal-active-"
    "edge semantics, use temporal_blast_radius.",
    {
        "entity_id": str,
        "timestamp": str,
        "max_depth": int,
    },
)
async def graph_as_of_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "graph_as_of"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import graph_as_of

        # Parameter standardization: accept both 'max_depth' (preferred) and
        # legacy 'depth' for the depth parameter. Underlying function expects
        # depth. Translate at the wrapper boundary.
        call_args = dict(args)
        if "max_depth" in call_args and "depth" not in call_args:
            call_args["depth"] = call_args.pop("max_depth")

        result = await graph_as_of(**call_args)
        duration = time.time() - start
        if isinstance(result, dict) and result.get("error"):
            await _emit_tool_error(tool_name, duration, result["error"])
            return _err(result["error"])
        node_count = len(result.get("nodes", []))
        await _emit_tool_complete(
            tool_name, duration, f"Reconstructed {node_count} nodes"
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(
    "mcp__chat__find_contradictions",
    "Find signals about an entity that say opposing things at different times — for example, "
    "'project on track' in one meeting followed by 'project delayed' a week later. "
    "Use when the user asks about inconsistencies, conflicting information, or whether the "
    "story has changed. Optionally bound the search to a date range.",
    {
        "entity_id": str,
        "date_from": str,
        "date_to": str,
    },
)
async def find_contradictions_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "find_contradictions"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import find_contradictions

        result = await find_contradictions(**args)
        duration = time.time() - start
        if isinstance(result, dict) and result.get("error"):
            await _emit_tool_error(tool_name, duration, result["error"])
            return _err(result["error"])
        count = len(result.get("contradictions", []))
        await _emit_tool_complete(tool_name, duration, f"Found {count} contradictions")
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


@tool(
    "mcp__chat__temporal_blast_radius",
    "Find every entity that was reachable from the given entity through the graph at a specific "
    "point in time, walking outward up to max_depth steps and only following relationships that "
    "were active at that time. "
    "Use for 'what depends on X?', 'who/what would be affected if X changed at time T?', or "
    "impact analysis. For just the directly-connected entities at time T, use graph_as_of with "
    "max_depth=1 instead.",
    {
        "entity_id": str,
        "timestamp": str,
        "max_depth": int,
    },
)
async def temporal_blast_radius_tool(args: dict[str, Any]) -> dict[str, Any]:
    start = time.time()
    tool_name = "temporal_blast_radius"
    await _emit_tool_start(tool_name, args)
    try:
        from app.services.chat_tools import temporal_blast_radius

        # Parameter standardization: accept both 'timestamp' (preferred) and
        # legacy 'at_time' for the time parameter. Underlying function expects
        # at_time. Translate at the wrapper boundary.
        call_args = dict(args)
        if "timestamp" in call_args and "at_time" not in call_args:
            call_args["at_time"] = call_args.pop("timestamp")

        result = await temporal_blast_radius(**call_args)
        duration = time.time() - start
        if isinstance(result, dict) and result.get("error"):
            await _emit_tool_error(tool_name, duration, result["error"])
            return _err(result["error"])
        node_count = len(result.get("nodes", []))
        await _emit_tool_complete(
            tool_name, duration, f"Found {node_count} nodes in blast radius"
        )
        return _ok(json.dumps(result, default=str))
    except Exception as e:
        await _emit_tool_error(tool_name, time.time() - start, str(e))
        return _err(str(e))


_TEMPORAL_TOOLS = [
    entity_at_time_tool,
    active_relationships_at_time_tool,
    get_entity_provenance_tool,
    what_changed_tool,
    graph_as_of_tool,
    find_contradictions_tool,
    temporal_blast_radius_tool,
]


# Decision-tool registration list. Defined here (after the temporal section)
# so it can reference decision_stats_tool, which lives in the temporal block
# for historical reasons. decision_chain_tool consolidates the previous
# trace_decision_chain + decision_influence tools (direction='upstream'
# vs 'downstream').
_DECISION_TOOLS = [
    list_decisions_tool,
    get_decision_tool,
    find_decision_precedents_tool,
    decision_chain_tool,
    decision_stats_tool,
]


# ============================================================================
# GRAPH MAINTENANCE TOOLS (7) — delegate to AgentToolRegistry
# ============================================================================


def _get_registry():
    """Lazy import to avoid circular dependencies."""
    from app.git_ops import git_ops
    from app.services.agent_tools import AgentToolRegistry
    from app.services.claude_client import ClaudeClient
    from app.services.file_cache import file_cache

    return AgentToolRegistry(ClaudeClient(), git_ops, file_cache)


def _coerce_properties(args: dict) -> dict:
    """Coerce the optional 'properties' field to a dict or remove it.

    Claude sometimes sends properties as a JSON string, an empty string,
    or other non-dict types. This normalises the value so downstream
    Neo4j code (which calls .items()) never crashes.
    """
    if "properties" not in args:
        return args

    props = args["properties"]

    # Already a dict — keep it (or drop if empty)
    if isinstance(props, dict):
        if not props:
            return {k: v for k, v in args.items() if k != "properties"}
        return args

    # String — try to JSON-parse it
    if isinstance(props, str):
        props = props.strip()
        if not props or props in ("null", "None", "{}", "''", '""'):
            return {k: v for k, v in args.items() if k != "properties"}
        try:
            parsed = json.loads(props)
            if isinstance(parsed, dict):
                return {**args, "properties": parsed}
        except (json.JSONDecodeError, TypeError):
            pass
        # Unparseable string — drop it to avoid downstream crash
        logger.warning(f"Dropping non-dict properties value: {props!r}")
        return {k: v for k, v in args.items() if k != "properties"}

    # Any other type — drop it
    logger.warning(f"Dropping non-dict properties type: {type(props).__name__}")
    return {k: v for k, v in args.items() if k != "properties"}


async def _execute_graph_tool(mcp_name: str, registry_name: str, args: dict) -> dict:
    """Common wrapper for graph maintenance tools via AgentToolRegistry."""
    start = time.time()
    await _emit_tool_start(mcp_name, args)
    try:
        # Coerce properties before passing to registry
        clean_args = _coerce_properties(args)
        registry = _get_registry()
        result = await registry.execute_tool(
            tool_name=registry_name,
            inputs=clean_args,
            agent_name="ChatAgent",
        )
        duration = time.time() - start
        if result.success:
            await _emit_tool_complete(mcp_name, duration, f"{registry_name} completed")
            return _ok(json.dumps(result.data, default=str))
        else:
            await _emit_tool_error(mcp_name, duration, result.error or "Unknown error")
            return _err(result.error or "Unknown error")
    except Exception as e:
        await _emit_tool_error(mcp_name, time.time() - start, str(e))
        return _err(str(e))


@tool(
    "mcp__chat__graph_add_node",
    "Add a new entity node to the knowledge graph. Required: entity_type and name. The optional properties field must be a JSON object (not a string).",
    {
        "entity_type": str,
        "name": str,
        "properties": dict,
    },
)
async def graph_add_node_tool(args: dict[str, Any]) -> dict[str, Any]:
    return await _execute_graph_tool("graph_add_node", "graph_add_node", args)


@tool(
    "mcp__chat__graph_update_node",
    "Update properties on an existing entity node in the knowledge graph",
    {
        "entity_id": str,
        "properties": dict,
    },
)
async def graph_update_node_tool(args: dict[str, Any]) -> dict[str, Any]:
    return await _execute_graph_tool("graph_update_node", "graph_update_node", args)


@tool(
    "mcp__chat__graph_delete_node",
    "Delete an entity node from the knowledge graph and archive its source file (soft-delete with is_archived flag). Cascades to relationships by default. The entity will not reappear on graph rebuilds.",
    {
        "entity_id": str,
        "cascade": bool,
    },
)
async def graph_delete_node_tool(args: dict[str, Any]) -> dict[str, Any]:
    return await _execute_graph_tool("graph_delete_node", "graph_delete_node", args)


@tool(
    "mcp__chat__graph_merge_nodes",
    "Merge a duplicate entity into a primary entity, transferring all relationships. Archives the duplicate's source file (soft-delete) so it won't reappear on graph rebuilds.",
    {
        "primary_id": str,
        "duplicate_id": str,
        "strategy": str,
    },
)
async def graph_merge_nodes_tool(args: dict[str, Any]) -> dict[str, Any]:
    return await _execute_graph_tool("graph_merge_nodes", "graph_merge_nodes", args)


@tool(
    "mcp__chat__graph_add_edge",
    "Add a relationship between two entities in the knowledge graph. Use ONLY valid relationship types from the domain schema (e.g. works_on_projects, member_of_team). The properties field is optional.",
    {
        "source_id": str,
        "target_id": str,
        "relationship_type": str,
        "properties": dict,
    },
)
async def graph_add_edge_tool(args: dict[str, Any]) -> dict[str, Any]:
    return await _execute_graph_tool("graph_add_edge", "graph_add_edge", args)


@tool(
    "mcp__chat__graph_update_edge",
    "Update properties on an existing relationship in the knowledge graph. The properties field must be a JSON object.",
    {
        "source_id": str,
        "target_id": str,
        "relationship_type": str,
        "properties": dict,
    },
)
async def graph_update_edge_tool(args: dict[str, Any]) -> dict[str, Any]:
    return await _execute_graph_tool("graph_update_edge", "graph_update_edge", args)


@tool(
    "mcp__chat__graph_delete_edge",
    "Delete a relationship between two entities in the knowledge graph",
    {
        "source_id": str,
        "target_id": str,
        "relationship_type": str,
    },
)
async def graph_delete_edge_tool(args: dict[str, Any]) -> dict[str, Any]:
    return await _execute_graph_tool("graph_delete_edge", "graph_delete_edge", args)


# ============================================================================
# SERVER FACTORY
# ============================================================================

# All query tools (always included)
_QUERY_TOOLS = [
    search_knowledge_graph_tool,
    read_document_tool,
    list_entities_tool,
    extract_entities_tool,
    get_entity_by_name_tool,
    find_related_entities_tool,
    list_entity_profiles_tool,
    search_meeting_transcripts_tool,
    list_meeting_documents_tool,
    get_entity_context_summary_tool,
    query_graph_cypher_tool,
    search_signals_tool,
    capture_thought_tool,
    memory_recall_tool,
]

# Graph maintenance tools (always included)
_GRAPH_TOOLS = [
    graph_add_node_tool,
    graph_update_node_tool,
    graph_delete_node_tool,
    graph_merge_nodes_tool,
    graph_add_edge_tool,
    graph_update_edge_tool,
    graph_delete_edge_tool,
]


def _python_type_to_json_schema(py_type: type) -> dict[str, Any]:
    """Convert Python types to JSON Schema."""
    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    return {"type": type_map.get(py_type, "string")}


def _build_mcp_server(name: str, sdk_tools: list):
    """
    Build an MCP server from SdkMcpTool instances, returning content items
    directly instead of CallToolResult to avoid the Pydantic iteration bug
    in mcp>=1.12 (CallToolResult is iterable, yielding field tuples).
    """
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    server = Server(name, version="1.0.0")

    # Build tool map and schemas from SdkMcpTool or fallback-decorated attributes
    tool_map = {}
    tool_list = []
    for t in sdk_tools:
        tool_name = getattr(t, "name", getattr(t, "_tool_name", None))
        handler = getattr(t, "handler", t)
        description = getattr(t, "description", getattr(t, "_tool_description", ""))
        input_schema = getattr(t, "input_schema", getattr(t, "_tool_schema", {}))

        tool_map[tool_name] = handler
        # Convert simple {param: type} schemas to JSON Schema
        if isinstance(input_schema, dict):
            if "type" in input_schema and "properties" in input_schema:
                schema = input_schema
            else:
                properties = {
                    k: _python_type_to_json_schema(v) for k, v in input_schema.items()
                }
                schema = {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties.keys()),
                }
        else:
            schema = {"type": "object", "properties": {}}
        tool_list.append(
            Tool(name=tool_name, description=description, inputSchema=schema)
        )

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tool_list

    @server.call_tool()
    async def call_tool(call_name: str, arguments: dict[str, Any]):
        """Execute a tool and return content items directly (not CallToolResult)."""
        if call_name not in tool_map:
            raise ValueError(f"Tool '{call_name}' not found")

        handler = tool_map[call_name]
        result = await handler(arguments)

        # Return list of TextContent directly — MCP server wraps in CallToolResult
        content = []
        if isinstance(result, dict) and "content" in result:
            for item in result["content"]:
                if isinstance(item, dict) and item.get("type") == "text":
                    content.append(TextContent(type="text", text=item["text"]))

        return content

    if SDK_AVAILABLE:
        return McpSdkServerConfig(type="sdk", name=name, instance=server)
    else:
        return {"type": "mock", "name": name, "instance": server}


def create_chat_tools_server(
    execution_id: str | None = None,
    bot_id: str | None = None,
):
    """
    Create MCP server with chat tools for a specific query.

    Context injection: Sets per-request ContextVar so tools can access
    execution_id (for SSE) and bot_id (for meeting context) without
    cross-request leaks under concurrent async usage.

    Args:
        execution_id: SSE execution ID for streaming events
        bot_id: Bot ID for meeting context tool (optional)

    Returns:
        MCP server instance with registered tools
    """
    _ctx_var.set(ChatToolContext(execution_id=execution_id, bot_id=bot_id))

    # The Cypher tool description is static at import time. Agents that need
    # the live Neo4j schema can fetch it via query_graph_cypher itself
    # (e.g. CALL db.schema.visualization()). Previously this block mutated
    # the tool's _tool_description at runtime — that broke contract
    # consistency and was removed in Phase A of the MCP surface cleanup.

    tools = list(_QUERY_TOOLS)

    # Always include signal mutation tools
    tools.extend(_SIGNAL_MUTATION_TOOLS)

    # Always include graph maintenance tools
    tools.extend(_GRAPH_TOOLS)

    # Always include decision intelligence tools
    tools.extend(_DECISION_TOOLS)

    # Always include temporal tools
    tools.extend(_TEMPORAL_TOOLS)

    import sys as _sys

    _sys.stderr.write(
        f"[MCP_SERVER] Creating chat MCP server: execution_id={execution_id}, bot_id={bot_id}, tools={len(tools)}\n"
    )
    _sys.stderr.flush()

    return _build_mcp_server("chat-tools", tools)


__all__ = [
    "create_chat_tools_server",
    "search_knowledge_graph_tool",
    "read_document_tool",
    "list_entities_tool",
    "extract_entities_tool",
    "get_entity_by_name_tool",
    "find_related_entities_tool",
    "list_entity_profiles_tool",
    "search_meeting_transcripts_tool",
    "list_meeting_documents_tool",
    "get_entity_context_summary_tool",
    "query_graph_cypher_tool",
    "search_signals_tool",
    "update_signal_tool",
    "delete_signal_tool",
    "graph_add_node_tool",
    "graph_update_node_tool",
    "graph_delete_node_tool",
    "graph_merge_nodes_tool",
    "graph_add_edge_tool",
    "graph_update_edge_tool",
    "graph_delete_edge_tool",
    "list_decisions_tool",
    "get_decision_tool",
    "find_decision_precedents_tool",
    "decision_chain_tool",
    "entity_at_time_tool",
    "active_relationships_at_time_tool",
    "get_entity_provenance_tool",
    "decision_stats_tool",
    "what_changed_tool",
    "graph_as_of_tool",
    "find_contradictions_tool",
    "temporal_blast_radius_tool",
]
