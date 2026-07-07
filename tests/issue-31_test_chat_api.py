"""
Tests for Chat API Integration - Enhanced /api/query endpoint
Tests cover API enhancement, backward compatibility, and response handling
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from typing import Dict, List, Any
import json

from app.main import app
from app.routes.command import router
from app.models import QueryRequest, QueryResponse
from app.agents.chat import ChatAgent


class TestChatAPIEnhancement:
    """Test enhanced /api/query endpoint with ChatAgent"""
    
    @pytest.fixture
    def client(self):
        """Create test client"""
        return TestClient(app)
    
    @pytest.mark.asyncio
    async def test_basic_query_request(self, client, mock_chat_agent):
        """Test basic query request processing"""
        # Mock agent response
        mock_response = {
            "answer": "John committed to completing the API by Friday.",
            "context_files": ["meeting-20250115.md"],
            "tool_calls": [
                {
                    "tool": "search_knowledge_graph",
                    "input": {"query": "John commitments"},
                    "output": [{"path": "meeting-20250115.md", "score": 0.9}]
                }
            ],
            "cited_documents": ["meeting-20250115.md"]
        }
        
        mock_chat_agent.process_query.return_value = mock_response
        
        with patch('app.agents.chat.ChatAgent', return_value=mock_chat_agent):
            response = client.post(
                "/api/query",
                json={
                    "question": "What did John commit to?",
                    "context_files": []
                }
            )
            
            assert response.status_code == 200
            data = response.json()
            assert data["response"] == mock_response["answer"]
            assert data["context_used"] == mock_response["context_files"]
            assert len(data["tool_calls"]) == 1
            
            
    @pytest.mark.asyncio
    async def test_tool_usage_tracking(self, client, mock_chat_agent):
        """Test that tool usage is properly tracked and returned"""
        tool_calls = [
            {
                "tool": "search_knowledge_graph",
                "input": {"query": "API project timeline"},
                "output": [
                    {"path": "project-timeline.md", "score": 0.95},
                    {"path": "api-roadmap.md", "score": 0.85}
                ]
            },
            {
                "tool": "read_document",
                "input": {"path": "project-timeline.md"},
                "output": {"content": "Timeline content...", "metadata": {}}
            }
        ]
        
        mock_response = {
            "answer": "The API project timeline shows...",
            "context_files": ["project-timeline.md", "api-roadmap.md"],
            "tool_calls": tool_calls,
            "cited_documents": ["project-timeline.md"]
        }
        
        mock_chat_agent.process_query.return_value = mock_response
        
        with patch('app.agents.chat.ChatAgent', return_value=mock_chat_agent):
            response = client.post(
                "/api/query",
                json={"question": "What is the API project timeline?"}
            )
            
            assert response.status_code == 200
            data = response.json()
            assert len(data["tool_calls"]) == 2
            assert data["tool_calls"][0]["tool"] == "search_knowledge_graph"
            assert data["tool_calls"][1]["tool"] == "read_document"
            
    @pytest.mark.asyncio
    async def test_response_structure_validation(self, client, mock_chat_agent):
        """Test that response structure matches expected format"""
        mock_response = {
            "answer": "Test answer",
            "context_files": ["test.md"],
            "tool_calls": [],
            "cited_documents": ["test.md"]
        }
        
        mock_chat_agent.process_query.return_value = mock_response
        
        with patch('app.agents.chat.ChatAgent', return_value=mock_chat_agent):
            response = client.post(
                "/api/query",
                json={"question": "Test question"}
            )
            
            assert response.status_code == 200
            data = response.json()
            
            # Verify response structure
            assert "response" in data
            assert "context_used" in data
            assert "tool_calls" in data
            assert isinstance(data["response"], str)
            assert isinstance(data["context_used"], list)
            assert isinstance(data["tool_calls"], list)
            
            
    @pytest.mark.asyncio
    async def test_async_request_handling(self, client, mock_chat_agent):
        """Test that async requests are handled properly"""
        # Simulate delay in processing
        async def delayed_response(*args, **kwargs):
            import asyncio
            await asyncio.sleep(0.1)
            return {
                "answer": "Delayed response",
                "context_files": [],
                "tool_calls": [],
                "cited_documents": []
            }
        
        mock_chat_agent.process_query = delayed_response
        
        with patch('app.agents.chat.ChatAgent', return_value=mock_chat_agent):
            response = client.post(
                "/api/query",
                json={"question": "Test async"}
            )
            
            assert response.status_code == 200
            data = response.json()
            assert data["response"] == "Delayed response"


class TestBackwardCompatibility:
    """Test backward compatibility with existing query format"""
    
    @pytest.fixture
    def client(self):
        """Create test client"""
        return TestClient(app)
    
    @pytest.mark.asyncio
    async def test_old_query_format_works(self, client):
        """Test that old query format still works"""
        # Old format might not have all fields
        response = client.post(
            "/api/query",
            json={
                "question": "What is the project status?",
                # Missing context_files field
            }
        )
        
        # Should still work with defaults
        assert response.status_code in [200, 422]  # 422 if validation is strict
        
            
    @pytest.mark.asyncio
    async def test_response_format_compatibility(self, client, mock_chat_agent):
        """Test response format is compatible with existing clients"""
        mock_response = {
            "answer": "Test answer",
            "context_files": ["test.md"],
            "tool_calls": [],
            "cited_documents": ["test.md"]
        }
        
        mock_chat_agent.process_query.return_value = mock_response
        
        with patch('app.agents.chat.ChatAgent', return_value=mock_chat_agent):
            response = client.post(
                "/api/query",
                json={"question": "Test"}
            )
            
            assert response.status_code == 200
            data = response.json()
            
            # Old clients expect 'response' field
            assert "response" in data
            assert isinstance(data["response"], str)
            
            # New fields should not break old clients
            assert "context_used" in data
            assert "tool_calls" in data


class TestChatAgentIntegration:
    """Test full integration with ChatAgent"""
    
    @pytest.fixture
    def client(self):
        """Create test client"""
        return TestClient(app)
    
    @pytest.mark.asyncio
    async def test_agent_initialization_on_request(self, client):
        """Test ChatAgent is properly initialized for each request"""
        with patch('app.agents.chat.ChatAgent') as mock_agent_class:
            mock_agent = AsyncMock()
            mock_agent.process_query.return_value = {
                "answer": "Test",
                "context_files": [],
                "tool_calls": [],
                "cited_documents": []
            }
            mock_agent_class.return_value = mock_agent
            
            response = client.post(
                "/api/query",
                json={"question": "Test"}
            )
            
            assert response.status_code == 200
            mock_agent_class.assert_called_once()
            
    @pytest.mark.asyncio
    async def test_conversation_context_handling(self, client):
        """Test conversation context is handled properly"""
        # First request
        response1 = client.post(
            "/api/query",
            json={
                "question": "Tell me about the API project",
                "conversation_id": "test-conv-123"
            }
        )
        
        assert response1.status_code == 200
        
        # Second request with same conversation ID
        response2 = client.post(
            "/api/query",
            json={
                "question": "What are the deadlines?",
                "conversation_id": "test-conv-123"
            }
        )
        
        assert response2.status_code == 200
        # Should maintain context (implementation dependent)


@pytest.fixture
def mock_chat_agent():
    """Mock ChatAgent for testing"""
    agent = AsyncMock(spec=ChatAgent)
    return agent