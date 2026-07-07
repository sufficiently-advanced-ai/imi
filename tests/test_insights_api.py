"""Tests for Insights API endpoints."""
import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
import json
from unittest.mock import Mock, patch, AsyncMock
from app.main import app

client = TestClient(app)

@pytest.fixture
def mock_insights_data():
    """Mock insights data for testing."""
    return [
        {
            "id": f"insight-{i}",
            "type": "opportunity" if i % 3 == 0 else "risk" if i % 3 == 1 else "prediction",
            "description": f"Test insight {i}",
            "confidence": 0.85 - (i * 0.01),
            "entities": [f"person-{i}", f"project-{i}"],
            "sources": [f"file{i}.md", f"meeting{i}.md"],
            "timestamp": (datetime.now() - timedelta(days=i)).isoformat(),
            "metadata": {"category": "test"}
        }
        for i in range(100)
    ]

@pytest.fixture
def mock_connections_data():
    """Mock connections graph data for testing."""
    return {
        "nodes": [
            {"id": f"node-{i}", "type": "document" if i % 2 == 0 else "person", 
             "label": f"Node {i}", "metadata": {}}
            for i in range(20)
        ],
        "edges": [
            {"source": f"node-{i}", "target": f"node-{i+1}", 
             "type": "relates_to", "strength": 0.75}
            for i in range(19)
        ]
    }

@pytest.fixture
def mock_timeline_data():
    """Mock timeline data for testing."""
    timeline = []
    for i in range(30):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        timeline.append({
            "date": date,
            "patterns": [
                {
                    "id": f"pattern-{i}-{j}",
                    "type": "escalation" if j % 2 == 0 else "opportunity",
                    "description": f"Pattern {j} on {date}",
                    "entities": [f"entity-{j}"],
                    "confidence": 0.8
                }
                for j in range(3)
            ],
            "count": 3
        })
    return timeline

class TestInsightsAPI:
    """Test the insights API endpoints."""
    
    def test_get_insights_endpoint_success(self, mock_insights_data):
        """Test successful retrieval of insights."""
        response = client.get("/api/insights")
        assert response.status_code == 200
        
        data = response.json()
        assert "insights" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data
        
        # Check default pagination
        assert data["offset"] == 0
        assert data["limit"] == 20
        assert len(data["insights"]) <= 20
        
        # Validate insight structure
        if data["insights"]:
            insight = data["insights"][0]
            assert "id" in insight
            assert "type" in insight
            assert "description" in insight
            assert "confidence" in insight
            assert "entities" in insight
            assert "sources" in insight
            assert "timestamp" in insight
            assert "metadata" in insight
    
    def test_get_insights_filtering(self, mock_insights_data):
        """Test filtering insights by various parameters."""
        # Filter by entity type
        response = client.get("/api/insights?type=opportunity")
        assert response.status_code == 200
        data = response.json()
        for insight in data["insights"]:
            assert insight["type"] == "opportunity"
        
        # Filter by confidence level
        response = client.get("/api/insights?min_confidence=0.8")
        assert response.status_code == 200
        data = response.json()
        for insight in data["insights"]:
            assert insight["confidence"] >= 0.8
        
        # Filter by date range
        start_date = (datetime.now() - timedelta(days=7)).isoformat()
        end_date = datetime.now().isoformat()
        response = client.get(f"/api/insights?start_date={start_date}&end_date={end_date}")
        assert response.status_code == 200
        data = response.json()
        # All insights should be within the date range
        for insight in data["insights"]:
            assert start_date <= insight["timestamp"] <= end_date
    
    def test_get_insights_pagination(self, mock_insights_data):
        """Test pagination of insights."""
        # First page
        response = client.get("/api/insights?offset=0&limit=10")
        assert response.status_code == 200
        data1 = response.json()
        assert data1["offset"] == 0
        assert data1["limit"] == 10
        assert len(data1["insights"]) == 10
        
        # Second page
        response = client.get("/api/insights?offset=10&limit=10")
        assert response.status_code == 200
        data2 = response.json()
        assert data2["offset"] == 10
        assert data2["limit"] == 10
        
        # Ensure no overlap between pages
        ids1 = {i["id"] for i in data1["insights"]}
        ids2 = {i["id"] for i in data2["insights"]}
        assert len(ids1.intersection(ids2)) == 0
    
    def test_get_insights_performance(self):
        """Test that endpoint returns 1000+ insights efficiently."""
        import time
        
        start_time = time.time()
        response = client.get("/api/insights?limit=1000")
        end_time = time.time()
        
        assert response.status_code == 200
        assert (end_time - start_time) < 0.5  # Should return in less than 500ms
        
        data = response.json()
        assert data["total"] >= 1000  # Should have 1000+ insights available
    
    def test_get_insights_empty_state(self):
        """Test handling of no insights."""
        # Mock empty insights
        with patch("app.routes.insights.get_insights_from_tools") as mock_get:
            mock_get.return_value = {"insights": [], "total": 0}
            
            response = client.get("/api/insights")
            assert response.status_code == 200
            data = response.json()
            assert data["insights"] == []
            assert data["total"] == 0

