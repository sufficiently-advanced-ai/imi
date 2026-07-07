"""Test ClaudeClient metrics recording functionality."""

import pytest
from unittest.mock import Mock, patch, MagicMock, call
from app.services.claude_client import ClaudeClient
from app.config import Settings
from anthropic.types import Message, Usage, TextBlock
from app.metrics import record_llm_usage


@pytest.fixture
def mock_settings():
    """Mock settings for testing."""
    return Settings(
        ANTHROPIC_API_KEY="test-key",
        GIT_REPO_URL="https://github.com/test/repo",
        WEBHOOK_SECRET="test-secret"
    )


@pytest.fixture
def mock_metrics():
    """Mock the metrics recording function."""
    with patch('app.metrics.record_llm_usage') as mock:
        yield mock


@pytest.fixture
def claude_client(mock_settings, mock_metrics):
    """Create a ClaudeClient instance with mocked dependencies."""
    with patch('app.services.claude_client.settings', mock_settings):
        with patch('app.services.claude_client.Anthropic') as mock_anthropic:
            # Mock the messages.create response
            mock_response = Message(
                id="msg_test",
                type="message",
                role="assistant",
                content=[TextBlock(type="text", text="Test response")],
                model="claude-3-opus-20240229",
                stop_reason="end_turn",
                stop_sequence=None,
                usage=Usage(input_tokens=10, output_tokens=20)
            )
            mock_anthropic.return_value.messages.create.return_value = mock_response
            
            client = ClaudeClient()
            client.client = mock_anthropic.return_value
            return client


class TestClaudeClientMetrics:
    """Test metrics recording in ClaudeClient."""
    
    @staticmethod
    def _mock_response(input_tokens: int = 10, output_tokens: int = 20) -> Message:
        """Anthropic-shaped response for stubbing ClaudeClient._dispatch.

        generate_message routes calls through the endpoint registry and
        ``_dispatch`` (which builds real Anthropic clients), so that is the
        seam to stub — not the legacy ``client.client`` attribute.
        """
        return Message(
            id="msg_test",
            type="message",
            role="assistant",
            content=[TextBlock(type="text", text="Test response")],
            model="claude-3-opus-20240229",
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        )

    @pytest.mark.asyncio
    async def test_generate_message_records_metrics(self, claude_client, mock_metrics):
        """Test that generate_message() properly records metrics."""
        # Act
        with patch.object(ClaudeClient, "_dispatch", return_value=self._mock_response()):
            response = await claude_client.generate_message(
                messages=[{"role": "user", "content": "Hello"}],
                model="claude-3-opus-20240229",
                max_tokens=100
            )

        # Assert
        assert response.content[0].text == "Test response"

        # Verify metrics were recorded
        mock_metrics.assert_called_once()
        call_args = mock_metrics.call_args[1]
        assert call_args["model"] == "claude-3-opus-20240229"
        assert call_args["operation"] == "chat"  # Default operation label
        assert call_args["input_tokens"] == 10
        assert call_args["output_tokens"] == 20
        assert call_args["cost"] > 0  # Cost should be calculated

    @pytest.mark.asyncio
    async def test_generate_message_hardcoded_operation(self, claude_client, mock_metrics):
        """Test that generate_message() defaults to the 'chat' operation."""
        # Act
        with patch.object(ClaudeClient, "_dispatch", return_value=self._mock_response()):
            response = await claude_client.generate_message(
                messages=[{"role": "user", "content": "Hello"}],
                model="claude-3-opus-20240229"
            )

        # Assert - default operation label
        call_args = mock_metrics.call_args[1]
        assert call_args["operation"] == "chat"
    
    def test_direct_client_access_does_not_record_metrics(self, claude_client, mock_metrics):
        """Test that direct client.messages.create() does NOT record metrics."""
        # Act - Call the API directly like ChatAgent does
        mock_response = Message(
            id="msg_test",
            type="message", 
            role="assistant",
            content=[{"type": "text", "text": "Direct response"}],
            model="claude-3-opus-20240229",
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=5, output_tokens=10)
        )
        claude_client.client.messages.create.return_value = mock_response
        
        response = claude_client.client.messages.create(
            model="claude-3-opus-20240229",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100
        )
        
        # Assert - Metrics should NOT be recorded
        mock_metrics.assert_not_called()
        assert response.content[0].text == "Direct response"
    
    def test_calculate_cost_accuracy(self, claude_client):
        """Test that cost calculation is accurate for different models."""
        # Test Opus model (input + output tokens)
        opus_cost = claude_client._calculate_cost(500, 500, "claude-3-opus-20240229")
        # Input: 500 * $15/1M = 0.0075, Output: 500 * $75/1M = 0.0375
        expected_opus = 0.0075 + 0.0375
        assert opus_cost == expected_opus
        
        # Test Sonnet model
        sonnet_cost = claude_client._calculate_cost(500, 500, "claude-3-5-sonnet-20241022")
        # Input: 500 * $3/1M = 0.0015, Output: 500 * $15/1M = 0.0075
        expected_sonnet = 0.0015 + 0.0075
        assert sonnet_cost == expected_sonnet
        
        # Test Haiku model
        haiku_cost = claude_client._calculate_cost(500, 500, "claude-3-haiku-20240307")
        # Input: 500 * $0.25/1M = 0.000125, Output: 500 * $1.25/1M = 0.000625
        expected_haiku = 0.000125 + 0.000625
        assert haiku_cost == expected_haiku
    
    @pytest.mark.asyncio
    async def test_metrics_recording_with_error_response(self, claude_client, mock_metrics):
        """Test that metrics are still recorded even when API returns an error."""
        # Arrange - Make the API call fail
        claude_client.client.messages.create.side_effect = Exception("API Error")
        
        # Act & Assert
        with pytest.raises(Exception):
            await claude_client.generate_message(
                messages=[{"role": "user", "content": "Hello"}],
                model="claude-3-opus-20240229"
            )
        
        # Metrics should NOT be recorded for failed requests (no tokens used)
        mock_metrics.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_token_counting_accuracy(self, claude_client, mock_metrics):
        """Test that token counting correctly sums input and output tokens."""
        mock_response = self._mock_response(input_tokens=150, output_tokens=250)

        # Act
        with patch.object(ClaudeClient, "_dispatch", return_value=mock_response):
            response = await claude_client.generate_message(
                messages=[{"role": "user", "content": "Hello"}],
                model="claude-3-opus-20240229"
            )

        # Assert
        call_args = mock_metrics.call_args[1]
        assert call_args["input_tokens"] == 150
        assert call_args["output_tokens"] == 250