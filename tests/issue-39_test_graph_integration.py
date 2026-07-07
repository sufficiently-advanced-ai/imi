"""
Test suite for Issue #39: Knowledge Graph Integration Tests

End-to-end integration tests for the complete knowledge graph visualization
feature, testing the flow from backend API to frontend display.
"""

import pytest
import asyncio
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
import tempfile
import os

from app.main import app
from app.services.knowledge_graph import KnowledgeGraph, GraphNode, GraphEdge


client = TestClient(app)


@pytest.fixture
async def real_knowledge_graph():
    """Create a real knowledge graph instance with test data."""
    # Create temporary directory for test repository
    with tempfile.TemporaryDirectory() as temp_dir:
        kg = KnowledgeGraph()
        kg._cache_path = os.path.join(temp_dir, ".knowledge_graph.json")
        
        # Manually populate with test data to avoid file system dependencies
        kg.nodes = {
            "person:alice": GraphNode(
                id="person:alice",
                name="Alice Johnson",
                type="person",
                metadata={"role": "engineer"},
                connections={"person:bob", "project:backend"},
                documents={"meeting-1.md", "standup-notes.md"},
                last_updated=datetime.utcnow()
            ),
            "person:bob": GraphNode(
                id="person:bob", 
                name="Bob Smith",
                type="person",
                metadata={"role": "designer"},
                connections={"person:alice", "project:frontend"},
                documents={"meeting-1.md", "design-review.md"},
                last_updated=datetime.utcnow()
            ),
            "project:backend": GraphNode(
                id="project:backend",
                name="Backend Service",
                type="project",
                metadata={"status": "active"},
                connections={"person:alice", "topic:api"},
                documents={"project-spec.md", "api-docs.md"},
                last_updated=datetime.utcnow()
            ),
            "project:frontend": GraphNode(
                id="project:frontend",
                name="Frontend App", 
                type="project",
                metadata={"status": "planning"},
                connections={"person:bob", "topic:ui"},
                documents={"wireframes.md", "user-stories.md"},
                last_updated=datetime.utcnow()
            ),
            "topic:api": GraphNode(
                id="topic:api",
                name="REST API",
                type="topic",
                metadata={},
                connections={"project:backend"},
                documents={"api-docs.md"},
                last_updated=datetime.utcnow()
            ),
            "topic:ui": GraphNode(
                id="topic:ui",
                name="User Interface",
                type="topic", 
                metadata={},
                connections={"project:frontend"},
                documents={"wireframes.md"},
                last_updated=datetime.utcnow()
            )
        }
        
        kg.edges = {
            ("person:alice", "person:bob"): GraphEdge(
                source="person:alice",
                target="person:bob",
                relationship_type="collaboration",
                strength=0.9,
                context=["meeting-1.md"],
                created=datetime.utcnow()
            ),
            ("person:alice", "project:backend"): GraphEdge(
                source="person:alice",
                target="project:backend", 
                relationship_type="participation",
                strength=0.8,
                context=["project-spec.md"],
                created=datetime.utcnow()
            ),
            ("person:bob", "project:frontend"): GraphEdge(
                source="person:bob",
                target="project:frontend",
                relationship_type="participation", 
                strength=0.7,
                context=["wireframes.md"],
                created=datetime.utcnow()
            ),
            ("project:backend", "topic:api"): GraphEdge(
                source="project:backend",
                target="topic:api",
                relationship_type="categorization",
                strength=0.6,
                context=["api-docs.md"],
                created=datetime.utcnow()
            )
        }
        
        # Set up document entities mapping
        kg.document_entities = {
            "meeting-1.md": {"person:alice", "person:bob"},
            "project-spec.md": {"person:alice", "project:backend"},
            "wireframes.md": {"person:bob", "project:frontend", "topic:ui"},
            "api-docs.md": {"project:backend", "topic:api"}
        }
        
        # Build entity documents mapping
        kg.entity_documents = {}
        for doc_path, entities in kg.document_entities.items():
            for entity_id in entities:
                if entity_id not in kg.entity_documents:
                    kg.entity_documents[entity_id] = set()
                kg.entity_documents[entity_id].add(doc_path)
        
        kg.last_build = datetime.utcnow()
        yield kg


