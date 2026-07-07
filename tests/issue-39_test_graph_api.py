"""
Test suite for Issue #39: Knowledge Graph Visualization API endpoints

Tests the new /api/memory/graph/visualization endpoint that serves
real knowledge graph data to the frontend.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from app.main import app
from app.services.knowledge_graph import GraphNode, GraphEdge


client = TestClient(app)


@pytest.fixture
def mock_knowledge_graph():
    """Mock knowledge graph with sample data for testing."""
    mock_kg = MagicMock()
    
    # Sample nodes
    mock_kg.nodes = {
        "person-john-doe": GraphNode(
            id="person-john-doe",
            name="John Doe",
            type="person",
            metadata={"role": "developer"},
            connections={"project-alpha", "person-jane-smith"},
            documents={"meeting-1.md", "project-notes.md"},
            last_updated=datetime.utcnow()
        ),
        "person-jane-smith": GraphNode(
            id="person-jane-smith", 
            name="Jane Smith",
            type="person",
            metadata={"role": "manager"},
            connections={"person-john-doe", "project-alpha"},
            documents={"meeting-1.md"},
            last_updated=datetime.utcnow()
        ),
        "project-alpha": GraphNode(
            id="project-alpha",
            name="Project Alpha",
            type="project", 
            metadata={"status": "active"},
            connections={"person-john-doe", "person-jane-smith"},
            documents={"project-notes.md"},
            last_updated=datetime.utcnow()
        ),
        "topic-ai": GraphNode(
            id="topic-ai",
            name="Artificial Intelligence", 
            type="topic",
            metadata={},
            connections=set(),
            documents={"ai-research.md"},
            last_updated=datetime.utcnow()
        )
    }
    
    # Sample edges
    mock_kg.edges = {
        ("person-john-doe", "person-jane-smith"): GraphEdge(
            source="person-john-doe",
            target="person-jane-smith", 
            relationship_type="collaboration",
            strength=0.8,
            context=["meeting-1.md"],
            created=datetime.utcnow()
        ),
        ("person-john-doe", "project-alpha"): GraphEdge(
            source="person-john-doe",
            target="project-alpha",
            relationship_type="participation", 
            strength=0.9,
            context=["project-notes.md"],
            created=datetime.utcnow()
        )
    }
    
    return mock_kg


class TestGraphVisualizationAPI:
    """Test the new graph visualization API endpoint."""
    
    @patch('app.routes.memory.knowledge_graph')
    async def test_get_graph_visualization_success(self, mock_kg, mock_knowledge_graph):
        """Test successful retrieval of graph visualization data."""
        mock_kg.return_value = mock_knowledge_graph
        
        response = client.get("/api/memory/graph/visualization")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify response structure
        assert "nodes" in data
        assert "edges" in data
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)
        
        # Verify node structure
        for node in data["nodes"]:
            assert "id" in node
            assert "label" in node
            assert "type" in node
            assert "metadata" in node
            assert node["type"] in ["person", "project", "topic", "document"]
        
        # Verify edge structure  
        for edge in data["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert "type" in edge
            assert "strength" in edge
            assert 0 <= edge["strength"] <= 1
            
    @patch('app.routes.memory.knowledge_graph') 
    async def test_graph_visualization_node_metadata(self, mock_kg, mock_knowledge_graph):
        """Test that node metadata includes required fields."""
        mock_kg.return_value = mock_knowledge_graph
        
        response = client.get("/api/memory/graph/visualization")
        data = response.json()
        
        person_nodes = [n for n in data["nodes"] if n["type"] == "person"]
        assert len(person_nodes) >= 1
        
        person_node = person_nodes[0]
        assert "documentCount" in person_node["metadata"]
        assert "connectionCount" in person_node["metadata"]
        assert isinstance(person_node["metadata"]["documentCount"], int)
        assert isinstance(person_node["metadata"]["connectionCount"], int)
        
    @patch('app.routes.memory.knowledge_graph')
    async def test_graph_visualization_empty_graph(self, mock_kg):
        """Test handling of empty knowledge graph."""
        empty_kg = MagicMock()
        empty_kg.nodes = {}
        empty_kg.edges = {}
        mock_kg.return_value = empty_kg
        
        response = client.get("/api/memory/graph/visualization")
        
        assert response.status_code == 200
        data = response.json()
        assert data["nodes"] == []
        assert data["edges"] == []
        
    @patch('app.routes.memory.knowledge_graph')
    async def test_graph_visualization_error_handling(self, mock_kg):
        """Test error handling when knowledge graph fails."""
        mock_kg.build_graph.side_effect = Exception("Graph build failed")
        
        response = client.get("/api/memory/graph/visualization")
        
        assert response.status_code == 500
        assert "failed" in response.json()["detail"].lower()
        
    @patch('app.routes.memory.knowledge_graph')
    async def test_graph_visualization_filters_document_nodes(self, mock_kg, mock_knowledge_graph):
        """Test that document nodes are properly handled in visualization."""
        # Add a document node to test filtering
        mock_knowledge_graph.nodes["doc:test-doc.md"] = GraphNode(
            id="doc:test-doc.md",
            name="test-doc.md",
            type="document",
            metadata={"path": "test-doc.md"},
            connections=set(),
            documents=set(),
            last_updated=datetime.utcnow()
        )
        
        mock_kg.return_value = mock_knowledge_graph
        
        response = client.get("/api/memory/graph/visualization")
        data = response.json()
        
        # Document nodes should be included in visualization
        doc_nodes = [n for n in data["nodes"] if n["type"] == "document"]
        assert len(doc_nodes) >= 1
        
        # Verify document node structure
        doc_node = doc_nodes[0]
        assert "path" in doc_node["metadata"]
        
    @patch('app.routes.memory.knowledge_graph')
    async def test_graph_visualization_performance(self, mock_kg, mock_knowledge_graph):
        """Test performance with larger dataset."""
        # Create a larger mock dataset
        large_kg = MagicMock()
        large_kg.nodes = {}
        large_kg.edges = {}
        
        # Generate 100 nodes
        for i in range(100):
            node_id = f"person-user-{i}"
            large_kg.nodes[node_id] = GraphNode(
                id=node_id,
                name=f"User {i}",
                type="person",
                metadata={"role": "user"},
                connections=set(),
                documents=set(),
                last_updated=datetime.utcnow()
            )
        
        mock_kg.return_value = large_kg
        
        response = client.get("/api/memory/graph/visualization")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 100
        
    @patch('app.routes.memory.knowledge_graph')
    async def test_graph_data_transformation(self, mock_kg, mock_knowledge_graph):
        """Test proper transformation from internal format to frontend format."""
        mock_kg.return_value = mock_knowledge_graph
        
        response = client.get("/api/memory/graph/visualization")
        data = response.json()
        
        # Find John Doe node to verify transformation
        john_node = next((n for n in data["nodes"] if n["id"] == "person-john-doe"), None)
        assert john_node is not None
        assert john_node["label"] == "John Doe"
        assert john_node["type"] == "person"
        assert john_node["metadata"]["documentCount"] == 2  # meeting-1.md, project-notes.md
        assert john_node["metadata"]["connectionCount"] == 2  # jane-smith, project-alpha
        
        # Find collaboration edge to verify transformation
        collab_edge = next((e for e in data["edges"] 
                           if e["source"] == "person-john-doe" and e["target"] == "person-jane-smith"), None)
        assert collab_edge is not None
        assert collab_edge["type"] == "collaboration" 
        assert collab_edge["strength"] == 0.8


class TestGraphVisualizationEndpointIntegration:
    """Integration tests for the graph visualization endpoint."""
    
    def test_endpoint_exists(self):
        """Test that the endpoint is properly registered."""
        # This test will fail until the endpoint is implemented
        response = client.get("/api/memory/graph/visualization")
        # Should not be 404 (not found) when implemented
        assert response.status_code != 404
        
    @patch('app.routes.memory.knowledge_graph')
    async def test_response_headers(self, mock_kg, mock_knowledge_graph):
        """Test that response has proper headers."""
        mock_kg.return_value = mock_knowledge_graph
        
        response = client.get("/api/memory/graph/visualization")
        
        assert response.headers["content-type"] == "application/json"
        
    @patch('app.routes.memory.knowledge_graph')
    async def test_response_cacheable(self, mock_kg, mock_knowledge_graph):
        """Test that graph data can be cached appropriately."""
        mock_kg.return_value = mock_knowledge_graph
        
        # Make two requests
        response1 = client.get("/api/memory/graph/visualization")
        response2 = client.get("/api/memory/graph/visualization")
        
        assert response1.status_code == 200
        assert response2.status_code == 200
        
        # Should return same data structure
        assert response1.json().keys() == response2.json().keys()