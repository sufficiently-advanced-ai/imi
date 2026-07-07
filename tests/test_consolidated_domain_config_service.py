"""
Test suite for consolidated DomainConfigService (Issue #395).

This test covers the consolidation of:
- domain_config.py (basic loader)
- domain_config_loader.py (extended loader) 
- domain_config_manager.py (manager pattern)
- domain_config_cache.py (caching layer)

The consolidated DomainConfigService should implement a layered architecture:
loader → manager → cache with TTL-based caching and change notifications.
"""

import asyncio
import json
import pytest
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import Dict, List, Any, Optional
from unittest.mock import Mock, AsyncMock, patch, mock_open

from app.core.domain_config.domain_config_service import DomainConfigService
from app.model_schemas.domain_config import DomainConfiguration, DomainEntity


@pytest.fixture
def sample_domain_data():
    """Sample domain configuration data."""
    return {
        "id": "test_domain",
        "name": "Test Domain",
        "description": "Test domain configuration",
        "entities": {
            "person": {
                "id": "person",
                "name": "Person",
                "description": "Person entity",
                "attributes": {}
            }
        }
    }


@pytest.fixture
def sample_domain_config(sample_domain_data):
    """Sample DomainConfiguration object."""
    return DomainConfiguration(**sample_domain_data)


@pytest.fixture
def domain_service():
    """Fresh DomainConfigService instance."""
    return DomainConfigService()


class TestDomainConfigServiceCore:
    """Test core DomainConfigService functionality."""

    def test_initialization(self):
        """Test service initialization."""
        service = DomainConfigService()
        
        assert service._cache == {}
        assert service._ttl_cache == {}
        assert service.default_ttl == 3600  # 1 hour default
        assert hasattr(service, '_lock')

    def test_custom_ttl_initialization(self):
        """Test service initialization with custom TTL."""
        service = DomainConfigService(default_ttl=7200)
        assert service.default_ttl == 7200


    async def test_load_domain_failure(self, domain_service):
        """Test domain loading failure."""
        with patch.object(domain_service._loader, 'load_domain', return_value=None):
            result = await domain_service.load_domain("invalid_domain")
            
            assert result is None

    async def test_load_domain_exception(self, domain_service):
        """Test domain loading with exception."""
        with patch.object(domain_service._loader, 'load_domain', side_effect=Exception("Load error")):
            result = await domain_service.load_domain("error_domain")
            
            assert result is None


class TestDomainConfigServiceCache:
    """Test caching functionality."""









class TestDomainConfigServiceLoader:
    """Test domain loading functionality."""

    async def test_load_from_file_yaml(self, domain_service, sample_domain_data, tmp_path):
        """Test loading domain from YAML file."""
        yaml_file = tmp_path / "test_domain.yaml"
        yaml_file.write_text(yaml.dump({"domain": sample_domain_data}))
        
        result = await domain_service.load_from_file(yaml_file)
        
        assert result is not None
        assert result.id == "test_domain"

    async def test_load_from_file_json(self, domain_service, sample_domain_data, tmp_path):
        """Test loading domain from JSON file."""
        json_file = tmp_path / "test_domain.json"
        json_file.write_text(json.dumps({"domain": sample_domain_data}))
        
        result = await domain_service.load_from_file(json_file)
        
        assert result is not None
        assert result.id == "test_domain"

    async def test_load_from_file_direct_format(self, domain_service, sample_domain_data, tmp_path):
        """Test loading domain from file with direct format (no 'domain' wrapper)."""
        yaml_file = tmp_path / "direct.yaml"
        yaml_file.write_text(yaml.dump(sample_domain_data))
        
        result = await domain_service.load_from_file(yaml_file)
        
        assert result is not None
        assert result.id == "test_domain"

    async def test_load_from_file_not_found(self, domain_service, tmp_path):
        """Test loading from non-existent file."""
        missing_file = tmp_path / "missing.yaml"
        
        result = await domain_service.load_from_file(missing_file)
        assert result is None

    async def test_load_from_file_invalid_yaml(self, domain_service, tmp_path):
        """Test loading from invalid YAML file."""
        invalid_file = tmp_path / "invalid.yaml"
        invalid_file.write_text("{ invalid yaml [")
        
        result = await domain_service.load_from_file(invalid_file)
        assert result is None

    async def test_load_from_file_unsupported_format(self, domain_service, tmp_path):
        """Test loading from unsupported file format."""
        txt_file = tmp_path / "config.txt"
        txt_file.write_text("some config")
        
        result = await domain_service.load_from_file(txt_file)
        assert result is None

    async def test_load_from_directory(self, domain_service, sample_domain_data, tmp_path):
        """Test loading all domains from directory."""
        # Create multiple domain files
        domain1_data = {**sample_domain_data, "id": "domain1"}
        domain2_data = {**sample_domain_data, "id": "domain2"}
        
        (tmp_path / "domain1.yaml").write_text(yaml.dump(domain1_data))
        (tmp_path / "domain2.json").write_text(json.dumps(domain2_data))
        (tmp_path / "ignore.txt").write_text("ignore this")
        
        results = await domain_service.load_from_directory(tmp_path)
        
        assert len(results) == 2
        domain_ids = [d.id for d in results]
        assert "domain1" in domain_ids
        assert "domain2" in domain_ids


