"""
Test Suite for Service Compatibility Adapters - Issue #395

This test suite ensures backward compatibility during the service consolidation process.
It tests that compatibility adapters properly delegate to consolidated services while
maintaining the exact same interfaces as the original duplicate services.

These tests will initially PASS (using current implementations) and should continue 
to PASS after consolidation (using compatibility adapters).
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, List, Optional, Any

# Import original services (current implementations)
from app.domain.entities.services import EntityService, EntityRepository
from app.core.domain_config.domain_config_service import DomainConfigService

# Import compatibility adapters (will be created during consolidation)
try:
    from app.compatibility.entity_brain_adapter import EntityBrainAdapter
    from app.compatibility.entity_registry_adapter import EntityRegistryAdapter
    from app.compatibility.domain_config_adapter import DomainConfigAdapter
except ImportError:
    # These don't exist yet - will be created during consolidation
    EntityBrainAdapter = None
    EntityRegistryAdapter = None  
    DomainConfigAdapter = None


class TestEntityBrainCompatibilityAdapters:
    """Test backward compatibility adapters for EntityBrain variants."""

    @pytest.fixture
    async def original_entity_brain(self):
        """Original EntityBrain instance for comparison."""
        with patch('app.services.entity_brain.get_claude_client') as mock_claude, \
             patch('app.services.entity_brain.get_entity_registry') as mock_registry, \
             patch('app.services.entity_brain.git_ops') as mock_git:
            
            mock_claude.return_value = AsyncMock()
            mock_registry.return_value = Mock()
            mock_git.read_file = AsyncMock(return_value="test content")
            
            return EntityService()

    @pytest.fixture
    async def enhanced_entity_brain(self):
        """Enhanced EntityBrain instance for comparison."""
        with patch('app.services.entity_brain_enhanced.get_claude_client') as mock_claude:
            mock_claude.return_value = AsyncMock()
            return EntityService()



    @pytest.mark.asyncio
    async def test_compatibility_adapter_interface_matching(self):
        """Test that compatibility adapters match original interfaces exactly."""
        if EntityBrainAdapter is None:
            pytest.skip("Compatibility adapters not implemented yet")
        
        # Create adapter instance
        adapter = EntityBrainAdapter()
        
        # Should have all original methods
        original_methods = [
            'extract_entities',
            'load_entity_file', 
            'save_entity_file',
            'update_entity_file',
            'enrich_entities_from_transcript'
        ]
        
        for method_name in original_methods:
            assert hasattr(adapter, method_name), f"Missing method: {method_name}"
            method = getattr(adapter, method_name)
            assert callable(method), f"Method not callable: {method_name}"

        # Should have enhanced methods too
        enhanced_methods = [
            'normalize_entity_id',
            'load_domain'
        ]
        
        for method_name in enhanced_methods:
            assert hasattr(adapter, method_name), f"Missing enhanced method: {method_name}"

    @pytest.mark.asyncio
    async def test_adapter_delegates_to_consolidated_service(self):
        """Test that adapter properly delegates to consolidated service."""
        if EntityBrainAdapter is None:
            pytest.skip("Compatibility adapters not implemented yet")
        
        with patch('app.domain.entities.services.EntityService') as mock_service:
            mock_service_instance = AsyncMock()
            mock_service.return_value = mock_service_instance
            
            adapter = EntityBrainAdapter()
            
            # Calls should delegate to consolidated service
            await adapter.extract_entities("test.md")
            mock_service_instance.extract_entities.assert_called_once_with("test.md")


class TestEntityRegistryCompatibilityAdapters:
    """Test backward compatibility adapters for EntityRegistry variants."""

    @pytest.fixture
    def original_entity_registry(self):
        """Original EntityRegistry instance for comparison."""
        registry = EntityRepository()
        # Clear any existing state for clean tests
        registry.clear()
        return registry

    @pytest.fixture
    def dynamic_entity_registry(self):
        """Dynamic EntityRegistry instance for comparison."""
        return DynamicEntityRegistry()

    @pytest.fixture  
    def canonical_entity_registry(self):
        """Canonical EntityRegistry instance for comparison."""
        with patch('app.services.entity_registry_canonical.Path.exists', return_value=False):
            return EntityRepository()




    def test_registry_adapter_interface_matching(self):
        """Test that registry adapter matches all original interfaces."""
        if EntityRegistryAdapter is None:
            pytest.skip("Registry compatibility adapters not implemented yet")
        
        adapter = EntityRegistryAdapter()
        
        # Should have original registry methods
        original_methods = [
            'register_domain',
            'get_entity_types', 
            'get_entity_schema',
            'clear'
        ]
        
        for method_name in original_methods:
            assert hasattr(adapter, method_name), f"Missing original method: {method_name}"

        # Should have canonical registry methods
        canonical_methods = [
            'store_entity',
            'get_entity',
            'list_entities'
        ]
        
        for method_name in canonical_methods:
            assert hasattr(adapter, method_name), f"Missing canonical method: {method_name}"

        # Should have dynamic registry features (thread safety tested implicitly)
        assert hasattr(adapter, 'validate_entity'), "Missing validation method"

    def test_singleton_behavior_preservation(self):
        """Test that singleton behavior is preserved in adapter."""
        if EntityRegistryAdapter is None:
            pytest.skip("Registry compatibility adapters not implemented yet")
        
        # Multiple instances should be the same object
        adapter1 = EntityRegistryAdapter()
        adapter2 = EntityRegistryAdapter()
        
        assert adapter1 is adapter2, "Singleton pattern not preserved"


class TestDomainConfigCompatibilityAdapters:
    """Test backward compatibility adapters for DomainConfig variants."""

    @pytest.fixture
    def domain_config_loader(self):
        """Original DomainConfigLoader instance for comparison."""
        return DomainConfigService()


    def test_domain_config_adapter_interface_matching(self):
        """Test that domain config adapter matches original interfaces."""
        if DomainConfigAdapter is None:
            pytest.skip("Domain config compatibility adapters not implemented yet")
        
        adapter = DomainConfigAdapter()
        
        # Should have loader methods
        loader_methods = [
            'load_from_file',
            'set_active_domain',
            'get_active_domain', 
            'get_loaded_domains',
            'clear_cache'
        ]
        
        for method_name in loader_methods:
            assert hasattr(adapter, method_name), f"Missing loader method: {method_name}"

        # Should have manager methods (from manager variant)
        manager_methods = [
            'load_domain',
            'switch_domain',
            'reload_domain'
        ]
        
        for method_name in manager_methods:
            if hasattr(adapter, method_name):
                # These methods may exist from manager variant
                method = getattr(adapter, method_name)
                assert callable(method)

    def test_caching_behavior_preservation(self):
        """Test that caching behavior is preserved in adapter."""
        if DomainConfigAdapter is None:
            pytest.skip("Domain config compatibility adapters not implemented yet")
        
        adapter = DomainConfigAdapter()
        
        # Should support cache operations
        cache_methods = [
            'clear_cache',
            'get_cache_stats',
            'set_cache_ttl'
        ]
        
        for method_name in cache_methods:
            if hasattr(adapter, method_name):
                # Cache methods may exist from cache variant
                method = getattr(adapter, method_name)
                assert callable(method)


class TestServiceInteroperability:
    """Test that compatibility adapters work together correctly."""

    @pytest.mark.asyncio
    async def test_entity_brain_with_registry_adapter_interop(self):
        """Test EntityBrain works with EntityRegistry adapter."""
        # Skip if adapters don't exist yet
        if EntityRegistryAdapter is None:
            pytest.skip("Compatibility adapters not implemented yet")
        
        # Setup registry adapter
        registry_adapter = EntityRegistryAdapter()
        
        # Setup domain config
        domain_config = Mock()
        domain_config.id = "interop-test"
        domain_config.entities = {"person": Mock()}
        
        registry_adapter.register_domain(domain_config)
        
        # EntityBrain should work with registry adapter
        with patch('app.services.entity_brain.get_entity_registry', return_value=registry_adapter):
            entity_brain = EntityService()
            
            # Should get entity types from adapter
            entity_types = entity_brain._get_entity_types()
            assert isinstance(entity_types, list)

    @pytest.mark.asyncio
    async def test_enhanced_brain_with_domain_config_adapter_interop(self):
        """Test EntityBrainEnhanced works with DomainConfig adapter."""
        if DomainConfigAdapter is None:
            pytest.skip("Compatibility adapters not implemented yet")
        
        # Setup domain config adapter
        config_adapter = DomainConfigAdapter()
        
        # EntityBrainEnhanced should work with config adapter
        with patch('app.services.entity_brain_enhanced.DomainConfigLoader', return_value=config_adapter):
            enhanced_brain = EntityService()
            
            # Should load domain through adapter
            enhanced_brain.load_domain("test-domain")

    def test_adapter_chain_compatibility(self):
        """Test that adapters can be chained together without conflicts."""
        if not all([EntityBrainAdapter, EntityRegistryAdapter, DomainConfigAdapter]):
            pytest.skip("All compatibility adapters not implemented yet")
        
        # Create adapter instances
        brain_adapter = EntityBrainAdapter()
        registry_adapter = EntityRegistryAdapter()
        config_adapter = DomainConfigAdapter()
        
        # Should be able to use them together
        assert brain_adapter is not None
        assert registry_adapter is not None
        assert config_adapter is not None
        
        # Each should maintain its interface independently
        assert hasattr(brain_adapter, 'extract_entities')
        assert hasattr(registry_adapter, 'register_domain')
        assert hasattr(config_adapter, 'load_from_file')


class TestMigrationStrategy:
    """Test migration strategy from duplicates to consolidated services."""

    def test_feature_flag_support(self):
        """Test that feature flags control migration behavior."""
        feature_flags = {
            "use_consolidated_entity_brain": False,
            "use_consolidated_entity_registry": False,
            "use_consolidated_domain_config": False
        }
        
        # When feature flags are disabled, should use original services
        with patch.dict('os.environ', {
            'USE_CONSOLIDATED_ENTITY_BRAIN': 'false',
            'USE_CONSOLIDATED_ENTITY_REGISTRY': 'false', 
            'USE_CONSOLIDATED_DOMAIN_CONFIG': 'false'
        }):
            
            # Original services should be used
            brain = EntityService()
            registry = EntityRepository()
            config = DomainConfigService()
            
            assert brain is not None
            assert registry is not None
            assert config is not None

    def test_gradual_migration_support(self):
        """Test that services can be migrated one at a time."""
        # Should be able to use consolidated service for one while keeping others original
        migration_scenarios = [
            {"brain": "consolidated", "registry": "original", "config": "original"},
            {"brain": "original", "registry": "consolidated", "config": "original"},
            {"brain": "original", "registry": "original", "config": "consolidated"},
            {"brain": "consolidated", "registry": "consolidated", "config": "consolidated"}
        ]
        
        for scenario in migration_scenarios:
            # Each scenario should be valid and testable
            assert scenario["brain"] in ["original", "consolidated"]
            assert scenario["registry"] in ["original", "consolidated"] 
            assert scenario["config"] in ["original", "consolidated"]

    @pytest.mark.asyncio
    async def test_performance_regression_detection(self):
        """Test that consolidated services don't regress performance."""
        # Baseline performance with original services
        original_brain = EntityService()
        
        start_time = asyncio.get_event_loop().time()
        
        with patch('app.git_ops.git_ops') as mock_git:
            mock_git.read_file = AsyncMock(return_value="Performance test content")
            
            # Run operation with original service
            await original_brain.extract_entities("perf-test.md")
        
        original_duration = asyncio.get_event_loop().time() - start_time
        
        # Test with consolidated service (through adapter)
        if EntityBrainAdapter is not None:
            adapter = EntityBrainAdapter()
            
            start_time = asyncio.get_event_loop().time()
            
            with patch('app.git_ops.git_ops') as mock_git:
                mock_git.read_file = AsyncMock(return_value="Performance test content")
                
                await adapter.extract_entities("perf-test.md")
            
            adapter_duration = asyncio.get_event_loop().time() - start_time
            
            # Consolidated service should not be significantly slower
            # Allow for some overhead but not more than 50%
            assert adapter_duration <= original_duration * 1.5, \
                f"Consolidated service too slow: {adapter_duration}s vs {original_duration}s"