"""
Test frontmatter service functionality - Issue #35
Tests for frontmatter format detection, conversion, and migration
"""
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch
import logging

from app.services.frontmatter import FrontmatterService, FrontmatterMigrationService


@pytest.fixture
def temp_repo():
    """Create a temporary repository for testing"""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def mock_git_ops(temp_repo):
    """Mock GitOps with temp repo path"""
    mock = MagicMock()
    mock.repo_path = str(temp_repo)
    return mock


class TestFrontmatterFormatDetection:
    """Test cases for frontmatter format detection"""
    
    def test_detects_correct_format_with_standard_delimiters(self):
        """Test detection of correct frontmatter format"""
        service = FrontmatterService()
        
        content = """---
name: Test Document
type: meeting
date: 2024-01-15
---

# Document Content
This is the body."""
        
        assert service.has_correct_frontmatter_format(content) is True
        assert service.has_incorrect_frontmatter_format(content) is False
    
    def test_detects_incorrect_format_with_yaml_blocks(self):
        """Test detection of incorrect yaml block format"""
        service = FrontmatterService()
        
        content = """```yaml
name: Test Document
type: meeting
date: 2024-01-15
```

# Document Content
This is the body."""
        
        assert service.has_correct_frontmatter_format(content) is False
        assert service.has_incorrect_frontmatter_format(content) is True
    
    def test_handles_empty_content(self):
        """Test handling of empty content"""
        service = FrontmatterService()
        
        assert service.has_correct_frontmatter_format("") is False
        assert service.has_incorrect_frontmatter_format("") is False
        assert service.has_correct_frontmatter_format("   \n  ") is False
        assert service.has_incorrect_frontmatter_format("   \n  ") is False
    
    def test_detects_missing_closing_delimiter(self):
        """Test detection when closing --- is missing"""
        service = FrontmatterService()
        
        content = """---
name: Test Document
type: meeting

# Document without closing delimiter"""
        
        assert service.has_correct_frontmatter_format(content) is False
    
    def test_detects_invalid_yaml_in_correct_format(self):
        """Test detection of syntactically incorrect YAML"""
        service = FrontmatterService()
        
        content = """---
name: Test: Document: Invalid
  bad indentation
[unclosed bracket
---

# Content"""
        
        assert service.has_correct_frontmatter_format(content) is False


class TestFrontmatterExtraction:
    """Test cases for extracting frontmatter from incorrect format"""
    
    def test_extracts_from_yaml_blocks(self):
        """Test extraction of metadata from yaml blocks"""
        service = FrontmatterService()
        
        content = """```yaml
name: Test Document
type: meeting
participants:
  - John Doe
  - Jane Smith
date: 2024-01-15
```

# Meeting Notes"""
        
        metadata = service.extract_frontmatter_from_incorrect_format(content)
        assert metadata is not None
        assert metadata['name'] == 'Test Document'
        assert metadata['type'] == 'meeting'
        assert len(metadata['participants']) == 2
        assert str(metadata['date']) == '2024-01-15'
    
    def test_returns_none_for_non_yaml_blocks(self):
        """Test that non-yaml blocks return None"""
        service = FrontmatterService()
        
        content = """# Regular Document
No frontmatter here."""
        
        assert service.extract_frontmatter_from_incorrect_format(content) is None
    
    def test_handles_invalid_yaml_in_blocks(self):
        """Test handling of invalid YAML in code blocks"""
        service = FrontmatterService()
        
        content = """```yaml
invalid: yaml: content: here
  bad indentation
```

# Content"""
        
        assert service.extract_frontmatter_from_incorrect_format(content) is None


class TestFrontmatterConversion:
    """Test cases for converting frontmatter format"""
    
    def test_converts_yaml_blocks_to_standard_format(self):
        """Test conversion from yaml blocks to standard format"""
        service = FrontmatterService()
        
        content = """```yaml
name: Test Document
type: meeting
date: 2024-01-15
```

# Document Content
This is the body."""
        
        expected = """---
name: Test Document
type: meeting
date: 2024-01-15
---

# Document Content
This is the body."""
        
        result = service.convert_to_correct_format(content)
        assert result == expected
    
    def test_preserves_already_correct_format(self):
        """Test that correct format is preserved"""
        service = FrontmatterService()
        
        content = """---
name: Already Correct
---

# Content"""
        
        result = service.convert_to_correct_format(content)
        assert result == content
    
    def test_handles_empty_yaml_content(self):
        """Test conversion of empty yaml blocks"""
        service = FrontmatterService()
        
        content = """```yaml
```

# Content Only"""
        
        expected = """---
---

# Content Only"""
        
        result = service.convert_to_correct_format(content)
        assert result == expected
    
    def test_preserves_content_without_frontmatter(self):
        """Test that content without frontmatter is preserved"""
        service = FrontmatterService()
        
        content = """# Regular Document
No frontmatter here."""
        
        result = service.convert_to_correct_format(content)
        assert result == content
    
    def test_handles_yaml_with_trailing_newlines(self):
        """Test proper handling of trailing newlines in YAML"""
        service = FrontmatterService()
        
        content = """```yaml
name: Test
date: 2024-01-15


```

# Content"""
        
        expected = """---
name: Test
date: 2024-01-15
---

# Content"""
        
        result = service.convert_to_correct_format(content)
        assert result == expected


