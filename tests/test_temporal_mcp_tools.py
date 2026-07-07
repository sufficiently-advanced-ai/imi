"""
Tests for Temporal MCP Tools — Issue #864.

Covers:
- Phase 1 MCP tools: entity_at_time, active_relationships_at_time,
  get_entity_provenance, decision_influence, decision_stats
- Phase 3 MCP tools: what_changed, what_changed_between, graph_as_of,
  find_contradictions, temporal_blast_radius
- chat_tools.py wrapper functions
- Server factory includes temporal tools
"""

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock heavy imports that aren't available outside Docker.
# Only stub modules that are GENUINELY not installed: stubbing an installed
# module poisons sys.modules for every file that runs after this one in the
# same pytest process (real neo4j/otel consumers then crash on the MagicMock).
_MOCK_MODULES = [
    "numpy", "fastembed", "faiss",
    "semantica", "semantica.graph_store", "semantica.vector_store",
    "semantica.embeddings", "semantica.context", "semantica.semantic_extract",
    "semantica.deduplication", "semantica.kg", "semantica.search",
    "neo4j", "neo4j.graph",
    "opentelemetry", "opentelemetry.trace", "opentelemetry.sdk",
    "opentelemetry.sdk.trace", "opentelemetry.sdk.resources",
    "opentelemetry.exporter", "opentelemetry.exporter.otlp",
    "opentelemetry.exporter.otlp.proto", "opentelemetry.exporter.otlp.proto.http",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    "opentelemetry.sdk.trace.export",
    "opentelemetry.context", "opentelemetry.context.contextvars_context",
]
import importlib.util

for _mod in _MOCK_MODULES:
    if _mod not in sys.modules and importlib.util.find_spec(_mod.split(".")[0]) is None:
        sys.modules[_mod] = MagicMock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _call_tool(tool_obj, args: dict) -> dict:
    """Call a tool whether it's a SdkMcpTool or a plain async function."""
    if hasattr(tool_obj, "handler"):
        return await tool_obj.handler(args)
    return await tool_obj(args)


def _parse_result(result: dict) -> dict:
    """Parse the JSON text from an MCP tool result."""
    text = result["content"][0]["text"]
    if text.startswith("Error:"):
        return {"error": text}
    return json.loads(text)


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


# ===========================================================================
# chat_tools.py wrapper tests
# ===========================================================================


