"""Tests for the inspector/trace-browsing surface (Phase 5).

Route contracts: GET /api/memories/{id}/inspector (404 on fully unknown),
GET /api/agent-memory/recall-traces list, and the inspect_memory MCP tool.
"""

from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    import app.routes.agent_memory as am_route
    import app.routes.memories_review as review_route

    async def fake_inspect(record_id, **kw):
        if record_id == "ghost":
            return None
        return {
            "schema_version": "imi.memory.inspector.v1",
            "record_id": record_id,
            "record_kind": "capture",
            "record": {"id": record_id},
            "audit_history": [],
            "usage": {"times_returned": 3, "times_used": 1, "times_ignored": 0},
            "judge_usage": [],
            "lineage": [{"record_id": record_id, "relation": "self"}],
            "influence": {"position": "evidence"},
        }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    async def fake_list_traces(session, *, task_id=None, surface=None, limit=50):
        return [{"request_id": "req-1", "query": "q", "task_id": task_id, "items": None}]

    monkeypatch.setattr(review_route, "inspect_memory", fake_inspect)
    monkeypatch.setattr(am_route, "_session_factory", lambda: FakeSession)
    monkeypatch.setattr(am_route, "list_traces", fake_list_traces)

    test_app = FastAPI()
    test_app.include_router(am_route.router)
    test_app.include_router(review_route.router)
    return TestClient(test_app)


def test_inspector_endpoint(client):
    resp = client.get("/api/memories/cap-1/inspector")
    assert resp.status_code == 200
    body = resp.json()
    assert body["usage"]["times_returned"] == 3
    assert body["influence"]["position"] == "evidence"


def test_inspector_unknown_404(client):
    assert client.get("/api/memories/ghost/inspector").status_code == 404


def test_traces_list(client):
    resp = client.get("/api/agent-memory/recall-traces", params={"task_id": "t-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["traces"][0]["request_id"] == "req-1"


def test_inspect_memory_tool_def():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    td = TOOL_DEFS["inspect_memory"]
    assert td["inputSchema"]["required"] == ["record_id"]


@pytest.mark.asyncio
async def test_inspect_memory_chat_tool_delegates():
    with patch(
        "app.services.memory_inspector.inspect_memory",
        new_callable=AsyncMock,
        return_value={"record_id": "cap-1", "record_kind": "capture"},
    ):
        from app.services.chat_tools import inspect_memory

        result = await inspect_memory("cap-1")
    assert result["record_id"] == "cap-1"


@pytest.mark.asyncio
async def test_inspect_memory_chat_tool_not_found_shape():
    with patch(
        "app.services.memory_inspector.inspect_memory",
        new_callable=AsyncMock,
        return_value=None,
    ):
        from app.services.chat_tools import inspect_memory

        result = await inspect_memory("ghost")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_mcp_dispatcher_routes_inspect_memory():
    mod = sys.modules.get("app.routes.mcp_server") or importlib.import_module(
        "app.routes.mcp_server"
    )
    assert "inspect_memory" in [t.name for t in mod.TOOLS]
    with patch(
        "app.services.chat_tools.inspect_memory",
        new_callable=AsyncMock,
        return_value={"record_id": "cap-1"},
    ) as mock_fn:
        response = await mod.handle_call_tool("inspect_memory", {"record_id": "cap-1"})
    mock_fn.assert_awaited_once()
    assert json.loads(response[0].text)["record_id"] == "cap-1"
