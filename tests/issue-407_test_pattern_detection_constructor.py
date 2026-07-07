"""
Tests for PatternDetectionService constructor argument mismatch (Issue #407).

This test file verifies that:
1. PatternDetectionService constructor accepts only claude_client argument
2. Passing git_ops as second argument raises TypeError
3. Service has access to git_ops after initialization via internal import
4. Proper initialization with mock claude_client
"""

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch
import sys
from datetime import datetime

# Import the service and dependencies
from app.services.pattern_detection_service import PatternDetectionService
from app.services.claude_client import ClaudeClient
from app.models import PatternAnalysis
from app.model_schemas.domain_config import DomainConfiguration


class TestPatternDetectionServiceConstructor:
    """Test cases for PatternDetectionService constructor"""

    @pytest.fixture
    def mock_claude_client(self):
        """Mock Claude client for testing"""
        mock_client = Mock(spec=ClaudeClient)
        mock_client.generate_response = AsyncMock(return_value='{"patterns_detected": []}')
        return mock_client

    @pytest.fixture
    def mock_git_ops(self):
        """Mock git_ops for testing"""
        mock_git = Mock()
        mock_git.get_file_content = AsyncMock(return_value='{}')
        mock_git.update_file = AsyncMock(return_value=None)
        return mock_git

    def test_constructor_accepts_only_claude_client(self, mock_claude_client):
        """Test that constructor accepts only claude_client argument"""
        # This should work without errors
        service = PatternDetectionService(mock_claude_client)
        
        assert service.claude_client is mock_claude_client
        assert hasattr(service, 'git_ops')  # Should have git_ops from internal import

    def test_constructor_with_two_arguments_raises_type_error(self, mock_claude_client, mock_git_ops):
        """Test that passing git_ops as second argument raises TypeError"""
        # This should fail because the constructor only accepts one argument
        with pytest.raises(TypeError) as exc_info:
            PatternDetectionService(mock_claude_client, mock_git_ops)
        
        # Verify the error message indicates too many arguments
        error_msg = str(exc_info.value)
        assert "takes" in error_msg and "positional argument" in error_msg
        assert "given" in error_msg or "but" in error_msg

    def test_constructor_with_keyword_git_ops_raises_type_error(self, mock_claude_client, mock_git_ops):
        """Test that passing git_ops as keyword argument raises TypeError"""
        # This should also fail since git_ops is not an expected parameter
        with pytest.raises(TypeError) as exc_info:
            PatternDetectionService(mock_claude_client, git_ops=mock_git_ops)
        
        # Verify the error message indicates unexpected keyword argument
        error_msg = str(exc_info.value)
        assert "unexpected keyword argument" in error_msg and "git_ops" in error_msg

    def test_service_has_git_ops_after_initialization(self, mock_claude_client):
        """Test that service has access to git_ops after initialization via internal import"""
        service = PatternDetectionService(mock_claude_client)
        
        # Service should have git_ops attribute from internal import
        assert hasattr(service, 'git_ops')
        assert service.git_ops is not None
        
        # git_ops should be the singleton instance, not passed argument
        from app.git_ops import git_ops
        assert service.git_ops is git_ops

    @patch('app.services.pattern_detection_service.git_ops')
    def test_service_uses_internal_git_ops_not_passed_argument(self, mock_internal_git_ops, mock_claude_client):
        """Test that service uses internal git_ops import, not a passed argument"""
        # Set up the mock for internal git_ops
        mock_internal_git_ops.get_file_content = AsyncMock(return_value='{}')
        mock_internal_git_ops.update_file = AsyncMock(return_value=None)
        
        service = PatternDetectionService(mock_claude_client)
        
        # Verify service uses the internally imported git_ops
        assert service.git_ops is mock_internal_git_ops

    def test_constructor_without_arguments_raises_type_error(self):
        """Test that constructor without arguments raises TypeError"""
        with pytest.raises(TypeError) as exc_info:
            PatternDetectionService()
        
        error_msg = str(exc_info.value)
        assert "missing" in error_msg and "required positional argument" in error_msg
        assert "claude_client" in error_msg

    def test_constructor_with_none_claude_client_works(self):
        """Test that constructor accepts None as claude_client (for testing scenarios)"""
        # This should work - the service should handle None gracefully
        service = PatternDetectionService(None)
        
        assert service.claude_client is None
        assert hasattr(service, 'git_ops')

    def test_multiple_arguments_error_message_clarity(self, mock_claude_client, mock_git_ops):
        """Test that error message clearly indicates the constructor signature"""
        with pytest.raises(TypeError) as exc_info:
            PatternDetectionService(mock_claude_client, mock_git_ops, "extra_arg")
        
        error_msg = str(exc_info.value)
        # Should indicate how many arguments were expected vs given
        assert "2" in error_msg  # Expected (including self)
        assert "4" in error_msg  # Given (including self)
        assert "but" in error_msg

    @pytest.mark.asyncio
    async def test_service_functionality_with_correct_constructor(self, mock_claude_client):
        """Test that service works correctly when constructed properly"""
        # Mock the Claude response
        mock_response = '{"patterns_detected": [{"pattern_name": "test_pattern", "confidence": "high", "evidence": ["test evidence"]}]}'
        mock_claude_client.generate_response = AsyncMock(return_value=mock_response)
        
        # Create service correctly
        service = PatternDetectionService(mock_claude_client)
        
        # Create a simple domain config
        domain_config = DomainConfiguration(
            id="test_domain",
            name="Test Domain",
            entity_types=[],
            patterns=[{
                "name": "test_pattern",
                "description": "A test pattern for validation"
            }]
        )
        
        # Test pattern detection works
        result = await service.detect_patterns("test content", domain_config)
        
        assert isinstance(result, PatternAnalysis)
        assert result.domain_id == "test_domain"
        assert len(result.patterns_detected) == 1
        assert result.patterns_detected[0]["pattern_name"] == "test_pattern"

    def test_constructor_signature_inspection(self):
        """Test that constructor signature can be inspected to confirm parameter count"""
        import inspect
        
        sig = inspect.signature(PatternDetectionService.__init__)
        params = list(sig.parameters.keys())
        
        # Should only have 'self' and 'claude_client' parameters
        assert len(params) == 2
        assert 'self' in params
        assert 'claude_client' in params
        assert 'git_ops' not in params

    def test_service_attributes_after_proper_initialization(self, mock_claude_client):
        """Test that service has all expected attributes after proper initialization"""
        service = PatternDetectionService(mock_claude_client)
        
        # Check all expected attributes exist
        assert hasattr(service, 'claude_client')
        assert hasattr(service, 'git_ops')
        assert hasattr(service, 'DEFAULT_PATTERNS')
        
        # Check attribute types
        assert service.claude_client is mock_claude_client
        assert isinstance(service.DEFAULT_PATTERNS, list)
        assert len(service.DEFAULT_PATTERNS) > 0  # Should have default patterns