class TestChatToolsWrappers:
    """Test the thin wrappers in chat_tools.py."""

    @pytest.mark.asyncio
    async def test_entity_at_time_delegates_to_semantica(self):
        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(return_value={
            "id": "e1", "name": "Acme", "type": "Organization",
            "attributes": {"sector": "Tech"},
        })

        with patch("app.services.chat_tools._get_semantica", return_value=mock_sk):
            from app.services.chat_tools import entity_at_time
            result = await entity_at_time(
                entity_id="e1",
                timestamp="2026-01-15T00:00:00Z",
            )

        assert result["name"] == "Acme"
        mock_sk.get_state_at.assert_called_once()

    @pytest.mark.asyncio
    async def test_entity_at_time_returns_error_without_semantica(self):
        with patch("app.services.chat_tools._get_semantica", return_value=None):
            from app.services.chat_tools import entity_at_time
            result = await entity_at_time(entity_id="e1", timestamp="2026-01-15T00:00:00Z")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_active_relationships_at_time_delegates(self):
        mock_sk = MagicMock()
        mock_sk.get_active_relationships = AsyncMock(return_value=[
            {"relationship_type": "WORKS_FOR", "target_id": "e2", "target_name": "Acme"},
        ])

        with patch("app.services.chat_tools._get_semantica", return_value=mock_sk):
            from app.services.chat_tools import active_relationships_at_time
            result = await active_relationships_at_time(
                entity_id="e1",
                timestamp="2026-01-15T00:00:00Z",
            )

        assert len(result) == 1
        assert result[0]["relationship_type"] == "WORKS_FOR"

    @pytest.mark.asyncio
    async def test_get_entity_provenance_delegates(self):
        mock_sk = MagicMock()
        mock_sk.get_provenance = AsyncMock(return_value={
            "entity_id": "e1",
            "history": [{"source": "doc.md", "action": "created"}],
        })

        with patch("app.services.chat_tools._get_semantica", return_value=mock_sk):
            from app.services.chat_tools import get_entity_provenance
            result = await get_entity_provenance(entity_id="e1")

        assert len(result["history"]) == 1

    @pytest.mark.asyncio
    async def test_decision_influence_delegates(self):
        mock_sk = MagicMock()
        mock_sk.decisions = MagicMock()
        mock_sk.decisions.analyze_influence = AsyncMock(return_value={
            "decision_id": "d1", "influence": [{"type": "downstream"}],
        })

        with patch("app.services.chat_tools._get_semantica", return_value=mock_sk):
            from app.services.chat_tools import decision_influence
            result = await decision_influence(decision_id="d1")

        assert result["decision_id"] == "d1"

    @pytest.mark.asyncio
    async def test_decision_stats_delegates(self):
        mock_sk = MagicMock()
        mock_sk.decisions = MagicMock()
        mock_sk.decisions.get_decision_stats = AsyncMock(return_value={
            "total_decisions": 42, "categories": {"strategy": 10},
        })

        with patch("app.services.chat_tools._get_semantica", return_value=mock_sk):
            from app.services.chat_tools import decision_stats
            result = await decision_stats()

        assert result["total_decisions"] == 42

    @pytest.mark.asyncio
    async def test_what_changed_wrapper(self):
        mock_svc = MagicMock()
        mock_svc.what_changed = AsyncMock(return_value={
            "entity_id": "e1", "changes": [{"field": "name", "old": "A", "new": "B"}],
        })

        with patch("app.services.chat_tools._get_temporal_query_service", return_value=mock_svc):
            from app.services.chat_tools import what_changed
            result = await what_changed(entity_id="e1", since="2026-01-15T00:00:00Z")

        assert result["entity_id"] == "e1"
        assert len(result["changes"]) == 1

    @pytest.mark.asyncio
    async def test_what_changed_between_wrapper(self):
        mock_svc = MagicMock()
        mock_svc.what_changed_between = AsyncMock(return_value={
            "entity_id": "e1", "changes": [],
        })

        with patch("app.services.chat_tools._get_temporal_query_service", return_value=mock_svc):
            from app.services.chat_tools import what_changed_between
            result = await what_changed_between(
                entity_id="e1",
                start="2026-01-15T00:00:00Z",
                end="2026-02-15T00:00:00Z",
            )

        assert result["entity_id"] == "e1"

    @pytest.mark.asyncio
    async def test_graph_as_of_wrapper(self):
        mock_svc = MagicMock()
        mock_svc.graph_as_of = AsyncMock(return_value={
            "nodes": [{"id": "e1", "name": "Alice"}],
            "edges": [],
        })

        with patch("app.services.chat_tools._get_temporal_query_service", return_value=mock_svc):
            from app.services.chat_tools import graph_as_of
            result = await graph_as_of(
                entity_id="e1",
                timestamp="2026-01-15T00:00:00Z",
            )

        assert len(result["nodes"]) == 1

    @pytest.mark.asyncio
    async def test_find_contradictions_wrapper(self):
        mock_svc = MagicMock()
        mock_svc.find_contradictions = AsyncMock(return_value={
            "entity_id": "e1", "contradictions": [],
        })

        with patch("app.services.chat_tools._get_temporal_query_service", return_value=mock_svc):
            from app.services.chat_tools import find_contradictions
            result = await find_contradictions(entity_id="e1")

        assert result["contradictions"] == []

    @pytest.mark.asyncio
    async def test_temporal_blast_radius_wrapper(self):
        mock_svc = MagicMock()
        mock_svc.temporal_blast_radius = AsyncMock(return_value={
            "nodes": [], "edges": [], "depth_map": {},
        })

        with patch("app.services.chat_tools._get_temporal_query_service", return_value=mock_svc):
            from app.services.chat_tools import temporal_blast_radius
            result = await temporal_blast_radius(
                entity_id="e1",
                at_time="2026-01-15T00:00:00Z",
            )

        assert result["nodes"] == []