class TestConnectionsAPI:
    """Test the connections graph API endpoint."""
    
    def test_get_connections_endpoint_success(self, mock_connections_data):
        """Test successful retrieval of connections graph."""
        response = client.get("/api/insights/connections")
        assert response.status_code == 200
        
        data = response.json()
        assert "nodes" in data
        assert "edges" in data
        
        # Validate node structure
        if data["nodes"]:
            node = data["nodes"][0]
            assert "id" in node
            assert "type" in node
            assert "label" in node
            assert "metadata" in node
        
        # Validate edge structure
        if data["edges"]:
            edge = data["edges"][0]
            assert "source" in edge
            assert "target" in edge
            assert "type" in edge
            assert "strength" in edge
    
    
    def test_get_connections_depth_limit(self):
        """Test limiting connection depth."""
        response = client.get("/api/insights/connections?depth=1")
        assert response.status_code == 200
        
        data = response.json()
        # Should have limited depth traversal
        assert len(data["nodes"]) < 100  # Reasonable limit for depth=1
    
    def test_get_connections_performance(self):
        """Test handling large graphs efficiently."""
        import time
        
        start_time = time.time()
        response = client.get("/api/insights/connections")
        end_time = time.time()
        
        assert response.status_code == 200
        assert (end_time - start_time) < 1.0  # Should return in less than 1 second

class TestTimelineAPI:
    """Test the timeline API endpoint."""
    
    def test_get_timeline_endpoint_success(self, mock_timeline_data):
        """Test successful retrieval of timeline data."""
        response = client.get("/api/insights/timeline")
        assert response.status_code == 200
        
        data = response.json()
        assert "timeline" in data
        
        # Validate timeline structure
        if data["timeline"]:
            entry = data["timeline"][0]
            assert "date" in entry
            assert "patterns" in entry
            assert "count" in entry
            
            # Validate pattern structure
            if entry["patterns"]:
                pattern = entry["patterns"][0]
                assert "id" in pattern
                assert "type" in pattern
                assert "description" in pattern
                assert "entities" in pattern
                assert "confidence" in pattern
    
    def test_get_timeline_date_range(self):
        """Test filtering timeline by date range."""
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")
        
        response = client.get(f"/api/insights/timeline?start_date={start_date}&end_date={end_date}")
        assert response.status_code == 200
        
        data = response.json()
        for entry in data["timeline"]:
            assert start_date <= entry["date"] <= end_date
    
    def test_get_timeline_entity_filter(self):
        """Test filtering timeline by entity."""
        response = client.get("/api/insights/timeline?entity=person-1")
        assert response.status_code == 200
        
        data = response.json()
        # All patterns should involve the specified entity
        for entry in data["timeline"]:
            for pattern in entry["patterns"]:
                assert "person-1" in pattern["entities"]
    
    def test_get_timeline_aggregation(self):
        """Test timeline aggregation by different periods."""
        # Test daily aggregation (default)
        response = client.get("/api/insights/timeline?aggregation=day")
        assert response.status_code == 200
        
        # Test weekly aggregation
        response = client.get("/api/insights/timeline?aggregation=week")
        assert response.status_code == 200
        data = response.json()
        # Should have fewer entries with weekly aggregation
        assert len(data["timeline"]) < 30  # Less than daily entries
        
        # Test monthly aggregation
        response = client.get("/api/insights/timeline?aggregation=month")
        assert response.status_code == 200
        data = response.json()
        # Should have even fewer entries with monthly aggregation
        assert len(data["timeline"]) < 10  # Less than weekly entries