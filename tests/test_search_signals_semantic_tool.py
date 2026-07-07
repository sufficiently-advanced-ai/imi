"""Tests for the MCP search_signals_semantic tool (Task 1 — G3 wiring).

Covers:
  - Tool def present in TOOL_DEFS with required=["query"] and authority enum.
  - chat_tools.search_signals_semantic returns the unavailable-error shape when
    the facade raises / returns None (monkeypatched).
  - chat_tools.search_signals_semantic passes authority through to
    signal_retrieval (kwargs captured via monkeypatch).
  - mcp_server TOOLS list contains a tool named "search_signals_semantic".
  - mcp_server dispatcher routes to the right function.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Tool definition tests (no runtime deps)
# ---------------------------------------------------------------------------


def test_tool_def_present_with_required_query():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    assert "search_signals_semantic" in TOOL_DEFS
    td = TOOL_DEFS["search_signals_semantic"]
    assert td["inputSchema"]["required"] == ["query"]


def test_tool_def_has_authority_enum():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    props = TOOL_DEFS["search_signals_semantic"]["inputSchema"]["properties"]
    assert "authority" in props
    authority_prop = props["authority"]
    assert "enum" in authority_prop
    assert "evidence" in authority_prop["enum"]
    assert "instruction" in authority_prop["enum"]


def test_tool_def_has_expected_params():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    props = TOOL_DEFS["search_signals_semantic"]["inputSchema"]["properties"]
    for param in ("query", "signal_types", "status", "authority", "limit",
                  "recency_weight", "include_rejected"):
        assert param in props, f"Expected param '{param}' missing from inputSchema"


def test_tool_def_authority_description_mentions_adr_002():
    """The description MUST tell callers that authority=instruction satisfies ADR-002."""
    from app.services.mcp_tool_definitions import TOOL_DEFS

    td = TOOL_DEFS["search_signals_semantic"]
    desc = td["description"]
    # The plan says: description MUST tell the agent: `authority="instruction"`
    # returns only records satisfying the ADR-002 invariant.
    assert "instruction" in desc
    assert "ADR-002" in desc


def test_build_mcp_tool_works_for_semantic():
    """build_mcp_tool should not raise for search_signals_semantic."""
    from app.services.mcp_tool_definitions import build_mcp_tool

    tool = build_mcp_tool("search_signals_semantic")
    assert tool.name == "search_signals_semantic"


# ---------------------------------------------------------------------------
# chat_tools.search_signals_semantic — unavailable-error shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_unavailable_error_when_facade_returns_none():
    """When get_semantica_knowledge() returns None, return the error shape."""
    with patch(
        "app.services.chat_tools._get_semantica_for_signals",
        return_value=None,
    ):
        from app.services.chat_tools import search_signals_semantic

        result = await search_signals_semantic("what decisions were made")
    assert result["error"] == "semantic index unavailable"
    assert result["results"] == []


@pytest.mark.asyncio
async def test_returns_unavailable_error_when_facade_raises():
    """When get_semantica_knowledge() raises, return the error shape."""
    with patch(
        "app.services.chat_tools._get_semantica_for_signals",
        side_effect=RuntimeError("semantica not init"),
    ):
        from app.services.chat_tools import search_signals_semantic

        result = await search_signals_semantic("ladder")
    assert result["error"] == "semantic index unavailable"
    assert result["results"] == []


# ---------------------------------------------------------------------------
# chat_tools.search_signals_semantic — kwargs pass-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authority_passed_to_signal_retrieval(monkeypatch):
    """authority, limit, and include_rejected reach signal_retrieval.search_signals_semantic."""
    captured_kwargs: dict = {}

    # Pin the legacy passthrough: the fake facade's store is only used on the
    # (non-default) faiss backend.
    from app.config import settings
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "faiss", raising=False)

    def fake_search(vector_store, embedder, query, **kwargs):
        captured_kwargs.update(kwargs)
        return [{"id": "sig-1", "content": "dummy"}]

    fake_sk = MagicMock()
    fake_sk.vector_store = MagicMock()
    fake_sk.embedder = MagicMock()

    with patch("app.services.chat_tools._get_semantica_for_signals", return_value=fake_sk):
        with patch("app.services.signal_retrieval.search_signals_semantic", side_effect=fake_search):
            from app.services.chat_tools import search_signals_semantic

            result = await search_signals_semantic(
                "decisions",
                authority="instruction",
                limit=5,
                include_rejected=True,
            )

    assert captured_kwargs.get("authority") == "instruction"
    assert captured_kwargs.get("limit") == 5
    assert captured_kwargs.get("include_rejected") is True
    assert result["count"] == 1
    assert result["results"][0]["id"] == "sig-1"


@pytest.mark.asyncio
async def test_default_authority_is_evidence(monkeypatch):
    """Default call should use authority='evidence'."""
    captured_kwargs: dict = {}

    # Pin the legacy passthrough: the fake facade's store is only used on the
    # (non-default) faiss backend.
    from app.config import settings
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "faiss", raising=False)

    def fake_search(vector_store, embedder, query, **kwargs):
        captured_kwargs.update(kwargs)
        return []

    fake_sk = MagicMock()
    fake_sk.vector_store = MagicMock()
    fake_sk.embedder = MagicMock()

    with patch("app.services.chat_tools._get_semantica_for_signals", return_value=fake_sk):
        with patch("app.services.signal_retrieval.search_signals_semantic", side_effect=fake_search):
            from app.services.chat_tools import search_signals_semantic

            await search_signals_semantic("ladder")

    assert captured_kwargs.get("authority") == "evidence"


# ---------------------------------------------------------------------------
# Tenant scoping (Phase 3 fix): the tool must resolve the tenant's vector
# store (pgvector on hosted) and pass tenant_id into the retrieval filter —
# previously it read sk.vector_store directly and dropped the tenant.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_uses_resolve_vector_store_and_tenant():
    captured: dict = {}
    resolved_store = MagicMock(name="resolved_store")

    def fake_search(vector_store, embedder, query, **kwargs):
        captured["vector_store"] = vector_store
        captured.update(kwargs)
        return []

    fake_sk = MagicMock()
    fake_sk.vector_store = MagicMock(name="raw_store")
    fake_sk.embedder = MagicMock()

    from app.core.middleware.request_context import current_tenant_id

    token = current_tenant_id.set("tenant-42")
    try:
        with (
            patch("app.services.chat_tools._get_semantica_for_signals", return_value=fake_sk),
            patch(
                "app.services.signal_indexing.resolve_vector_store",
                return_value=resolved_store,
            ) as mock_resolve,
            patch(
                "app.services.signal_retrieval.search_signals_semantic",
                side_effect=fake_search,
            ),
        ):
            from app.services.chat_tools import search_signals_semantic

            await search_signals_semantic("ladder")
    finally:
        current_tenant_id.reset(token)

    mock_resolve.assert_called_once_with(fake_sk.vector_store)
    assert captured["vector_store"] is resolved_store
    assert captured.get("tenant_id") == "tenant-42"


# ---------------------------------------------------------------------------
# mcp_server TOOLS list
# ---------------------------------------------------------------------------


def test_mcp_tools_list_contains_search_signals_semantic():
    """search_signals_semantic must appear in the mcp_server TOOLS list."""
    # Re-import to pick up module-level TOOLS constant; avoid triggering MCP
    # server startup side-effects by importing the module directly.
    import importlib
    import sys

    # mcp_server may already be imported; re-use the cached module.
    mcp_mod = sys.modules.get("app.routes.mcp_server")
    if mcp_mod is None:
        mcp_mod = importlib.import_module("app.routes.mcp_server")

    tool_names = [t.name for t in mcp_mod.TOOLS]
    assert "search_signals_semantic" in tool_names, (
        f"search_signals_semantic not found in TOOLS; got: {tool_names}"
    )


# ---------------------------------------------------------------------------
# mcp_server dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_dispatcher_routes_search_signals_semantic():
    """handle_call_tool('search_signals_semantic', ...) should call chat_tools."""
    import sys
    import importlib

    mcp_mod = sys.modules.get("app.routes.mcp_server")
    if mcp_mod is None:
        mcp_mod = importlib.import_module("app.routes.mcp_server")

    mock_result = {"results": [{"id": "s1"}], "count": 1}

    with patch(
        "app.services.chat_tools.search_signals_semantic",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_fn:
        response = await mcp_mod.handle_call_tool(
            "search_signals_semantic",
            {"query": "test query", "authority": "evidence"},
        )

    mock_fn.assert_awaited_once()
    assert response  # non-empty TextContent list
    import json

    data = json.loads(response[0].text)
    assert data["count"] == 1
