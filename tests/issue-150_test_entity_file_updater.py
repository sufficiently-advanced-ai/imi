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
    
    @pytest.fixture
    def sample_project_content(self):
        """Sample project entity file content."""
        return """---
name: CRM Modernization
type: project
status: active
timeline: "2023-Q4 to 2024-Q3"
key_people:
  - person-alex-chen
  - person-sarah-johnson
teams:
  - team-engineering
  - team-sales
meetings:
  - meeting-123
last_updated: "2023-11-14T00:00:00Z"
---

# CRM Modernization

## Current Status
The CRM Modernization project is actively progressing with Phase 1 nearing completion.

## Meeting Participation

### Sprint Planning (2023-11-10)
**Status Update**: Phase 1 on track for December completion
**Milestones Achieved**:
- Completed integration testing with 92% coverage
- Migrated 70% of legacy data

**Blockers Identified**:
- API authentication issues in security review

## Active Milestones
- [ ] Complete Phase 1 deployment (Due: 2023-12-31)
- [ ] Resolve API authentication issues (Due: 2023-11-20)
- [✓] Complete data migration to 70% (Due: 2023-10-15) - COMPLETED

## Dependencies & Blockers
- Data Warehouse Upgrade must complete before Phase 2
- API Gateway Implementation required for external integrations

## Team Updates
- Engineering: Addressing authentication issues
- Sales: Completed UAT for core modules
"""
    
    @pytest.fixture
    def sample_team_content(self):
        """Sample team entity file content."""
        return """---
name: Engineering Team
type: team
department: Technology
lead: person-sarah-johnson
members:
  - person-alex-chen
  - person-david-patel
meetings:
  - meeting-456
last_updated: "2023-11-14T00:00:00Z"
---

# Engineering Team

## Current Focus
Working on CRM modernization and addressing technical debt across platforms.

## Meeting Participation

### Team Retrospective (2023-11-08)
**Achievements**:
- Completed sprint with 95% story completion
- Reduced bug backlog by 30%

**Goals Set**:
- Improve code review turnaround to <24 hours
- Implement automated testing for all new features

**Team Commitments**:
- Weekly architecture reviews starting November
- Pair programming for complex features

## Active Goals
- [ ] Reduce code review turnaround to <24 hours (Due: 2023-12-01)
- [ ] Achieve 90% test coverage (Due: 2024-01-31)
- [✓] Complete security audit (Due: 2023-10-31) - COMPLETED

## Team Achievements
- Sprint velocity increased by 20% over last quarter
- Zero critical bugs in production for 60 days

## Cross-Team Collaborations
- Working with Sales on CRM user requirements
- Supporting Customer Success with API documentation
"""
    
    def test_parse_project_sections(self, updater, sample_project_content):
        """Test parsing project entity sections."""
        # This should fail because parse_entity_sections doesn't support entity_type parameter
        sections = updater.parse_entity_sections(sample_project_content, entity_type='project')
        
        assert sections['frontmatter']['type'] == 'project'
        assert sections['frontmatter']['status'] == 'active'
        assert 'current_status' in sections
        assert 'active_milestones' in sections
        assert 'dependencies_blockers' in sections
        assert 'team_updates' in sections
        assert len(sections['meeting_participation']) == 1
    
    def test_parse_team_sections(self, updater, sample_team_content):
        """Test parsing team entity sections."""
        # This should fail because parse_entity_sections doesn't support entity_type parameter
        sections = updater.parse_entity_sections(sample_team_content, entity_type='team')
        
        assert sections['frontmatter']['type'] == 'team'
        assert sections['frontmatter']['lead'] == 'person-sarah-johnson'
        assert 'current_focus' in sections
        assert 'active_goals' in sections
        assert 'team_achievements' in sections
        assert 'cross_team_collaborations' in sections
        assert len(sections['meeting_participation']) == 1
    
    def test_format_project_meeting_section(self, updater):
        """Test formatting meeting data for project entities."""
        meeting_data = {
            'meeting_id': 'meeting-789',
            'meeting_title': 'Project Status Update',
            'date': '2023-11-15',
            'status_update': 'Phase 2 planning initiated',
            'milestones': [
                {'text': 'Complete API integration', 'due_date': '2023-12-15', 'status': 'in_progress'},
                {'text': 'Deploy to staging', 'due_date': '2023-12-20', 'status': 'pending'}
            ],
            'blockers': [
                'Waiting for security team approval',
                'Third-party API documentation incomplete'
            ],
            'decisions': [
                {'decision': 'Adopt microservices architecture for Phase 2'}
            ],
            'team_updates': {
                'Engineering': 'Completed technical design review',
                'QA': 'Test automation framework selected'
            }
        }
        
        # This should fail because format_project_meeting_section method doesn't exist
        result = updater.format_project_meeting_section(meeting_data)
        
        assert '### Project Status Update (2023-11-15)' in result
        assert '**Status Update**: Phase 2 planning initiated' in result
        assert '**Milestones**:' in result
        assert '- Complete API integration (Due: 2023-12-15) - IN PROGRESS' in result
        assert '**Blockers Identified**:' in result
        assert '- Waiting for security team approval' in result
        assert '**Team Updates**:' in result
        assert '- Engineering: Completed technical design review' in result
    
    def test_format_team_meeting_section(self, updater):
        """Test formatting meeting data for team entities."""
        meeting_data = {
            'meeting_id': 'meeting-890',
            'meeting_title': 'Team Planning Session',
            'date': '2023-11-16',
            'achievements': [
                'Reduced deployment time by 40%',
                'Onboarded 2 new team members successfully'
            ],
            'goals': [
                {'text': 'Implement CI/CD pipeline', 'due_date': '2023-12-31'},
                {'text': 'Conduct knowledge sharing sessions', 'due_date': '2023-11-30'}
            ],
            'team_commitments': [
                'Daily standups at 9 AM',
                'Code reviews within 24 hours'
            ],
            'collaborations': [
                'Working with DevOps on infrastructure automation',
                'Supporting Product team with technical feasibility'
            ]
        }
        
        # This should fail because format_team_meeting_section method doesn't exist
        result = updater.format_team_meeting_section(meeting_data)
        
        assert '### Team Planning Session (2023-11-16)' in result
        assert '**Achievements**:' in result
        assert '- Reduced deployment time by 40%' in result
        assert '**Goals Set**:' in result
        assert '- Implement CI/CD pipeline (Due: 2023-12-31)' in result
        assert '**Team Commitments**:' in result
        assert '- Daily standups at 9 AM' in result
        assert '**Cross-Team Collaborations**:' in result
        assert '- Working with DevOps on infrastructure automation' in result
    
    def test_update_entity_with_meeting_project(self, updater, sample_project_content):
        """Test updating project entity with meeting data."""
        sections = updater.parse_entity_sections(sample_project_content)
        
        meeting_data = {
            'meeting_id': 'meeting-999',
            'meeting_title': 'Steering Committee Review',
            'date': '2023-11-20',
            'status_update': 'Phase 1 nearing completion',
            'milestones': [
                {'text': 'Launch beta version', 'due_date': '2024-01-15', 'status': 'pending'}
            ],
            'blockers': ['Budget approval pending'],
            'entity_type': 'project'
        }
        
        # This should fail because update_entity_with_meeting doesn't handle entity_type
        updated_content = updater.update_entity_with_meeting(sections, meeting_data)
        
        assert 'meeting-999' in updated_content
        assert 'Steering Committee Review' in updated_content
        assert 'Launch beta version' in updated_content
        assert 'Budget approval pending' in updated_content
    
    def test_update_entity_with_meeting_team(self, updater, sample_team_content):
        """Test updating team entity with meeting data."""
        sections = updater.parse_entity_sections(sample_team_content)
        
        meeting_data = {
            'meeting_id': 'meeting-888',
            'meeting_title': 'Quarterly Review',
            'date': '2023-11-22',
            'achievements': ['100% uptime for Q4'],
            'goals': [
                {'text': 'Hire 3 senior engineers', 'due_date': '2024-03-31'}
            ],
            'team_commitments': ['Mentorship program launch'],
            'entity_type': 'team'
        }
        
        # This should fail because update_entity_with_meeting doesn't handle entity_type
        updated_content = updater.update_entity_with_meeting(sections, meeting_data)
        
        assert 'meeting-888' in updated_content
        assert 'Quarterly Review' in updated_content
        assert '100% uptime for Q4' in updated_content
        assert 'Hire 3 senior engineers' in updated_content
    
    def test_merge_milestones(self, updater):
        """Test merging project milestones avoiding duplicates."""
        existing = [
            {'text': 'Complete Phase 1', 'due_date': '2023-12-31', 'status': 'pending'},
            {'text': 'Deploy to production', 'due_date': '2024-01-15', 'status': 'completed'}
        ]
        
        new = [
            {'text': 'Complete Phase 1', 'due_date': '2023-12-31', 'status': 'pending'},  # Duplicate
            {'text': 'Start Phase 2', 'due_date': '2024-02-01', 'status': 'pending'}  # New
        ]
        
        # This should fail because merge_milestones method doesn't exist
        merged = updater.merge_milestones(existing, new)
        
        assert len(merged) == 3  # No duplicates
        assert any(m['text'] == 'Start Phase 2' for m in merged)
    
    def test_merge_goals(self, updater):
        """Test merging team goals avoiding duplicates."""
        existing = [
            {'text': 'Improve test coverage', 'due_date': '2023-12-31', 'status': 'pending'},
            {'text': 'Reduce tech debt', 'due_date': '2024-03-31', 'status': 'pending'}
        ]
        
        new = [
            {'text': 'Improve test coverage', 'due_date': '2023-12-31'},  # Duplicate
            {'text': 'Implement monitoring', 'due_date': '2024-01-31'}  # New
        ]
        
        # This should fail because merge_goals method doesn't exist
        merged = updater.merge_goals(existing, new)
        
        assert len(merged) == 3
        assert any(g['text'] == 'Implement monitoring' for g in merged)
    
    def test_detect_entity_type(self, updater):
        """Test detecting entity type from meeting data."""
        # Project meeting data
        project_data = {
            'entity_type': 'project',
            'milestones': [{'text': 'Test'}],
            'status_update': 'In progress'
        }
        # This should fail because detect_entity_type method doesn't exist
        assert updater.detect_entity_type(project_data) == 'project'
        
        # Team meeting data
        team_data = {
            'entity_type': 'team',
            'goals': [{'text': 'Test'}],
            'achievements': ['Test achievement']
        }
        assert updater.detect_entity_type(team_data) == 'team'
        
        # Person meeting data (default)
        person_data = {
            'commitments': [{'text': 'Test'}],
            'role': 'Developer'
        }
        assert updater.detect_entity_type(person_data) == 'person'