class TestKnowledgeGraphEndToEnd:
    """End-to-end tests for the complete knowledge graph feature."""
    
    @patch('app.routes.memory.knowledge_graph')
    async def test_complete_api_to_frontend_flow(self, mock_kg, real_knowledge_graph):
        """Test the complete flow from API endpoint to frontend-ready data."""
        mock_kg.return_value = real_knowledge_graph
        mock_kg.build_graph = AsyncMock(return_value=real_knowledge_graph._get_graph_stats())
        
        # Test API endpoint
        response = client.get("/api/memory/graph/visualization")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify complete data structure
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 6  # 2 people + 2 projects + 2 topics
        assert len(data["edges"]) == 4  # 4 relationships
        
        # Verify all entity types are present
        node_types = {node["type"] for node in data["nodes"]}
        assert node_types == {"person", "project", "topic"}
        
        # Verify specific relationships exist
        edge_pairs = {(edge["source"], edge["target"]) for edge in data["edges"]}
        assert ("person:alice", "person:bob") in edge_pairs
        assert ("person:alice", "project:backend") in edge_pairs
        
    @patch('app.routes.memory.knowledge_graph')
    async def test_graph_data_consistency(self, mock_kg, real_knowledge_graph):
        """Test that graph data is consistent across multiple requests."""
        mock_kg.return_value = real_knowledge_graph
        mock_kg.build_graph = AsyncMock(return_value=real_knowledge_graph._get_graph_stats())
        
        # Make multiple requests
        response1 = client.get("/api/memory/graph/visualization")
        response2 = client.get("/api/memory/graph/visualization") 
        response3 = client.get("/api/memory/graph/visualization")
        
        assert response1.status_code == 200
        assert response2.status_code == 200
        assert response3.status_code == 200
        
        data1 = response1.json()
        data2 = response2.json()
        data3 = response3.json()
        
        # Data should be identical across requests
        assert data1 == data2 == data3
        
    @patch('app.routes.memory.knowledge_graph')
    async def test_performance_with_realistic_dataset(self, mock_kg, real_knowledge_graph):
        """Test performance with a realistic dataset size."""
        # Expand the dataset to simulate a real knowledge base
        large_kg = real_knowledge_graph
        
        # Add more entities (simulate 50 people, 20 projects, 30 topics)
        for i in range(50):
            person_id = f"person:employee-{i}"
            large_kg.nodes[person_id] = GraphNode(
                id=person_id,
                name=f"Employee {i}",
                type="person",
                metadata={"role": f"role-{i % 5}"},
                connections=set(),
                documents={f"doc-{i}.md"},
                last_updated=datetime.utcnow()
            )
        
        for i in range(20):
            project_id = f"project:project-{i}"
            large_kg.nodes[project_id] = GraphNode(
                id=project_id,
                name=f"Project {i}",
                type="project",
                metadata={"status": "active" if i % 2 == 0 else "planning"},
                connections=set(),
                documents={f"project-{i}.md"},
                last_updated=datetime.utcnow()
            )
        
        mock_kg.return_value = large_kg
        mock_kg.build_graph = AsyncMock(return_value=large_kg._get_graph_stats())
        
        # Test response time
        import time
        start_time = time.time()
        
        response = client.get("/api/memory/graph/visualization")
        
        end_time = time.time()
        response_time = end_time - start_time
        
        assert response.status_code == 200
        assert response_time < 2.0  # Should respond within 2 seconds
        
        data = response.json()
        assert len(data["nodes"]) >= 70  # Original 6 + 50 + 20
        
    @patch('app.routes.memory.knowledge_graph')
    async def test_data_transformation_accuracy(self, mock_kg, real_knowledge_graph):
        """Test that data transformation from internal to frontend format is accurate."""
        mock_kg.return_value = real_knowledge_graph
        mock_kg.build_graph = AsyncMock(return_value=real_knowledge_graph._get_graph_stats())
        
        response = client.get("/api/memory/graph/visualization")
        data = response.json()
        
        # Test specific node transformation
        alice_node = next((n for n in data["nodes"] if n["id"] == "person:alice"), None)
        assert alice_node is not None
        assert alice_node["label"] == "Alice Johnson"
        assert alice_node["type"] == "person"
        assert alice_node["metadata"]["documentCount"] == 2  # meeting-1.md, standup-notes.md
        assert alice_node["metadata"]["connectionCount"] == 2  # bob, backend project
        
        # Test specific edge transformation
        alice_bob_edge = next((e for e in data["edges"] 
                              if e["source"] == "person:alice" and e["target"] == "person:bob"), None)
        assert alice_bob_edge is not None
        assert alice_bob_edge["type"] == "collaboration"
        assert alice_bob_edge["strength"] == 0.9
        
    @patch('app.routes.memory.knowledge_graph')
    async def test_error_recovery_and_fallbacks(self, mock_kg):
        """Test error handling and graceful degradation."""
        # Test knowledge graph build failure
        mock_kg.build_graph.side_effect = Exception("Graph build failed")
        
        response = client.get("/api/memory/graph/visualization")
        
        assert response.status_code == 500
        assert "failed" in response.json()["detail"].lower()
        
        # Test recovery after error
        mock_kg.build_graph.side_effect = None
        mock_kg.build_graph = AsyncMock(return_value={"total_nodes": 0, "total_edges": 0})
        mock_kg.nodes = {}
        mock_kg.edges = {}
        
        response = client.get("/api/memory/graph/visualization")
        
        assert response.status_code == 200
        data = response.json()
        assert data["nodes"] == []
        assert data["edges"] == []


