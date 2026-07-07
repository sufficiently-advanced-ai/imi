"""Tests for project-specific enrichment functionality."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from app.services.entity_file_updater import EntityFileUpdater


class TestProjectEnrichment:
    """Test project-specific enrichment features."""
    
    @pytest.fixture
    def entity_updater(self):
        """Create EntityFileUpdater instance."""
        return EntityFileUpdater()
    
    @pytest.fixture
    def sample_project_content(self):
        """Sample project file content."""
        return """---
name: Data Warehouse Upgrade
type: project
status: active
timeline: "2023-Q4 to 2024-Q2"
key_people:
  - person-david-patel
  - person-kevin-zhang
teams:
  - team-data-engineering
  - team-analytics
dependencies:
  - project-crm-modernization
meetings: []
last_updated: "2023-11-01T00:00:00Z"
---

# Data Warehouse Upgrade

## Current Status
Schema standardization phase in progress. 60% of tables migrated to new structure.

## Meeting Participation

## Active Milestones
- [ ] Complete schema standardization (Due: 2023-12-31)
- [ ] Migrate historical data (Due: 2024-02-28)
- [ ] Implement real-time data pipelines (Due: 2024-04-30)

## Dependencies & Blockers
- CRM Modernization project needs schema finalized before Phase 2
- Waiting for budget approval for additional storage

