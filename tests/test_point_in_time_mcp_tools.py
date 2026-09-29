"""ADR-004 §8: point-in-time tools on the external MCP surface."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

TOOLS = [
    "get_entity_at_time",
    "find_relationships_at_time",
    "find_changes",
    "get_graph_at_time",
    "get_entity_provenance",
]


def _body(result):
    return result[0].text


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", TOOLS)
def test_tool_is_defined_once_in_the_shared_module(name):
    from app.services.mcp_tool_definitions import MIGRATED_TOOLS, TOOL_DEFS

    td = TOOL_DEFS[name]
    assert td["name"] == name and name in MIGRATED_TOOLS
    assert td["inputSchema"]["type"] == "object"
    assert "entity_id" in td["inputSchema"]["required"]
    assert len(td["description"]) > 80


@pytest.mark.parametrize("name", TOOLS)
def test_tool_follows_the_naming_conventions(name):
    from app.services.mcp_tool_definitions import TOOL_DEFS

    assert name.split("_")[0] in {"get", "find"}
    props = TOOL_DEFS[name]["inputSchema"]["properties"]
    assert not {"limit", "depth", "since", "start", "end", "at_time"} & set(props)


def test_time_parameters_use_the_shared_names():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    for name in ("get_entity_at_time", "find_relationships_at_time", "get_graph_at_time"):
        schema = TOOL_DEFS[name]["inputSchema"]
        assert "timestamp" in schema["required"]
    changes = TOOL_DEFS["find_changes"]["inputSchema"]
    assert changes["required"] == ["entity_id", "date_from"]
    assert {"date_from", "date_to"} <= set(changes["properties"])
    assert "max_depth" in TOOL_DEFS["get_graph_at_time"]["inputSchema"]["properties"]


@pytest.mark.parametrize("name", TOOLS)
def test_descriptions_do_not_name_internals(name):
    from app.services.mcp_tool_definitions import TOOL_DEFS

    text = TOOL_DEFS[name]["description"].lower()
    for word in ("neo4j", "semantica", "cypher", "bfs", "occurred_at", "datetime"):
        assert word not in text


def test_tools_are_listed_on_the_server():
    from app.routes.mcp_server import TOOLS as SERVER_TOOLS

    names = [t.name for t in SERVER_TOOLS]
    for name in TOOLS:
        assert names.count(name) == 1


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_entity_at_time_dispatches():
    from app.routes.mcp_server import handle_call_tool

    state = {"id": "person-alice", "as_of": "2026-03-01T00:00:00+00:00", "evidence": {}}
    with patch("app.services.chat_tools.entity_at_time", AsyncMock(return_value=state)) as fn:
        result = await handle_call_tool(
            "get_entity_at_time", {"entity_id": "person-alice", "timestamp": "2026-03-01"}
        )
    fn.assert_awaited_once_with("person-alice", "2026-03-01")
    assert json.loads(_body(result))["id"] == "person-alice"


@pytest.mark.asyncio
async def test_find_relationships_at_time_dispatches():
    from app.routes.mcp_server import handle_call_tool

    with patch(
        "app.services.chat_tools.active_relationships_at_time", AsyncMock(return_value=[])
    ) as fn:
        result = await handle_call_tool(
            "find_relationships_at_time",
            {"entity_id": "person-alice", "timestamp": "2026-03-01", "include_co_mentions": False},
        )
    fn.assert_awaited_once_with("person-alice", "2026-03-01", include_co_mentions=False)
    assert json.loads(_body(result)) == []


@pytest.mark.asyncio
async def test_find_changes_with_and_without_an_end():
    from app.routes.mcp_server import handle_call_tool

    with (
        patch("app.services.chat_tools.what_changed", AsyncMock(return_value={"changes": []})) as since,
        patch(
            "app.services.chat_tools.what_changed_between", AsyncMock(return_value={"changes": []})
        ) as between,
    ):
        await handle_call_tool("find_changes", {"entity_id": "e", "date_from": "2026-03-01"})
        await handle_call_tool(
            "find_changes", {"entity_id": "e", "date_from": "2026-03-01", "date_to": "2026-05-01"}
        )
    since.assert_awaited_once_with("e", since="2026-03-01")
    between.assert_awaited_once_with("e", start="2026-03-01", end="2026-05-01")


@pytest.mark.asyncio
@pytest.mark.parametrize("given, used", [(None, 2), (3, 3), (99, 4), (-1, 0)])
async def test_get_graph_at_time_bounds_the_depth(given, used):
    from app.routes.mcp_server import handle_call_tool

    args = {"entity_id": "e", "timestamp": "2026-03-01"}
    if given is not None:
        args["max_depth"] = given
    with patch(
        "app.services.chat_tools.graph_as_of", AsyncMock(return_value={"nodes": [], "edges": []})
    ) as fn:
        await handle_call_tool("get_graph_at_time", args)
    fn.assert_awaited_once_with("e", "2026-03-01", depth=used, include_co_mentions=True)


@pytest.mark.asyncio
async def test_get_entity_provenance_dispatches():
    from app.routes.mcp_server import handle_call_tool

    with patch(
        "app.services.chat_tools.get_entity_provenance",
        AsyncMock(return_value={"entity_id": "e", "history": []}),
    ) as fn:
        result = await handle_call_tool("get_entity_provenance", {"entity_id": "e"})
    fn.assert_awaited_once_with("e")
    assert json.loads(_body(result))["history"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, args, missing",
    [
        ("get_entity_at_time", {"timestamp": "2026-03-01"}, "entity_id"),
        ("get_entity_at_time", {"entity_id": "e"}, "timestamp"),
        ("find_relationships_at_time", {"entity_id": "e"}, "timestamp"),
        ("get_graph_at_time", {"entity_id": "e"}, "timestamp"),
        ("find_changes", {"entity_id": "e"}, "date_from"),
        ("get_entity_provenance", {}, "entity_id"),
    ],
)
async def test_missing_arguments_are_reported(name, args, missing):
    from app.routes.mcp_server import handle_call_tool

    result = await handle_call_tool(name, args)
    assert f"{missing} is required" in _body(result)


@pytest.mark.asyncio
async def test_nothing_known_is_an_error_result():
    from app.routes.mcp_server import handle_call_tool

    with patch(
        "app.services.chat_tools.entity_at_time",
        AsyncMock(return_value={"error": "Nothing was known about 'e' at 2020-01-01"}),
    ):
        result = await handle_call_tool(
            "get_entity_at_time", {"entity_id": "e", "timestamp": "2020-01-01"}
        )
    assert "Nothing was known" in json.loads(_body(result))["error"]


# ---------------------------------------------------------------------------
# chat_tools wrappers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrappers_need_the_graph():
    from app.services import chat_tools

    with patch("app.services.chat_tools._get_temporal_query_service", return_value=None):
        assert "error" in await chat_tools.entity_at_time("e", "2026-03-01")
        assert "error" in (await chat_tools.active_relationships_at_time("e", "2026-03-01"))[0]
        assert "error" in await chat_tools.what_changed("e", since="2026-03-01")
        assert "error" in await chat_tools.graph_as_of("e", "2026-03-01")
        assert "error" in await chat_tools.get_entity_provenance("e")


@pytest.mark.asyncio
async def test_wrappers_do_not_need_semantica():
    """The old tools refused to run without Semantica; these do not use it."""
    from app.services import chat_tools

    svc = AsyncMock()
    svc.entity_at.return_value = {"id": "e"}
    with (
        patch("app.services.chat_tools._get_semantica", return_value=None),
        patch("app.services.chat_tools._get_temporal_query_service", return_value=svc),
    ):
        assert await chat_tools.entity_at_time("e", "2026-03-01") == {"id": "e"}


@pytest.mark.asyncio
async def test_bad_timestamp_is_reported_not_raised():
    from app.services import chat_tools

    with patch("app.services.chat_tools._get_temporal_query_service", return_value=AsyncMock()):
        result = await chat_tools.entity_at_time("e", "last tuesday")
    assert "Not a valid ISO-8601 time" in result["error"]
