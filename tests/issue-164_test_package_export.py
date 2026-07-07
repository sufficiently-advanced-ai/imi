"""Tests for domain package export functionality."""
import pytest
from pathlib import Path
import tempfile
import yaml
import zipfile
import tarfile
from unittest.mock import Mock, patch, AsyncMock
from typing import Dict, Any

from app.services.domain_package_manager import DomainPackageManager
from app.config import get_settings


@pytest.fixture
def mock_domain_dir(tmp_path) -> Path:
    """Create a mock domain directory with files."""
    domain_dir = tmp_path / "test-domain"
    domain_dir.mkdir()
    
    # Create domain.yaml
    domain_config = {
        "name": "Test Domain",
        "description": "A test domain for export",
        "entity_types": ["person", "project"],
        "version": "0.1.0",
        "created_at": "2024-01-01"
    }
    with open(domain_dir / "domain.yaml", "w") as f:
        yaml.dump(domain_config, f)
    
    # Create prompts directory
    prompts_dir = domain_dir / "prompts"
    prompts_dir.mkdir()
    
    # Add sample prompts
    meeting_prompt = prompts_dir / "meeting_analysis.xml"
    meeting_prompt.write_text("""<prompt>
    <description>Analyze meeting notes</description>
    <template>Analyze the following meeting...</template>
</prompt>""")
    
    entity_prompt = prompts_dir / "entity_extraction.xml"
    entity_prompt.write_text("""<prompt>
    <description>Extract entities</description>
    <template>Extract entities from...</template>
</prompt>""")
    
    # Create workflows directory
    workflows_dir = domain_dir / "workflows"
    workflows_dir.mkdir()
    
    workflow = workflows_dir / "onboarding.yaml"
    workflow.write_text("""name: Client Onboarding
steps:
  - name: Initial Contact
  - name: Requirements Gathering""")
    
    # Create examples directory
    examples_dir = domain_dir / "examples"
    examples_dir.mkdir()
    
    example = examples_dir / "sample_entities.json"
    example.write_text('{"entities": [{"type": "person", "name": "John Doe"}]}')
    
    # Create README
    readme = domain_dir / "README.md"
    readme.write_text("""# Test Domain

This is a test domain for package export testing.

## Features
- Person and Project entities
- Meeting analysis
- Client onboarding workflow""")
    
    return domain_dir


@pytest.fixture
def mock_settings(tmp_path):
    """Mock application settings."""
    settings = Mock()
    settings.DOMAINS_DIR = tmp_path / "domains"
    settings.DOMAINS_DIR.mkdir()
    settings.PACKAGES_DIR = tmp_path / "packages"
    settings.PACKAGES_DIR.mkdir()
    return settings


