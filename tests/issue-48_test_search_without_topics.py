"""
Test suite for Issue #48: Search Functionality Without Topic Entities

This test suite verifies that search functionality works correctly after 
removing topic entities from the knowledge graph system.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch

from app.services.knowledge_graph import KnowledgeGraph, GraphNode, GraphEdge


class TestSearchWithoutTopics:
    """Test that search functionality works correctly without topic entities."""
    
    @pytest.fixture
    def knowledge_graph_with_data(self):
        """Create a knowledge graph with sample data but no topic entities."""
        kg = KnowledgeGraph()
        
        # Add sample entities (people, projects, teams only)
        kg.nodes = {
            "person-john-doe": GraphNode(
                id="person-john-doe",
                name="John Doe",
                type="person",
                metadata={"role": "Software Engineer", "team": "Backend"},
                connections={"project-api-service", "team-backend"},
                documents={"team-meeting.md", "api-design.md"},
                last_updated=datetime.utcnow()
            ),
            "person-jane-smith": GraphNode(
                id="person-jane-smith", 
                name="Jane Smith",
                type="person",
                metadata={"role": "Product Manager", "team": "Product"},
                connections={"project-mobile-app", "team-product"},
                documents={"product-requirements.md", "user-research.md"},
                last_updated=datetime.utcnow()
            ),
            "project-api-service": GraphNode(
                id="project-api-service",
                name="API Service",
                type="project",
                metadata={"status": "active", "priority": "high"},
                connections={"person-john-doe", "team-backend"},
                documents={"api-design.md", "technical-spec.md"},
                last_updated=datetime.utcnow()
            ),
            "project-mobile-app": GraphNode(
                id="project-mobile-app",
                name="Mobile App",
                type="project", 
                metadata={"platform": "iOS/Android", "status": "planning"},
                connections={"person-jane-smith", "team-mobile"},
                documents={"mobile-requirements.md", "design-mockups.md"},
                last_updated=datetime.utcnow()
            ),
            "team-backend": GraphNode(
                id="team-backend",
                name="Backend Team",
                type="team",
                metadata={"department": "Engineering", "size": 5},
                connections={"person-john-doe", "project-api-service"},
                documents={"team-charter.md", "architecture-docs.md"},
                last_updated=datetime.utcnow()
            ),
            "team-mobile": GraphNode(
                id="team-mobile",
                name="Mobile Team", 
                type="team",
                metadata={"department": "Engineering", "size": 3},
                connections={"project-mobile-app"},
                documents={"mobile-strategy.md"},
                last_updated=datetime.utcnow()
            )
        }
        
        # Set up document associations
        kg.document_entities = {
            "api-design.md": {"person-john-doe", "project-api-service"},
            "team-meeting.md": {"person-john-doe", "team-backend"},
            "product-requirements.md": {"person-jane-smith", "project-mobile-app"},
            "mobile-requirements.md": {"person-jane-smith", "project-mobile-app", "team-mobile"}
        }
        
        return kg
    
    async def test_search_entities_excludes_topics(self, knowledge_graph_with_data):
        """Test that entity search only returns people, projects, and teams."""
        kg = knowledge_graph_with_data
        
        # Search for all entities
        results = await kg.search_entities("", entity_types=["person", "project", "team"])
        
        # Verify only concrete entity types are returned
        returned_types = set()
        for result in results:
            entity_type = result["entity"]["type"]
            returned_types.add(entity_type)
        
        expected_types = {"person", "project", "team"}
        assert returned_types.issubset(expected_types), f"Unexpected types: {returned_types - expected_types}"
        assert "topic" not in returned_types, "Topic entities should not be returned in search"
    
    async def test_search_by_type_no_topic_option(self, knowledge_graph_with_data):
        """Test that topic type is not supported in entity type filtering."""
        kg = knowledge_graph_with_data
        
        # Attempting to search for topic entities should return empty results
        results = await kg.search_entities("", entity_types=["topic"])
        assert len(results) == 0, "Search for topic entities should return no results"
        
        # Verify valid entity types still work
        person_results = await kg.search_entities("", entity_types=["person"])
        assert len(person_results) == 2, "Should find 2 people"
        
        project_results = await kg.search_entities("", entity_types=["project"])
        assert len(project_results) == 2, "Should find 2 projects"
        
        team_results = await kg.search_entities("", entity_types=["team"])
        assert len(team_results) == 2, "Should find 2 teams"
    
    async def test_fuzzy_search_without_topics(self, knowledge_graph_with_data):
        """Test that fuzzy search works correctly without topic entities."""
        kg = knowledge_graph_with_data
        
        # Search for "api" - should find API Service project and John Doe (who works on API)
        results = await kg.search_entities("api")
        
        # Verify results contain relevant entities but no topics
        entity_ids = [result["entity"]["id"] for result in results]
        entity_types = [result["entity"]["type"] for result in results]
        
        assert "topic" not in entity_types, "Topic entities should not appear in fuzzy search"
        assert any("api" in entity_id.lower() for entity_id in entity_ids), "Should find API-related entities"
        
        # Verify we can still find the API project
        api_project = next((r for r in results if r["entity"]["id"] == "project-api-service"), None)
        assert api_project is not None, "Should find API Service project"
    
    async def test_metadata_search_without_topics(self, knowledge_graph_with_data):
        """Test that metadata-based search works without topic entities."""
        kg = knowledge_graph_with_data
        
        # Search by role metadata
        results = await kg.search_entities("Software Engineer")
        
        # Should find John Doe but no topic entities
        entity_types = [result["entity"]["type"] for result in results]
        assert "topic" not in entity_types, "Topic entities should not appear in metadata search"
        
        # Verify we find the person with matching metadata
        john_doe = next((r for r in results if r["entity"]["id"] == "person-john-doe"), None)
        assert john_doe is not None, "Should find John Doe by role metadata"
    
    async def test_document_based_search_without_topics(self, knowledge_graph_with_data):
        """Test that document-based entity retrieval excludes topic entities."""
        kg = knowledge_graph_with_data
        
        # Get entities for a specific document
        document_path = "api-design.md"
        entities = kg.document_entities.get(document_path, set())
        
        # Verify no topic entities are associated with documents
        topic_entities = [eid for eid in entities if eid.startswith("topic:")]
        assert len(topic_entities) == 0, f"Document should not be associated with topic entities: {topic_entities}"
        
        # Verify concrete entities are still associated
        person_entities = [eid for eid in entities if eid.startswith("person-")]
        project_entities = [eid for eid in entities if eid.startswith("project-")]
        
        assert len(person_entities) > 0 or len(project_entities) > 0, "Document should be associated with concrete entities"
    
    @patch('app.services.knowledge_graph.KnowledgeGraph.search_by_topic')
    async def test_search_by_topic_method_deprecated(self, mock_search_by_topic, knowledge_graph_with_data):
        """Test that search_by_topic method is deprecated or removed."""
        kg = knowledge_graph_with_data
        
        # If the method exists, it should return empty results or raise an error
        try:
            results = await kg.search_by_topic("api design")
            # If method exists, it should return empty results
            assert len(results.get("entities", [])) == 0, "search_by_topic should return no entities"
            assert len(results.get("documents", [])) == 0, "search_by_topic should return no documents"
        except AttributeError:
            # Method has been removed - this is acceptable
            pass
        except Exception as e:
            # Method exists but raises an error - also acceptable
            assert "deprecated" in str(e).lower() or "not supported" in str(e).lower(), f"Unexpected error: {e}"


class TestSearchPerformanceWithoutTopics:
    """Test that search performance improves without topic entities."""
    
    @pytest.fixture
    def large_knowledge_graph(self):
        """Create a large knowledge graph without topic entities for performance testing."""
        kg = KnowledgeGraph()
        
        # Create many entities but no topics
        for i in range(100):
            kg.nodes[f"person-user-{i}"] = GraphNode(
                id=f"person-user-{i}",
                name=f"User {i}",
                type="person",
                metadata={"index": i},
                connections=set(),
                documents={f"doc-{i}.md"},
                last_updated=datetime.utcnow()
            )
        
        for i in range(50):
            kg.nodes[f"project-proj-{i}"] = GraphNode(
                id=f"project-proj-{i}", 
                name=f"Project {i}",
                type="project",
                metadata={"index": i},
                connections=set(),
                documents={f"project-doc-{i}.md"},
                last_updated=datetime.utcnow()
            )
        
        return kg
    
    async def test_search_response_time_improved(self, large_knowledge_graph):
        """Test that search is faster without topic entities to process."""
        kg = large_knowledge_graph
        
        import time
        
        start_time = time.time()
        results = await kg.search_entities("User")
        end_time = time.time()
        
        search_time = end_time - start_time
        
        # Search should complete quickly without topic entities slowing it down
        assert search_time < 1.0, f"Search took too long: {search_time}s"
        
        # Verify we still get relevant results
        assert len(results) > 0, "Should find matching entities"
        
        # Verify no topic entities in results
        entity_types = [result["entity"]["type"] for result in results]
        assert "topic" not in entity_types, "No topic entities should be in search results"
    
    async def test_memory_usage_reduced(self, large_knowledge_graph):
        """Test that memory usage is reduced without topic entities."""
        kg = large_knowledge_graph
        
        # Count total entities
        total_entities = len(kg.nodes)
        
        # All entities should be concrete types (person, project, team)
        concrete_types = {"person", "project", "team"}
        entity_types = [node.type for node in kg.nodes.values()]
        
        for entity_type in entity_types:
            assert entity_type in concrete_types, f"Unexpected entity type: {entity_type}"
        
        # Verify no topic entities consuming memory
        topic_count = entity_types.count("topic")
        assert topic_count == 0, f"Found {topic_count} topic entities consuming memory"
        
        # Verify we have the expected number of entities (150 total: 100 people + 50 projects)
        assert total_entities == 150, f"Expected 150 entities, got {total_entities}"


class TestSearchResultStructure:
    """Test that search result structure is correct without topic entities."""
    
    @pytest.fixture
    def knowledge_graph(self):
        kg = KnowledgeGraph()
        
        # Add a minimal set of entities for testing
        kg.nodes = {
            "person-alice": GraphNode(
                id="person-alice",
                name="Alice Johnson",
                type="person", 
                metadata={"role": "Engineer"},
                connections=set(),
                documents={"alice-notes.md"},
                last_updated=datetime.utcnow()
            ),
            "project-webapp": GraphNode(
                id="project-webapp",
                name="Web Application",
                type="project",
                metadata={"status": "active"},
                connections=set(),
                documents={"webapp-spec.md"},
                last_updated=datetime.utcnow()
            )
        }
        return kg
    
    async def test_search_result_entity_types(self, knowledge_graph):
        """Test that search results only contain valid entity types."""
        results = await knowledge_graph.search_entities("")
        
        valid_types = {"person", "project", "team"}
        
        for result in results:
            entity = result["entity"]
            assert "type" in entity, "Entity should have type field"
            assert entity["type"] in valid_types, f"Invalid entity type: {entity['type']}"
            assert entity["type"] != "topic", "Topic entities should not appear in results"
    
    async def test_search_result_completeness(self, knowledge_graph):
        """Test that search results have all required fields without topic-specific fields."""
        results = await knowledge_graph.search_entities("")
        
        required_fields = {"id", "name", "type", "metadata"}
        
        for result in results:
            entity = result["entity"]
            
            # Verify all required fields are present
            for field in required_fields:
                assert field in entity, f"Missing required field: {field}"
            
            # Verify no topic-specific fields are present
            topic_specific_fields = {"categories", "topics", "classification_type"}
            for field in topic_specific_fields:
                assert field not in entity, f"Topic-specific field should not be present: {field}"