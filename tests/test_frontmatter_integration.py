"""
Integration tests for frontmatter format fix - Issue #35
Tests the complete workflow from entity generation to knowledge graph indexing
"""
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import yaml

from app.services.frontmatter import FrontmatterService
from app.domain.entities.services import EntityService


class TestFrontmatterEntityIntegration:
    """Test integration between entity generation and frontmatter format"""
    
    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository for testing"""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def mock_claude_client(self):
        """Mock Claude client for entity generation"""
        mock = AsyncMock()
        return mock
    
    @pytest.fixture
    def mock_git_ops(self, temp_repo):
        """Mock GitOps with temp repo path"""
        mock = MagicMock()
        mock.repo_path = str(temp_repo)
        mock.add_and_commit = MagicMock()
        return mock
    
    @pytest.mark.asyncio
    async def test_entity_generation_creates_correct_frontmatter_format(
        self, temp_repo, mock_claude_client, mock_git_ops
    ):
        """Test that generated entity content should have correct frontmatter format"""
        # Example of what entity generation should produce
        correct_entity_content = """---
name: John Smith
title: Senior Engineer
department: Engineering
email: john.smith@example.com
---

# John Smith

## Professional Background

John Smith is a Senior Engineer in the Engineering department.

## Key Information

- **Role**: Senior Engineer
- **Department**: Engineering
- **Email**: john.smith@example.com"""
        
        # Verify the format is correct
        service = FrontmatterService()
        assert service.has_correct_frontmatter_format(correct_entity_content) is True
        assert service.has_incorrect_frontmatter_format(correct_entity_content) is False
        
        # Verify frontmatter can be extracted
        metadata = service.extract_all(correct_entity_content)
        assert metadata is not None
        assert metadata['name'] == 'John Smith'
        assert metadata['title'] == 'Senior Engineer'
        
        # Test that old format would be detected as incorrect
        incorrect_entity_content = """```yaml
name: John Smith
title: Senior Engineer
department: Engineering
email: john.smith@example.com
```

# John Smith

## Professional Background

John Smith is a Senior Engineer in the Engineering department."""
        
        assert service.has_correct_frontmatter_format(incorrect_entity_content) is False
        assert service.has_incorrect_frontmatter_format(incorrect_entity_content) is True
    
    @pytest.mark.asyncio
    async def test_prompt_generates_correct_format(self, mock_claude_client):
        """Test that the updated prompts generate correct frontmatter format"""
        # Read the actual prompt
        prompt_path = Path(__file__).parent.parent / "app" / "prompts" / "person_update.xml"
        prompt_content = prompt_path.read_text()
        
        # Verify prompt asks for standard format
        assert "standard format (enclosed in --- delimiters)" in prompt_content
        
        # Test with project prompt too
        project_prompt_path = Path(__file__).parent.parent / "app" / "prompts" / "project_update.xml"
        project_prompt_content = project_prompt_path.read_text()
        
        assert "standard format (enclosed in --- delimiters)" in project_prompt_content
        assert "Format the frontmatter like this:" in project_prompt_content
        assert "---" in project_prompt_content and "name: Project Name" in project_prompt_content
    
    def test_knowledge_graph_indexes_correct_format(self, temp_repo, mock_git_ops):
        """Test that files with correct frontmatter can be indexed properly"""
        # Create a test file with correct format
        test_file = temp_repo / "people" / "person-test.md"
        test_file.parent.mkdir(exist_ok=True)
        test_file.write_text("""---
name: Test Person
title: Test Title
department: Testing
related_people:
  - person-jane-doe
---

# Test Person

Test content.""")
        
        # Test that frontmatter can be extracted correctly
        service = FrontmatterService()
        content = test_file.read_text()
        metadata = service.extract_all(content)
        
        assert metadata is not None
        assert metadata['name'] == 'Test Person'
        assert metadata['title'] == 'Test Title'
        assert 'person-jane-doe' in metadata['related_people']
        
        # Verify this would work with KnowledgeGraph indexing
        assert service.has_correct_frontmatter_format(content) is True
    
    def test_migration_preserves_data_integrity(self, temp_repo):
        """Test that migration preserves all data when converting formats"""
        service = FrontmatterService()
        
        # Create content with incorrect format but complex data
        incorrect_content = """```yaml
name: Complex Person
title: Senior Software Engineer
department: Engineering
skills:
  - Python
  - TypeScript
  - Go
projects:
  - project-alpha
  - project-beta
metadata:
  last_review: 2024-01-15
  performance_rating: 4.5
  direct_reports: 3
```

# Complex Person Profile

Detailed information about this person..."""
        
        # Convert to correct format
        correct_content = service.convert_to_correct_format(incorrect_content)
        
        # Extract metadata from both
        incorrect_metadata = service.extract_frontmatter_from_incorrect_format(incorrect_content)
        correct_metadata = service.extract_all(correct_content)
        
        # Verify all data is preserved
        assert correct_metadata == incorrect_metadata
        assert correct_metadata['name'] == 'Complex Person'
        assert len(correct_metadata['skills']) == 3
        assert correct_metadata['metadata']['performance_rating'] == 4.5
        
        # Verify content after frontmatter is preserved
        assert "# Complex Person Profile" in correct_content
        assert "Detailed information about this person..." in correct_content