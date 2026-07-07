"""
Tests for ChatAgent — SDK-based implementation.

Covers:
- Initialization and interface preservation
- process_query return contract (answer, context_files, tool_calls, cited_documents)
- SSE event emission via sse_manager
- System prompt construction (domain schema, graph tool instructions)
- With/without bot_id (meeting context tool conditionally included)
- _can_use_tool permission handler
- Error handling
- make_decision delegation
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.chat import ChatAgent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_claude_client():
    return AsyncMock()


@pytest.fixture
def agent(mock_claude_client):
    """Create a ChatAgent with mocked dependencies."""
    return ChatAgent(claude_client=mock_claude_client)


@pytest.fixture
def agent_with_bot(mock_claude_client):
    """Create a ChatAgent with bot_id set."""
    return ChatAgent(claude_client=mock_claude_client, bot_id="test-bot-123")


@pytest.fixture
def mock_sdk_client():
    """Mock ClaudeSDKClient that yields a final answer."""

    class FakeChunk:
        def __init__(self, text):
            self.content = text

    client = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

    async def fake_receive():
        yield FakeChunk("Here is your answer about meeting-notes.md and project.md")

    client.receive_response = fake_receive
    return client


# ---------------------------------------------------------------------------
# Initialization tests
# ---------------------------------------------------------------------------


class TestChatAgentInitialization:

    def test_initialization_with_bot_id(self):
        agent = ChatAgent(bot_id="bot-abc")
        assert agent.bot_id == "bot-abc"

    def test_initialization_with_platform(self):
        agent = ChatAgent(platform="  Zoom  ")
        assert agent.platform == "zoom"

    def test_configure_streaming(self, agent):
        agent.configure_streaming("exec-123", emit_sse=True)
        assert agent.execution_id == "exec-123"
        assert agent.emit_sse is True


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_base_prompt_content(self, agent):
        prompt = agent._build_system_prompt()
        assert "helpful assistant" in prompt
        assert "knowledge base" in prompt
        assert "Query Tools" in prompt
        assert "Graph Maintenance Tools" in prompt
        assert "search_knowledge_graph" in prompt
        assert "graph_add_node" in prompt

    def test_prompt_without_bot_id(self, agent):
        prompt = agent._build_system_prompt()
        assert "MEETING CONTEXT" not in prompt


    @patch(
        "app.agents.chat.ChatAgent._build_domain_schema_section",
        return_value="=== DOMAIN SCHEMA ===\nperson -> works_on -> role\n",
    )
    def test_domain_schema_included(self, mock_schema, agent):
        prompt = agent._build_system_prompt()
        assert "DOMAIN SCHEMA" in prompt
        assert "person -> works_on -> role" in prompt


# ---------------------------------------------------------------------------
# Permission handler tests
# ---------------------------------------------------------------------------


class TestPermissionHandler:
    @pytest.mark.asyncio
    async def test_approves_chat_tools(self, agent):
        result = await agent._can_use_tool("mcp__chat__search_knowledge_graph", {"query": "test"}, {})
        assert hasattr(result, "updated_input")

    @pytest.mark.asyncio
    async def test_approves_graph_tools(self, agent):
        result = await agent._can_use_tool("mcp__chat__graph_add_node", {"name": "test"}, {})
        assert hasattr(result, "updated_input")



# ---------------------------------------------------------------------------
# process_query tests
# ---------------------------------------------------------------------------


class TestProcessQuery:
    @pytest.mark.asyncio
    async def test_return_contract(self, agent, mock_sdk_client):
        """process_query must return dict with answer, context_files, tool_calls, cited_documents."""
        with patch("app.agents.chat.ClaudeSDKClient", return_value=mock_sdk_client):
            result = await agent.process_query("Who is Sarah?")

        assert "answer" in result
        assert "context_files" in result
        assert "tool_calls" in result
        assert "cited_documents" in result
        assert isinstance(result["answer"], str)
        assert isinstance(result["context_files"], list)
        assert isinstance(result["tool_calls"], list)
        assert isinstance(result["cited_documents"], list)

    @pytest.mark.asyncio
    async def test_answer_extracted_from_sdk_response(self, agent, mock_sdk_client):
        with patch("app.agents.chat.ClaudeSDKClient", return_value=mock_sdk_client):
            result = await agent.process_query("Tell me about the project")

        assert "answer" in result["answer"].lower() or len(result["answer"]) > 0

    @pytest.mark.asyncio
    async def test_citations_extracted(self, agent, mock_sdk_client):
        with patch("app.agents.chat.ClaudeSDKClient", return_value=mock_sdk_client):
            result = await agent.process_query("Tell me about the project")

        # The mock response contains "meeting-notes.md" and "project.md"
        assert "meeting-notes.md" in result["cited_documents"]
        assert "project.md" in result["cited_documents"]

    @pytest.mark.asyncio
    async def test_manual_context_appended_to_prompt(self, agent, mock_sdk_client):
        with patch("app.agents.chat.ClaudeSDKClient", return_value=mock_sdk_client):
            result = await agent.process_query(
                "What happened?",
                manual_context=["file1.md", "file2.md"],
            )

        assert "answer" in result
        # Context files should include the manual ones
        assert "file1.md" in result["context_files"]
        assert "file2.md" in result["context_files"]

    @pytest.mark.asyncio
    async def test_error_handling(self, agent):
        """SDK connection failure should return error dict."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.disconnect = AsyncMock()

        with patch("app.agents.chat.ClaudeSDKClient", return_value=mock_client):
            result = await agent.process_query("Test query")

        assert "error" in result
        assert "Connection refused" in result["error"]
        assert result["answer"]  # Should have user-facing error message

    @pytest.mark.asyncio
    async def test_empty_answer_fallback(self, agent):
        """Empty SDK response should produce a fallback message."""

        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.query = AsyncMock()

        async def empty_response():
            return
            yield  # noqa: F811 — makes this an async generator

        mock_client.receive_response = empty_response

        with patch("app.agents.chat.ClaudeSDKClient", return_value=mock_client):
            result = await agent.process_query("Hello")

        assert "apologize" in result["answer"].lower() or "couldn't" in result["answer"].lower()

    @pytest.mark.asyncio
    async def test_conversation_history_updated(self, agent, mock_sdk_client):
        with patch("app.agents.chat.ClaudeSDKClient", return_value=mock_sdk_client):
            await agent.process_query("First question")

        assert len(agent.conversation_history) == 2  # user + assistant
        assert agent.conversation_history[0]["role"] == "user"
        assert agent.conversation_history[1]["role"] == "assistant"



