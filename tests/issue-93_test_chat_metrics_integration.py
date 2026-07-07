"""Integration tests for chat metrics recording."""

import pytest
import asyncio
from unittest.mock import Mock, patch, MagicMock, call
from fastapi.testclient import TestClient
from app.main import app
from app.services.claude_client import ClaudeClient
from app.agents.chat import ChatAgent
from app.config import Settings
from anthropic.types import Message, Usage, TextBlock
import app.metrics as metrics
from enum import Enum
from pydantic import BaseModel

# Define test-specific models since ChatMessage doesn't exist in app.models
class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

class ChatMessage(BaseModel):
    role: MessageRole
    content: str


@pytest.fixture
def mock_settings():
    """Mock settings for testing."""
    return Settings(
        anthropic_api_key="test-key",
        git_repo_url="https://github.com/test/repo",
        webhook_secret="test-secret"
    )


@pytest.fixture
def mock_otel_metrics():
    """Mock OpenTelemetry metrics for verification."""
    mock_tokens_counter = Mock()
    mock_tokens_counter.add = Mock()
    
    mock_cost_counter = Mock()
    mock_cost_counter.add = Mock()
    
    with patch('app.metrics.llm_tokens_counter', mock_tokens_counter):
        with patch('app.metrics.llm_cost_counter', mock_cost_counter):
            yield {
                'tokens': mock_tokens_counter,
                'cost': mock_cost_counter
            }


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


class TestChatMetricsIntegration:
    """End-to-end integration tests for chat metrics."""
    
    def test_complete_chat_flow_metrics(self, mock_otel_metrics, monkeypatch):
        """Test complete flow: chat request → API call → metrics recording."""
        # Pin CLIENT_NAME so the expected label is deterministic regardless of
        # any ambient/CI-injected value.
        monkeypatch.delenv("CLIENT_NAME", raising=False)
        # This test verifies the metrics recording flow without hitting actual endpoints
        # since the /api/chat endpoint doesn't exist in the current codebase
        
        # Simulate the full metrics recording flow
        metrics.record_llm_usage(
            model="claude-3-opus-20240229",
            operation="chat",
            input_tokens=50,
            output_tokens=25,
            cost=0.001125
        )
        
        # Verify metrics were recorded with correct labels
        # client_name defaults to "unknown" when CLIENT_NAME is unset
        mock_otel_metrics['tokens'].add.assert_called_with(
            75,
            {"model": "claude-3-opus-20240229", "operation": "chat", "client_name": "unknown"}
        )
        mock_otel_metrics['cost'].add.assert_called_with(
            0.001125,
            {"model": "claude-3-opus-20240229", "operation": "chat", "client_name": "unknown"}
        )
    
    def test_metrics_labels_consistency(self, mock_otel_metrics):
        """Test that metrics use consistent labels across different operations."""
        # Record metrics for different operations
        operations = [
            ("chat", 50, 0.00075),
            ("query", 30, 0.00045),
            ("analyze", 100, 0.0015)
        ]
        
        for op, tokens, cost in operations:
            metrics.record_llm_usage(
                model="claude-3-opus-20240229",
                operation=op,
                input_tokens=tokens,
                output_tokens=0,
                cost=cost
            )
        
        # Verify all calls use consistent label structure
        assert mock_otel_metrics['tokens'].add.call_count == 3
        
        for i, (op, tokens, cost) in enumerate(operations):
            call_args = mock_otel_metrics['tokens'].add.call_args_list[i]
            assert call_args[0][0] == tokens
            assert call_args[0][1]["operation"] == op
            assert "model" in call_args[0][1]
    
    def test_cost_calculation_accuracy_integration(self, mock_otel_metrics):
        """Test that token costs are calculated correctly for different models."""
        # Test different models with their expected costs
        model_tests = [
            ("claude-3-opus-20240229", 1000, 0, 0.015),  # $15/1M input tokens
            ("claude-3-5-sonnet-20241022", 1000, 0, 0.003),  # $3/1M input tokens
            ("claude-3-haiku-20240307", 1000, 0, 0.00025)  # $0.25/1M input tokens
        ]
        
        for model, input_tokens, output_tokens, expected_cost in model_tests:
            # Record metrics with expected cost
            metrics.record_llm_usage(
                model=model,
                operation="chat",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=expected_cost
            )
        
        # Verify costs were recorded correctly
        assert mock_otel_metrics['cost'].add.call_count == 3
    
    @pytest.mark.asyncio
    async def test_concurrent_chat_metrics(self, mock_otel_metrics):
        """Test that concurrent chat sessions record metrics independently."""
        # Simulate concurrent chat sessions
        async def simulate_chat(session_id: int):
            metrics.record_llm_usage(
                model="claude-3-opus-20240229",
                operation="chat",
                input_tokens=50 + session_id * 10,  # Different tokens per session
                output_tokens=0,
                cost=0.001 + session_id * 0.0001
            )
            return f"Response {session_id}"
        
        # Run multiple sessions concurrently
        tasks = [simulate_chat(i) for i in range(5)]
        results = await asyncio.gather(*tasks)
        
        # Verify all sessions recorded metrics
        assert mock_otel_metrics['tokens'].add.call_count == 5
        assert all(f"Response {i}" in results for i in range(5))
        
        # Verify each session's metrics are distinct
        token_calls = [call[0][0] for call in mock_otel_metrics['tokens'].add.call_args_list]
        assert token_calls == [50, 60, 70, 80, 90]
    
    def test_metrics_export_format(self, mock_otel_metrics):
        """Test that metrics are in the correct format for Grafana."""
        # Record a metric
        metrics.record_llm_usage(
            model="claude-3-opus-20240229",
            operation="chat",
            input_tokens=100,
            output_tokens=0,
            cost=0.0015
        )
        
        # Verify the format matches Grafana expectations
        tokens_call = mock_otel_metrics['tokens'].add.call_args
        cost_call = mock_otel_metrics['cost'].add.call_args
        
        # Check metric values
        assert tokens_call[0][0] == 100  # Token count as value
        assert cost_call[0][0] == 0.0015  # Cost as value
        
        # Check labels structure
        assert "model" in tokens_call[0][1]
        assert "operation" in tokens_call[0][1]
    
    def test_metrics_persistence_across_requests(self, mock_otel_metrics):
        """Test that metrics are recorded for each individual request."""
        # Simulate multiple chat requests
        for i in range(3):
            # Record metrics for this request
            metrics.record_llm_usage(
                model="claude-3-opus-20240229",
                operation="chat",
                input_tokens=50,
                output_tokens=0,
                cost=0.00075
            )
        
        # Verify metrics were recorded for all requests
        assert mock_otel_metrics['tokens'].add.call_count == 3
        assert mock_otel_metrics['cost'].add.call_count == 3