class TestPackageExport:
    """Test suite for package export functionality."""
    
    @pytest.mark.asyncio
    async def test_export_domain_to_directory(self, mock_domain_dir, mock_settings, tmp_path):
        """Test exporting a domain to a directory."""
        # Move mock domain to settings domains dir
        target_domain = mock_settings.DOMAINS_DIR / "test-domain"
        shutil.copytree(mock_domain_dir, target_domain)
        
        output_dir = tmp_path / "export"
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            manager = DomainPackageManager()
            
            result = await manager.export_domain("test-domain", output_dir)
            
            assert result.success
            assert result.export_path == output_dir
            assert (output_dir / "manifest.yaml").exists()
            assert (output_dir / "domain.yaml").exists()
            assert (output_dir / "prompts").exists()
            assert (output_dir / "workflows").exists()
            assert (output_dir / "README.md").exists()
    
    @pytest.mark.asyncio
    async def test_export_generates_manifest(self, mock_domain_dir, mock_settings, tmp_path):
        """Test that export generates a proper manifest file."""
        target_domain = mock_settings.DOMAINS_DIR / "test-domain"
        shutil.copytree(mock_domain_dir, target_domain)
        
        output_dir = tmp_path / "export"
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            manager = DomainPackageManager()
            
            result = await manager.export_domain("test-domain", output_dir)
            
            assert result.success
            
            # Check manifest contents
            with open(output_dir / "manifest.yaml") as f:
                manifest = yaml.safe_load(f)
            
            assert manifest["name"] == "test-domain"
            assert manifest["version"] == "0.1.0"  # From domain.yaml
            assert manifest["description"] == "A test domain for export"
            assert "author" in manifest
            assert "exported_at" in manifest
            assert manifest["dependencies"] == []
    
    @pytest.mark.asyncio
    async def test_export_to_zip_archive(self, mock_domain_dir, mock_settings, tmp_path):
        """Test exporting a domain to a zip archive."""
        target_domain = mock_settings.DOMAINS_DIR / "test-domain"
        shutil.copytree(mock_domain_dir, target_domain)
        
        archive_path = tmp_path / "test-domain-0.1.0.zip"
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            manager = DomainPackageManager()
            
            result = await manager.export_domain("test-domain", archive_path, format="zip")
            
            assert result.success
            assert result.export_path == archive_path
            assert archive_path.exists()
            
            # Verify archive contents
            with zipfile.ZipFile(archive_path, 'r') as zf:
                files = zf.namelist()
                assert "test-domain/manifest.yaml" in files
                assert "test-domain/domain.yaml" in files
                assert "test-domain/prompts/meeting_analysis.xml" in files
                assert "test-domain/README.md" in files
    
    @pytest.mark.asyncio
    async def test_export_to_tar_archive(self, mock_domain_dir, mock_settings, tmp_path):
        """Test exporting a domain to a tar.gz archive."""
        target_domain = mock_settings.DOMAINS_DIR / "test-domain"
        shutil.copytree(mock_domain_dir, target_domain)
        
        archive_path = tmp_path / "test-domain-0.1.0.tar.gz"
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            manager = DomainPackageManager()
            
            result = await manager.export_domain("test-domain", archive_path, format="tar.gz")
            
            assert result.success
            assert archive_path.exists()
            
            # Verify archive contents
            with tarfile.open(archive_path, 'r:gz') as tf:
                members = [m.name for m in tf.getmembers()]
                assert "test-domain/manifest.yaml" in members
                assert "test-domain/domain.yaml" in members
    
    @pytest.mark.asyncio
    async def test_export_nonexistent_domain(self, mock_settings, tmp_path):
        """Test exporting a domain that doesn't exist."""
        output_dir = tmp_path / "export"
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            manager = DomainPackageManager()
            
            result = await manager.export_domain("nonexistent", output_dir)
            
            assert not result.success
            assert "Domain not found" in result.error
    
    @pytest.mark.asyncio
    async def test_export_excludes_internal_files(self, mock_domain_dir, mock_settings, tmp_path):
        """Test that export excludes internal/system files."""
        target_domain = mock_settings.DOMAINS_DIR / "test-domain"
        shutil.copytree(mock_domain_dir, target_domain)
        
        # Add internal files that should be excluded
        (target_domain / ".git").mkdir()
        (target_domain / ".git" / "config").write_text("git config")
        (target_domain / "__pycache__").mkdir()
        (target_domain / ".DS_Store").write_text("mac file")
        
        output_dir = tmp_path / "export"
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            manager = DomainPackageManager()
            
            result = await manager.export_domain("test-domain", output_dir)
            
            assert result.success
            assert not (output_dir / ".git").exists()
            assert not (output_dir / "__pycache__").exists()
            assert not (output_dir / ".DS_Store").exists()
    
    @pytest.mark.asyncio
    async def test_export_with_custom_metadata(self, mock_domain_dir, mock_settings, tmp_path):
        """Test exporting with custom metadata."""
        target_domain = mock_settings.DOMAINS_DIR / "test-domain"
        shutil.copytree(mock_domain_dir, target_domain)
        
        output_dir = tmp_path / "export"
        
        custom_metadata = {
            "author": "Test Author",
            "organization": "Test Org",
            "tags": ["consulting", "enterprise"]
        }
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            manager = DomainPackageManager()
            
            result = await manager.export_domain("test-domain", output_dir, metadata=custom_metadata)
            
            assert result.success
            
            with open(output_dir / "manifest.yaml") as f:
                manifest = yaml.safe_load(f)
            
            assert manifest["author"] == "Test Author"
            assert manifest["organization"] == "Test Org"
            assert manifest["tags"] == ["consulting", "enterprise"]
    
    @pytest.mark.asyncio
    async def test_export_preserves_structure(self, mock_domain_dir, mock_settings, tmp_path):
        """Test that export preserves the exact directory structure."""
        target_domain = mock_settings.DOMAINS_DIR / "test-domain"
        shutil.copytree(mock_domain_dir, target_domain)
        
        # Add nested structure
        nested_dir = target_domain / "prompts" / "subdomain"
        nested_dir.mkdir()
        (nested_dir / "specialized.xml").write_text("<prompt>Specialized</prompt>")
        
        output_dir = tmp_path / "export"
        
        with patch('app.services.domain_package_manager.get_settings', return_value=mock_settings):
            manager = DomainPackageManager()
            
            result = await manager.export_domain("test-domain", output_dir)
            
            assert result.success
            assert (output_dir / "prompts" / "subdomain" / "specialized.xml").exists()


import shutil  # Add this import at the top