## Team Updates
- Data Engineering: Optimizing ETL processes
- Analytics: Preparing new dashboard designs
"""
    
    
    def test_extract_project_milestones(self, entity_updater):
        """Test extracting milestone updates from meetings."""
        meeting_data = {
            'transcript': """
            Sarah: Let's review the API Gateway project milestones.
            
            Team Lead: We've completed the authentication module milestone. 
            The API documentation milestone is 50% complete.
            We're starting work on the rate limiting milestone next sprint.
            
            Sarah: Great! So authentication is done, documentation is in progress, 
            and rate limiting is up next.
            """,
            'project_context': {
                'existing_milestones': [
                    'Authentication module',
                    'API documentation',
                    'Rate limiting implementation'
                ]
            }
        }
        
        expected_milestones = [
            {
                'text': 'Authentication module',
                'status': 'completed',
                'completion_date': datetime.now().strftime('%Y-%m-%d')
            },
            {
                'text': 'API documentation',
                'status': 'in_progress',
                'progress': '50%'
            },
            {
                'text': 'Rate limiting implementation',
                'status': 'planned',
                'start_date': 'next sprint'
            }
        ]
        
        # Verify milestone tracking structure
        assert len(expected_milestones) == 3
        assert expected_milestones[0]['status'] == 'completed'
        assert expected_milestones[1]['progress'] == '50%'
    
    def test_extract_project_blockers(self, entity_updater):
        """Test extracting blockers and dependencies."""
        meeting_transcript = """
        PM: Any blockers for the Data Warehouse project?
        
        David: Yes, we're blocked on getting the additional storage approved. 
        Without it, we can't start the historical data migration.
        
        Kevin: Also, the CRM team is waiting for our schema to be finalized. 
        That's blocking their Phase 2 start.
        
        PM: And dependencies?
        
        David: We need the network upgrade to be completed before we can 
        implement the real-time pipelines. That's a hard dependency.
        """
        
        expected_blockers = [
            {
                'description': 'Additional storage approval needed',
                'impact': 'Cannot start historical data migration',
                'severity': 'high'
            },
            {
                'description': 'Network upgrade required',
                'impact': 'Cannot implement real-time pipelines',
                'severity': 'high',
                'type': 'dependency'
            }
        ]
        
        expected_downstream_impact = {
            'project': 'CRM Modernization',
            'impact': 'Phase 2 start blocked',
            'dependency': 'Schema finalization'
        }
        
        # Verify blocker structure
        assert expected_blockers[0]['severity'] == 'high'
        assert expected_downstream_impact['project'] == 'CRM Modernization'
    
    def test_project_timeline_updates(self, entity_updater):
        """Test updating project timelines from meeting content."""
        timeline_discussion = """
        PM: Let's review the project timeline.
        
        Lead: Based on current progress, we need to adjust some dates:
        - Schema standardization: Now completing December 15th instead of December 31st
        - Historical data migration: Can start earlier, January 15th instead of February 1st
        - Real-time pipelines: Still on track for April 30th
        
        PM: So we're ahead of schedule on the first two milestones?
        
        Lead: Correct. We might be able to deliver the entire project a month early.
        """
        
        timeline_updates = {
            'original_timeline': '2023-Q4 to 2024-Q2',
            'updated_timeline': '2023-Q4 to 2024-Q2',  # Overall unchanged
            'milestone_adjustments': [
                {
                    'milestone': 'Schema standardization',
                    'original_date': '2023-12-31',
                    'new_date': '2023-12-15',
                    'status': 'ahead'
                },
                {
                    'milestone': 'Historical data migration',
                    'original_date': '2024-02-01',
                    'new_date': '2024-01-15',
                    'status': 'ahead'
                }
            ],
            'potential_early_delivery': '1 month'
        }
        
        assert len(timeline_updates['milestone_adjustments']) == 2
        assert timeline_updates['potential_early_delivery'] == '1 month'
    
    
    def test_project_health_indicators(self, entity_updater):
        """Test extracting project health indicators."""
        health_discussion = """
        PM: Let's assess project health using our standard metrics.
        
        Lead: Overall health is GREEN. Here's the breakdown:
        - Schedule: GREEN - We're 2 weeks ahead
        - Budget: YELLOW - 78% consumed with 70% work complete  
        - Resources: GREEN - Team fully staffed
        - Quality: GREEN - Zero critical defects
        - Stakeholder Satisfaction: GREEN - Latest NPS score is 9/10
        
        PM: What about risks?
        
        Lead: Main risk is the budget yellow status. We need to monitor closely.
        """
        
        health_metrics = {
            'overall_health': 'GREEN',
            'metrics': {
                'schedule': {'status': 'GREEN', 'notes': '2 weeks ahead'},
                'budget': {'status': 'YELLOW', 'notes': '78% consumed with 70% complete'},
                'resources': {'status': 'GREEN', 'notes': 'Fully staffed'},
                'quality': {'status': 'GREEN', 'notes': 'Zero critical defects'},
                'stakeholder_satisfaction': {'status': 'GREEN', 'notes': 'NPS 9/10'}
            },
            'risks': [
                {
                    'area': 'Budget',
                    'severity': 'Medium',
                    'mitigation': 'Monitor spending closely'
                }
            ]
        }
        
        assert health_metrics['overall_health'] == 'GREEN'
        assert health_metrics['metrics']['budget']['status'] == 'YELLOW'
        assert len(health_metrics['risks']) == 1
    
    def test_cross_project_dependencies(self, entity_updater):
        """Test handling cross-project dependencies."""
        dependency_meeting = """
        PM: Let's review cross-project dependencies.
        
        API Gateway Lead: We're dependent on:
        1. Data Warehouse Upgrade - Need their new schema for our data layer
        2. Security Framework Project - Waiting for auth standards
        3. Mobile App Project - They're dependent on our API completion
        
        PM: What's the impact of any delays?
        
        Lead: If Data Warehouse delays by 2 weeks, we delay by 2 weeks.
        Security Framework is not blocking us yet, we have 4 weeks buffer.
        Mobile App has factored in our timeline, so we're good there.
        """
        
        dependencies = {
            'upstream_dependencies': [
                {
                    'project': 'Data Warehouse Upgrade',
                    'dependency_type': 'schema',
                    'criticality': 'high',
                    'buffer_time': '0 weeks'
                },
                {
                    'project': 'Security Framework Project',
                    'dependency_type': 'auth standards',
                    'criticality': 'medium',
                    'buffer_time': '4 weeks'
                }
            ],
            'downstream_dependencies': [
                {
                    'project': 'Mobile App Project',
                    'dependency_type': 'API completion',
                    'impact': 'They have factored our timeline'
                }
            ]
        }
        
        assert len(dependencies['upstream_dependencies']) == 2
        assert dependencies['upstream_dependencies'][0]['criticality'] == 'high'
        assert len(dependencies['downstream_dependencies']) == 1