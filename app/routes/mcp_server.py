"""
MCP Server — Exposes knowledge graph tools via Model Context Protocol (SSE transport).

Wraps existing query tools (chat_tools.py) and mutation tools (graph_node_tools.py,
graph_edge_tools.py) so Claude Code can call them natively from the CLI.

Mutation tools handle the full lifecycle: Neo4j operations + source file archival + git commits.
"""

import json
import logging
from typing import Any

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.routing import Route

from app.services.mcp_tool_definitions import build_mcp_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP Server & SSE Transport
# ---------------------------------------------------------------------------

server = Server("kb-graph")


def _build_allowed_hosts() -> list[str]:
    """Build the SDK DNS-rebinding-protection Host allowlist.

    Always includes localhost variants so loopback Tailscale-internal access
    works out of the box. Two config sources are supported:

    - ``MCP_ALLOWED_HOSTS`` setting (comma-separated): explicit list of
      additional Host header values to accept.

    The IP allowlist in nginx (loopback + Tailscale CGNAT) is the primary
    access control. This list just has to match the hostnames legitimate
    callers actually use; without it, Tailscale users reaching the MCP SSE
    endpoint via the public hostname see HTTP 421 "Invalid Host header"
    from the SDK middleware.
    """
    base = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]
    configured: list[str] = []
    try:
        from app.config import settings

        raw = settings.MCP_ALLOWED_HOSTS or ""
        for raw_host in raw.split(","):
            host = raw_host.strip().lower()
            if not host:
                continue
            # Reject obviously-malformed entries: URL schemes, path segments,
            # or internal whitespace. The allowlist must be plain host or
            # host:port values for the SDK's Host-header check to make sense.
            if "://" in host or "/" in host or any(c.isspace() for c in host):
                logger.warning("Ignoring invalid MCP_ALLOWED_HOSTS entry: %r", raw_host)
                continue
            configured.append(host)
    except Exception:
        logger.warning(
            "Failed to load MCP_ALLOWED_HOSTS; falling back to localhost-only allowlist",
            exc_info=True,
        )
        configured = []
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    return [h for h in base + configured if not (h in seen or seen.add(h))]


_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=_build_allowed_hosts(),
)
sse = SseServerTransport("/messages/", security_settings=_security)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_tool_deps():
    """Lazy-import singleton services needed by mutation tools."""
    from app.git_ops import git_ops
    from app.services.claude_client import get_claude_client
    from app.services.file_cache import file_cache

    return get_claude_client(), git_ops, file_cache


def _serialize(obj: Any) -> str:
    """Serialize tool output to JSON string for MCP response."""
    from app.services.chat_tools import _serialize_for_json

    return json.dumps(_serialize_for_json(obj), indent=2, default=str)


def _text(data: Any) -> list[TextContent]:
    """Wrap data as MCP TextContent response."""
    return [TextContent(type="text", text=_serialize(data))]


def _error(msg: str) -> list[TextContent]:
    """Return an error response."""
    return [TextContent(type="text", text=json.dumps({"error": msg}))]


# ---------------------------------------------------------------------------
# Tool Definitions
#
# Verb taxonomy and parameter conventions are documented in
# docs/mcp_tool_conventions.md.
#
# Tools migrated to app/services/mcp_tool_definitions.py register via
# build_mcp_tool("<name>") so that mcp_server.py and chat_tools_mcp.py
# share one source of truth for descriptions and schemas. Tools still
# defined inline below have not been migrated yet — those migrations are
# tracked as follow-ups to Phase D.
# ---------------------------------------------------------------------------

