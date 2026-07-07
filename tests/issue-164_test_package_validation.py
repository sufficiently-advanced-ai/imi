"""Tests for domain package validation functionality."""
import pytest
from pathlib import Path
import tempfile
import yaml
from typing import Dict, Any

from app.services.domain_package_manager import DomainPackageManager


@pytest.fixture
def temp_package_dir():
    """Create a temporary directory for package testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def valid_manifest() -> Dict[str, Any]:
    """Create a valid package manifest."""
    return {
        "name": "consulting-firm",
        "version": "1.0.0",
        "description": "Domain package for consulting firms",
        "author": "Test Author",
        "dependencies": [],
        "min_platform_version": "0.1.0"
    }


@pytest.fixture
def valid_domain_config() -> Dict[str, Any]:
    """Create a valid domain configuration."""
    return {
        "name": "Consulting Firm",
        "description": "Domain for managing consulting projects and clients",
        "entity_types": ["person", "project", "client", "team"],
        "relationships": {
            "person": ["works_on", "manages", "reports_to"],
            "project": ["belongs_to", "delivered_for"],
            "client": ["sponsors", "owns"],
            "team": ["includes", "responsible_for"]
        }
    }


class TestPackageValidation:
    """Test suite for package validation."""
    
    def test_validate_manifest_structure(self, temp_package_dir):
        """Test validation of manifest.yaml structure."""
        manager = DomainPackageManager()
        
        # Missing manifest should fail
        result = manager.validate_package(temp_package_dir)
        assert not result.is_valid
        assert "manifest.yaml not found" in result.errors
        
    def test_validate_manifest_required_fields(self, temp_package_dir, valid_manifest):
        """Test validation of required manifest fields."""
        manager = DomainPackageManager()
        
        # Create manifest with missing required field
        incomplete_manifest = valid_manifest.copy()
        del incomplete_manifest["version"]
        
        manifest_path = temp_package_dir / "manifest.yaml"
        with open(manifest_path, "w") as f:
            yaml.dump(incomplete_manifest, f)
            
        result = manager.validate_package(temp_package_dir)
        assert not result.is_valid
        assert "Missing required field: version" in result.errors
        
        
    def test_validate_required_files(self, temp_package_dir, valid_manifest):
        """Test validation of required package files."""
        manager = DomainPackageManager()
        
        # Create manifest
        manifest_path = temp_package_dir / "manifest.yaml"
        with open(manifest_path, "w") as f:
            yaml.dump(valid_manifest, f)
            
        # Missing domain.yaml should fail
        result = manager.validate_package(temp_package_dir)
        assert not result.is_valid
        assert "domain.yaml not found" in result.errors
        
    def test_validate_directory_structure(self, temp_package_dir, valid_manifest, valid_domain_config):
        """Test validation of package directory structure."""
        manager = DomainPackageManager()
        
        # Create valid package structure
        manifest_path = temp_package_dir / "manifest.yaml"
        with open(manifest_path, "w") as f:
            yaml.dump(valid_manifest, f)
            
        domain_path = temp_package_dir / "domain.yaml"
        with open(domain_path, "w") as f:
            yaml.dump(valid_domain_config, f)
            
        # Create expected directories
        (temp_package_dir / "prompts").mkdir()
        (temp_package_dir / "workflows").mkdir()
        
        result = manager.validate_package(temp_package_dir)
        assert result.is_valid
        assert len(result.errors) == 0
        
    def test_validate_prompt_files(self, temp_package_dir, valid_manifest, valid_domain_config):
        """Test validation of prompt files."""
        manager = DomainPackageManager()
        
        # Setup basic structure
        manifest_path = temp_package_dir / "manifest.yaml"
        with open(manifest_path, "w") as f:
            yaml.dump(valid_manifest, f)
            
        domain_path = temp_package_dir / "domain.yaml"
        with open(domain_path, "w") as f:
            yaml.dump(valid_domain_config, f)
            
        prompts_dir = temp_package_dir / "prompts"
        prompts_dir.mkdir()
        
        # Create invalid prompt file (not XML)
        invalid_prompt = prompts_dir / "test_prompt.txt"
        invalid_prompt.write_text("This is not XML")
        
        result = manager.validate_package(temp_package_dir)
        assert not result.is_valid
        assert "Invalid prompt file format" in str(result.errors)
        
    def test_validate_complete_package(self, temp_package_dir, valid_manifest, valid_domain_config):
        """Test validation of a complete valid package."""
        manager = DomainPackageManager()
        
        # Create complete package structure
        manifest_path = temp_package_dir / "manifest.yaml"
        with open(manifest_path, "w") as f:
            yaml.dump(valid_manifest, f)
            
        domain_path = temp_package_dir / "domain.yaml"
        with open(domain_path, "w") as f:
            yaml.dump(valid_domain_config, f)
            
        # Create directories
        (temp_package_dir / "prompts").mkdir()
        (temp_package_dir / "workflows").mkdir()
        (temp_package_dir / "examples").mkdir()
        
        # Create README
        readme_path = temp_package_dir / "README.md"
        readme_path.write_text("# Consulting Firm Domain Package")
        
        # Create sample prompt
        prompt_path = temp_package_dir / "prompts" / "meeting_analysis.xml"
        prompt_path.write_text("""<prompt>
    <description>Analyze meeting transcript</description>
    <template>Analyze the following meeting transcript...</template>
</prompt>""")
        
        result = manager.validate_package(temp_package_dir)
        assert result.is_valid
        assert len(result.errors) == 0
        assert result.package_info["name"] == "consulting-firm"
        assert result.package_info["version"] == "1.0.0"