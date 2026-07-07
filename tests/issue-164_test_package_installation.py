"""Tests for domain package installation functionality."""
import pytest
from pathlib import Path
import tempfile
import yaml
import shutil
import zipfile
from unittest.mock import Mock, patch, AsyncMock
from typing import Dict, Any

from app.services.domain_package_manager import DomainPackageManager
from app.config import get_settings


@pytest.fixture(autouse=True)
def _isolated_domain_registry(tmp_path):
    """Point the global registry at a per-test file.

    The singleton persists to a fixed /tmp/domain_registry.json, so without
    this, state written by one run (or another checkout on the same host)
    makes register_domain raise 'already registered' in later runs.
    """
    from app.services.domain_registry import domain_registry

    old_file, old_domains = domain_registry._registry_file, domain_registry._domains
    domain_registry._registry_file = tmp_path / "domain_registry.json"
    domain_registry._domains = {}
    yield
    domain_registry._registry_file = old_file
    domain_registry._domains = old_domains


@pytest.fixture
def mock_settings():
    """Mock application settings."""
    settings = Mock()
    settings.DOMAINS_DIR = Path("/tmp/test_domains")
    settings.PACKAGES_DIR = Path("/tmp/test_packages")
    return settings


@pytest.fixture
def temp_install_dir():
    """Create temporary installation directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        install_dir = Path(tmpdir) / "domains"
        install_dir.mkdir()
        packages_dir = Path(tmpdir) / "packages"
        packages_dir.mkdir()
        yield install_dir, packages_dir


@pytest.fixture
def sample_package(tmp_path) -> Path:
    """Create a sample valid package."""
    package_dir = tmp_path / "sample-package"
    package_dir.mkdir()
    
    # Create manifest
    manifest = {
        "name": "consulting-firm",
        "version": "1.0.0",
        "description": "Consulting firm domain",
        "author": "Test",
        "dependencies": []
    }
    with open(package_dir / "manifest.yaml", "w") as f:
        yaml.dump(manifest, f)
    
    # Create domain config
    domain = {
        "name": "Consulting Firm",
        "entity_types": ["person", "project", "client"]
    }
    with open(package_dir / "domain.yaml", "w") as f:
        yaml.dump(domain, f)
    
    # Create directories
    (package_dir / "prompts").mkdir()
    (package_dir / "workflows").mkdir()
    
    # Create README
    (package_dir / "README.md").write_text("# Consulting Firm")
    
    return package_dir


@pytest.fixture
def package_archive(sample_package, tmp_path) -> Path:
    """Create a package archive (zip file)."""
    archive_path = tmp_path / "consulting-firm-1.0.0.zip"
    
    with zipfile.ZipFile(archive_path, 'w') as zf:
        for file_path in sample_package.rglob('*'):
            if file_path.is_file():
                arcname = str(file_path.relative_to(sample_package.parent))
                zf.write(file_path, arcname)
    
    return archive_path


class TestPackageInstallation:
    """Test suite for package installation."""
    
    @pytest.mark.asyncio
    async def test_install_from_directory(self, sample_package, temp_install_dir, mock_settings):
        """Test installing a package from a directory."""
        install_dir, packages_dir = temp_install_dir
        mock_settings.DOMAINS_DIR = install_dir
        mock_settings.PACKAGES_DIR = packages_dir
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            manager = DomainPackageManager()
            
            result = await manager.install_package(sample_package)
            
            assert result.success
            assert result.package_name == "consulting-firm"
            assert result.version == "1.0.0"
            assert (install_dir / "consulting-firm").exists()
            assert (install_dir / "consulting-firm" / "domain.yaml").exists()
    
    
    @pytest.mark.asyncio
    async def test_install_duplicate_package(self, sample_package, temp_install_dir, mock_settings):
        """Test installing a package that's already installed."""
        install_dir, packages_dir = temp_install_dir
        mock_settings.DOMAINS_DIR = install_dir
        mock_settings.PACKAGES_DIR = packages_dir
        
        # Pre-create the domain directory
        existing_dir = install_dir / "consulting-firm"
        existing_dir.mkdir()
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            manager = DomainPackageManager()
            
            result = await manager.install_package(sample_package)
            
            assert not result.success
            assert "already exists" in result.error
    
    
    @pytest.mark.asyncio
    async def test_install_registers_domain(self, sample_package, temp_install_dir, mock_settings):
        """Test that installation registers the domain in the system."""
        install_dir, packages_dir = temp_install_dir
        mock_settings.DOMAINS_DIR = install_dir
        mock_settings.PACKAGES_DIR = packages_dir
        
        mock_registry = Mock()
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            with patch('app.services.domain_package_manager.domain_registry', mock_registry):
                manager = DomainPackageManager()
                
                result = await manager.install_package(sample_package)
                
                assert result.success
                mock_registry.register_domain.assert_called_once()
                call_args = mock_registry.register_domain.call_args[0]
                assert call_args[0] == "consulting-firm"
    
    
    @pytest.mark.asyncio
    async def test_install_transaction_rollback(self, sample_package, temp_install_dir, mock_settings):
        """Test that failed installation rolls back changes."""
        install_dir, packages_dir = temp_install_dir
        mock_settings.DOMAINS_DIR = install_dir
        mock_settings.PACKAGES_DIR = packages_dir
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            manager = DomainPackageManager()
            
            # Mock a failure during domain registration
            with patch('app.services.domain_package_manager.domain_registry.register_domain', side_effect=Exception("Registration failed")):
                result = await manager.install_package(sample_package)
                
                assert not result.success
                assert "Registration failed" in result.error
                # Directory should be cleaned up
                assert not (install_dir / "consulting-firm").exists()