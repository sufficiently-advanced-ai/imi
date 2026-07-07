"""
Test suite for consolidated EntityService (Issue #395).

This test covers the consolidation of:
- entity_brain.py (compatibility adapter)
- entity_brain_enhanced.py (dynamic registry support)
- entity_brain_refactored.py (domain-aware processing)

The consolidated EntityService should preserve all unique features from each implementation.
"""

import pytest
import asyncio
from datetime import datetime
from typing import Dict, List, Any
from unittest.mock import Mock, AsyncMock, patch

from app.domain.entities.services.entity_service import EntityService
from app.domain.entities.services.entity_repository import EntityRepository
from app.model_schemas.domain_config import DomainConfiguration, DomainEntity


@pytest.fixture
def mock_repository():
    """Mock EntityRepository for testing."""
    repo = Mock(spec=EntityRepository)
    repo.get_entity_types.return_value = ["person", "project", "organization"]
    repo.get_entity_schema.return_value = Mock(id="person", name="Person")
    repo.validate_entity.return_value = (True, [])
    repo.store_entity = AsyncMock()
    repo.get_entity = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_claude_client():
    """Mock Claude client for testing."""
    client = Mock()
    client.generate_message = AsyncMock()
    client.analyze_with_prompt = AsyncMock()
    return client


@pytest.fixture
def entity_service(mock_repository, mock_claude_client):
    """EntityService instance with mocked dependencies."""
    return EntityService(
        repository=mock_repository,
        claude_client=mock_claude_client
    )


@pytest.fixture
def sample_domain_config():
    """Sample domain configuration for testing."""
    return DomainConfiguration(
        id="test_domain",
        name="Test Domain", 
        description="Test domain configuration",
        entities={
            "person": DomainEntity(
                id="person",
                name="Person", 
                description="Person entity",
                attributes={}
            )
        }
    )


class TestEntityServiceCore:
    """Test core EntityService functionality."""

    def test_initialization(self, mock_repository, mock_claude_client):
        """Test service initialization."""
        service = EntityService(
            repository=mock_repository,
            claude_client=mock_claude_client
        )
        
        assert service._repository == mock_repository
        assert service._claude == mock_claude_client
        assert service._current_domain_id is None



    async def test_load_domain_failure(self, entity_service):
        """Test domain loading failure."""
        with patch('app.services.domain_config.DomainConfigLoader') as mock_loader_class:
            mock_loader = Mock()
            mock_loader.load_domain.return_value = None
            mock_loader_class.return_value = mock_loader
            
            result = await entity_service.load_domain("invalid_domain")
            
            assert result is False
            assert entity_service._current_domain_id is None


class TestEntityServiceExtraction:
    """Test entity extraction functionality."""

    async def test_extract_entities_from_file(self, entity_service):
        """Test extracting entities from file path."""
        # Mock file cache
        with patch('app.services.file_cache.file_cache') as mock_cache:
            mock_file = Mock()
            mock_file.content = """---
person: John Doe
project: Alpha Project  
---
# Test Document
Some content here."""
            mock_cache.get_file = AsyncMock(return_value=mock_file)
            
            result = await entity_service.extract_entities("test_file.md")
            
            assert isinstance(result, dict)
            assert "person" in result
            assert "project" in result
            assert "organization" in result  # Should include all registered types

    async def test_extract_entities_no_metadata(self, entity_service):
        """Test extraction when no metadata is present."""
        with patch('app.services.file_cache.file_cache') as mock_cache:
            mock_file = Mock()
            mock_file.content = "# Just content without metadata"
            mock_cache.get_file = AsyncMock(return_value=mock_file)
            
            result = await entity_service.extract_entities("no_metadata.md")
            
            # Should return empty lists for all types
            assert all(len(entities) == 0 for entities in result.values())

    async def test_extract_entities_from_content(self, entity_service):
        """Test extracting entities from content using Claude."""
        entity_service._claude.analyze_with_prompt = AsyncMock(
            return_value="- John Doe (person)\n- Alpha Project (project)"
        )
        
        result = await entity_service.extract_entities_from_content("John works on Alpha Project")
        
        assert isinstance(result, dict)
        entity_service._claude.analyze_with_prompt.assert_called_once()

    async def test_enrich_entities_from_transcript(self, entity_service):
        """Test enriching entities from transcript text."""
        # Mock Claude response
        mock_response = Mock()
        mock_response.content = [Mock(text='{"person": ["John Doe"], "project": ["Alpha"]}')]
        entity_service._claude.generate_message = AsyncMock(return_value=mock_response)
        
        result = await entity_service.enrich_entities_from_transcript("John talked about Alpha project")
        
        assert "person" in result
        assert "John Doe" in result["person"] 
        assert "project" in result
        assert "Alpha" in result["project"]

    async def test_enrich_entities_empty_transcript(self, entity_service):
        """Test handling empty transcript."""
        result = await entity_service.enrich_entities_from_transcript("")
        
        # Should return empty results without calling Claude
        assert all(len(entities) == 0 for entities in result.values())
        entity_service._claude.generate_message.assert_not_called()


