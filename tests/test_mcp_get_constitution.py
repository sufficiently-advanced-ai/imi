"""Tests for the `get_constitution` MCP tool and its service helper.

Covers:
  1. render_current_constitution() — builds current Markdown in-memory.
  2. The kb-graph MCP wiring — tool definition is registered, and the dispatch
     branch returns the Markdown VERBATIM (not JSON-escaped via _text()).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _mock_store_with_active_decision() -> MagicMock:
    """A SignalStore mock returning one confirmed-active decision.

    Mirrors the seeding pattern in tests/issue-954_test_constitution_export.py:
    load_decision_signals(store=...) calls store.load_all().
    """
    from app.models.signal import MeetingSignals, Signal

    sig = Signal(
        id="sig-const-001",
        type="decision",
        content="Use Redis for session storage",
        source_meeting_id="bot-session-1",
        source_timestamp="2026-03-01T09:00:00Z",
        review_status="confirmed",
        provenance_status="user_confirmed",
        can_use_as_evidence=True,
        can_use_as_instruction=True,
    )
    ms = MeetingSignals(
        meeting_id="meet-session-1", bot_id="bot-session-1", signals=[sig]
    )
    store = MagicMock()
    store.load_all.return_value = [ms]
    return store


# ---------------------------------------------------------------------------
# 1. render_current_constitution — in-memory fresh build
# ---------------------------------------------------------------------------


class TestRenderCurrentConstitution:
    def test_returns_markdown_with_frontmatter_and_decision(self):
        from app.services.constitution import render_current_constitution

        store = _mock_store_with_active_decision()
        with patch("app.services.constitution.signal_store", store):
            out = render_current_constitution()

        assert isinstance(out, str)
        # Raw Markdown: starts with YAML frontmatter, not a JSON-quoted blob.
        assert out.startswith("---"), out[:40]
        assert "artifact: constitution" in out
        assert "Use Redis for session storage" in out

    def test_empty_corpus_returns_valid_document_not_error(self):
        from app.services.constitution import render_current_constitution

        store = MagicMock()
        store.load_all.return_value = []
        with patch("app.services.constitution.signal_store", store):
            out = render_current_constitution()

        assert out.startswith("---")
        assert "_No confirmed decisions yet._" in out

    def test_reflects_current_signals_each_call(self):
        """No persisted file is read — the build is driven purely by the store."""
        from app.services.constitution import render_current_constitution

        store = _mock_store_with_active_decision()
        with patch("app.services.constitution.signal_store", store):
            render_current_constitution()
            render_current_constitution()

        # Loaded live on every call (no caching of a stale artifact).
        assert store.load_all.call_count == 2


# ---------------------------------------------------------------------------
# 2. MCP wiring — tool definition + dispatch returns raw Markdown
# ---------------------------------------------------------------------------


class TestGetConstitutionToolWiring:
    def test_tool_definition_registered_no_params(self):
        from app.services.mcp_tool_definitions import TOOL_DEFS

        assert "get_constitution" in TOOL_DEFS
        td = TOOL_DEFS["get_constitution"]
        assert td["name"] == "get_constitution"
        # No-parameter contract (filtering is a documented future extension).
        assert td["inputSchema"].get("properties") == {}
        assert "required" not in td["inputSchema"]

    def test_tool_listed_in_kb_graph_server(self):
        from app.routes.mcp_server import TOOLS

        assert any(t.name == "get_constitution" for t in TOOLS)

    @pytest.mark.asyncio
    async def test_dispatch_returns_raw_markdown_not_json(self):
        """The branch must bypass _text(): no JSON quoting/escaping of the doc."""
        from app.routes.mcp_server import handle_call_tool

        store = _mock_store_with_active_decision()
        with patch("app.services.constitution.signal_store", store):
            result = await handle_call_tool("get_constitution", {})

        assert isinstance(result, list) and len(result) == 1
        content = result[0]
        assert content.type == "text"
        text = content.text
        # Raw Markdown — would be '"---\\n..."' if it had gone through json.dumps.
        assert text.startswith("---"), text[:40]
        assert "artifact: constitution" in text
        assert "Use Redis for session storage" in text

    @pytest.mark.asyncio
    async def test_dispatch_rejects_unexpected_arguments(self):
        """No-param contract: unknown keys are rejected, not silently ignored."""
        import json

        from app.routes.mcp_server import handle_call_tool

        store = _mock_store_with_active_decision()
        with patch("app.services.constitution.signal_store", store):
            result = await handle_call_tool("get_constitution", {"filter": "redis"})

        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert "error" in payload
        assert "filter" in payload["error"]
        # The document must NOT have been built when arguments are invalid.
        store.load_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_handles_empty_corpus(self):
        from app.routes.mcp_server import handle_call_tool

        store = MagicMock()
        store.load_all.return_value = []
        with patch("app.services.constitution.signal_store", store):
            result = await handle_call_tool("get_constitution", {})

        assert len(result) == 1
        assert result[0].text.startswith("---")
        assert "_No confirmed decisions yet._" in result[0].text
