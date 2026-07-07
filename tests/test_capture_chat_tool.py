"""Tests for the chat-agent capture_thought wrapper (Phase 1, chat wiring).

chat_tools_mcp registers tools per-tool (explicit delegation) — this covers
the capture_thought wrapper: registration in the always-included tool list
and delegation to chat_tools.capture_thought ("remember this" in imi chat).
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.chat_tools_mcp import _QUERY_TOOLS, capture_thought_tool


async def _call_tool(tool_obj, args):
    if hasattr(tool_obj, "handler"):
        return await tool_obj.handler(args)
    return await tool_obj(args)


def test_capture_thought_tool_registered():
    assert capture_thought_tool in _QUERY_TOOLS


@pytest.mark.asyncio
async def test_capture_thought_tool_delegates():
    mock_result = {"success": True, "id": "cap-1", "deduped": False}
    with patch(
        "app.services.chat_tools.capture_thought",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_fn:
        result = await _call_tool(
            capture_thought_tool, {"content": "Remember this fact."}
        )

    mock_fn.assert_awaited_once()
    parsed = json.loads(result["content"][0]["text"])
    assert parsed["id"] == "cap-1"


@pytest.mark.asyncio
async def test_capture_thought_tool_error_shape():
    with patch(
        "app.services.chat_tools.capture_thought",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        result = await _call_tool(capture_thought_tool, {"content": "x"})
    assert result.get("isError") or "boom" in result["content"][0]["text"]
