import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from app.main import app
from app.git_ops import git_ops
from app.models import DocumentMetadata, ProcessingStatus
from unittest.mock import patch, MagicMock, AsyncMock

client = TestClient(app)

@pytest.fixture
def mock_git_ops():
    with patch('app.main.git_ops') as mock:
        from app.models import File
        from datetime import datetime
        
        mock.get_status = AsyncMock(return_value="connected")
        mock.read_markdown_files = AsyncMock(return_value=[
            File(
                path="test.md",
                content="# Test Content",
                created_at=datetime.utcnow(),
                modified_at=datetime.utcnow()
            )
        ])
        yield mock

@pytest.fixture
def mock_anthropic():
    with patch('anthropic.Anthropic') as mock:
        mock_response = MagicMock()
        mock_response.content = "Test response"
        mock_response.usage.input = 10
        mock_response.usage.output = 5
        mock.return_value.messages.create.return_value = mock_response
        yield mock

@pytest.fixture
def sample_metadata():
    return {
        "type": "documentation",
        "created": datetime.utcnow(),
        "modified": datetime.utcnow(),
        "source": "manual",
        "classification": {
            "confidence": 0.8,
            "categories": ["technical", "guide"]
        },
        "summary": {
            "key_points": ["Point 1", "Point 2"],
            "action_items": ["Task 1", "Task 2"],
            "participants": ["Alice", "Bob"]
        },
        "references": {
            "related_docs": ["doc1.md", "doc2.md"],
            "context_files": ["context1.md"]
        }
    }


def test_get_knowledge(mock_git_ops):
    """Test retrieving knowledge base content."""
    response = client.get("/api/knowledge")
    assert response.status_code == 200
    data = response.json()
    assert "files" in data
    assert len(data["files"]) > 0







    