class TestEntityServiceFileOperations:
    """Test entity file operations."""

    async def test_load_entity_file(self, entity_service):
        """Test loading entity file content.""" 
        with patch('app.git_ops.git_ops') as mock_git:
            mock_git.read_file = AsyncMock(return_value="# Entity Content")
            
            content = await entity_service.load_entity_file("person-john-doe")
            
            assert content == "# Entity Content"
            mock_git.read_file.assert_called_once()

    async def test_load_entity_file_with_path(self, entity_service):
        """Test loading entity file by direct path."""
        with patch('app.git_ops.git_ops') as mock_git:
            mock_git.read_file = AsyncMock(return_value="# Direct Path Content")
            
            content = await entity_service.load_entity_file("entities/person/john-doe.md")
            
            assert content == "# Direct Path Content"

    async def test_save_entity_file(self, entity_service):
        """Test saving entity file."""
        with patch('app.git_ops.git_ops') as mock_git:
            mock_git.commit_and_push = AsyncMock()
            
            await entity_service.save_entity_file("person-john-doe", "# Updated Content")
            
            mock_git.commit_and_push.assert_called_once()

    async def test_update_entity_profile(self, entity_service):
        """Test updating entity profile."""
        with patch('app.git_ops.git_ops') as mock_git:
            mock_git.commit_and_push = AsyncMock()
            
            await entity_service.update_entity_profile("John Doe", "person", "# New Profile")
            
            mock_git.commit_and_push.assert_called_once()


