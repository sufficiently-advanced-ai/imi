import pytest
from datetime import datetime
from typing import Dict, Any

from app.services.entity_file_updater import EntityFileUpdater


class TestEntityFileUpdater:
    
    @pytest.fixture
    def sample_entity_markdown(self):
        return """---
name: John Smith
type: person
meetings:
  - meeting-20250115-2
last_updated: 2025-01-15T10:00:00Z
---

# John Smith

## Current Role & Responsibilities
Financial Services Practice Lead

## Meeting Participation

### Q4 Review (2025-01-15)
**Role**: Practice Lead
**Key Contributions**:
- Presented Q4 results

## Active Commitments
- [✓] Complete Q4 financial review (Due: 2025-01-15) - COMPLETED

## Relationships
- Works closely with: Rachel Green (Legal Counsel)
- Reports to: Senior Partners
"""
    
    @pytest.fixture
    def new_meeting_data(self):
        return {
            'meeting_id': 'meeting-20250117-1',
            'meeting_title': 'Healthcare Market Assessment',
            'date': '2025-01-17',
            'role': 'Financial Services Practice Lead',
            'key_contributions': [
                'Led discussion on entering healthcare executive search market',
                'Proposed leveraging financial services relationships as competitive advantage',
                'Committed to initiating partner discussions by mid-February'
            ],
            'commitments': [
                {
                    'text': 'Initiate partner discussions about healthcare practice',
                    'due_date': '2025-02-15',
                    'status': 'pending'
                },
                {
                    'text': 'Review business case with senior partners',
                    'due_date': '2025-02-01',
                    'status': 'pending'
                }
            ],
            'decisions': [
                {
                    'decision': 'Agreed to $2.5M initial investment for 18-month launch',
                    'role': 'Participant'
                },
                {
                    'decision': 'Approved timeline for preliminary assessment by January 31st',
                    'role': 'Participant'
                }
            ],
            'insights': [
                'Sees opportunity in healthcare C-suite with 40% higher turnover',
                'Concerned about potential conflicts with CRM modernization project',
                'Values compliance and risk mitigation in new ventures'
            ],
            'relationships': [
                'Rachel Green (Legal Counsel)',
                'Alex Turner (Consultant)'
            ]
        }
    
    def test_parse_entity_sections(self, sample_entity_markdown):
        """Test parsing existing entity markdown into sections"""
        updater = EntityFileUpdater()
        sections = updater.parse_entity_sections(sample_entity_markdown)
        
        assert sections['frontmatter']['name'] == 'John Smith'
        assert sections['frontmatter']['type'] == 'person'
        assert 'meeting-20250115-2' in sections['frontmatter']['meetings']
        assert sections['current_role'] == 'Financial Services Practice Lead'
        assert len(sections['meeting_participation']) == 1
        assert sections['meeting_participation'][0]['title'] == 'Q4 Review (2025-01-15)'
        assert len(sections['active_commitments']) == 1
        assert sections['relationships'] == ['Rachel Green (Legal Counsel)', 'Senior Partners']
    
    def test_format_meeting_section(self, new_meeting_data):
        """Test formatting new meeting data into markdown section"""
        updater = EntityFileUpdater()
        section = updater.format_meeting_section(new_meeting_data)
        
        assert '### Healthcare Market Assessment (2025-01-17)' in section
        assert '**Role**: Financial Services Practice Lead' in section
        assert '**Key Contributions**:' in section
        assert '- Led discussion on entering healthcare executive search market' in section
        assert '**Commitments Made**:' in section
        assert '- Initiate partner discussions about healthcare practice (Due: 2025-02-15)' in section
        assert '**Decisions Participated In**:' in section
        assert '- Agreed to $2.5M initial investment for 18-month launch' in section
        assert '**Key Insights**:' in section
        assert '- Sees opportunity in healthcare C-suite with 40% higher turnover' in section
    
    def test_merge_commitments(self):
        """Test merging new commitments with existing ones"""
        updater = EntityFileUpdater()
        
        existing_commitments = [
            {'text': 'Complete Q4 review', 'due_date': '2025-01-15', 'status': 'completed'},
            {'text': 'Update budget forecast', 'due_date': '2025-01-20', 'status': 'pending'}
        ]
        
        new_commitments = [
            {'text': 'Review compliance requirements', 'due_date': '2025-01-31', 'status': 'pending'},
            {'text': 'Update budget forecast', 'due_date': '2025-01-20', 'status': 'pending'}  # Duplicate
        ]
        
        merged = updater.merge_commitments(existing_commitments, new_commitments)
        
        assert len(merged) == 3  # No duplicates
        assert any(c['text'] == 'Complete Q4 review' for c in merged)
        assert any(c['text'] == 'Update budget forecast' for c in merged)
        assert any(c['text'] == 'Review compliance requirements' for c in merged)
    
    def test_update_relationships(self):
        """Test updating entity relationships"""
        updater = EntityFileUpdater()
        
        existing_relationships = {
            'Rachel Green': 'Legal Counsel',
            'Senior Partners': 'Reports to'
        }
        
        new_relationships = [
            'Rachel Green (Legal Counsel)',  # Existing
            'Alex Turner (Consultant)',      # New
            'Sarah Johnson'                  # New without role
        ]
        
        updated = updater.update_relationships(existing_relationships, new_relationships)
        
        assert 'Rachel Green' in updated
        assert 'Alex Turner' in updated
        assert 'Sarah Johnson' in updated
        assert updated['Alex Turner'] == 'Consultant'
        assert updated['Sarah Johnson'] == ''  # No role specified
    
    def test_rebuild_entity_file(self, sample_entity_markdown, new_meeting_data):
        """Test rebuilding complete entity file with updates"""
        updater = EntityFileUpdater()
        
        sections = updater.parse_entity_sections(sample_entity_markdown)
        
        # Add new meeting
        sections['frontmatter']['meetings'].append(new_meeting_data['meeting_id'])
        sections['frontmatter']['last_updated'] = datetime.now().isoformat() + 'Z'
        
        # Add meeting section
        new_meeting_section = updater.format_meeting_section(new_meeting_data)
        sections['meeting_participation'].insert(0, new_meeting_section)  # Add at beginning
        
        # Merge commitments
        sections['active_commitments'] = updater.merge_commitments(
            sections['active_commitments'],
            new_meeting_data['commitments']
        )
        
        rebuilt = updater.rebuild_entity_file(sections)
        
        assert '---' in rebuilt  # Frontmatter markers
        assert 'meeting-20250117-1' in rebuilt
        assert 'Healthcare Market Assessment' in rebuilt
        assert 'Initiate partner discussions' in rebuilt
        assert '## Active Commitments' in rebuilt
        assert '## Relationships' in rebuilt
    
    def test_format_active_commitments(self):
        """Test formatting active commitments section"""
        updater = EntityFileUpdater()
        
        commitments = [
            {'text': 'Complete Q4 review', 'due_date': '2025-01-15', 'status': 'completed'},
            {'text': 'Review compliance', 'due_date': '2025-01-31', 'status': 'pending'},
            {'text': 'Update forecast', 'due_date': '2025-01-20', 'status': 'pending'}
        ]
        
        formatted = updater.format_active_commitments(commitments)
        
        assert '- [✓] Complete Q4 review (Due: 2025-01-15) - COMPLETED' in formatted
        assert '- [ ] Review compliance (Due: 2025-01-31)' in formatted
        assert '- [ ] Update forecast (Due: 2025-01-20)' in formatted
    
    def test_limit_meeting_references(self):
        """Test limiting number of meeting references in frontmatter"""
        updater = EntityFileUpdater(max_meeting_refs=5)
        
        meetings = [f'meeting-{i}' for i in range(10)]
        limited = updater.limit_meeting_references(meetings)
        
        assert len(limited) == 5
        assert limited == ['meeting-5', 'meeting-6', 'meeting-7', 'meeting-8', 'meeting-9']
    
    def test_create_new_entity_file(self):
        """Test creating a new entity file from scratch"""
        updater = EntityFileUpdater()
        
        entity_data = {
            'id': 'jane_doe',
            'name': 'Jane Doe',
            'type': 'person',
            'role': 'VP of Sales'
        }
        
        meeting_data = {
            'meeting_id': 'meeting-20250117-1',
            'meeting_title': 'Sales Strategy',
            'date': '2025-01-17',
            'commitments': [
                {'text': 'Prepare Q1 forecast', 'due_date': '2025-01-25', 'status': 'pending'}
            ]
        }
        
        content = updater.create_new_entity_file(entity_data, meeting_data)
        
        assert '---' in content
        assert 'name: Jane Doe' in content
        assert 'type: person' in content
        assert '# Jane Doe' in content
        assert '## Current Role & Responsibilities' in content
        assert 'VP of Sales' in content
        assert '## Meeting Participation' in content
        assert '### Sales Strategy (2025-01-17)' in content
        assert '## Active Commitments' in content
        assert '- [ ] Prepare Q1 forecast (Due: 2025-01-25)' in content
    
    def test_validate_entity_file_format(self):
        """Test validation of entity file format"""
        updater = EntityFileUpdater()
        
        valid_content = """---
name: Test Person
type: person
---

# Test Person

## Current Role & Responsibilities
Test Role
"""
        
        invalid_content = """
# Test Person
No frontmatter here
"""
        
        assert updater.validate_entity_file_format(valid_content) is True
        assert updater.validate_entity_file_format(invalid_content) is False
    
    def test_extract_entity_metadata(self, sample_entity_markdown):
        """Test extracting metadata from entity file"""
        updater = EntityFileUpdater()
        
        metadata = updater.extract_entity_metadata(sample_entity_markdown)
        
        assert metadata['name'] == 'John Smith'
        assert metadata['type'] == 'person'
        assert metadata['meetings_count'] == 1
        assert metadata['active_commitments_count'] == 0  # One is completed
        assert metadata['relationships_count'] == 2