class TestFrontmatterMigrationService:
    """Test cases for migration service"""
    
    def test_finds_files_with_incorrect_format(self, temp_repo, mock_git_ops):
        """Test finding files with incorrect frontmatter format"""
        # Create test files
        correct_file = temp_repo / "correct.md"
        correct_file.write_text("""---
name: Correct
---

Content""")
        
        incorrect_file = temp_repo / "incorrect.md"
        incorrect_file.write_text("""```yaml
name: Incorrect
```

Content""")
        
        regular_file = temp_repo / "regular.md"
        regular_file.write_text("# No frontmatter")
        
        service = FrontmatterMigrationService(git_ops=mock_git_ops)
        incorrect_files, errors = service.find_files_with_incorrect_format()
        
        assert len(incorrect_files) == 1
        assert incorrect_file in incorrect_files
        assert len(errors) == 0
    
    def test_migrate_file_converts_content(self, temp_repo, mock_git_ops):
        """Test migrating a single file"""
        test_file = temp_repo / "test.md"
        test_file.write_text("""```yaml
name: Test File
type: document
```

# Content""")
        
        service = FrontmatterMigrationService(git_ops=mock_git_ops)
        result = service.migrate_file(str(test_file))
        
        assert result is True
        
        # Check converted content
        new_content = test_file.read_text()
        assert new_content.startswith("---\n")
        assert "name: Test File" in new_content
        assert "```yaml" not in new_content
    
    def test_migrate_file_with_commit(self, temp_repo, mock_git_ops):
        """Test file migration with git commit"""
        test_file = temp_repo / "test.md"
        test_file.write_text("""```yaml
name: Test
```

Content""")
        
        service = FrontmatterMigrationService(git_ops=mock_git_ops)
        result = service.migrate_file(str(test_file), commit=True)
        
        assert result is True
        mock_git_ops.add_and_commit.assert_called_once()
        
    def test_migrate_all_files_with_progress(self, temp_repo, mock_git_ops):
        """Test migrating all files with progress callback"""
        # Create multiple test files
        for i in range(3):
            test_file = temp_repo / f"test{i}.md"
            test_file.write_text(f"""```yaml
name: Test {i}
```

Content {i}""")
        
        progress_calls = []
        def progress_callback(current, total, filename):
            progress_calls.append((current, total, filename))
        
        service = FrontmatterMigrationService(git_ops=mock_git_ops)
        results = service.migrate_all_files(progress_callback=progress_callback)
        
        assert results['total_files'] == 3
        assert results['successful'] == 3
        assert results['failed'] == 0
        assert len(progress_calls) == 3
        
        # Verify all files were converted
        for i in range(3):
            content = (temp_repo / f"test{i}.md").read_text()
            assert content.startswith("---\n")
    
    def test_dry_run_does_not_modify_files(self, temp_repo, mock_git_ops):
        """Test dry run doesn't modify files"""
        test_file = temp_repo / "test.md"
        original_content = """```yaml
name: Test
```

Content"""
        test_file.write_text(original_content)
        
        service = FrontmatterMigrationService(git_ops=mock_git_ops)
        result = service.dry_run()
        changes = result['changes']
        errors = result['errors']
        
        assert len(changes) == 1
        assert changes[0]['file'] == str(test_file)
        assert changes[0]['would_change'] is True
        assert len(errors) == 0
        
        # Verify file wasn't modified
        assert test_file.read_text() == original_content


