"""Tests for team-specific enrichment functionality."""

import pytest
from datetime import datetime
from app.services.entity_file_updater import EntityFileUpdater


class TestTeamEnrichment:
    """Test team-specific enrichment features."""
    
    @pytest.fixture
    def entity_updater(self):
        """Create EntityFileUpdater instance."""
        return EntityFileUpdater()
    
    @pytest.fixture
    def sample_team_content(self):
        """Sample team file content."""
        return """---
name: Platform Engineering
type: team
department: Technology
lead: person-sarah-johnson
members:
  - person-alex-chen
  - person-david-patel
  - person-kevin-zhang
  - person-lisa-thompson
focus_areas:
  - Cloud infrastructure
  - DevOps automation
  - Platform reliability
meetings: []
last_updated: "2023-11-01T00:00:00Z"
---

# Platform Engineering

## Current Focus
Building scalable cloud infrastructure and improving platform reliability.

## Meeting Participation

## Active Goals
- [ ] Achieve 99.99% uptime (Due: 2024-Q1)
- [ ] Reduce deployment time to <5 minutes (Due: 2023-12-31)
- [ ] Implement full observability stack (Due: 2024-02-28)

## Team Achievements
- Automated 90% of deployment processes
- Reduced incident response time by 60%
- Zero security breaches in 2023

## Cross-Team Collaborations
- Supporting Product teams with infrastructure needs
- Working with Security on compliance automation
- Partnering with Data team on pipeline optimization
"""
    
    def test_extract_team_achievements(self, entity_updater):
        """Test extracting team achievements from meetings."""
        meeting_content = """
        Team Lead: Let's celebrate our achievements this sprint.
        
        Sarah: The Platform Engineering team hit several milestones:
        - We achieved 99.97% uptime this month, nearly reaching our goal
        - Deployment time is now down to 4 minutes - we beat our target!
        - The new monitoring dashboard is live and already caught 3 potential issues
        
        Team Member: We also onboarded two new team members successfully and they're 
        already contributing to the codebase.
        
        Sarah: Great point! Our mentorship program is really working well.
        """
        
        expected_achievements = [
            {
                'achievement': 'Achieved 99.97% uptime',
                'metric': '99.97%',
                'target_progress': 'Nearly reached 99.99% goal'
            },
            {
                'achievement': 'Reduced deployment time to 4 minutes',
                'metric': '4 minutes',
                'target_progress': 'Beat target of <5 minutes'
            },
            {
                'achievement': 'Monitoring dashboard catching issues proactively',
                'impact': '3 potential issues prevented'
            },
            {
                'achievement': 'Successfully onboarded 2 new team members',
                'impact': 'Already contributing to codebase'
            }
        ]
        
        assert len(expected_achievements) == 4
        assert expected_achievements[1]['target_progress'] == 'Beat target of <5 minutes'
    
    def test_extract_team_goals(self, entity_updater):
        """Test extracting team goals and objectives."""
        goal_setting_meeting = """
        Manager: Let's set our Q1 2024 goals.
        
        Sarah: For Platform Engineering, I propose:
        1. Implement zero-downtime deployments across all services
        2. Achieve ISO 27001 compliance certification
        3. Reduce cloud costs by 20% through optimization
        4. Launch internal developer platform (IDP) beta
        
        Team: Timeline?
        
        Sarah: Zero-downtime by end of January, ISO cert by March, 
        cost reduction throughout Q1, and IDP beta by February 15th.
        
        Manager: Approved. Make sure to track progress weekly.
        """
        
        expected_goals = [
            {
                'text': 'Implement zero-downtime deployments',
                'due_date': '2024-01-31',
                'priority': 'high',
                'success_criteria': 'All services support zero-downtime deployment'
            },
            {
                'text': 'Achieve ISO 27001 compliance certification',
                'due_date': '2024-03-31',
                'priority': 'high',
                'success_criteria': 'Pass certification audit'
            },
            {
                'text': 'Reduce cloud costs by 20%',
                'due_date': '2024-03-31',
                'priority': 'medium',
                'success_criteria': '20% reduction from Q4 2023 baseline'
            },
            {
                'text': 'Launch internal developer platform beta',
                'due_date': '2024-02-15',
                'priority': 'high',
                'success_criteria': 'Beta available to all engineering teams'
            }
        ]
        
        assert len(expected_goals) == 4
        assert all('due_date' in goal for goal in expected_goals)
    
    def test_extract_team_commitments(self, entity_updater):
        """Test extracting team commitments and agreements."""
        commitment_discussion = """
        Sarah: Let's review our team commitments and working agreements.
        
        Team consensus on commitments:
        - 24-hour SLA for production incident response
        - Weekly architecture review sessions every Wednesday
        - Pair programming for all critical system changes
        - Monthly knowledge sharing presentations
        - Quarterly hackathons for innovation
        
        Also, we're committing to:
        - Supporting the Data team's migration next month
        - Leading the company-wide DevOps training initiative
        - Maintaining on-call rotation with no gaps
        """
        
        expected_commitments = {
            'operational_commitments': [
                '24-hour SLA for production incidents',
                'Weekly architecture reviews on Wednesdays',
                'Pair programming for critical changes',
                'No gaps in on-call rotation'
            ],
            'cultural_commitments': [
                'Monthly knowledge sharing presentations',
                'Quarterly hackathons'
            ],
            'cross_team_commitments': [
                'Support Data team migration',
                'Lead DevOps training initiative'
            ]
        }
        
        total_commitments = (
            len(expected_commitments['operational_commitments']) +
            len(expected_commitments['cultural_commitments']) +
            len(expected_commitments['cross_team_commitments'])
        )
        assert total_commitments == 8
    
    def test_extract_cross_team_collaboration(self, entity_updater):
        """Test extracting cross-team collaboration details."""
        collaboration_meeting = """
        Facilitator: Let's discuss cross-team collaborations.
        
        Platform Engineering updates:
        
        With Security Team:
        - Joint effort on automated compliance scanning
        - They're using our deployment pipeline for security tools
        - Weekly sync meetings established
        
        With Data Team:
        - We're providing Kubernetes expertise for their new platform
        - They're helping us optimize data storage costs
        - Shared on-call for data pipeline incidents
        
        With Product Teams:
        - Embedded SRE model working well with Mobile team
        - Frontend team using our new CI/CD templates
        - API team collaborating on service mesh implementation
        """
        
        expected_collaborations = [
            {
                'team': 'Security Team',
                'initiatives': [
                    'Automated compliance scanning',
                    'Security tools deployment pipeline',
                    'Weekly sync meetings'
                ],
                'type': 'technical partnership'
            },
            {
                'team': 'Data Team',
                'initiatives': [
                    'Kubernetes platform expertise sharing',
                    'Data storage cost optimization',
                    'Shared on-call rotation'
                ],
                'type': 'mutual support'
            },
            {
                'team': 'Product Teams',
                'initiatives': [
                    'Embedded SRE with Mobile team',
                    'CI/CD templates for Frontend team',
                    'Service mesh with API team'
                ],
                'type': 'service delivery'
            }
        ]
        
        assert len(expected_collaborations) == 3
        assert expected_collaborations[0]['team'] == 'Security Team'
    
    def test_team_performance_metrics(self, entity_updater):
        """Test extracting team performance metrics."""
        metrics_review = """
        Manager: Let's review Platform Engineering's Q4 performance metrics.
        
        Sarah: Here are our key metrics:
        - Velocity: 85 story points per sprint (up from 72)
        - Cycle time: 3.2 days average (down from 5.1 days)
        - Deployment frequency: 47 per week (up from 31)
        - MTTR: 18 minutes (down from 45 minutes)
        - Team satisfaction: 8.5/10 (up from 7.8)
        - Bug escape rate: 0.3% (down from 1.2%)
        
        Manager: Excellent improvements across the board!
        
        Sarah: Yes, and our learning velocity is high - team completed 
        15 training courses and 3 certifications this quarter.
        """
        
        performance_metrics = {
            'velocity': {
                'current': 85,
                'previous': 72,
                'unit': 'story points/sprint',
                'trend': 'improving'
            },
            'cycle_time': {
                'current': 3.2,
                'previous': 5.1,
                'unit': 'days',
                'trend': 'improving'
            },
            'deployment_frequency': {
                'current': 47,
                'previous': 31,
                'unit': 'per week',
                'trend': 'improving'
            },
            'mttr': {
                'current': 18,
                'previous': 45,
                'unit': 'minutes',
                'trend': 'improving'
            },
            'team_satisfaction': {
                'current': 8.5,
                'previous': 7.8,
                'unit': 'out of 10',
                'trend': 'improving'
            },
            'learning_metrics': {
                'courses_completed': 15,
                'certifications': 3,
                'period': 'Q4'
            }
        }
        
        assert performance_metrics['velocity']['current'] == 85
        assert performance_metrics['mttr']['trend'] == 'improving'
    
    def test_format_team_meeting_section_complete(self, entity_updater):
        """Test complete team meeting section formatting."""
        meeting_data = {
            'meeting_id': 'meeting-team-complete',
            'meeting_title': 'Sprint Retrospective',
            'date': '2024-01-12',
            'achievements': [
                'Completed migration to Kubernetes 1.28',
                'Reduced average build time by 40%',
                'Zero downtime during Black Friday'
            ],
            'goals': [
                {
                    'text': 'Implement automated rollback system',
                    'due_date': '2024-02-28',
                    'assigned_to': 'Platform team'
                },
                {
                    'text': 'Achieve SOC2 compliance',
                    'due_date': '2024-06-30',
                    'assigned_to': 'Security subteam'
                }
            ],
            'team_commitments': [
                'Daily standups moving to 9:30 AM',
                'Code review SLA reduced to 4 hours',
                'Monthly team building activities'
            ],
            'performance_metrics': {
                'sprint_velocity': '92 points',
                'sprint_completion': '95%',
                'team_happiness': '8.7/10'
            },
            'collaborations': [
                'Started embedded SRE program with Payment team',
                'Joint training session with QA on test automation'
            ]
        }
        
        formatted = entity_updater.format_team_meeting_section(meeting_data)
        
        # Verify all sections included
        assert '### Sprint Retrospective (2024-01-12)' in formatted
        assert '**Achievements**:' in formatted
        assert '- Completed migration to Kubernetes 1.28' in formatted
        assert '**Goals Set**:' in formatted
        assert '- Implement automated rollback system (Due: 2024-02-28)' in formatted
        assert '**Team Commitments**:' in formatted
        assert '**Performance Metrics**:' in formatted
        assert 'Sprint Velocity: 92 points' in formatted
        assert '**Cross-Team Collaborations**:' in formatted
    
    def test_team_structure_changes(self, entity_updater):
        """Test tracking team structure and membership changes."""
        structure_meeting = """
        HR: Let's discuss the Platform Engineering team changes.
        
        Sarah: We have several updates:
        - Lisa Thompson promoted to Senior Engineer
        - Two new hires joining next Monday: Bob Smith and Carol White
        - David Patel transitioning to Tech Lead role
        - Creating two sub-teams: Infrastructure and Developer Experience
        
        HR: And reporting structure?
        
        Sarah: I'll continue as team lead. David will lead Infrastructure, 
        and Lisa will lead Developer Experience. Both report to me.
        """
        
        structure_updates = {
            'promotions': [
                {
                    'member': 'Lisa Thompson',
                    'new_role': 'Senior Engineer',
                    'previous_role': 'Engineer'
                }
            ],
            'new_members': [
                {
                    'name': 'Bob Smith',
                    'start_date': 'next Monday',
                    'role': 'Engineer'
                },
                {
                    'name': 'Carol White',
                    'start_date': 'next Monday',
                    'role': 'Engineer'
                }
            ],
            'role_changes': [
                {
                    'member': 'David Patel',
                    'new_role': 'Tech Lead - Infrastructure',
                    'previous_role': 'Senior Engineer'
                }
            ],
            'organizational_changes': [
                {
                    'change': 'Team split into two sub-teams',
                    'sub_teams': ['Infrastructure', 'Developer Experience'],
                    'leads': {
                        'Infrastructure': 'David Patel',
                        'Developer Experience': 'Lisa Thompson'
                    }
                }
            ]
        }
        
        assert len(structure_updates['new_members']) == 2
        assert structure_updates['organizational_changes'][0]['sub_teams'][0] == 'Infrastructure'
    
    def test_team_learning_and_development(self, entity_updater):
        """Test tracking team learning initiatives."""
        learning_discussion = """
        Sarah: Let's review our Q1 learning and development plan.
        
        Team Learning Initiatives:
        1. Kubernetes Advanced - 3 team members getting CKA certified
        2. AWS Solutions Architect - 2 members pursuing certification  
        3. Team-wide GraphQL workshop scheduled for January 25th
        4. Monthly "Lunch and Learn" series starting February
        
        Individual Development:
        - Alex: Focusing on distributed systems design
        - Lisa: Learning Go for backend development
        - Kevin: Security certification track
        
        Budget allocated: $15,000 for Q1 training and conferences
        """
        
        learning_plan = {
            'team_initiatives': [
                {
                    'topic': 'Kubernetes Advanced',
                    'format': 'Certification',
                    'participants': 3,
                    'target': 'CKA certification'
                },
                {
                    'topic': 'AWS Solutions Architect',
                    'format': 'Certification',
                    'participants': 2
                },
                {
                    'topic': 'GraphQL',
                    'format': 'Workshop',
                    'date': '2024-01-25'
                },
                {
                    'topic': 'Various topics',
                    'format': 'Lunch and Learn series',
                    'frequency': 'Monthly'
                }
            ],
            'individual_development': [
                {'member': 'Alex', 'focus': 'Distributed systems design'},
                {'member': 'Lisa', 'focus': 'Go programming language'},
                {'member': 'Kevin', 'focus': 'Security certification'}
            ],
            'budget': {
                'amount': 15000,
                'period': 'Q1',
                'allocation': 'Training and conferences'
            }
        }
        
        assert len(learning_plan['team_initiatives']) == 4
        assert learning_plan['budget']['amount'] == 15000