TOOLS = [
    # --- Query tools (migrated to shared definitions) ---
    build_mcp_tool("search_knowledge_graph"),
    build_mcp_tool("list_entities"),
    build_mcp_tool("get_entity_by_name"),
    build_mcp_tool("find_related_entities"),
    build_mcp_tool("search_signals"),
    build_mcp_tool("search_signals_semantic"),
    build_mcp_tool("capture_thought"),
    build_mcp_tool("memory_writeback"),
    build_mcp_tool("memory_recall"),
    build_mcp_tool("record_memory_usage"),
    build_mcp_tool("inspect_memory"),
    build_mcp_tool("read_document"),
    build_mcp_tool("extract_entities"),
    build_mcp_tool("list_entity_profiles"),
    # --- Query tools NOT yet migrated to shared definitions ---
    Tool(
        name="query_graph_cypher",
        description="Execute a read-only Cypher query against Neo4j. Use for complex queries with filtering, multi-hop traversal, or compound conditions. Max 100 rows. No CREATE/SET/DELETE.",
        inputSchema={
            "type": "object",
            "properties": {
                "cypher": {
                    "type": "string",
                    "description": "Read-only Cypher query string",
                },
                "parameters": {
                    "type": "object",
                    "description": "Optional query parameters dict",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (default 100)",
                    "default": 100,
                },
            },
            "required": ["cypher"],
        },
    ),
    # --- Node mutation tools ---
    Tool(
        name="graph_delete_node",
        description="Delete an entity node and archive its source markdown file (soft-delete). Cascades to relationships by default.",
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The entity ID to delete (e.g. 'person-john-doe')",
                },
                "cascade": {
                    "type": "boolean",
                    "description": "Also remove all relationships (default true)",
                    "default": True,
                },
            },
            "required": ["entity_id"],
        },
    ),
    Tool(
        name="graph_merge_nodes",
        description="Merge a duplicate entity into a primary entity. Transfers relationships, adds alias, archives duplicate's source file.",
        inputSchema={
            "type": "object",
            "properties": {
                "primary_id": {
                    "type": "string",
                    "description": "The surviving entity ID",
                },
                "duplicate_id": {
                    "type": "string",
                    "description": "The entity ID to merge away",
                },
                "strategy": {
                    "type": "string",
                    "enum": ["primary_wins", "duplicate_wins", "merge_all"],
                    "description": "Conflict resolution strategy (default 'primary_wins')",
                },
            },
            "required": ["primary_id", "duplicate_id"],
        },
    ),
    Tool(
        name="graph_update_node",
        description=(
            "Patch (merge) properties onto an existing entity node. Only the fields you provide "
            "are changed; omitted fields are left intact. Also updates the source markdown file's "
            "frontmatter (and git-commits the change) when the entity has a source_file. "
            "Symmetric with graph_add_node, graph_delete_node, and graph_merge_nodes — all four "
            "node mutations now manage source files."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The entity ID to update",
                },
                "properties": {
                    "type": "object",
                    "description": "Properties dict to merge onto the node",
                },
            },
            "required": ["entity_id", "properties"],
        },
    ),
    Tool(
        name="graph_add_node",
        description=(
            "Create a new entity node in the graph. Validates entity_type against the domain config. "
            "Also creates a markdown source file at `{plural}/{slug}.md` (path resolved from the "
            "domain config's plural field) with frontmatter, and git-commits the new file. "
            "If the entity_type isn't in the domain config the file step is skipped and the "
            "response reports file_created=false; the Neo4j node is still created."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": "Entity type (must match domain config, e.g. 'person', 'project')",
                },
                "name": {
                    "type": "string",
                    "description": "Display name for the entity",
                },
                "properties": {
                    "type": "object",
                    "description": "Optional additional properties dict",
                },
            },
            "required": ["entity_type", "name"],
        },
    ),
    # --- Edge mutation tools ---
    Tool(
        name="graph_delete_edge",
        description="Delete a relationship between two entities in the knowledge graph.",
        inputSchema={
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "Source entity ID"},
                "target_id": {"type": "string", "description": "Target entity ID"},
                "relationship_type": {
                    "type": "string",
                    "description": "The relationship type to delete",
                },
            },
            "required": ["source_id", "target_id", "relationship_type"],
        },
    ),
    Tool(
        name="graph_add_edge",
        description="Add a relationship between two entities in the knowledge graph with domain validation.",
        inputSchema={
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "Source entity ID"},
                "target_id": {"type": "string", "description": "Target entity ID"},
                "relationship_type": {
                    "type": "string",
                    "description": "Relationship type (must match domain config, e.g. 'has_projects')",
                },
                "properties": {
                    "type": "object",
                    "description": "Optional relationship properties dict",
                },
            },
            "required": ["source_id", "target_id", "relationship_type"],
        },
    ),
    Tool(
        name="graph_update_edge",
        description="Update properties on an existing relationship in the knowledge graph.",
        inputSchema={
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "Source entity ID"},
                "target_id": {"type": "string", "description": "Target entity ID"},
                "relationship_type": {
                    "type": "string",
                    "description": "The relationship type to update",
                },
                "properties": {
                    "type": "object",
                    "description": "Properties dict to merge onto the relationship",
                },
            },
            "required": ["source_id", "target_id", "relationship_type", "properties"],
        },
    ),
    # --- Meeting transcript tools (migrated to shared definitions) ---
    build_mcp_tool("list_meetings"),
    build_mcp_tool("get_meeting_transcript"),
    build_mcp_tool("add_call_transcript"),
    # --- Intent tool (natural-language sub-agent dispatch) ---
    build_mcp_tool("ask_kb"),
    # --- Signal mutation tools (migrated to shared definitions) ---
    build_mcp_tool("update_signal"),
    build_mcp_tool("delete_signal"),
    # --- Decision tools (migrated to shared definitions) ---
    build_mcp_tool("list_decisions"),
    build_mcp_tool("get_decision"),
    build_mcp_tool("get_constitution"),
]


