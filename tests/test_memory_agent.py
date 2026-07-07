"""
Tests for the Organizational Memory Agent functionality.

Tests the core capabilities outlined in GitHub issue #5:
- Perfect recall and connection of organizational knowledge
- Auto-organization by topic, project, person
- Context surfacing during work
- Connection of disparate information
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from app.agents.memory_agent import MemoryAgent, memory_agent
from app.services.knowledge_graph import KnowledgeGraph, GraphNode, GraphEdge


class TestKnowledgeGraph:
    """Test the knowledge graph foundation."""
    
    def test_graph_node_creation(self):
        """Test creating graph nodes for different entity types."""
        # Test person node
        person_node = GraphNode(
            id="person:john-doe",
            name="John Doe",
            type="person",
            metadata={"role": "Engineer"}
        )
        
        assert person_node.id == "person:john-doe"
        assert person_node.name == "John Doe"
        assert person_node.type == "person"
        assert person_node.metadata["role"] == "Engineer"
        assert len(person_node.connections) == 0
        assert len(person_node.documents) == 0
    
    def test_graph_edge_creation(self):
        """Test creating relationships between entities."""
        edge = GraphEdge(
            source="person:john-doe",
            target="project:alpha",
            relationship_type="participation",
            strength=0.8,
            context=["meeting-notes.md", "project-plan.md"]
        )
        
        assert edge.source == "person:john-doe"
        assert edge.target == "project:alpha"
        assert edge.relationship_type == "participation"
        assert edge.strength == 0.8
        assert len(edge.context) == 2
    


class TestMemoryAgent:
    """Test the central memory agent functionality."""
    
    @pytest.fixture
    def memory_agent_instance(self):
        """Get the memory agent singleton instance for testing."""
        return memory_agent
    
    
    
    
    


class TestMemoryAPI:
    """Test the memory API endpoints."""
    
    @pytest.mark.asyncio
    async def test_memory_query_endpoint_structure(self):
        """Test that memory query endpoint returns proper structure."""
        from app.routes.memory import MemoryQueryRequest, MemoryQueryResponse
        
        # Test request model
        request = MemoryQueryRequest(
            question="What is Project Alpha status?",
            context_hint="quarterly review",
            max_documents=5
        )
        
        assert request.question == "What is Project Alpha status?"
        assert request.context_hint == "quarterly review"
        assert request.max_documents == 5
        
        # Test response model structure
        response_data = {
            "answer": "Project Alpha is in development",
            "confidence": "high",
            "sources": ["doc1.md", "doc2.md"],
            "related_entities": [],
            "connection_analysis": {},
            "query_metadata": {"entities_found": 2}
        }
        
        response = MemoryQueryResponse(**response_data)
        assert response.answer == "Project Alpha is in development"
        assert response.confidence == "high"
        assert len(response.sources) == 2


class TestPerformanceMetrics:
    """Test performance requirements from GitHub issue #5."""
    
    
    def test_connection_density_calculation(self):
        """Test connection density score calculation for >80% target."""
        kg = KnowledgeGraph()
        
        # Add test nodes
        kg.nodes["person:a"] = GraphNode("person:a", "Person A", "person")
        kg.nodes["person:b"] = GraphNode("person:b", "Person B", "person")
        kg.nodes["project:x"] = GraphNode("project:x", "Project X", "project")
        
        # Add edges to increase density
        kg.edges[("person:a", "person:b")] = GraphEdge("person:a", "person:b", "collaboration", 0.8)
        kg.edges[("person:a", "project:x")] = GraphEdge("person:a", "project:x", "participation", 0.9)
        kg.edges[("person:b", "project:x")] = GraphEdge("person:b", "project:x", "participation", 0.7)
        
        stats = kg._get_graph_stats()
        
        # With 3 nodes and 3 edges, density should be 1.0 (100%)
        # Formula: edges / (nodes * (nodes-1) / 2) = 3 / (3 * 2 / 2) = 3/3 = 1.0
        assert stats["connection_density"] == 1.0
        assert stats["connection_density"] > 0.8  # Meets requirement


if __name__ == "__main__":
    pytest.main([__file__])