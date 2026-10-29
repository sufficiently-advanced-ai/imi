"""Tests for the capture_thought MCP tool (Phase 1 of the OB1 absorption).

Covers:
  - Tool def present in TOOL_DEFS with required=["content"] and NO governance
    parameters (ADR-002: provenance/authority are server-injected).
  - chat_tools.capture_thought delegates to capture_service.capture_and_persist.
  - mcp_server TOOLS list contains capture_thought; dispatcher routes to it.
"""

import importlib
import json
import sys
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------


def test_tool_def_present_with_required_content():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    assert "capture_thought" in TOOL_DEFS
    td = TOOL_DEFS["capture_thought"]
    assert td["inputSchema"]["required"] == ["content"]


def test_tool_def_has_expected_params_and_no_governance_params():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    props = TOOL_DEFS["capture_thought"]["inputSchema"]["properties"]
    for param in ("content", "source", "source_id", "tags", "source_date"):
        assert param in props, f"Expected param '{param}' missing from inputSchema"
    # ADR-002: governance is never a client parameter
    for forbidden in (
        "provenance_status",
        "review_status",
        "can_use_as_evidence",
        "can_use_as_instruction",
    ):
        assert forbidden not in props, f"Governance param '{forbidden}' must not be exposed"


def test_build_mcp_tool_works_for_capture_thought():
    from app.services.mcp_tool_definitions import build_mcp_tool

    tool = build_mcp_tool("capture_thought")
    assert tool.name == "capture_thought"


# ---------------------------------------------------------------------------
# chat_tools.capture_thought — delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_thought_delegates_to_capture_service():
    captured: dict = {}

    async def fake_capture_and_persist(content, source="manual", source_id=None, **kw):
        captured.update({"content": content, "source": source, **kw})
        return {"success": True, "id": "cap-1", "deduped": False}

    with patch(
        "app.services.capture_service.capture_and_persist",
        side_effect=fake_capture_and_persist,
    ):
        from app.services.chat_tools import capture_thought

        result = await capture_thought(
            "Remember: prefer bun over npm.",
            source="manual",
            tags=["tooling"],
            channel="mcp",
        )

    assert result["success"] is True
    assert result["id"] == "cap-1"
    assert captured["content"] == "Remember: prefer bun over npm."
    assert captured["tags"] == ["tooling"]


@pytest.mark.asyncio
async def test_capture_thought_returns_error_shape_on_failure():
    async def fake_fail(content, **kw):
        return {"success": False, "error": "store exploded"}

    with patch(
        "app.services.capture_service.capture_and_persist", side_effect=fake_fail
    ):
        from app.services.chat_tools import capture_thought

        result = await capture_thought("A thought.", channel="mcp")
    assert result["success"] is False
    assert "store exploded" in result["error"]


# ---------------------------------------------------------------------------
# mcp_server registration + dispatch
# ---------------------------------------------------------------------------


def _mcp_mod():
    mod = sys.modules.get("app.routes.mcp_server")
    if mod is None:
        mod = importlib.import_module("app.routes.mcp_server")
    return mod


def test_mcp_tools_list_contains_capture_thought():
    tool_names = [t.name for t in _mcp_mod().TOOLS]
    assert "capture_thought" in tool_names


@pytest.mark.asyncio
async def test_mcp_dispatcher_routes_capture_thought():
    mock_result = {"success": True, "id": "cap-1", "deduped": False}

    with patch(
        "app.services.chat_tools.capture_thought",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_fn:
        response = await _mcp_mod().handle_call_tool(
            "capture_thought", {"content": "A thought from MCP."}
        )

    mock_fn.assert_awaited_once()
    data = json.loads(response[0].text)
    assert data["id"] == "cap-1"


# ---------------------------------------------------------------------------
# ADR-011: about + owner capture links
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_thought_about_attaches_after_persisting():
    async def fake_capture_and_persist(content, **kw):
        return {"success": True, "id": "cap-9", "deduped": False}

    attach = AsyncMock(return_value={"linked": [{"target_id": "person-ada"}], "unresolved": ["Bob"]})
    with patch("app.services.capture_service.capture_and_persist",
               side_effect=fake_capture_and_persist), \
            patch("app.services.capture_linker.attach_about", attach):
        from app.services.chat_tools import capture_thought

        result = await capture_thought("Note on Ada.", channel="mcp", about=["Ada", "Bob", " "])

    attach.assert_awaited_once_with("cap-9", ["Ada", "Bob"], actor="mcp")
    assert result["about"]["unresolved"] == ["Bob"]


@pytest.mark.asyncio
async def test_mcp_rejects_non_list_about():
    response = await _mcp_mod().handle_call_tool(
        "capture_thought", {"content": "x", "about": "Ada"}
    )
    assert "about" in response[0].text


def test_capture_link_tools_registered():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    names = [t.name for t in _mcp_mod().TOOLS]
    for name in ("graph_add_capture_link", "graph_delete_capture_link"):
        assert name in names
        assert TOOL_DEFS[name]["inputSchema"]["required"] == ["capture_id", "target"]
    assert "about" in TOOL_DEFS["capture_thought"]["inputSchema"]["properties"]


@pytest.mark.asyncio
async def test_mcp_capture_link_dispatch_stamps_actor_and_maps_errors():
    with patch("app.services.chat_tools.graph_add_capture_link", new_callable=AsyncMock,
               return_value={"linked": True, "target_id": "person-ada"}) as add:
        response = await _mcp_mod().handle_call_tool(
            "graph_add_capture_link", {"capture_id": "cap-1", "target": "Ada", "edge": "about"}
        )
    assert json.loads(response[0].text)["linked"] is True
    args, kwargs = add.call_args
    assert args[:3] == ("cap-1", "Ada", "about") and kwargs["actor"].startswith("mcp")

    with patch("app.services.chat_tools.graph_delete_capture_link", new_callable=AsyncMock,
               return_value={"error": "edge must be one of ..."}):
        response = await _mcp_mod().handle_call_tool(
            "graph_delete_capture_link", {"capture_id": "cap-1", "target": "Ada"}
        )
    assert "edge must be one of" in response[0].text
    missing = await _mcp_mod().handle_call_tool("graph_add_capture_link", {"target": "Ada"})
    assert "capture_id is required" in missing[0].text