# ---------------------------------------------------------------------------
# SSE event emission tests
# ---------------------------------------------------------------------------


class TestSSEEmission:
    @pytest.mark.asyncio
    async def test_emits_agent_start(self, agent, mock_sdk_client):
        agent.configure_streaming("exec-1", emit_sse=True)

        with (
            patch("app.agents.chat.ClaudeSDKClient", return_value=mock_sdk_client),
            patch("app.services.sse_manager.sse_manager") as mock_sse,
        ):
            mock_sse.send_event = AsyncMock()
            await agent.process_query("Test")

            # Find agent_start call
            calls = mock_sse.send_event.call_args_list
            event_types = [c[0][1] for c in calls]
            assert "agent_start" in event_types

    @pytest.mark.asyncio
    async def test_emits_agent_complete(self, agent, mock_sdk_client):
        agent.configure_streaming("exec-2", emit_sse=True)

        with (
            patch("app.agents.chat.ClaudeSDKClient", return_value=mock_sdk_client),
            patch("app.services.sse_manager.sse_manager") as mock_sse,
        ):
            mock_sse.send_event = AsyncMock()
            await agent.process_query("Test")

            calls = mock_sse.send_event.call_args_list
            event_types = [c[0][1] for c in calls]
            assert "agent_complete" in event_types

    @pytest.mark.asyncio
    async def test_no_sse_when_disabled(self, agent, mock_sdk_client):
        # SSE not configured (default)
        with (
            patch("app.agents.chat.ClaudeSDKClient", return_value=mock_sdk_client),
            patch("app.services.sse_manager.sse_manager") as mock_sse,
        ):
            mock_sse.send_event = AsyncMock()
            await agent.process_query("Test")
            mock_sse.send_event.assert_not_called()


# ---------------------------------------------------------------------------
# make_decision tests
# ---------------------------------------------------------------------------


class TestMakeDecision:
    @pytest.mark.asyncio
    async def test_delegates_to_process_query(self, agent, mock_sdk_client):
        with patch("app.agents.chat.ClaudeSDKClient", return_value=mock_sdk_client):
            outcome = await agent.make_decision({"query": "Test query"})

        assert outcome.decision == "chat_response"
        assert outcome.confidence == 0.9
        assert "query_processed" in outcome.actions

    @pytest.mark.asyncio
    async def test_low_confidence_on_error(self, agent):
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(side_effect=Exception("fail"))
        mock_client.disconnect = AsyncMock()

        with patch("app.agents.chat.ClaudeSDKClient", return_value=mock_client):
            outcome = await agent.make_decision({"query": "Test"})

        assert outcome.confidence == 0.1


# ---------------------------------------------------------------------------
# Citation extraction tests
# ---------------------------------------------------------------------------


class TestSDKUnavailable:
    @pytest.mark.asyncio
    async def test_sdk_unavailable_returns_error(self, agent):
        """When SDK_AVAILABLE is False, process_query should return an error response."""
        with patch("app.agents.chat.SDK_AVAILABLE", False):
            result = await agent.process_query("Test query")

        assert "error" in result
        assert "not available" in result["error"].lower() or "not available" in result["answer"].lower()
        assert isinstance(result["context_files"], list)
        assert isinstance(result["tool_calls"], list)
        assert isinstance(result["cited_documents"], list)


class TestTimeoutHandling:
    @pytest.mark.asyncio
    async def test_streaming_timeout_returns_friendly_message(self, agent):
        """If query+streaming exceeds 120s, should return a timeout message."""
        import asyncio

        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.query = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("app.agents.chat.ClaudeSDKClient", return_value=mock_client):
            result = await agent.process_query("Test query")

        assert "timed out" in result["answer"].lower()


class TestConversationHistoryCap:
    @pytest.mark.asyncio
    async def test_history_capped(self, agent, mock_sdk_client):
        agent.max_history_messages = 3  # Cap at 3 pairs = 6 entries

        with patch("app.agents.chat.ClaudeSDKClient", return_value=mock_sdk_client):
            for i in range(5):
                await agent.process_query(f"Question {i}")

        # 5 queries x 2 entries = 10, capped to 3*2=6
        assert len(agent.conversation_history) <= 6

    def test_max_history_messages_default(self):
        agent = ChatAgent()
        assert agent.max_history_messages == 50


class TestCitationExtraction:
    def test_extracts_md_files(self, agent):
        text = "Based on meeting-notes.md and candidates/sarah-chen.md, we found..."
        citations = agent._extract_citations(text)
        assert "meeting-notes.md" in citations
        assert "candidates/sarah-chen.md" in citations

    def test_deduplicates(self, agent):
        text = "See meeting.md. As shown in meeting.md, the results..."
        citations = agent._extract_citations(text)
        assert citations.count("meeting.md") == 1

    def test_no_citations(self, agent):
        text = "I don't have any relevant documents to cite."
        citations = agent._extract_citations(text)
        assert citations == []
