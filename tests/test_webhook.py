import json
import sys
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock, call
from io import StringIO
from fastapi import HTTPException
from fastapi.testclient import TestClient
from github import Github
from app.main import app
from app.config import settings
from app.models import WebhookResponse, MetadataResponse
from app.github_client import GitHubClient

client = TestClient(app)

def create_push_event(
    branch: str = "main",
    message: str = "Test commit",
    added: list = None,
    modified: list = None,
    removed: list = None
) -> dict:
    """Create a test push event payload."""
    if added is None:
        added = []
    if modified is None:
        modified = []
    if removed is None:
        removed = []
        
    return {
        "ref": f"refs/heads/{branch}",
        "repository": {
            "name": "test-repo",
            "full_name": "test-org/test-repo",
            "private": False,
            "html_url": "https://github.com/test-org/test-repo",
            "default_branch": "main"
        },
        "head_commit": {
            "id": "test-commit-id",
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            "added": added,
            "removed": removed,
            "modified": modified
        }
    }

def capture_logs():
    """Helper to capture stderr log output."""
    logs = StringIO()
    sys.stderr = logs
    return logs

@patch('app.github_client.Github')
def test_github_client_init(mock_github):
    """Test GitHubClient initialization."""
    mock_repo = MagicMock()
    mock_github.return_value.get_repo.return_value = mock_repo
    
    client = GitHubClient("test-token", "test-org/test-repo", "test-secret")
    assert client.github == mock_github.return_value
    assert client.repo == mock_repo
    mock_github.assert_called_once_with("test-token")
    mock_github.return_value.get_repo.assert_called_once_with("test-org/test-repo")