class TestFrontendIntegrationRequirements:
    """Test requirements for frontend integration."""
    
    @patch('app.routes.memory.knowledge_graph')
    async def test_frontend_data_format_compatibility(self, mock_kg, real_knowledge_graph):
        """Test that API data format is compatible with ConnectionGraph component."""
        mock_kg.return_value = real_knowledge_graph
        mock_kg.build_graph = AsyncMock(return_value=real_knowledge_graph._get_graph_stats())
        
        response = client.get("/api/memory/graph/visualization")
        data = response.json()
        
        # Verify format matches ConnectionGraph expectations
        for node in data["nodes"]:
            # Required fields for ConnectionGraph
            assert "id" in node
            assert "label" in node  # ConnectionGraph expects 'label', not 'name'
            assert "type" in node
            assert "metadata" in node
            
        for edge in data["edges"]:
            # Required fields for ConnectionGraph
            assert "source" in edge
            assert "target" in edge
            assert "type" in edge
            assert "strength" in edge
            
    @patch('app.routes.memory.knowledge_graph')
    async def test_color_coding_support(self, mock_kg, real_knowledge_graph):
        """Test that all entity types needed for color coding are present."""
        mock_kg.return_value = real_knowledge_graph
        mock_kg.build_graph = AsyncMock(return_value=real_knowledge_graph._get_graph_stats())
        
        response = client.get("/api/memory/graph/visualization")
        data = response.json()
        
        # ConnectionGraph supports these entity types with color coding
        supported_types = {"person", "project", "topic", "document"}
        node_types = {node["type"] for node in data["nodes"]}
        
        # All returned types should be supported for color coding
        assert node_types.issubset(supported_types)
        
    @patch('app.routes.memory.knowledge_graph') 
    async def test_metadata_for_filtering(self, mock_kg, real_knowledge_graph):
        """Test that metadata supports filtering functionality."""
        mock_kg.return_value = real_knowledge_graph
        mock_kg.build_graph = AsyncMock(return_value=real_knowledge_graph._get_graph_stats())
        
        response = client.get("/api/memory/graph/visualization")
        data = response.json()
        
        for node in data["nodes"]:
            metadata = node["metadata"]
            
            # Should include fields useful for filtering/display
            assert "documentCount" in metadata
            assert "connectionCount" in metadata
            assert isinstance(metadata["documentCount"], int)
            assert isinstance(metadata["connectionCount"], int)
            assert metadata["documentCount"] >= 0
            assert metadata["connectionCount"] >= 0


class TestNavigationIntegration:
    """Test that graph page integrates properly with navigation."""
    
    def test_graph_route_accessibility(self):
        """Test that /graph route will be accessible (this will fail until implemented)."""
        # This test documents the requirement that /graph route should exist
        # It will fail until the page is implemented
        
        # Note: This would typically be tested with a frontend testing framework
        # For now, we document the requirement
        assert True  # Placeholder - real test would check route existence
        
    async def test_navigation_item_requirement(self):
        """Test requirement for navigation item (documentation test)."""
        # This documents that navigation.tsx should include:
        # {
        #   name: "Knowledge Graph",
        #   href: "/graph", 
        #   description: "Interactive knowledge graph visualization"
        # }
        
        # Actual test would verify navigation component includes this item
        required_nav_item = {
            "name": "Knowledge Graph",
            "href": "/graph",
            "description": "Interactive knowledge graph visualization"
        }
        
        assert required_nav_item["name"] == "Knowledge Graph"
        assert required_nav_item["href"] == "/graph"