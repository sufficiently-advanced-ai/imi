"""Tests for the memory_writeback MCP tool (Phase 2 of the OB1 absorption).

Covers: tool def (memory_payload required, provenance enum clamped to
observed/inferred/generated — never user_confirmed/imported), delegation to
memory_writeback.writeback, dispatcher routing, and the rejection shape.
"""

import importlib
import json
import sys
from unittest.mock import patch

import pytest


def test_tool_def_present_with_required_payload():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    assert "memory_writeback" in TOOL_DEFS
    td = TOOL_DEFS["memory_writeback"]
    assert td["inputSchema"]["required"] == ["memory_payload"]


def test_tool_def_provenance_enum_is_clamped():
    """ADR-002: the schema itself must not offer user_confirmed/imported."""
    from app.services.mcp_tool_definitions import TOOL_DEFS

    props = TOOL_DEFS["memory_writeback"]["inputSchema"]["properties"]
    enum = props["provenance_default_status"]["enum"]
    assert set(enum) == {"observed", "inferred", "generated"}


@pytest.mark.asyncio
async def test_memory_writeback_delegates():
    captured = {}

    async def fake_writeback(request, **kw):
        captured["request"] = request
        return {"success": True, "created": [{"id": "m1", "memory_type": "lesson"}],
                "replayed": False, "committed": True}

    with patch("app.services.memory_writeback.writeback", side_effect=fake_writeback):
        from app.services.chat_tools import memory_writeback

        result = await memory_writeback(
            memory_payload={"lessons": ["Batch calls."]},
            task_id="task-9",
            idempotency_key="task-9-run-1",
            provenance_default_status="observed",
        )

    assert result["success"] is True
    req = captured["request"]
    assert req.task_id == "task-9"
    assert req.provenance.default_status == "observed"
    assert req.memory_payload.lessons == ["Batch calls."]


@pytest.mark.asyncio
async def test_memory_writeback_invalid_payload_returns_error_shape():
    from app.services.chat_tools import memory_writeback

    result = await memory_writeback(
        memory_payload={"lessons": ["x"]},
        provenance_default_status="user_confirmed",
    )
    assert result["success"] is False
    assert "provenance" in result["error"].lower() or "user_confirmed" in result["error"]


def test_mcp_tools_list_contains_memory_writeback():
    mod = sys.modules.get("app.routes.mcp_server") or importlib.import_module(
        "app.routes.mcp_server"
    )
    assert "memory_writeback" in [t.name for t in mod.TOOLS]


@pytest.mark.asyncio
async def test_mcp_dispatcher_routes_memory_writeback():
    mod = sys.modules.get("app.routes.mcp_server") or importlib.import_module(
        "app.routes.mcp_server"
    )

    from unittest.mock import AsyncMock

    with patch(
        "app.services.chat_tools.memory_writeback",
        new_callable=AsyncMock,
        return_value={"success": True, "created": []},
    ) as mock_fn:
        response = await mod.handle_call_tool(
            "memory_writeback", {"memory_payload": {"lessons": ["x"]}}
        )
    mock_fn.assert_awaited_once()
    assert json.loads(response[0].text)["success"] is True
