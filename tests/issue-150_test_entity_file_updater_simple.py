"""Tests for EntityFileUpdater supporting multiple entity types."""

import pytest
from datetime import datetime
from app.services.entity_file_updater import EntityFileUpdater


class TestEntityFileUpdaterMultiType:
    """Test EntityFileUpdater with project and team entities."""
    
    @pytest.fixture
    def updater(self):
        """Create EntityFileUpdater instance."""
        return EntityFileUpdater()
    
    def test_parse_project_sections(self, updater):
        """Test parsing project entity sections."""
        sample_project_content = """---
name: CRM Modernization
type: project
status: active
---

# CRM Modernization

## Current Status
The project is active.

## Active Milestones
- [ ] Complete Phase 1 (Due: 2023-12-31)
"""
        # This should fail because parse_entity_sections doesn't support entity_type parameter
        sections = updater.parse_entity_sections(sample_project_content, entity_type='project')
        
        assert sections['frontmatter']['type'] == 'project'
    
    def test_format_project_meeting_section(self, updater):
        """Test formatting meeting data for project entities."""
        meeting_data = {
            'meeting_id': 'meeting-789',
            'meeting_title': 'Project Status Update',
            'date': '2023-11-15',
            'status_update': 'Phase 2 planning initiated'
        }
        
        # This should fail because format_project_meeting_section method doesn't exist
        result = updater.format_project_meeting_section(meeting_data)
        
        assert 'Project Status Update' in result