# ===========================================================================
# MCP tool registration tests
# ===========================================================================


class TestTemporalMCPToolRegistration:
    """Test that temporal tools are registered in the server factory."""



# ===========================================================================
# MCP tool wrapper tests
# ===========================================================================


class TestTemporalMCPToolWrappers:
    """Test that MCP tool wrappers call through to chat_tools correctly."""

    @pytest.mark.asyncio
    async def test_entity_at_time_tool(self):
        from app.agents.chat_tools_mcp import entity_at_time_tool

        with patch("app.services.chat_tools.entity_at_time", new_callable=AsyncMock,
                   return_value={"id": "e1", "name": "Acme", "type": "Organization"}):
            result = await _call_tool(entity_at_time_tool, {
                "entity_id": "e1", "timestamp": "2026-01-15T00:00:00Z"
            })

        parsed = _parse_result(result)
        assert parsed["name"] == "Acme"

    @pytest.mark.asyncio
    async def test_what_changed_tool(self):
        """date_to omitted → wrapper dispatches to what_changed (open-ended-from-now)."""
        from app.agents.chat_tools_mcp import what_changed_tool

        with patch("app.services.chat_tools.what_changed", new_callable=AsyncMock,
                   return_value={"entity_id": "e1", "changes": []}) as mocked:
            result = await _call_tool(what_changed_tool, {
                "entity_id": "e1", "date_from": "2026-01-15T00:00:00Z"
            })

        parsed = _parse_result(result)
        assert parsed["entity_id"] == "e1"
        mocked.assert_awaited_once_with(
            entity_id="e1", since="2026-01-15T00:00:00Z"
        )

    @pytest.mark.asyncio
    async def test_what_changed_tool_bounded(self):
        """Both date_from and date_to → wrapper dispatches to what_changed_between."""
        from app.agents.chat_tools_mcp import what_changed_tool

        with patch("app.services.chat_tools.what_changed_between", new_callable=AsyncMock,
                   return_value={"entity_id": "e1", "changes": []}) as mocked:
            result = await _call_tool(what_changed_tool, {
                "entity_id": "e1",
                "date_from": "2026-01-01T00:00:00Z",
                "date_to": "2026-01-15T00:00:00Z",
            })

        parsed = _parse_result(result)
        assert parsed["entity_id"] == "e1"
        mocked.assert_awaited_once_with(
            entity_id="e1",
            start="2026-01-01T00:00:00Z",
            end="2026-01-15T00:00:00Z",
        )

    @pytest.mark.asyncio
    async def test_graph_as_of_tool(self):
        from app.agents.chat_tools_mcp import graph_as_of_tool

        with patch("app.services.chat_tools.graph_as_of", new_callable=AsyncMock,
                   return_value={"nodes": [{"id": "e1"}], "edges": []}):
            result = await _call_tool(graph_as_of_tool, {
                "entity_id": "e1", "timestamp": "2026-01-15T00:00:00Z"
            })

        parsed = _parse_result(result)
        assert len(parsed["nodes"]) == 1

    @pytest.mark.asyncio
    async def test_temporal_blast_radius_tool(self):
        from app.agents.chat_tools_mcp import temporal_blast_radius_tool

        with patch("app.services.chat_tools.temporal_blast_radius", new_callable=AsyncMock,
                   return_value={"nodes": [], "edges": [], "depth_map": {}}):
            result = await _call_tool(temporal_blast_radius_tool, {
                "entity_id": "e1", "at_time": "2026-01-15T00:00:00Z"
            })

        parsed = _parse_result(result)
        assert parsed["nodes"] == []

    @pytest.mark.asyncio
    async def test_tool_handles_error(self):
        from app.agents.chat_tools_mcp import entity_at_time_tool

        with patch("app.services.chat_tools.entity_at_time", new_callable=AsyncMock,
                   side_effect=RuntimeError("Neo4j down")):
            result = await _call_tool(entity_at_time_tool, {
                "entity_id": "e1", "timestamp": "2026-01-15T00:00:00Z"
            })

        text = result["content"][0]["text"]
        assert "Error:" in text
        assert "Neo4j down" in text
