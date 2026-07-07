"""
Test entity profile tracking - Issue #36 Critical Fix 2
Tests for fixing the _get_modified_entity_files() returning empty list
"""
import pytest
import os
import tempfile
from unittest.mock import MagicMock, patch, AsyncMock

from app.services.batch_upload import BatchUploadService
from app.services.batch_models import Entity, EntityType


class TestEntityProfileTracking:
    """Test cases for entity profile tracking"""
    
    def test_get_modified_entity_files_returns_empty_initially(self):
        """Test that _get_modified_entity_files returns empty list when no files exist"""
        batch_service = BatchUploadService()
        
        with patch('app.git_ops.git_ops.repo_path', '/tmp/test-repo'):
            files = batch_service._get_modified_entity_files()
            assert files == []
    
    
    
    
    