# ---------------------------------------------------------------------------
# MCP Handlers
# ---------------------------------------------------------------------------


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    logger.info(f"[MCP] call_tool: {name}({list(args.keys())})")

    try:
        # --- Query tools ---
        if name == "search_knowledge_graph":
            from app.services.chat_tools import search_knowledge_graph

            entity_types = args.get("entity_types")
            if isinstance(entity_types, str):
                entity_types = [
                    t.strip() for t in entity_types.split(",") if t.strip()
                ] or None
            result = await search_knowledge_graph(
                args["query"],
                entity_types=entity_types,
                max_results=args.get("max_results", 10),
            )
            return _text(result)

        elif name == "list_entities":
            from app.services.chat_tools import list_entities

            result = await list_entities(
                args["entity_type"],
                include_relationships=args.get("include_relationships", True),
                max_results=args.get("max_results", 50),
            )
            return _text(result)

        elif name == "get_entity_by_name":
            from app.services.chat_tools import get_entity_by_name

            result = await get_entity_by_name(
                args["name"], entity_type=args.get("entity_type")
            )
            return _text(result)

        elif name == "find_related_entities":
            mode = args.get("mode", "neighbors")
            if mode not in ("neighbors", "types_only"):
                return _error("Invalid 'mode': expected 'neighbors' or 'types_only'")
            if mode == "types_only":
                # Relationship-type inventory only — slim the underlying response so
                # the MCP payload stays compact for high-degree entities (callers
                # who want the actual neighbors should use mode='neighbors').
                from app.services.chat_tools import get_entity_relationships

                raw = await get_entity_relationships(args["entity_id"])
                if isinstance(raw, dict):
                    result = {
                        "entity_id": raw.get("entity_id"),
                        "outgoing_relationship_types": raw.get(
                            "available_outgoing_types", []
                        ),
                        "incoming_relationship_types": raw.get(
                            "available_incoming_types", []
                        ),
                        "total_outgoing": raw.get("total_outgoing", 0),
                        "total_incoming": raw.get("total_incoming", 0),
                    }
                else:
                    result = raw
                return _text(result)
            # Default: neighbor traversal.
            from app.services.chat_tools import find_related_entities

            rel_types = (
                [args["relationship_type"]] if args.get("relationship_type") else None
            )
            result = await find_related_entities(
                args["entity_id"],
                relationship_types=rel_types,
                max_results=args.get("max_results", 20),
            )
            return _text(result)

        elif name == "search_signals":
            from app.services.chat_tools import search_signals

            result = await search_signals(
                entity_id=args.get("entity_id"),
                signal_type=args.get("signal_type"),
                status=args.get("status"),
                client_id=args.get("client_id"),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
                max_results=args.get("max_results", args.get("limit", 20)),
            )
            return _text(result)

        elif name == "search_signals_semantic":
            from app.services.chat_tools import search_signals_semantic

            result = await search_signals_semantic(
                query=args["query"],
                signal_types=args.get("signal_types"),
                status=args.get("status"),
                authority=args.get("authority", "evidence"),
                limit=args.get("limit", 10),
                recency_weight=args.get("recency_weight", 0.0),
                include_rejected=args.get("include_rejected", False),
            )
            return _text(result)

        elif name == "capture_thought":
            from app.services.chat_tools import capture_thought

            content = args.get("content")
            if not isinstance(content, str) or not content.strip():
                return _error("Invalid 'content': expected a non-empty string")
            result = await capture_thought(
                content,
                source=args.get("source", "manual"),
                source_id=args.get("source_id"),
                tags=args.get("tags"),
                source_date=args.get("source_date"),
            )
            return _text(result)

        elif name == "memory_writeback":
            from app.services.chat_tools import memory_writeback

            payload = args.get("memory_payload")
            if not isinstance(payload, dict):
                return _error("Invalid 'memory_payload': expected an object")
            result = await memory_writeback(
                payload,
                task_id=args.get("task_id"),
                flow_id=args.get("flow_id"),
                runtime_name=args.get("runtime_name"),
                runtime_version=args.get("runtime_version"),
                confidence=args.get("confidence", 0.5),
                provenance_default_status=args.get(
                    "provenance_default_status", "generated"
                ),
                stale_after=args.get("stale_after"),
                idempotency_key=args.get("idempotency_key"),
            )
            return _text(result)

        elif name == "memory_recall":
            from app.services.chat_tools import memory_recall

            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                return _error("Invalid 'query': expected a non-empty string")
            result = await memory_recall(
                query,
                authority=args.get("authority", "evidence"),
                record_kinds=args.get("record_kinds"),
                limit=args.get("limit", 10),
                recency_weight=args.get("recency_weight", 0.0),
                task_id=args.get("task_id"),
                runtime_name=args.get("runtime_name"),
            )
            return _text(result)

        elif name == "record_memory_usage":
            from app.services.chat_tools import record_memory_usage

            request_id = args.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                return _error("Invalid 'request_id': expected a non-empty string")
            result = await record_memory_usage(
                request_id,
                used_memory_ids=args.get("used_memory_ids"),
                ignored=args.get("ignored"),
            )
            return _text(result)

        elif name == "inspect_memory":
            from app.services.chat_tools import inspect_memory

            record_id = args.get("record_id")
            if not isinstance(record_id, str) or not record_id:
                return _error("Invalid 'record_id': expected a non-empty string")
            result = await inspect_memory(record_id)
            return _text(result)

        elif name == "read_document":
            from app.services.chat_tools import read_document

            result = await read_document(args["path"])
            return _text(result)

        elif name == "extract_entities":
            from app.services.chat_tools import extract_entities

            text = args.get("text")
            if not isinstance(text, str) or not text:
                return _error("Invalid 'text': expected a non-empty string")
            result = await extract_entities(text)
            return _text(result)

        elif name == "list_entity_profiles":
            from app.services.chat_tools import list_entity_profiles

            entity_ids = args["entity_ids"]
            if isinstance(entity_ids, str):
                entity_ids = [e.strip() for e in entity_ids.split(",") if e.strip()]
            result = await list_entity_profiles(
                entity_ids,
                include_relationships=args.get("include_relationships", True),
                max_relationships_per_entity=args.get(
                    "max_relationships_per_entity", 20
                ),
            )
            return _text(result)

        elif name == "query_graph_cypher":
            from app.services.chat_tools import execute_cypher_query

            result = await execute_cypher_query(
                args["cypher"],
                parameters=args.get("parameters"),
                limit=args.get("limit", 100),
            )
            return _text(result)

        # --- Node mutation tools ---
        elif name == "graph_delete_node":
            from app.services.tools.graph_node_tools import DeleteNodeTool

            claude_client, git_ops, file_cache = _get_tool_deps()
            tool = DeleteNodeTool(claude_client, git_ops, file_cache)
            result = await tool.execute(
                {"entity_id": args["entity_id"], "cascade": args.get("cascade", True)}
            )
            return _text(
                {
                    "success": result.success,
                    "error": result.error,
                    **(result.data or {}),
                }
            )

        elif name == "graph_merge_nodes":
            from app.services.tools.graph_node_tools import MergeNodesTool

            claude_client, git_ops, file_cache = _get_tool_deps()
            tool = MergeNodesTool(claude_client, git_ops, file_cache)
            result = await tool.execute(
                {
                    "primary_id": args["primary_id"],
                    "duplicate_id": args["duplicate_id"],
                    "strategy": args.get("strategy", "primary_wins"),
                }
            )
            return _text(
                {
                    "success": result.success,
                    "error": result.error,
                    **(result.data or {}),
                }
            )

        elif name == "graph_update_node":
            from app.services.tools.graph_node_tools import UpdateNodeTool

            claude_client, git_ops, file_cache = _get_tool_deps()
            tool = UpdateNodeTool(claude_client, git_ops, file_cache)
            result = await tool.execute(
                {"entity_id": args["entity_id"], "properties": args["properties"]}
            )
            return _text(
                {
                    "success": result.success,
                    "error": result.error,
                    **(result.data or {}),
                }
            )

        elif name == "graph_add_node":
            from app.services.tools.graph_node_tools import AddNodeTool

            claude_client, git_ops, file_cache = _get_tool_deps()
            tool = AddNodeTool(claude_client, git_ops, file_cache)
            inputs = {"entity_type": args["entity_type"], "name": args["name"]}
            if args.get("properties"):
                inputs["properties"] = args["properties"]
            result = await tool.execute(inputs)
            return _text(
                {
                    "success": result.success,
                    "error": result.error,
                    **(result.data or {}),
                }
            )

        # --- Edge mutation tools ---
        elif name == "graph_delete_edge":
            from app.services.tools.graph_edge_tools import DeleteEdgeTool

            claude_client, git_ops, file_cache = _get_tool_deps()
            tool = DeleteEdgeTool(claude_client, git_ops, file_cache)
            result = await tool.execute(
                {
                    "source_id": args["source_id"],
                    "target_id": args["target_id"],
                    "relationship_type": args["relationship_type"],
                }
            )
            return _text(
                {
                    "success": result.success,
                    "error": result.error,
                    **(result.data or {}),
                }
            )

        elif name == "graph_add_edge":
            from app.services.tools.graph_edge_tools import AddEdgeTool

            claude_client, git_ops, file_cache = _get_tool_deps()
            tool = AddEdgeTool(claude_client, git_ops, file_cache)
            inputs = {
                "source_id": args["source_id"],
                "target_id": args["target_id"],
                "relationship_type": args["relationship_type"],
            }
            if args.get("properties"):
                inputs["properties"] = args["properties"]
            result = await tool.execute(inputs)
            return _text(
                {
                    "success": result.success,
                    "error": result.error,
                    **(result.data or {}),
                }
            )

        elif name == "graph_update_edge":
            from app.services.tools.graph_edge_tools import UpdateEdgeTool

            claude_client, git_ops, file_cache = _get_tool_deps()
            tool = UpdateEdgeTool(claude_client, git_ops, file_cache)
            result = await tool.execute(
                {
                    "source_id": args["source_id"],
                    "target_id": args["target_id"],
                    "relationship_type": args["relationship_type"],
                    "properties": args["properties"],
                }
            )
            return _text(
                {
                    "success": result.success,
                    "error": result.error,
                    **(result.data or {}),
                }
            )

        # --- Meeting transcript tools ---
        # Both delegate to chat_tools.py functions extracted in Phase E1.
        elif name == "list_meetings":
            from app.services.chat_tools import list_meetings

            # Accept legacy 'limit' as max_results alias for older callers.
            max_results = args.get("max_results", args.get("limit", 20))
            result = await list_meetings(
                max_results=max_results,
                status=args.get("status", "all"),
            )
            return _text(result)

        elif name == "get_meeting_transcript":
            from app.services.chat_tools import get_meeting_transcript

            result = await get_meeting_transcript(
                bot_id=args["bot_id"],
                max_length=args.get("max_length", 50000),
            )
            if "error" in result:
                return _error(result["error"])
            return _text(result)

        elif name == "add_call_transcript":
            from app.services.chat_tools import add_call_transcript

            result = await add_call_transcript(
                transcript=args["transcript"],
                start_time=args["start_time"],
                participants=args["participants"],
                title=args.get("title"),
                source=args.get("source", "local_recording"),
                duration_minutes=args.get("duration_minutes"),
                conversation_id=args.get("conversation_id"),
                source_id=args.get("source_id"),
                wait_timeout_seconds=args.get("wait_timeout_seconds", 30),
            )
            if "error" in result:
                return _error(result["error"])
            return _text(result)

        # --- Signal mutation tools ---
        elif name == "update_signal":
            from app.services.chat_tools import update_signal

            result = await update_signal(
                args["signal_id"],
                status=args.get("status"),
                content=args.get("content"),
                owner_id=args.get("owner_id"),
                due_date=args.get("due_date"),
                review_action=args.get("review_action"),
                actor=args.get("actor"),
                superseded_by=args.get("superseded_by"),
            )
            return _text(result)

        elif name == "delete_signal":
            from app.services.chat_tools import delete_signal

            result = await delete_signal(args["signal_id"])
            return _text(result)

        # --- Decision tools ---
        elif name == "list_decisions":
            from app.services.decision_view import list_decisions

            try:
                result = list_decisions(
                    state=args.get("state"),
                    owner_id=args.get("owner_id"),
                    client_id=args.get("client_id"),
                    date_from=args.get("date_from"),
                    date_to=args.get("date_to"),
                    max_results=args.get("max_results", 50),
                )
            except ValueError as exc:
                return _error(str(exc))
            return _text(result)

        elif name == "get_decision":
            from app.services.decision_view import get_decision

            decision_id = args.get("decision_id")
            if not decision_id:
                return _error("decision_id is required")
            result = get_decision(decision_id)
            if result is None:
                return _error(f"Decision '{decision_id}' not found")
            return _text(result)

        elif name == "get_constitution":
            from app.services.constitution import render_current_constitution

            # No-parameter contract: reject unexpected keys instead of silently
            # ignoring them, so a caller who (e.g.) passes a filter learns it
            # had no effect rather than assuming a filtered result.
            if args:
                unexpected = ", ".join(sorted(args))
                return _error(
                    f"get_constitution takes no arguments; unexpected fields: {unexpected}"
                )

            # Return the Markdown verbatim — do NOT route through _text(), which
            # JSON-encodes (and thus escapes) the string. Agents want the raw
            # document in context.
            markdown = render_current_constitution()
            return [TextContent(type="text", text=markdown)]

        elif name == "ask_kb":
            from app.services.ask_kb import ask_kb

            result = await ask_kb(
                intent=args["intent"],
                entity_context=args.get("entity_context"),
                allow_mutations=args.get("allow_mutations", False),
                max_steps=args.get("max_steps", 8),
            )
            return _text(result)

        else:
            return _error(f"Unknown tool: {name}")

    except Exception as e:
        logger.exception(f"[MCP] Error in tool {name}")
        return _error(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Starlette App (mounted by FastAPI at /api/mcp)
# ---------------------------------------------------------------------------


class _SseHandler:
    """ASGI handler for MCP SSE connections.

    Starlette Route treats class instances as raw ASGI apps,
    calling them with (scope, receive, send) instead of (request).
    """

    async def __call__(self, scope, receive, send):
        async with sse.connect_sse(scope, receive, send) as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )


class _MessageHandler:
    """ASGI handler for MCP client POST messages."""

    async def __call__(self, scope, receive, send):
        await sse.handle_post_message(scope, receive, send)


# Build the Starlette app.
#
# Path layout when mounted at /api/mcp by FastAPI:
#   GET  /api/mcp/sse       → SSE event stream (connect_sse)
#   POST /api/mcp/messages/ → Client messages (handle_post_message)
#
# The SSE transport computes the message URL as:
#   scope["root_path"] + sse._endpoint
# When starlette_app is mounted at /api/mcp, root_path = "/api/mcp",
# so the client gets: "/api/mcp/messages/?session_id=..."
starlette_app = Starlette(
    debug=False,
    routes=[
        Route("/sse", endpoint=_SseHandler(), methods=["GET"]),
        Route("/messages/", endpoint=_MessageHandler(), methods=["POST"]),
    ],
)
