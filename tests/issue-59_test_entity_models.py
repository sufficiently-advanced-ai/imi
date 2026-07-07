"""Tests for enhanced entity models with relationships - Issue #59"""

import pytest
from datetime import datetime
from typing import List, Optional
from pydantic import ValidationError

from app.models import CanonicalEntity, CanonicalPerson, CanonicalProject, CanonicalTeam
from app.models import EnrichedEntityRelationship as EntityRelationship, EnrichedEntity, OrganizationalContext, RelationshipType


class TestEntityRelationshipModel:
    """Test the EntityRelationship model"""
    
    def test_valid_relationship_creation(self):
        """Test creating a valid entity relationship"""
        relationship = EntityRelationship(
            target_entity_id="person-jane-smith",
            relationship_type=RelationshipType.REPORTS_TO,
            strength=0.9,
            confidence=0.85,
            evidence_documents=["meetings/2024-01-15.md", "meetings/2024-02-01.md"]
        )
        
        assert relationship.target_entity_id == "person-jane-smith"
        assert relationship.relationship_type == RelationshipType.REPORTS_TO
        assert relationship.strength == 0.9
        assert relationship.confidence == 0.85
        assert len(relationship.evidence_documents) == 2
    
    def test_relationship_type_validation(self):
        """Test that only valid relationship types are accepted"""
        with pytest.raises(ValidationError):
            EntityRelationship(
                target_entity_id="person-john-doe",
                relationship_type="invalid_type",  # Should fail
                strength=0.8,
                confidence=0.7,
                evidence_documents=[]
            )
    
    def test_strength_confidence_bounds(self):
        """Test that strength and confidence are bounded 0-1"""
        with pytest.raises(ValidationError):
            EntityRelationship(
                target_entity_id="person-john-doe",
                relationship_type=RelationshipType.COLLABORATES_WITH,
                strength=1.5,  # Should fail
                confidence=0.8,
                evidence_documents=[]
            )
        
        with pytest.raises(ValidationError):
            EntityRelationship(
                target_entity_id="person-john-doe",
                relationship_type=RelationshipType.COLLABORATES_WITH,
                strength=0.8,
                confidence=-0.1,  # Should fail
                evidence_documents=[]
            )
    
    def test_bidirectional_relationship(self):
        """Test creating bidirectional relationships"""
        rel1 = EntityRelationship(
            target_entity_id="person-jane-smith",
            relationship_type=RelationshipType.REPORTS_TO,
            strength=0.9,
            confidence=0.85,
            evidence_documents=["meetings/2024-01-15.md"]
        )
        
        # The inverse relationship
        rel2 = rel1.create_inverse(source_entity_id="person-john-doe")
        
        assert rel2.target_entity_id == "person-john-doe"
        assert rel2.relationship_type == RelationshipType.MANAGES
        assert rel2.strength == rel1.strength
        assert rel2.confidence == rel1.confidence
        assert rel2.evidence_documents == rel1.evidence_documents


class TestOrganizationalContext:
    """Test the OrganizationalContext model"""
    
    def test_organizational_context_creation(self):
        """Test creating organizational context"""
        context = OrganizationalContext(
            hierarchy_level=3,
            department="Engineering",
            division="Product Development",
            reporting_chain=["person-ceo", "person-cto", "person-eng-director"],
            peer_entities=["person-peer1", "person-peer2"],
            subordinate_entities=["person-report1", "person-report2"]
        )
        
        assert context.hierarchy_level == 3
        assert context.department == "Engineering"
        assert len(context.reporting_chain) == 3
        assert len(context.peer_entities) == 2
        assert len(context.subordinate_entities) == 2
    
    def test_hierarchy_depth_calculation(self):
        """Test calculating organizational depth"""
        context = OrganizationalContext(
            hierarchy_level=2,
            department="Sales",
            reporting_chain=["person-ceo", "person-vp-sales"]
        )
        
        assert context.get_organizational_depth() == 2
        assert context.is_leadership_role() is True  # Level 2 = leadership


class TestEnrichedEntity:
    """Test the EnrichedEntity model"""
    
    
    


class TestRelationshipTypes:
    """Test relationship type definitions and properties"""
    
    
