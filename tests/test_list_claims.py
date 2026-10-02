"""ADR-006 §6: list_claims — the claims timeline for an entity.

A graph read, not recall: claims linked to an entity (about it, or attributed
to it), ordered by event time, each with attribution, supersession and decay
state. Window and decay bounds are typed UTC datetimes, never strings.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import app.services.lane_admission as la
from app.services.temporal_queries import TemporalQueryService

NOW = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_config(monkeypatch, tmp_path):
    monkeypatch.setenv("LANES_CONFIG_PATH", str(tmp_path / "absent.yaml"))
    la.reset_lanes_config()
    yield
    la.reset_lanes_config()


def _body(result):
    return result[0].text


# ---- definition ---------------------------------------------------------------------


def test_defined_once_and_follows_the_conventions():
    from app.services.mcp_tool_definitions import MIGRATED_TOOLS, TOOL_DEFS

    td = TOOL_DEFS["list_claims"]
    assert td["name"] == "list_claims" and "list_claims" in MIGRATED_TOOLS
    schema = td["inputSchema"]
    assert schema["required"] == ["entity_id"]
    assert set(schema["properties"]) == {
        "entity_id", "date_from", "date_to", "max_results", "include_stale",
    }
    assert not {"limit", "since", "start", "end"} & set(schema["properties"])
    text = td["description"].lower()
    for word in ("neo4j", "cypher", "occurred_at", "datetime", "signal"):
        assert word not in text


def test_listed_once_on_the_external_server():
    from app.routes.mcp_server import TOOLS

    assert [t.name for t in TOOLS].count("list_claims") == 1


def test_registered_on_the_chat_surface():
    pytest.importorskip("claude_agent_sdk")
    from app.agents import chat_tools_mcp

    assert chat_tools_mcp.list_claims_tool in chat_tools_mcp._TEMPORAL_TOOLS


# ---- MCP dispatch ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatches_with_defaults():
    from app.routes.mcp_server import handle_call_tool

    payload = {"entity": {"id": "e"}, "claims": [], "count": 0}
    with patch("app.services.chat_tools.list_claims", AsyncMock(return_value=payload)) as fn:
        result = await handle_call_tool("list_claims", {"entity_id": "e"})
    fn.assert_awaited_once_with(
        "e", date_from=None, date_to=None, max_results=50, include_stale=False
    )
    assert json.loads(_body(result))["count"] == 0


@pytest.mark.asyncio
async def test_missing_entity_and_errors_are_reported():
    from app.routes.mcp_server import handle_call_tool

    assert "entity_id is required" in _body(await handle_call_tool("list_claims", {}))
    with patch(
        "app.services.chat_tools.list_claims",
        AsyncMock(return_value={"error": "Entity 'x' not found"}),
    ):
        result = await handle_call_tool("list_claims", {"entity_id": "x"})
    assert "not found" in _body(result)


# ---- chat_tools wrapper ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_is_typed_and_date_to_is_inclusive():
    from app.services import chat_tools

    svc = AsyncMock()
    svc.claims.return_value = {"entity": {"id": "e"}, "claims": [], "count": 0, "truncated": False}
    with patch("app.services.chat_tools._get_temporal_query_service", return_value=svc):
        result = await chat_tools.list_claims(
            "e", date_from="2026-03-01", date_to="2026-03-31", max_results=999, include_stale=True
        )
    args, kwargs = svc.claims.await_args
    assert args == ("e", datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC))
    assert kwargs == {
        "include_stale": True, "decay_enabled": True, "max_results": chat_tools.LIST_CLAIMS_MAX_RESULTS,
    }
    assert result["date_from"] == "2026-03-01" and result["include_stale"] is True


@pytest.mark.asyncio
async def test_a_timestamp_upper_bound_includes_that_instant():
    from app.services import chat_tools

    svc = AsyncMock()
    svc.claims.return_value = {"entity": {"id": "e"}, "claims": [], "count": 0, "truncated": False}
    with patch("app.services.chat_tools._get_temporal_query_service", return_value=svc):
        await chat_tools.list_claims("e", date_to="2026-03-31T12:00:00Z")
    end = svc.claims.await_args.args[2]
    assert datetime(2026, 3, 31, 12, tzinfo=UTC) < end < datetime(2026, 3, 31, 12, 0, 1, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"date_from": "last spring"}, "Not a valid ISO-8601 time"),
        ({"date_from": "2026-04-01", "date_to": "2026-03-01"}, "date_to must not be before"),
    ],
)
async def test_bad_windows_are_reported_not_raised(kwargs, message):
    from app.services import chat_tools

    with patch("app.services.chat_tools._get_temporal_query_service", return_value=AsyncMock()):
        result = await chat_tools.list_claims("e", **kwargs)
    assert message in result["error"]


@pytest.mark.asyncio
async def test_needs_the_graph_and_an_existing_entity():
    from app.services import chat_tools

    with patch("app.services.chat_tools._get_temporal_query_service", return_value=None):
        assert "error" in await chat_tools.list_claims("e")
    svc = AsyncMock()
    svc.claims.return_value = None
    with patch("app.services.chat_tools._get_temporal_query_service", return_value=svc):
        assert "not found" in (await chat_tools.list_claims("nobody"))["error"]


@pytest.mark.asyncio
async def test_decay_disabled_flows_into_the_query(monkeypatch, tmp_path):
    from app.services import chat_tools

    cfg = tmp_path / "lanes.yaml"
    cfg.write_text("library:\n  decay:\n    enabled: false\n")
    monkeypatch.setenv("LANES_CONFIG_PATH", str(cfg))
    la.reset_lanes_config()
    svc = AsyncMock()
    svc.claims.return_value = {"entity": {"id": "e"}, "claims": [], "count": 0, "truncated": False}
    with patch("app.services.chat_tools._get_temporal_query_service", return_value=svc):
        await chat_tools.list_claims("e")
    assert svc.claims.await_args.kwargs["decay_enabled"] is False


# ---- the graph query ---------------------------------------------------------------------


class _Neo4j:
    """Answers the entity lookup and the claims query; records parameters."""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    async def execute_read(self, query, params=None):
        self.calls.append((query, params or {}))
        if "toLower(n.name)" in query:  # _RESOLVE
            if params["lookup"] in ("technology-dac", "DAC"):
                return [{"id": "technology-dac", "name": "DAC", "entity_type": "technology", "props": {}}]
            return []
        return self.rows


ROWS = [
    {
        "id": "c1", "content": "DAC will stay above $600/t through 2030",
        "occurred_at": "2025-01-10T00:00:00+00:00", "recorded_at": "2026-08-01T00:00:00+00:00",
        "valid_to": "2026-02-01T00:00:00+00:00", "attributed_to": "Carbon Desk",
        "lane": "library", "stale_after": "2025-07-09T00:00:00+00:00", "review_status": "pending",
        "source_id": "ingest-1", "source_title": "Outlook 2025",
        "links": ["mentions"],
        "attributed": [{"id": "organization-carbon-desk", "name": "Carbon Desk", "entity_type": "organization"}],
        "successors": ["c2"],
    },
    {
        "id": "c2", "content": "DAC costs fall below $200/t by 2030",
        "occurred_at": "2026-02-01T00:00:00+00:00", "recorded_at": "2026-08-01T00:00:00+00:00",
        "valid_to": None, "attributed_to": "Jo Analyst", "lane": None, "stale_after": None,
        "review_status": "pending", "source_id": "ingest-2", "source_title": "Revised outlook",
        "links": ["mentions", "attributed_to"], "attributed": [None], "successors": [],
    },
]


@pytest.mark.asyncio
async def test_claims_query_uses_typed_parameters():
    neo = _Neo4j(ROWS)
    start, end = datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)

    await TemporalQueryService(neo).claims("DAC", start, end, max_results=10, now=NOW)

    query, params = neo.calls[-1]
    assert params["id"] == "technology-dac"
    assert params["claim_edges"] == ["MENTIONS", "ATTRIBUTED_TO"]
    for key in ("start", "end", "now"):
        assert isinstance(params[key], datetime) and params[key].tzinfo is not None
    assert params["include_stale"] is False and params["max_results"] == 10
    assert "s.signal_type = 'claim'" in query
    assert "ORDER BY occurred_at ASC" in query
    assert "s.stale_after > $now" in query


@pytest.mark.asyncio
async def test_claims_are_shaped_with_attribution_supersession_and_decay():
    result = await TemporalQueryService(_Neo4j(ROWS)).claims("technology-dac", max_results=10, now=NOW)

    assert result["entity"] == {"id": "technology-dac", "name": "DAC", "entity_type": "technology"}
    assert result["count"] == 2 and result["truncated"] is False
    first, second = result["claims"]
    assert first["as_of"] == "2025-01-10T00:00:00+00:00"
    assert first["attribution"] == {
        "entities": [{"id": "organization-carbon-desk", "name": "Carbon Desk", "entity_type": "organization"}],
        "text": "Carbon Desk",
    }
    assert first["supersession"] == {
        "superseded": True, "superseded_by": ["c2"], "valid_to": "2026-02-01T00:00:00+00:00",
    }
    assert first["stale"] is True and first["lane"] == "library"
    assert first["source"] == {"id": "ingest-1", "title": "Outlook 2025"}
    # Unresolved attribution keeps the text; a pre-ADR-006 node has no lane
    assert second["attribution"] == {"entities": [], "text": "Jo Analyst"}
    assert second["supersession"]["superseded"] is False
    assert second["stale"] is False and second["lane"] == "library"
    assert second["links"] == ["attributed_to", "mentions"]


@pytest.mark.asyncio
async def test_decay_disabled_includes_and_never_marks_stale():
    neo = _Neo4j(ROWS[:1])
    result = await TemporalQueryService(neo).claims("DAC", decay_enabled=False, now=NOW)
    assert neo.calls[-1][1]["include_stale"] is True
    assert result["claims"][0]["stale"] is False


@pytest.mark.asyncio
async def test_unknown_entity_is_none():
    neo = _Neo4j(ROWS)
    assert await TemporalQueryService(neo).claims("nobody", now=NOW) is None
    assert len(neo.calls) == 1  # no claims query without an entity