class TestErrorHandling:
    """Test error handling in frontmatter service"""
    
    def test_migration_handles_file_read_errors(self, temp_repo, mock_git_ops, caplog):
        """Test handling of file read errors during migration"""
        # Create a directory instead of file to cause read error
        bad_file = temp_repo / "bad.md"
        bad_file.mkdir()
        
        service = FrontmatterMigrationService(git_ops=mock_git_ops)
        with caplog.at_level(logging.ERROR):
            result = service.migrate_file(str(bad_file))
        
        assert result is False
        assert "Failed to migrate" in caplog.text
        assert len(service.errors) > 0
    
    def test_migration_handles_permission_errors(self, temp_repo, mock_git_ops):
        """Test handling of permission errors"""
        test_file = temp_repo / "test.md"
        test_file.write_text("""```yaml
name: Test
```

Content""")
        
        service = FrontmatterMigrationService(git_ops=mock_git_ops)
        
        # Mock Path.write_text to raise PermissionError
        with patch.object(Path, 'write_text', side_effect=PermissionError("No permission")):
            result = service.migrate_file(str(test_file))
            assert result is False


class TestInputValidation:
    """Test input validation for security"""
    
    def test_regex_with_large_input(self):
        """Test regex doesn't hang on large malicious input"""
        service = FrontmatterService()
        
        # Create a large YAML block that could cause ReDoS
        large_yaml = "```yaml\n" + "a: " * 10000 + "\n```\nContent"
        
        # This should complete quickly without hanging
        import time
        start_time = time.time()
        result = service.has_incorrect_frontmatter_format(large_yaml)
        elapsed_time = time.time() - start_time
        
        assert elapsed_time < 1.0  # Should complete in less than 1 second
        assert result is True
    
    def test_content_size_validation(self):
        """Test that extremely large content is handled properly"""
        service = FrontmatterService()
        
        # Create very large content
        huge_content = "x" * (10 * 1024 * 1024)  # 10MB
        
        # Should handle without crashing
        assert service.has_correct_frontmatter_format(huge_content) is False
        assert service.has_incorrect_frontmatter_format(huge_content) is False


class TestPathHandling:
    """Test proper path handling across platforms"""
    
    def test_handles_paths_with_spaces(self, temp_repo, mock_git_ops):
        """Test handling of paths with spaces"""
        dir_with_spaces = temp_repo / "my documents"
        dir_with_spaces.mkdir()
        
        test_file = dir_with_spaces / "test file.md"
        test_file.write_text("""```yaml
name: Test
```

Content""")
        
        service = FrontmatterMigrationService(git_ops=mock_git_ops)
        result = service.migrate_file(str(test_file))
        
        assert result is True
        assert test_file.read_text().startswith("---\n")
    
    def test_handles_unicode_paths(self, temp_repo, mock_git_ops):
        """Test handling of Unicode characters in paths"""
        unicode_dir = temp_repo / "документы"
        unicode_dir.mkdir()
        
        test_file = unicode_dir / "тест.md"
        test_file.write_text("""```yaml
name: Test
```

Content""")
        
        service = FrontmatterMigrationService(git_ops=mock_git_ops)
        result = service.migrate_file(str(test_file))
        
        assert result is True
    
    def test_uses_pathlib_consistently(self, temp_repo, mock_git_ops):
        """Test that pathlib is used consistently"""
        service = FrontmatterMigrationService(git_ops=mock_git_ops)
        
        # The service should accept both str and Path objects
        test_file = temp_repo / "test.md"
        test_file.write_text("""```yaml
name: Test
```

Content""")
        
        # Test with Path object
        result1 = service.migrate_file(test_file)
        assert result1 is True
        
        # Reset file
        test_file.write_text("""```yaml
name: Test
```

Content""")
        
        # Test with string
        result2 = service.migrate_file(str(test_file))
        assert result2 is True


class TestYAMLValidation:
    """Test YAML validation and schema checking"""
    
    def test_validates_yaml_structure(self):
        """Test validation of YAML structure beyond syntax"""
        service = FrontmatterService()
        
        # Valid syntax but potentially problematic structure
        content = """---
name: Test
nested:
  very:
    deep:
      structure:
        that:
          could:
            cause:
              issues: true
---

Content"""
        
        # Should still be considered valid for now
        assert service.has_correct_frontmatter_format(content) is True
    
    def test_handles_yaml_aliases_safely(self):
        """Test safe handling of YAML aliases to prevent attacks"""
        service = FrontmatterService()
        
        # YAML with aliases (could be used in billion laughs attack)
        content = """```yaml
name: &name Test
references:
  - *name
  - *name
```

Content"""
        
        # Should extract safely
        metadata = service.extract_frontmatter_from_incorrect_format(content)
        assert metadata is not None
        assert metadata['name'] == 'Test'
        assert len(metadata['references']) == 2