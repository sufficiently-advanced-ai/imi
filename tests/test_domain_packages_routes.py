"""Tests for domain package API routes."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
import tempfile
import zipfile
import json
from io import BytesIO

from fastapi.testclient import TestClient
from app.main import app
from app.services.domain_package_manager import (
    ValidationResult, InstallResult, ExportResult
)


client = TestClient(app)


@pytest.fixture
def mock_package_manager():
    """Mock the DomainPackageManager."""
    with patch('app.routes.domain_packages.package_manager') as mock_pm:
        yield mock_pm


@pytest.fixture
def valid_package_zip():
    """Create a valid package zip file in memory."""
    zip_buffer = BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add manifest.yaml
        manifest_content = """
name: test-package
version: 1.0.0
description: Test package
author: Test Author
"""
        zf.writestr("test-package/manifest.yaml", manifest_content)
        
        # Add domain.yaml
        domain_content = """
name: test-domain
description: Test domain configuration
"""
        zf.writestr("test-package/domain.yaml", domain_content)
        
        # Add some prompts
        zf.writestr("test-package/prompts/test.xml", "<prompt>Test prompt</prompt>")
        
    zip_buffer.seek(0)
    return zip_buffer


class TestDomainPackageRoutes:
    """Test domain package API endpoints."""
    
    def test_validate_package_success(self, mock_package_manager, valid_package_zip):
        """Test successful package validation."""
        # Mock validation result
        mock_package_manager.validate_package.return_value = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=["Missing workflows directory"],
            package_info={
                "name": "test-package",
                "version": "1.0.0",
                "description": "Test package",
                "author": "Test Author"
            }
        )
        
        # Upload file for validation
        response = client.post(
            "/api/domain-packages/validate",
            files={"file": ("test-package.zip", valid_package_zip, "application/zip")}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["is_valid"] is True
        assert len(data["data"]["warnings"]) == 1
        assert data["data"]["package_info"]["name"] == "test-package"
    
    
    @pytest.mark.asyncio
    async def test_install_package_success(self, mock_package_manager, valid_package_zip):
        """Test successful package installation."""
        # Mock install result
        mock_package_manager.install_package = AsyncMock(return_value=InstallResult(
            success=True,
            package_name="test-package",
            version="1.0.0",
            installed_path=Path("/tmp/domains/test-package")
        ))
        
        response = client.post(
            "/api/domain-packages/install",
            files={"file": ("test-package.zip", valid_package_zip, "application/zip")}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["package_name"] == "test-package"
        assert data["data"]["version"] == "1.0.0"
        assert "Successfully installed" in data["message"]
    
    @pytest.mark.asyncio  
    async def test_install_package_failure(self, mock_package_manager, valid_package_zip):
        """Test failed package installation."""
        # Mock install failure
        mock_package_manager.install_package = AsyncMock(return_value=InstallResult(
            success=False,
            error="Domain 'test-package' already exists"
        ))
        
        response = client.post(
            "/api/domain-packages/install",
            files={"file": ("test-package.zip", valid_package_zip, "application/zip")}
        )
        
        assert response.status_code == 400
        assert "Domain 'test-package' already exists" in response.json()["detail"]
    
    @pytest.mark.asyncio
    async def test_export_domain_zip(self, mock_package_manager):
        """Test exporting domain as zip."""
        # Create a temporary file for export
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
            # Write some content
            with zipfile.ZipFile(tmp_file.name, 'w') as zf:
                zf.writestr("test.txt", "test content")
            
            # Mock export result
            mock_package_manager.export_domain = AsyncMock(return_value=ExportResult(
                success=True,
                export_path=Path(tmp_file.name),
                package_name="test-domain",
                version="1.0.0"
            ))
            
            response = client.post(
                "/api/domain-packages/export/test-domain",
                params={"format": "zip", "author": "Test Author"}
            )
            
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/octet-stream"
            assert "test-domain-1.0.0.zip" in response.headers.get("content-disposition", "")
            
            # Clean up
            Path(tmp_file.name).unlink(missing_ok=True)
    
    @pytest.mark.asyncio
    async def test_export_domain_failure(self, mock_package_manager):
        """Test failed domain export."""
        # Mock export failure
        mock_package_manager.export_domain = AsyncMock(return_value=ExportResult(
            success=False,
            error="Domain not found: test-domain"
        ))
        
        response = client.post("/api/domain-packages/export/test-domain")
        
        assert response.status_code == 400
        assert "Domain not found" in response.json()["detail"]
    
    def test_list_installed_packages(self, mock_package_manager):
        """Test listing installed packages."""
        # Mock installed packages
        mock_package_manager.installed_packages = {
            "consulting-firm": {
                "version": "1.0.0",
                "installed_at": "2024-01-01T12:00:00",
                "path": "/tmp/domains/consulting-firm"
            },
            "tech-startup": {
                "version": "2.1.0",
                "installed_at": "2024-01-02T12:00:00",
                "path": "/tmp/domains/tech-startup"
            }
        }
        
        response = client.get("/api/domain-packages/installed")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["packages"]) == 2
        assert "consulting-firm" in data["data"]["packages"]
        assert "Found 2 installed packages" in data["message"]
    
    def test_uninstall_package_success(self, mock_package_manager):
        """Test successful package uninstallation."""
        # Mock installed packages
        mock_package_manager.installed_packages = {
            "test-package": {
                "version": "1.0.0",
                "installed_at": "2024-01-01T12:00:00",
                "path": "/tmp/domains/test-package"
            }
        }
        mock_package_manager._save_installed_packages = MagicMock()
        
        response = client.delete("/api/domain-packages/test-package")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Successfully uninstalled" in data["message"]
        assert "test-package" not in mock_package_manager.installed_packages
        mock_package_manager._save_installed_packages.assert_called_once()
    
    def test_uninstall_package_not_found(self, mock_package_manager):
        """Test uninstalling non-existent package."""
        mock_package_manager.installed_packages = {}
        
        response = client.delete("/api/domain-packages/non-existent")
        
        assert response.status_code == 404
        assert "Package 'non-existent' not found" in response.json()["detail"]
    
    def test_validate_unsupported_format(self, mock_package_manager):
        """Test validation with unsupported file format."""
        response = client.post(
            "/api/domain-packages/validate",
            files={"file": ("test.txt", b"plain text content", "text/plain")}
        )
        
        # Should still process but validation will fail
        assert response.status_code == 200
        data = response.json()
        # The actual validation logic will determine if it's valid
    
    def test_export_invalid_format(self, mock_package_manager):
        """Test export with invalid format."""
        response = client.post(
            "/api/domain-packages/export/test-domain",
            params={"format": "invalid"}
        )
        
        assert response.status_code == 400
        assert "Format must be 'zip' or 'tar.gz'" in response.json()["detail"]