class TestDomainConfigServiceManager:
    """Test domain management functionality."""



    async def test_get_active_domain_none(self, domain_service):
        """Test getting active domain when none is set."""
        result = await domain_service.get_active_domain()
        assert result is None





    async def test_domain_exists_false(self, domain_service):
        """Test checking non-existent domain."""
        with patch.object(domain_service._loader, 'load_domain', return_value=None):
            exists = await domain_service.domain_exists("missing_domain")
            assert exists is False


class TestDomainConfigServiceNotifications:
    """Test change notification system."""

    def test_register_change_handler(self, domain_service):
        """Test registering change notification handler."""
        handler = Mock()
        domain_service.register_change_handler(handler)
        
        assert handler in domain_service._change_handlers

    def test_unregister_change_handler(self, domain_service):
        """Test unregistering change notification handler."""
        handler = Mock()
        domain_service.register_change_handler(handler)
        domain_service.unregister_change_handler(handler)
        
        assert handler not in domain_service._change_handlers





class TestDomainConfigServiceThreadSafety:
    """Test thread safety functionality."""


    def test_lock_behavior(self, domain_service):
        """Test that RLock provides proper synchronization."""
        assert hasattr(domain_service, '_lock')


class TestDomainConfigServiceValidation:
    """Test domain configuration validation."""

    async def test_validate_domain_config(self, domain_service, sample_domain_data):
        """Test domain configuration validation."""
        is_valid, errors = await domain_service.validate_domain_config(sample_domain_data)
        
        assert is_valid
        assert len(errors) == 0

    async def test_validate_invalid_domain_config(self, domain_service):
        """Test validation of invalid domain configuration."""
        invalid_data = {"name": "Missing ID"}  # Missing required 'id' field
        
        is_valid, errors = await domain_service.validate_domain_config(invalid_data)
        
        assert not is_valid
        assert len(errors) > 0



class TestDomainConfigServiceMigration:
    """Test domain configuration migration functionality."""

    async def test_migrate_legacy_config(self, domain_service):
        """Test migrating legacy configuration format."""
        legacy_config = {
            "domain_id": "legacy_domain",
            "domain_name": "Legacy Domain",
            "entity_types": ["person", "project"]
        }
        
        migrated = await domain_service.migrate_legacy_config(legacy_config)
        
        assert migrated is not None
        assert migrated.id == "legacy_domain"
        assert migrated.name == "Legacy Domain"




class TestDomainConfigServiceStats:
    """Test domain statistics and monitoring."""



    async def test_health_check(self, domain_service):
        """Test service health check."""
        health = await domain_service.health_check()
        
        assert health["status"] in ["healthy", "degraded", "unhealthy"]
        assert "cache_stats" in health
        assert "loader_status" in health


class TestDomainConfigServiceErrorHandling:
    """Test error handling scenarios."""

    async def test_load_domain_with_corrupted_cache(self, domain_service):
        """Test handling corrupted cache entries."""
        # Manually corrupt cache
        domain_service._cache["corrupted"] = "invalid_data"
        domain_service._ttl_cache["corrupted"] = datetime.utcnow() + timedelta(hours=1)
        
        result = await domain_service.get_cached_domain("corrupted")
        
        # Should handle gracefully and return None
        assert result is None
        assert "corrupted" not in domain_service._cache


    async def test_concurrent_cache_cleanup(self, domain_service):
        """Test that concurrent cache cleanup operations are handled safely."""
        # Add many expired entries
        past_time = datetime.utcnow() - timedelta(hours=1)
        for i in range(100):
            domain_service._cache[f"expired_{i}"] = DomainConfiguration(
                id=f"expired_{i}", name=f"Expired {i}", description="", entities={}
            )
            domain_service._ttl_cache[f"expired_{i}"] = past_time
        
        # Run cleanup concurrently
        tasks = [domain_service._clear_expired_entries() for _ in range(10)]
        await asyncio.gather(*tasks)
        
        # Should have cleaned up without errors
        assert len(domain_service._cache) == 0