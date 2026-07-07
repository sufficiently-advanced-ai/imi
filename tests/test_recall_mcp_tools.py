"""Tests for the memory_recall / record_memory_usage MCP tools (Phase 3).

Covers: tool defs (query required; authority enum with the ADR-002 promise;
record_kinds enum), chat_tools delegation, dispatcher routing, and the
chat-agent wrapper registration ("draw on governed memory" in imi chat).
"""

import importlib
import json
import sys
from unittest.mock import AsyncMock, patch

import pytest


def _mcp_mod():
    return sys.modules.get("app.routes.mcp_server") or importlib.import_module(
        "app.routes.mcp_server"
    )


def test_recall_tool_def():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    td = TOOL_DEFS["memory_recall"]
    assert td["inputSchema"]["required"] == ["query"]
    props = td["inputSchema"]["properties"]
    assert set(props["authority"]["enum"]) == {"evidence", "instruction"}
    assert "ADR-002" in td["description"]
    assert set(props["record_kinds"]["items"]["enum"]) == {
        "signal",
        "capture",
        "agent_memory",
    }


def test_usage_tool_def():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    td = TOOL_DEFS["record_memory_usage"]
    assert td["inputSchema"]["required"] == ["request_id"]


@pytest.mark.asyncio
async def test_memory_recall_delegates():
    captured = {}

    async def fake_recall(request, **kw):
        captured["request"] = request
        return {"request_id": "req-9", "memories": [], "warnings": []}

    with patch("app.services.memory_recall.recall", side_effect=fake_recall):
        from app.services.chat_tools import memory_recall

        result = await memory_recall(
            "what did we decide", authority="instruction", limit=5, task_id="t-1"
        )

    assert result["request_id"] == "req-9"
    req = captured["request"]
    assert req.authority == "instruction"
    assert req.limit == 5
    assert req.task_id == "t-1"
    assert req.surface == "mcp"


@pytest.mark.asyncio
async def test_memory_recall_invalid_args_error_shape():
    from app.services.chat_tools import memory_recall

    result = await memory_recall("   ")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_record_memory_usage_delegates():
    captured = {}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def commit(self):
            captured["committed"] = True

    async def fake_apply_usage(session, request_id, used_memory_ids=None, ignored=None):
        captured.update(
            {"request_id": request_id, "used": used_memory_ids, "ignored": ignored}
        )
        return 1

    with (
        patch(
            "app.services.chat_tools._memory_ops_session_factory",
            return_value=FakeSession,
        ),
        patch(
            "app.services.recall_trace_store.apply_usage", side_effect=fake_apply_usage
        ),
    ):
        from app.services.chat_tools import record_memory_usage

        result = await record_memory_usage("req-1", used_memory_ids=["cap-1"])

    assert result["updated"] == 1
    assert captured["request_id"] == "req-1"
    assert captured.get("committed") is True


def test_mcp_tools_list_contains_recall_tools():
    names = [t.name for t in _mcp_mod().TOOLS]
    assert "memory_recall" in names
    assert "record_memory_usage" in names


@pytest.mark.asyncio
async def test_dispatcher_routes_memory_recall():
    with patch(
        "app.services.chat_tools.memory_recall",
        new_callable=AsyncMock,
        return_value={"request_id": "r", "memories": []},
    ) as mock_fn:
        response = await _mcp_mod().handle_call_tool(
            "memory_recall", {"query": "test"}
        )
    mock_fn.assert_awaited_once()
    assert json.loads(response[0].text)["request_id"] == "r"


def test_chat_agent_registers_memory_recall():
    from app.agents.chat_tools_mcp import _QUERY_TOOLS, memory_recall_tool

    assert memory_recall_tool in _QUERY_TOOLS
