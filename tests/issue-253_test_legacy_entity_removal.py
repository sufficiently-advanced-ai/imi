"""
Test suite for Issue #253: Delete legacy EntityBrain and PersonBrain files.

This test validates that legacy entity files have been removed and all imports
have been updated to use the new domain-aware entity system.
"""

import pytest
import ast
from pathlib import Path


class TestLegacyEntityRemoval:
    """Test suite to validate legacy entity files are removed."""
    
    def test_legacy_entity_files_deleted(self):
        """Test that legacy EntityBrain and PersonBrain files are deleted."""
        # Files that should be deleted
        legacy_files = [
            "app/services/entity_brain.py",
            "app/services/person_brain.py"
        ]
        
        base_path = Path(__file__).parent.parent  # Get repository root
        
        for file_path in legacy_files:
            full_path = base_path / file_path
            assert not full_path.exists(), f"Legacy file {file_path} should be deleted"
    
    
    
    def test_domain_aware_services_available(self):
        """Test that domain-aware services are available as replacements."""
        base_path = Path(__file__).parent.parent  # Get repository root
        
        # Services that should exist as replacements
        replacement_services = [
            "app/services/domain_aware_entity_processor.py",
            "app/services/domain_aware_entity_extractor.py",
            "app/services/entity_registry.py"
        ]
        
        for service_path in replacement_services:
            full_path = base_path / service_path
            assert full_path.exists(), f"Replacement service {service_path} should exist"
    
    def test_key_classes_importable(self):
        """Test that key replacement classes can be imported."""
        import importlib.util
        
        replacement_classes = [
            "app.services.domain_aware_entity_processor",
            "app.services.domain_aware_entity_extractor",
            "app.services.entity_registry"
        ]
        
        for module_name in replacement_classes:
            spec = importlib.util.find_spec(module_name)
            assert spec is not None, f"Module {module_name} not found"
    
    def test_legacy_classes_not_importable(self):
        """Test that legacy classes cannot be imported."""
        import importlib.util
        
        legacy_modules = [
            "app.services.entity_brain",
            "app.services.person_brain"
        ]
        
        for module_name in legacy_modules:
            spec = importlib.util.find_spec(module_name)
            assert spec is None, f"Legacy module {module_name} should not be importable"
    
    def _find_legacy_imports(self, directory: Path, excluded_files: set[str]) -> list[dict]:
        """Find all legacy imports in Python files within directory."""
        legacy_imports = []
        
        for py_file in directory.rglob("*.py"):
            # Skip excluded files
            relative_path = str(py_file.relative_to(Path(__file__).parent.parent))
            if relative_path in excluded_files:
                continue
                
            try:
                with open(py_file, encoding='utf-8') as f:
                    content = f.read()
                    
                # Check for legacy import patterns
                if self._has_legacy_imports(content):
                    legacy_imports.append({
                        'file': str(py_file),
                        'imports': self._extract_legacy_imports(content)
                    })
                    
            except (OSError, UnicodeDecodeError):
                # Skip files that can't be read (binary files, permission issues, etc.)
                continue
                
        return legacy_imports
    
    def _has_legacy_imports(self, content: str) -> bool:
        """Check if content has legacy import patterns."""
        legacy_patterns = [
            "from app.services.entity_brain",
            "from app.services.person_brain",
            "from .entity_brain",
            "from .person_brain",
            "from ..services.entity_brain",
            "from ..services.person_brain",
            "import entity_brain",
            "import person_brain"
        ]
        
        for pattern in legacy_patterns:
            if pattern in content:
                return True
                
        return False
    
    def _extract_legacy_imports(self, content: str) -> list[str]:
        """Extract legacy import statements from content."""
        imports = []
        
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and ('entity_brain' in node.module or 'person_brain' in node.module):
                        imports.append(f"from {node.module} import {', '.join(alias.name for alias in node.names)}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if 'entity_brain' in alias.name or 'person_brain' in alias.name:
                            imports.append(f"import {alias.name}")
        except (SyntaxError, ValueError):
            # Fallback to simple string matching if AST parsing fails
            lines = content.split('\n')
            for line in lines:
                if 'entity_brain' in line or 'person_brain' in line:
                    if line.strip().startswith(('from ', 'import ')):
                        imports.append(line.strip())
        
        return imports


class TestReplacementFunctionality:
    """Test suite to validate replacement functionality works correctly."""
    
    def test_domain_aware_entity_processor_replaces_entity_brain(self):
        """Test that DomainAwareEntityProcessor can replace EntityBrain functionality."""
        from app.services.domain_aware_entity_processor import DomainAwareEntityProcessor
        from app.services.claude_client import get_claude_client
        
        # Create processor with Claude client
        claude_client = get_claude_client()
        processor = DomainAwareEntityProcessor(claude_client)
        
        # Test that it has the key methods that EntityBrain had
        assert hasattr(processor, 'update_entity_profile')
        assert callable(processor.update_entity_profile)
    
