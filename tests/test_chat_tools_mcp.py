"""
Tests for chat_tools_mcp.py — MCP tool wrappers and server factory.

Covers:
- Each MCP wrapper calls the correct underlying function
- SSE events emitted (tool_start, tool_complete, tool_error)
- Error handling returns MCP error format
- create_chat_tools_server with/without bot_id
- Graph tools delegate to AgentToolRegistry
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.chat_tools_mcp import (
    _coerce_properties,
    _err,
    _ok,
    create_chat_tools_server,
    extract_entities_tool,
    find_related_entities_tool,
    get_entity_by_name_tool,
    graph_add_node_tool,
    graph_delete_node_tool,
    read_document_tool,
    search_knowledge_graph_tool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _call_tool(tool_obj, args: dict) -> dict:
    """Call a tool whether it's a SdkMcpTool or a plain async function.

    Real SDK @tool decorator produces SdkMcpTool (not callable, has .handler).
    Mock fallback @tool produces a plain async function.
    """
    if hasattr(tool_obj, "handler"):
        return await tool_obj.handler(args)
    return await tool_obj(args)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_context():
    """Reset ContextVar-based context before each test."""
    import app.agents.chat_tools_mcp as mod

    mod._ctx_var.set(mod.ChatToolContext(execution_id=None, bot_id=None))
    yield
    mod._ctx_var.set(mod.ChatToolContext(execution_id=None, bot_id=None))


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_ok_format(self):
        result = _ok("hello")
        assert result == {"content": [{"type": "text", "text": "hello"}]}

    def test_err_format(self):
        result = _err("something broke")
        assert result == {"content": [{"type": "text", "text": "Error: something broke"}]}


# ---------------------------------------------------------------------------
# Server factory tests
# ---------------------------------------------------------------------------


class TestServerFactory:
    def test_creates_server_without_bot_id(self):
        server = create_chat_tools_server(execution_id="exec-1")
        assert server["name"] == "chat-tools"

    def test_creates_server_with_bot_id(self):
        server = create_chat_tools_server(execution_id="exec-2", bot_id="bot-1")
        assert server["name"] == "chat-tools"

    def test_sets_module_context(self):
        import app.agents.chat_tools_mcp as mod

        create_chat_tools_server(execution_id="exec-3", bot_id="bot-2")
        ctx = mod._ctx_var.get()
        assert ctx.execution_id == "exec-3"
        assert ctx.bot_id == "bot-2"


# ---------------------------------------------------------------------------
# Query tool wrapper tests
# ---------------------------------------------------------------------------


class TestQueryToolWrappers:
    @pytest.mark.asyncio
    async def test_search_knowledge_graph(self):
        mock_results = [{"path": "doc.md", "score": 0.9}]
        with patch(
            "app.services.chat_tools.search_knowledge_graph",
            new_callable=AsyncMock,
            return_value=mock_results,
        ):
            result = await _call_tool(search_knowledge_graph_tool, {"query": "test"})

        text = result["content"][0]["text"]
        parsed = json.loads(text)
        assert len(parsed) == 1
        assert parsed[0]["path"] == "doc.md"

    @pytest.mark.asyncio
    async def test_read_document(self):
        mock_doc = {"path": "notes.md", "content": "Hello", "metadata": {}}
        with patch(
            "app.services.chat_tools.read_document",
            new_callable=AsyncMock,
            return_value=mock_doc,
        ):
            result = await _call_tool(read_document_tool, {"path": "notes.md"})

        text = result["content"][0]["text"]
        parsed = json.loads(text)
        assert parsed["path"] == "notes.md"
        assert parsed["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_extract_entities(self):
        mock_entities = {"people": ["Alice"], "projects": [], "teams": []}
        with patch(
            "app.services.chat_tools.extract_entities",
            new_callable=AsyncMock,
            return_value=mock_entities,
        ):
            result = await _call_tool(extract_entities_tool, {"text": "Alice works here"})

        text = result["content"][0]["text"]
        parsed = json.loads(text)
        assert "Alice" in parsed["people"]

    @pytest.mark.asyncio
    async def test_get_entity_by_name(self):
        mock_entity = {"id": "person-1", "name": "Alice", "type": "person"}
        with patch(
            "app.services.chat_tools.get_entity_by_name",
            new_callable=AsyncMock,
            return_value=mock_entity,
        ):
            result = await _call_tool(get_entity_by_name_tool, {"name": "Alice"})

        text = result["content"][0]["text"]
        parsed = json.loads(text)
        assert parsed["id"] == "person-1"

    @pytest.mark.asyncio
    async def test_find_related_entities_types_only(self):
        """find_related_entities with mode='types_only' returns the relationship-type
        inventory (consolidated from the former get_entity_relationships tool)."""
        mock_rels = {
            "entity_id": "person-1",
            "outgoing": [],
            "incoming": [],
            "total_outgoing": 0,
            "total_incoming": 0,
        }
        with patch(
            "app.services.chat_tools.get_entity_relationships",
            new_callable=AsyncMock,
            return_value=mock_rels,
        ):
            result = await _call_tool(
                find_related_entities_tool,
                {"entity_id": "person-1", "mode": "types_only"},
            )

        text = result["content"][0]["text"]
        parsed = json.loads(text)
        assert parsed["entity_id"] == "person-1"

    @pytest.mark.asyncio
    async def test_find_related_entities(self):
        mock_related = [{"entity": {"id": "role-1", "name": "VP"}, "relationship": {"type": "works_on"}}]
        with patch(
            "app.services.chat_tools.find_related_entities",
            new_callable=AsyncMock,
            return_value=mock_related,
        ):
            result = await _call_tool(find_related_entities_tool, {"entity_id": "person-1"})

        text = result["content"][0]["text"]
        parsed = json.loads(text)
        assert len(parsed) == 1
        assert parsed[0]["entity"]["name"] == "VP"




# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_search_error_returns_mcp_error(self):
        with patch(
            "app.services.chat_tools.search_knowledge_graph",
            new_callable=AsyncMock,
            side_effect=Exception("DB down"),
        ):
            result = await _call_tool(search_knowledge_graph_tool, {"query": "test"})

        text = result["content"][0]["text"]
        assert "Error: DB down" in text

    @pytest.mark.asyncio
    async def test_read_document_error(self):
        with patch(
            "app.services.chat_tools.read_document",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError("not found"),
        ):
            result = await _call_tool(read_document_tool, {"path": "missing.md"})

        text = result["content"][0]["text"]
        assert "Error" in text


# ---------------------------------------------------------------------------
# SSE emission tests
# ---------------------------------------------------------------------------


class TestSSEEmission:
    @pytest.mark.asyncio
    async def test_tool_start_emitted(self):
        import app.agents.chat_tools_mcp as mod

        mod._ctx_var.set(mod.ChatToolContext(execution_id="exec-sse-1"))

        with (
            patch("app.services.sse_manager.sse_manager") as mock_sse,
            patch(
                "app.services.chat_tools.search_knowledge_graph",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mock_sse.send_event = AsyncMock()
            await _call_tool(search_knowledge_graph_tool, {"query": "test"})

            calls = mock_sse.send_event.call_args_list
            event_types = [c[0][1] for c in calls]
            assert "tool_start" in event_types

    @pytest.mark.asyncio
    async def test_tool_complete_emitted(self):
        import app.agents.chat_tools_mcp as mod

        mod._ctx_var.set(mod.ChatToolContext(execution_id="exec-sse-2"))

        with (
            patch("app.services.sse_manager.sse_manager") as mock_sse,
            patch(
                "app.services.chat_tools.search_knowledge_graph",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mock_sse.send_event = AsyncMock()
            await _call_tool(search_knowledge_graph_tool, {"query": "test"})

            calls = mock_sse.send_event.call_args_list
            event_types = [c[0][1] for c in calls]
            assert "tool_complete" in event_types

    @pytest.mark.asyncio
    async def test_tool_error_emitted(self):
        import app.agents.chat_tools_mcp as mod

        mod._ctx_var.set(mod.ChatToolContext(execution_id="exec-sse-3"))

        with (
            patch("app.services.sse_manager.sse_manager") as mock_sse,
            patch(
                "app.services.chat_tools.search_knowledge_graph",
                new_callable=AsyncMock,
                side_effect=Exception("fail"),
            ),
        ):
            mock_sse.send_event = AsyncMock()
            await _call_tool(search_knowledge_graph_tool, {"query": "test"})

            calls = mock_sse.send_event.call_args_list
            event_types = [c[0][1] for c in calls]
            assert "tool_error" in event_types

    @pytest.mark.asyncio
    async def test_no_sse_without_execution_id(self):
        """SSE events should not fire when execution_id is None."""
        with (
            patch("app.services.sse_manager.sse_manager") as mock_sse,
            patch(
                "app.services.chat_tools.search_knowledge_graph",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mock_sse.send_event = AsyncMock()
            await _call_tool(search_knowledge_graph_tool, {"query": "test"})
            mock_sse.send_event.assert_not_called()


# ---------------------------------------------------------------------------
# Properties coercion tests
# ---------------------------------------------------------------------------


class TestCoerceProperties:
    def test_no_properties_key(self):
        args = {"entity_type": "person", "name": "Alice"}
        assert _coerce_properties(args) == args

    def test_dict_properties_kept(self):
        args = {"name": "Alice", "properties": {"role": "advisor"}}
        assert _coerce_properties(args) == args

    def test_empty_dict_removed(self):
        args = {"name": "Alice", "properties": {}}
        result = _coerce_properties(args)
        assert "properties" not in result
        assert result["name"] == "Alice"

    def test_json_string_parsed(self):
        args = {"name": "Alice", "properties": '{"role": "advisor"}'}
        result = _coerce_properties(args)
        assert result["properties"] == {"role": "advisor"}

    def test_empty_string_removed(self):
        args = {"name": "Alice", "properties": ""}
        result = _coerce_properties(args)
        assert "properties" not in result

    def test_null_string_removed(self):
        args = {"name": "Alice", "properties": "null"}
        result = _coerce_properties(args)
        assert "properties" not in result

    def test_unparseable_string_removed(self):
        args = {"name": "Alice", "properties": "not json at all"}
        result = _coerce_properties(args)
        assert "properties" not in result

    def test_non_dict_type_removed(self):
        args = {"name": "Alice", "properties": 42}
        result = _coerce_properties(args)
        assert "properties" not in result

    def test_list_type_removed(self):
        args = {"name": "Alice", "properties": ["a", "b"]}
        result = _coerce_properties(args)
        assert "properties" not in result

    def test_json_string_non_dict_removed(self):
        """JSON-parses to a list, not a dict — should be dropped."""
        args = {"name": "Alice", "properties": '["a", "b"]'}
        result = _coerce_properties(args)
        assert "properties" not in result


# ---------------------------------------------------------------------------
# Graph tool wrapper tests
# ---------------------------------------------------------------------------


class TestGraphToolWrappers:
    @pytest.mark.asyncio
    async def test_graph_add_node_delegates(self):
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {"node": {"id": "person-new", "name": "Test"}, "created": True}

        with patch("app.agents.chat_tools_mcp._get_registry") as mock_get_reg:
            mock_registry = AsyncMock()
            mock_registry.execute_tool = AsyncMock(return_value=mock_result)
            mock_get_reg.return_value = mock_registry

            result = await _call_tool(graph_add_node_tool, {"entity_type": "person", "name": "Test"})

        text = result["content"][0]["text"]
        parsed = json.loads(text)
        assert parsed["node"]["id"] == "person-new"
        assert parsed["created"] is True

    @pytest.mark.asyncio
    async def test_graph_tool_error(self):
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "Invalid entity type"

        with patch("app.agents.chat_tools_mcp._get_registry") as mock_get_reg:
            mock_registry = AsyncMock()
            mock_registry.execute_tool = AsyncMock(return_value=mock_result)
            mock_get_reg.return_value = mock_registry

            result = await _call_tool(graph_add_node_tool, {"entity_type": "invalid", "name": "X"})

        text = result["content"][0]["text"]
        assert "Error" in text
        assert "Invalid entity type" in text

    @pytest.mark.asyncio
    async def test_graph_delete_node(self):
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {"deleted_node": {"id": "person-1"}, "relationships_removed": 3}

        with patch("app.agents.chat_tools_mcp._get_registry") as mock_get_reg:
            mock_registry = AsyncMock()
            mock_registry.execute_tool = AsyncMock(return_value=mock_result)
            mock_get_reg.return_value = mock_registry

            result = await _call_tool(graph_delete_node_tool, {"entity_id": "person-1"})

        text = result["content"][0]["text"]
        parsed = json.loads(text)
        assert parsed["relationships_removed"] == 3
