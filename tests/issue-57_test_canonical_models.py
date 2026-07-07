"""Tests for canonical entity models - Issue #57"""

import pytest
from datetime import datetime
from typing import List

from app.models import CanonicalPerson, CanonicalProject, CanonicalTeam


class TestCanonicalPerson:
    """Test CanonicalPerson model functionality"""
    
    def test_person_creation_with_required_fields(self):
        """Test creating a canonical person with required fields"""
        person = CanonicalPerson(
            id="person-john-smith",
            canonical_name="John Smith",
            aliases=["J. Smith", "John A. Smith"],
            titles=["VP Engineering"],
            confidence=0.95,
            created_at=datetime.utcnow(),
            last_seen=datetime.utcnow()
        )
        
        assert person.id == "person-john-smith"
        assert person.canonical_name == "John Smith"
        assert len(person.aliases) == 2
        assert "VP Engineering" in person.titles
        assert person.confidence == 0.95
    
    def test_person_validation_missing_fields(self):
        """Test validation errors for missing required fields"""
        with pytest.raises(ValueError):
            CanonicalPerson(
                canonical_name="John Smith",
                # Missing id
            )
    
    def test_add_alias(self):
        """Test adding new alias to person"""
        person = CanonicalPerson(
            id="person-jane-doe",
            canonical_name="Jane Doe",
            aliases=["J. Doe"]
        )
        
        person.add_alias("Jane D.")
        assert "Jane D." in person.aliases
        assert len(person.aliases) == 2
        
        # Test duplicate alias not added
        person.add_alias("J. Doe")
        assert len(person.aliases) == 2
    
    def test_remove_alias(self):
        """Test removing alias from person"""
        person = CanonicalPerson(
            id="person-bob-wilson",
            canonical_name="Bob Wilson",
            aliases=["B. Wilson", "Robert Wilson", "Bob W."]
        )
        
        person.remove_alias("Bob W.")
        assert "Bob W." not in person.aliases
        assert len(person.aliases) == 2
    
    def test_has_alias(self):
        """Test checking if person has specific alias"""
        person = CanonicalPerson(
            id="person-alice-johnson",
            canonical_name="Alice Johnson",
            aliases=["A. Johnson", "Alice J."]
        )
        
        assert person.has_alias("A. Johnson")
        assert person.has_alias("alice j.")  # Case insensitive
        assert not person.has_alias("A. Smith")
    
    def test_update_last_seen(self):
        """Test updating last seen timestamp"""
        initial_time = datetime(2024, 1, 1)
        person = CanonicalPerson(
            id="person-mike-brown",
            canonical_name="Mike Brown",
            last_seen=initial_time
        )
        
        person.update_last_seen()
        assert person.last_seen > initial_time
    
    def test_person_serialization(self):
        """Test serializing person to dict"""
        person = CanonicalPerson(
            id="person-sarah-davis",
            canonical_name="Sarah Davis",
            aliases=["S. Davis"],
            titles=["Director", "Head of Sales"],
            confidence=0.9
        )
        
        data = person.model_dump()
        assert data["id"] == "person-sarah-davis"
        assert data["canonical_name"] == "Sarah Davis"
        assert "S. Davis" in data["aliases"]
        assert len(data["titles"]) == 2
        
        # Test deserialization
        person2 = CanonicalPerson(**data)
        assert person2.id == person.id
        assert person2.canonical_name == person.canonical_name


class TestCanonicalProject:
    """Test CanonicalProject model functionality"""
    
    def test_project_creation(self):
        """Test creating a canonical project"""
        project = CanonicalProject(
            id="project-crm-modernization",
            canonical_name="CRM Modernization",
            aliases=["CRM Upgrade", "Customer System Update"],
            status="active",
            teams=["team-engineering", "team-sales"],
            confidence=0.85
        )
        
        assert project.id == "project-crm-modernization"
        assert project.status == "active"
        assert "team-engineering" in project.teams
        assert len(project.aliases) == 2
    
    def test_project_status_validation(self):
        """Test project status validation"""
        valid_statuses = ["planning", "active", "completed", "on-hold", "cancelled"]
        
        for status in valid_statuses:
            project = CanonicalProject(
                id=f"project-test-{status}",
                canonical_name="Test Project",
                status=status
            )
            assert project.status == status
        
        # Test invalid status
        with pytest.raises(ValueError):
            CanonicalProject(
                id="project-invalid",
                canonical_name="Invalid Project",
                status="invalid-status"
            )
    
    def test_add_team_association(self):
        """Test adding team to project"""
        project = CanonicalProject(
            id="project-data-migration",
            canonical_name="Data Migration",
            teams=["team-it"]
        )
        
        project.add_team("team-data-science")
        assert "team-data-science" in project.teams
        assert len(project.teams) == 2
        
        # Test duplicate team not added
        project.add_team("team-it")
        assert len(project.teams) == 2


class TestCanonicalTeam:
    """Test CanonicalTeam model functionality"""
    
    def test_team_creation(self):
        """Test creating a canonical team"""
        team = CanonicalTeam(
            id="team-engineering",
            canonical_name="Engineering",
            aliases=["Eng", "Engineering Team"],
            department="Technology",
            division="Product",
            parent_team="team-technology",
            members=["person-john-smith", "person-jane-doe"],
            confidence=0.92
        )
        
        assert team.id == "team-engineering"
        assert team.department == "Technology"
        assert team.division == "Product"
        assert team.parent_team == "team-technology"
        assert len(team.members) == 2
    
    def test_team_hierarchy(self):
        """Test team hierarchy relationships"""
        parent_team = CanonicalTeam(
            id="team-technology",
            canonical_name="Technology",
            department="Technology"
        )
        
        child_team = CanonicalTeam(
            id="team-frontend",
            canonical_name="Frontend",
            parent_team=parent_team.id,
            department="Technology"
        )
        
        assert child_team.parent_team == "team-technology"
        assert child_team.is_child_of(parent_team.id)
    
    def test_add_member(self):
        """Test adding member to team"""
        team = CanonicalTeam(
            id="team-marketing",
            canonical_name="Marketing",
            members=["person-alice-johnson"]
        )
        
        team.add_member("person-bob-wilson")
        assert "person-bob-wilson" in team.members
        assert len(team.members) == 2
        
        # Test duplicate member not added
        team.add_member("person-alice-johnson")
        assert len(team.members) == 2
    
    def test_remove_member(self):
        """Test removing member from team"""
        team = CanonicalTeam(
            id="team-sales",
            canonical_name="Sales",
            members=["person-mike-brown", "person-sarah-davis", "person-tom-white"]
        )
        
        team.remove_member("person-sarah-davis")
        assert "person-sarah-davis" not in team.members
        assert len(team.members) == 2