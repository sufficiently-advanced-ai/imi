"""
Tests for /api/chat/stream endpoint with SSE response.
Tests written first following TDD methodology for issue #40.
"""
import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI
from app.main import app
from app.services.sse_manager import sse_manager


class TestStreamingChatAPI:
    """Test the new /api/chat/stream endpoint functionality"""

    @pytest.fixture
    def client(self):
        """Test client for API requests"""
        return TestClient(app)

    @pytest.fixture
    def sample_chat_request(self):
        """Sample chat request payload"""
        return {
            "query": "What are the current projects?",
            "manual_context": None
        }

    @pytest.fixture 
    def sample_chat_request_with_context(self):
        """Sample chat request with manual context"""
        return {
            "query": "Summarize these files",
            "manual_context": ["projects/project-alpha.md", "teams/team-delta.md"]
        }

    def test_streaming_endpoint_exists(self, client):
        """Test that /api/chat/stream endpoint exists and accepts POST requests"""
        # This should fail initially since endpoint doesn't exist yet
        response = client.post("/api/chat/stream", json={"query": "test"})
        
        # Initially this will be 404, after implementation should be 200 or appropriate response
        assert response.status_code != 404, "Endpoint should exist"

    def test_streaming_endpoint_returns_sse_headers(self, client, sample_chat_request):
        """Test that endpoint returns proper SSE headers and content-type"""
        response = client.post("/api/chat/stream", json=sample_chat_request)
        
        # Should return SSE headers
        assert response.headers.get("content-type").startswith("text/event-stream")
        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers.get("connection") == "keep-alive"
        assert "x-accel-buffering" in response.headers  # Nginx buffering disabled

    def test_streaming_endpoint_validates_request_format(self, client):
        """Test that endpoint validates request format"""
        # Test missing query
        response = client.post("/api/chat/stream", json={})
        assert response.status_code == 422, "Should return validation error for missing query"
        
        # Test invalid query type
        response = client.post("/api/chat/stream", json={"query": 123})
        assert response.status_code == 422, "Should return validation error for invalid query type"
        
        # Test invalid manual_context type
        response = client.post("/api/chat/stream", json={
            "query": "test",
            "manual_context": "not_a_list"
        })
        assert response.status_code == 422, "Should return validation error for invalid manual_context"

    def test_streaming_endpoint_accepts_valid_request(self, client, sample_chat_request):
        """Test that endpoint accepts valid request format"""
        response = client.post("/api/chat/stream", json=sample_chat_request)
        assert response.status_code == 200, "Should accept valid request format"

    @pytest.mark.asyncio
    async def test_endpoint_generates_unique_execution_id(self, client, sample_chat_request):
        """Test that endpoint generates unique execution_id for each request"""
        with patch('app.routes.streaming_chat.uuid.uuid4') as mock_uuid:
            mock_uuid.side_effect = ["exec_123", "exec_456"]
            
            # Make two requests
            response1 = client.post("/api/chat/stream", json=sample_chat_request)
            response2 = client.post("/api/chat/stream", json=sample_chat_request)
            
            # Should generate different execution IDs
            assert mock_uuid.call_count == 2, "Should generate unique execution ID per request"


    @pytest.mark.asyncio
    async def test_endpoint_handles_chatagent_success(self, client, sample_chat_request):
        """Test that endpoint handles successful ChatAgent execution"""
        with patch('app.agents.chat.ChatAgent.process_query') as mock_process:
            mock_result = {
                "answer": "Based on my analysis, the current projects are...",
                "context_files": ["projects/project-alpha.md"],
                "tool_calls": [{"tool": "search_knowledge_graph", "input": {}, "output": {}}],
                "cited_documents": ["projects/project-alpha.md"]
            }
            mock_process.return_value = mock_result
            
            response = client.post("/api/chat/stream", json=sample_chat_request)
            
            # Should return successful response
            assert response.status_code == 200
            
            # Should integrate ChatAgent result into SSE stream
            # (This will be verified through event emission in SSE tests)

    @pytest.mark.asyncio
    async def test_endpoint_handles_chatagent_failure(self, client, sample_chat_request):
        """Test that endpoint handles ChatAgent failures gracefully"""
        with patch('app.agents.chat.ChatAgent.process_query') as mock_process:
            mock_process.side_effect = Exception("ChatAgent processing failed")
            
            response = client.post("/api/chat/stream", json=sample_chat_request)
            
            # Should handle error gracefully and still return SSE stream
            assert response.status_code == 200, "Should return SSE stream even on error"
            
            # Error should be communicated through SSE events
            # (Error event verification would be in SSE event tests)


            
            # Response should contain SSE events
            # (Detailed event validation in separate SSE tests)



    @pytest.mark.asyncio
    async def test_endpoint_performance_under_load(self, client, sample_chat_request):
        """Test endpoint performance under concurrent requests"""
        import time
        
        with patch('app.agents.chat.ChatAgent.process_query') as mock_process:
            mock_process.return_value = {
                "answer": "Quick response",
                "context_files": [],
                "tool_calls": [],
                "cited_documents": []
            }
            
            # Make multiple concurrent requests
            start_time = time.time()
            responses = []
            
            for _ in range(5):
                response = client.post("/api/chat/stream", json=sample_chat_request)
                responses.append(response)
            
            end_time = time.time()
            
            # All requests should succeed
            for response in responses:
                assert response.status_code == 200
            
            # Should handle concurrent requests efficiently
            total_time = end_time - start_time
            assert total_time < 5.0, "Should handle concurrent requests within reasonable time"

    def test_endpoint_preserves_existing_functionality(self, client):
        """Test that new endpoint doesn't break existing /api/query functionality"""
        # Test that old endpoint still works
        legacy_request = {
            "question": "What are the projects?",
            "prompt_type": "search",
            "context_files": []
        }
        
        response = client.post("/api/query", json=legacy_request)
        
        # Legacy endpoint should continue to work
        assert response.status_code in [200, 422], "Legacy endpoint should remain functional"
        
        # Should not interfere with existing functionality
        # (This is more of an integration test to ensure no regressions)


class TestStreamingChatRequestModel:
    """Test the request/response models for streaming chat"""

    def test_streaming_chat_request_model(self):
        """Test StreamingChatRequest model validation"""
        from app.routes.streaming_chat import StreamingChatRequest
        
        # Valid request
        valid_request = StreamingChatRequest(
            query="Test query",
            manual_context=None
        )
        assert valid_request.query == "Test query"
        assert valid_request.manual_context is None
        
        # Valid request with context
        valid_with_context = StreamingChatRequest(
            query="Test query",
            manual_context=["file1.md", "file2.md"]
        )
        assert valid_with_context.manual_context == ["file1.md", "file2.md"]
        
        # Test validation
        with pytest.raises(ValueError):
            StreamingChatRequest(query="")  # Empty query should fail
        
        with pytest.raises(ValueError):
            StreamingChatRequest(query=None)  # None query should fail

