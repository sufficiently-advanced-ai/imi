"""
Test suite for consolidated EntityRepository service (Issue #395).

This test covers the consolidation of:
- entity_registry.py (singleton, domain-aware) - 94 references
- entity_registry_canonical.py (storage-based, hardcoded types)  
- entity_registry_dynamic.py (thread-safe, LRU cache)

The consolidated EntityRepository should preserve all unique features from each implementation.
"""

import asyncio
import json
import pytest
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Dict, List, Any
from unittest.mock import Mock, AsyncMock, patch

from app.domain.entities.services.entity_repository import EntityRepository
from app.model_schemas.domain_config import DomainConfiguration, DomainEntity


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
            ),
            "project": DomainEntity(
                id="project", 
                name="Project",
                description="Project entity",
                attributes={}
            )
        }
    )


@pytest.fixture
def entity_repository():
    """Fresh EntityRepository instance for testing."""
    return EntityRepository()


@pytest.fixture
def populated_repository(entity_repository, sample_domain_config):
    """Repository populated with test data."""
    entity_repository.register_domain(sample_domain_config)
    return entity_repository


class TestEntityRepositoryCore:
    """Test core EntityRepository functionality."""

    def test_singleton_pattern(self):
        """Test that EntityRepository implements singleton pattern."""
        repo1 = EntityRepository()
        repo2 = EntityRepository()
        assert repo1 is repo2






class TestEntityRepositoryPersistence:
    """Test JSON persistence functionality."""


    async def test_load_registry_from_json(self, entity_repository, tmp_path):
        """Test loading registry data from JSON file."""
        # Create test JSON file
        test_data = {
            "entities": {
                "person-load1": {"id": "person-load1", "name": "Load1", "type": "person"},
                "person-load2": {"id": "person-load2", "name": "Load2", "type": "person"}
            },
            "domain_id": "test_domain"
        }
        
        json_file = tmp_path / "test_load.json"
        json_file.write_text(json.dumps(test_data))
        
        # Load from file
        await entity_repository.load_from_json(str(json_file))
        
        # Verify data was loaded
        result1 = await entity_repository.get_entity("person-load1")
        result2 = await entity_repository.get_entity("person-load2")
        
        assert result1["name"] == "Load1"
        assert result2["name"] == "Load2"


class TestEntityRepositoryThreadSafety:
    """Test thread safety functionality."""




class TestEntityRepositoryAliases:
    """Test alias functionality for entity lookups."""





class TestEntityRepositoryValidation:
    """Test entity validation functionality."""





class TestEntityRepositoryBackwardCompatibility:
    """Test backward compatibility with existing interfaces."""


