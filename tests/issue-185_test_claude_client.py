"""Test Claude client pricing updates for issue #185."""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from app.services.claude_client import ClaudeClient


class TestClaudeClientPricing:
    """Test Claude client pricing calculations with new models."""
    
    @pytest.fixture
    def claude_client(self):
        """Create a Claude client instance for testing."""
        with patch('app.services.claude_client.Anthropic'):
            client = ClaudeClient()
            yield client
    
    def test_haiku_35_pricing(self, claude_client):
        """Test pricing calculation for Claude 3.5 Haiku."""
        # Test with new model format
        cost = claude_client._calculate_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            model="claude-3-5-haiku-20241022"
        )
        # $0.80 per MTok input + $4 per MTok output = $4.80
        assert cost == pytest.approx(4.80, rel=1e-3)
        
        # Test with alternative format
        cost = claude_client._calculate_cost(
            input_tokens=500_000,
            output_tokens=500_000,
            model="claude-3.5-haiku-20241022"
        )
        # $0.40 + $2.00 = $2.40
        assert cost == pytest.approx(2.40, rel=1e-3)
    
    def test_sonnet_4_pricing(self, claude_client):
        """Test pricing calculation for Claude Sonnet 4."""
        # Test with new model format
        cost = claude_client._calculate_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            model="claude-sonnet-4-20250514"
        )
        # $3 per MTok input + $15 per MTok output = $18
        assert cost == pytest.approx(18.00, rel=1e-3)
        
        # Test partial millions
        cost = claude_client._calculate_cost(
            input_tokens=100_000,
            output_tokens=200_000,
            model="claude-sonnet-4-20250514"
        )
        # $0.30 + $3.00 = $3.30
        assert cost == pytest.approx(3.30, rel=1e-3)
    
    def test_model_name_normalization(self, claude_client):
        """Test that various model name formats are normalized correctly."""
        # Test Haiku variations
        haiku_variations = [
            "claude-3-5-haiku-20241022",
            "claude-3.5-haiku-20241022",
            "Claude-3-5-Haiku-20241022",
            "CLAUDE-3-5-HAIKU-20241022"
        ]
        
        for model_name in haiku_variations:
            cost = claude_client._calculate_cost(
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                model=model_name
            )
            assert cost == pytest.approx(4.80, rel=1e-3), f"Failed for {model_name}"
        
        # Test Sonnet 4 variations
        sonnet_variations = [
            "claude-sonnet-4-20250514",
            "Claude-Sonnet-4-20250514",
            "claude.sonnet.4.20250514"
        ]
        
        for model_name in sonnet_variations:
            cost = claude_client._calculate_cost(
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                model=model_name
            )
            assert cost == pytest.approx(18.00, rel=1e-3), f"Failed for {model_name}"
    
    def test_existing_model_pricing_unchanged(self, claude_client):
        """Test that existing model pricing remains unchanged."""
        # Test Claude 3.5 Sonnet (existing)
        cost = claude_client._calculate_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            model="claude-3-5-sonnet"
        )
        # $3 + $15 = $18
        assert cost == pytest.approx(18.00, rel=1e-3)
        
        # Test Claude 3 Haiku (existing)
        cost = claude_client._calculate_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            model="claude-3-haiku"
        )
        # $0.25 + $1.25 = $1.50
        assert cost == pytest.approx(1.50, rel=1e-3)
    
    def test_fallback_pricing(self, claude_client):
        """Test fallback pricing for unknown models."""
        # Unknown model should fall back to Sonnet pricing
        cost = claude_client._calculate_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            model="unknown-model-xyz"
        )
        # Should use Claude 3.5 Sonnet pricing as fallback
        assert cost == pytest.approx(18.00, rel=1e-3)
    
    @pytest.mark.asyncio
    async def test_generate_message_with_model_parameter(self, claude_client):
        """Test that generate_message correctly uses the model parameter."""
        # Mock the Anthropic client response
        mock_response = Mock()
        mock_response.content = [Mock(text="Test response", type="text")]
        mock_response.usage = Mock(input_tokens=100, output_tokens=50)
        mock_response.stop_reason = "end_turn"
        
        with patch.object(claude_client.client.messages, 'create', return_value=mock_response):
            # Test with explicit model
            response = await claude_client.generate_message(
                messages=[{"role": "user", "content": "Test"}],
                model="claude-3-5-haiku-20241022",
                operation="metadata_extraction"
            )
            
            # Verify the response
            assert response.content[0].text == "Test response"
            
            # Verify the model was passed correctly
            claude_client.client.messages.create.assert_called_once()
            call_args = claude_client.client.messages.create.call_args[1]
            assert call_args['model'] == "claude-3-5-haiku-20241022"