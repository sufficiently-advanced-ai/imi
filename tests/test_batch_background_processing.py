"""
Test batch upload background processing - Issue #36 Critical Fix 1
Tests for fixing the 404 status endpoint issue
"""
import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.services.batch_progress_tracker import BatchProgressTracker
from app.services.batch_models import BatchPhase, BatchStatus


@pytest.fixture
def client():
    """Test client fixture"""
    return TestClient(app)


class TestBatchBackgroundProcessing:
    """Test cases for batch upload background processing"""
    
    def test_status_endpoint_returns_404_for_nonexistent_batch(self, client):
        """Test that status endpoint correctly returns 404 for non-existent batch"""
        response = client.get("/api/batch-upload/nonexistent-id/status")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
    
    
    
    
    