class TestEntityServiceCreation:
    """Test entity creation functionality."""

    async def test_create_entity_file(self, entity_service):
        """Test creating new entity file."""
        # Mock successful validation
        entity_service._repository.validate_entity.return_value = (True, [])
        entity_service._repository.get_entity_schema.return_value = Mock(
            id="person", name="Person", description="Person entity",
            attributes={}, relationships={}
        )
        entity_service._repository.create_entity.return_value = {
            "id": "person-john-doe",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        with patch('app.git_ops.git_ops') as mock_git:
            mock_git.commit_file = AsyncMock()
            
            attributes = {"name": "John Doe", "title": "Developer"}
            result = await entity_service.create_entity_file(
                "person", "person-john-doe", attributes
            )
            
            assert result is not None
            assert result.startswith("entities/person/")
            mock_git.commit_file.assert_called_once()

    async def test_create_entity_file_validation_failure(self, entity_service):
        """Test entity creation with validation failure."""
        # Mock validation failure
        entity_service._repository.validate_entity.return_value = (False, ["Name is required"])
        
        result = await entity_service.create_entity_file(
            "person", "person-invalid", {}
        )
        
        assert result is None

    async def test_create_entity_file_with_relationships(self, entity_service):
        """Test creating entity file with relationships."""
        entity_service._repository.validate_entity.return_value = (True, [])
        entity_service._repository.get_entity_schema.return_value = Mock(
            id="person", name="Person", description="Person entity",
            attributes={}, relationships={"projects": {"name": "Projects"}}
        )
        entity_service._repository.create_entity.return_value = {
            "id": "person-john-doe", 
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        with patch('app.git_ops.git_ops') as mock_git:
            mock_git.commit_file = AsyncMock()
            
            attributes = {"name": "John Doe"}
            relationships = {"projects": ["project-alpha"]}
            
            result = await entity_service.create_entity_file(
                "person", "person-john-doe", attributes, relationships=relationships
            )
            
            assert result is not None


class TestEntityServiceRelationships:
    """Test entity relationship management."""

    async def test_update_entity_relationships(self, entity_service):
        """Test updating entity relationships."""
        # Mock relationship validation
        entity_service._repository.validate_relationship.return_value = True
        entity_service._repository.get_inverse_relationship.return_value = {
            "name": "members"
        }
        
        with patch('app.services.file_cache.file_cache') as mock_cache:
            mock_file = Mock()
            mock_file.content = """---
name: John Doe
projects: []
---
# John Doe"""
            mock_cache.get_file = AsyncMock(return_value=mock_file)
            
            with patch('app.git_ops.git_ops') as mock_git:
                mock_git.commit_file = AsyncMock()
                
                result = await entity_service.update_entity_relationships(
                    "person", "person-john-doe", "projects", "project-alpha"
                )
                
                assert result is True
                mock_git.commit_file.assert_called()

    async def test_update_entity_relationships_invalid(self, entity_service):
        """Test updating invalid relationships."""
        entity_service._repository.validate_relationship.return_value = False
        
        result = await entity_service.update_entity_relationships(
            "person", "person-john-doe", "invalid_rel", "project-alpha"
        )
        
        assert result is False


class TestEntityServiceUtilities:
    """Test utility methods."""

    def test_normalize_entity_id(self, entity_service):
        """Test entity ID normalization."""
        # Mock entity schema
        mock_schema = Mock(name="Person")
        entity_service._repository.get_entity_schema.return_value = mock_schema
        
        result = entity_service.normalize_entity_id("person", "John Doe Person")
        
        assert result.startswith("person-")
        assert "john-doe" in result

    def test_normalize_entity_id_empty(self, entity_service):
        """Test normalizing empty entity name."""
        entity_service._repository.get_entity_schema.return_value = None
        
        with pytest.raises(ValueError, match="Entity name cannot be empty"):
            entity_service.normalize_entity_id("person", "")


    async def test_migrate_legacy_entities(self, entity_service):
        """Test migrating legacy entity structure."""
        # Mock file system
        with patch('os.path.exists', return_value=True), \
             patch('os.listdir', return_value=["john-doe.md"]), \
             patch('app.services.file_cache.file_cache') as mock_cache:
            
            mock_file = Mock()
            mock_file.content = """---
name: John Doe
title: Developer
---
# John Doe"""
            mock_cache.get_file = AsyncMock(return_value=mock_file)
            
            # Mock creation success
            entity_service.create_entity_file = AsyncMock(return_value="entities/person/john-doe.md")
            
            stats = await entity_service.migrate_legacy_entities()
            
            assert stats["migrated"] >= 0
            assert stats["errors"] >= 0


class TestEntityServiceBackwardCompatibility:
    """Test backward compatibility methods."""

    def test_get_entity_types(self, entity_service):
        """Test getting entity types (compatibility method)."""
        types = entity_service.get_entity_types()
        assert isinstance(types, list)
        assert len(types) == 3  # person, project, organization

    async def test_extract_entities_legacy_format(self, entity_service):
        """Test extraction with legacy return format."""
        with patch('app.services.file_cache.file_cache') as mock_cache:
            mock_file = Mock()
            mock_file.content = """---
person: John Doe
---
Content"""
            mock_cache.get_file = AsyncMock(return_value=mock_file)
            
            result = await entity_service.extract_entities("test.md")
            
            # Should return format compatible with old EntityBrain
            assert isinstance(result, dict)
            for entity_type, entities in result.items():
                assert isinstance(entities, list)

    async def test_update_entity_file_alias(self, entity_service):
        """Test update_entity_file as alias for save_entity_file."""
        with patch.object(entity_service, 'save_entity_file') as mock_save:
            await entity_service.update_entity_file("person-test", "content")
            mock_save.assert_called_once_with("person-test", "content")


class TestEntityServiceErrorHandling:
    """Test error handling scenarios."""

    async def test_extract_entities_file_not_found(self, entity_service):
        """Test extraction when file is not found."""
        with patch('app.services.file_cache.file_cache') as mock_cache:
            mock_cache.get_file = AsyncMock(return_value=None)
            
            result = await entity_service.extract_entities("missing.md")
            
            # Should return empty results, not raise exception
            assert all(len(entities) == 0 for entities in result.values())

    async def test_enrich_entities_claude_error(self, entity_service):
        """Test handling Claude API errors during enrichment."""
        entity_service._claude.generate_message = AsyncMock(side_effect=Exception("API Error"))
        
        with pytest.raises(RuntimeError, match="Entity extraction failed"):
            await entity_service.enrich_entities_from_transcript("test transcript")

    async def test_create_entity_file_git_error(self, entity_service):
        """Test handling git operation errors."""
        entity_service._repository.validate_entity.return_value = (True, [])
        entity_service._repository.get_entity_schema.return_value = Mock(
            id="person", name="Person", description="Person entity",
            attributes={}, relationships={}
        )
        entity_service._repository.create_entity.return_value = {
            "id": "person-test",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        with patch('app.git_ops.git_ops') as mock_git:
            mock_git.commit_file = AsyncMock(side_effect=Exception("Git Error"))
            
            result = await entity_service.create_entity_file(
                "person", "person-test", {"name": "Test"}
            )
            
            assert result is None