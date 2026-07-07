"""Tests for MCP list_decisions / get_decision tools — Task 6+7 of Issue #954.

Covers:
- TOOL_DEFS entries registered with correct schemas
- mcp_server.py dispatch: list_decisions, get_decision (happy path, not-found, missing arg)
- Both tools appear in the server TOOLS list
- chat_tools_mcp.py wrappers registered via chat_tool_args
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Tool definition tests
# ---------------------------------------------------------------------------


class TestToolDefs:
    def test_list_decisions_tool_def_registered(self):
        from app.services.mcp_tool_definitions import TOOL_DEFS

        td = TOOL_DEFS["list_decisions"]
        props = td["inputSchema"]["properties"]
        assert {"state", "owner_id", "client_id", "date_from", "date_to", "max_results"} <= set(props)
        assert set(props["state"]["enum"]) == {
            "candidate",
            "active",
            "stale",
            "superseded",
            "rejected",
            "temporary",
            "zombie",
            "conflicting",
        }

    def test_get_decision_tool_def_registered(self):
        from app.services.mcp_tool_definitions import TOOL_DEFS

        assert TOOL_DEFS["get_decision"]["inputSchema"]["required"] == ["decision_id"]

    def test_list_decisions_no_required_fields(self):
        from app.services.mcp_tool_definitions import TOOL_DEFS

        td = TOOL_DEFS["list_decisions"]
        # No required fields — all filters are optional
        assert "required" not in td["inputSchema"] or td["inputSchema"].get("required") == []

    def test_get_decision_has_description(self):
        from app.services.mcp_tool_definitions import TOOL_DEFS

        td = TOOL_DEFS["get_decision"]
        assert len(td["description"]) > 20
        # Should mention read-only nature
        assert "read" in td["description"].lower() or "get" in td["description"].lower()

    def test_list_decisions_max_results_default(self):
        from app.services.mcp_tool_definitions import TOOL_DEFS

        td = TOOL_DEFS["list_decisions"]
        assert td["inputSchema"]["properties"]["max_results"]["default"] == 50

    def test_both_in_migrated_tools(self):
        from app.services.mcp_tool_definitions import MIGRATED_TOOLS

        assert "list_decisions" in MIGRATED_TOOLS
        assert "get_decision" in MIGRATED_TOOLS


# ---------------------------------------------------------------------------
# mcp_server TOOLS list tests
# ---------------------------------------------------------------------------


class TestMcpServerToolsList:
    def test_list_decisions_in_tools(self):
        from app.routes.mcp_server import TOOLS

        tool_names = [t.name for t in TOOLS]
        assert "list_decisions" in tool_names

    def test_get_decision_in_tools(self):
        from app.routes.mcp_server import TOOLS

        tool_names = [t.name for t in TOOLS]
        assert "get_decision" in tool_names


# ---------------------------------------------------------------------------
# mcp_server dispatch tests
# ---------------------------------------------------------------------------


class TestMcpServerDispatch:
    @pytest.mark.asyncio
    async def test_list_decisions_happy_path(self):
        """list_decisions with no args returns a text payload containing 'decisions'."""
        fake_result = {"decisions": [], "total": 0, "counts_by_state": {}}

        with patch("app.services.decision_view.list_decisions", return_value=fake_result):
            from app.routes.mcp_server import handle_call_tool

            result = await handle_call_tool("list_decisions", {})

        assert len(result) == 1
        text = result[0].text
        parsed = json.loads(text)
        assert "decisions" in parsed

    @pytest.mark.asyncio
    async def test_list_decisions_with_state_filter(self):
        """list_decisions passes state filter through to service."""
        fake_result = {
            "decisions": [{"id": "abc", "state": "active"}],
            "total": 1,
            "counts_by_state": {"active": 1},
        }

        with patch("app.services.decision_view.list_decisions", return_value=fake_result) as mock_fn:
            from app.routes.mcp_server import handle_call_tool

            result = await handle_call_tool("list_decisions", {"state": "active"})

        # Service was called with state="active"
        mock_fn.assert_called_once()
        call_kwargs = mock_fn.call_args
        assert call_kwargs.kwargs.get("state") == "active" or (
            call_kwargs.args and call_kwargs.args[0] == "active"
        )

        text = result[0].text
        parsed = json.loads(text)
        assert parsed["total"] == 1

    @pytest.mark.asyncio
    async def test_list_decisions_invalid_state_returns_error(self):
        """list_decisions with unknown state raises ValueError → error response."""
        with patch(
            "app.services.decision_view.list_decisions",
            side_effect=ValueError("Unknown decision state: 'bogus'"),
        ):
            from app.routes.mcp_server import handle_call_tool

            result = await handle_call_tool("list_decisions", {"state": "bogus"})

        text = result[0].text
        parsed = json.loads(text)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_get_decision_happy_path(self):
        """get_decision with a known id returns the decision dict."""
        fake_decision = {
            "id": "some-id",
            "content": "We use PostgreSQL",
            "state": "active",
            "lineage": [],
            "audit_history": [],
            "governance_ladder": {"position": "instruction"},
        }

        with patch("app.services.decision_view.get_decision", return_value=fake_decision):
            from app.routes.mcp_server import handle_call_tool

            result = await handle_call_tool("get_decision", {"decision_id": "some-id"})

        text = result[0].text
        parsed = json.loads(text)
        assert parsed["id"] == "some-id"
        assert parsed["state"] == "active"

    @pytest.mark.asyncio
    async def test_get_decision_not_found(self):
        """get_decision with unknown id returns error, not crash."""
        with patch("app.services.decision_view.get_decision", return_value=None):
            from app.routes.mcp_server import handle_call_tool

            result = await handle_call_tool("get_decision", {"decision_id": "no-such-id"})

        text = result[0].text
        parsed = json.loads(text)
        assert "error" in parsed
        assert "no-such-id" in parsed["error"]

    @pytest.mark.asyncio
    async def test_get_decision_missing_decision_id(self):
        """get_decision with no decision_id arg returns error gracefully."""
        from app.routes.mcp_server import handle_call_tool

        result = await handle_call_tool("get_decision", {})

        text = result[0].text
        parsed = json.loads(text)
        assert "error" in parsed
        assert "decision_id" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_list_decisions_passes_all_filter_args(self):
        """list_decisions forwards all filter params to the service."""
        fake_result = {"decisions": [], "total": 0, "counts_by_state": {}}

        with patch("app.services.decision_view.list_decisions", return_value=fake_result) as mock_fn:
            from app.routes.mcp_server import handle_call_tool

            await handle_call_tool(
                "list_decisions",
                {
                    "state": "stale",
                    "owner_id": "person-alice",
                    "client_id": "client-acme",
                    "date_from": "2026-01-01",
                    "date_to": "2026-06-11",
                    "max_results": 10,
                },
            )

        mock_fn.assert_called_once()
        kwargs = mock_fn.call_args.kwargs
        assert kwargs["state"] == "stale"
        assert kwargs["owner_id"] == "person-alice"
        assert kwargs["client_id"] == "client-acme"
        assert kwargs["date_from"] == "2026-01-01"
        assert kwargs["date_to"] == "2026-06-11"
        assert kwargs["max_results"] == 10


# ---------------------------------------------------------------------------
# chat_tools_mcp.py registration tests
# ---------------------------------------------------------------------------


class TestChatSurfaceRegistration:
    def test_list_decisions_tool_in_decision_tools(self):
        """list_decisions_tool is registered in _DECISION_TOOLS."""
        import app.agents.chat_tools_mcp as mod

        decision_tool_names = []
        for t in mod._DECISION_TOOLS:
            name = getattr(t, "name", getattr(t, "_tool_name", None))
            decision_tool_names.append(name)

        assert any("list_decisions" in (n or "") for n in decision_tool_names)

    def test_get_decision_tool_in_decision_tools(self):
        """get_decision_tool is registered in _DECISION_TOOLS."""
        import app.agents.chat_tools_mcp as mod

        decision_tool_names = []
        for t in mod._DECISION_TOOLS:
            name = getattr(t, "name", getattr(t, "_tool_name", None))
            decision_tool_names.append(name)

        assert any("get_decision" in (n or "") for n in decision_tool_names)

    def test_list_decisions_tool_exported(self):
        """list_decisions_tool is importable from chat_tools_mcp."""
        from app.agents.chat_tools_mcp import list_decisions_tool  # noqa: F401

    def test_get_decision_tool_exported(self):
        """get_decision_tool is importable from chat_tools_mcp."""
        from app.agents.chat_tools_mcp import get_decision_tool  # noqa: F401

    @pytest.mark.asyncio
    async def test_list_decisions_chat_tool_invocable(self):
        """list_decisions_tool can be called and returns ok/err shaped output."""
        from app.agents.chat_tools_mcp import list_decisions_tool

        fake_result = {"decisions": [], "total": 0, "counts_by_state": {}}

        with patch("app.services.decision_view.list_decisions", return_value=fake_result):
            # Call either via .handler (SDK tool) or direct call (mock tool)
            if hasattr(list_decisions_tool, "handler"):
                result = await list_decisions_tool.handler({})
            else:
                result = await list_decisions_tool({})

        # Should return a dict with "content" key (MCP _ok format)
        assert "content" in result
        text_item = result["content"][0]
        assert text_item["type"] == "text"
        payload = json.loads(text_item["text"])
        assert "decisions" in payload

    @pytest.mark.asyncio
    async def test_get_decision_chat_tool_not_found(self):
        """get_decision_tool returns error-shaped output for missing decision."""
        from app.agents.chat_tools_mcp import get_decision_tool

        with patch("app.services.decision_view.get_decision", return_value=None):
            if hasattr(get_decision_tool, "handler"):
                result = await get_decision_tool.handler({"decision_id": "no-such"})
            else:
                result = await get_decision_tool({"decision_id": "no-such"})

        # Should return a dict with "content" key
        assert "content" in result
        text_item = result["content"][0]
        text = text_item["text"]
        # Error path: either "Error: ..." prefix or JSON with error key
        assert "error" in text.lower() or "not found" in text.lower()
