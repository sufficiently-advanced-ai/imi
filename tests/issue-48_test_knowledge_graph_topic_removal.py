"""
Test suite for Issue #48: Remove Topic Entities from Knowledge Graph

This test suite verifies that topic entities are completely removed from the 
knowledge graph system, including processing, connections, and storage.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch

from app.services.knowledge_graph import KnowledgeGraph, GraphNode, GraphEdge


class TestTopicEntityRemoval:
    """Test that topic entities are not created or processed in the knowledge graph."""
    
    @pytest.fixture
    def knowledge_graph(self):
        """Create a KnowledgeGraph instance for testing."""
        return KnowledgeGraph()
    
    @pytest.fixture
    def sample_metadata_with_topics(self):
        """Sample metadata that includes categories that would create topic entities."""
        return {
            "classification": {
                "categories": [
                    "api design",
                    "user interface", 
                    "database optimization",
                    "project backend-system",  # Should be ignored as project
                    "security protocols"
                ]
            },
            "people": ["john-doe", "jane-smith"],
            "projects": ["backend-system"],
            "teams": ["engineering"]
        }
    
    @pytest.mark.asyncio
    async def test_topic_entities_not_created_from_categories(self, knowledge_graph, sample_metadata_with_topics):
        """Test that topic entities are not created from classification categories."""
        file_path = "test-document.md"
        
        # Process document entities (this currently creates topic entities)
        await knowledge_graph._process_document_entities(file_path, sample_metadata_with_topics)
        
        # Verify NO topic entities are created
        topic_nodes = [node for node in knowledge_graph.nodes.values() if node.type == "topic"]
        assert len(topic_nodes) == 0, f"Found {len(topic_nodes)} topic entities, expected 0"
        
        # Verify topic IDs are not in the nodes dictionary
        topic_ids = [node_id for node_id in knowledge_graph.nodes.keys() if node_id.startswith("topic-")]
        assert len(topic_ids) == 0, f"Found topic IDs in nodes: {topic_ids}"
    
    @pytest.mark.asyncio
    async def test_only_concrete_entities_created(self, knowledge_graph, sample_metadata_with_topics):
        """Test that only people, projects, and teams are created - no topics."""
        file_path = "test-document.md"
        
        await knowledge_graph._process_document_entities(file_path, sample_metadata_with_topics)
        
        # Count entities by type
        entity_counts = {}
        for node in knowledge_graph.nodes.values():
            entity_counts[node.type] = entity_counts.get(node.type, 0) + 1
        
        # Should only have concrete entity types (plus document)
        expected_types = {"person", "project", "team", "document"}
        actual_types = set(entity_counts.keys())
        
        assert actual_types.issubset(expected_types), f"Unexpected entity types: {actual_types - expected_types}"
        assert "topic" not in actual_types, "Topic entities should not be created"
        
        # Verify we have the expected entities
        assert entity_counts.get("person", 0) == 2, "Should have 2 people"
        assert entity_counts.get("project", 0) == 1, "Should have 1 project"  
        assert entity_counts.get("team", 0) == 1, "Should have 1 team"
    
    
    @pytest.mark.asyncio
    async def test_document_entities_exclude_topics(self, knowledge_graph, sample_metadata_with_topics):
        """Test that document entity associations don't include topic entities."""
        file_path = "test-document.md"
        
        await knowledge_graph._process_document_entities(file_path, sample_metadata_with_topics)
        
        # Get entities associated with the document
        document_entities = knowledge_graph.document_entities.get(file_path, set())
        
        # Verify no topic entities are associated with the document
        topic_entity_ids = [entity_id for entity_id in document_entities if entity_id.startswith("topic-")]
        assert len(topic_entity_ids) == 0, f"Document associated with topic entities: {topic_entity_ids}"
        
        # Verify concrete entities are still associated
        person_entities = [entity_id for entity_id in document_entities if entity_id.startswith("person-")]
        project_entities = [entity_id for entity_id in document_entities if entity_id.startswith("project-")]
        team_entities = [entity_id for entity_id in document_entities if entity_id.startswith("team-")]
        
        assert len(person_entities) > 0, "Should have person entities associated"
        assert len(project_entities) > 0, "Should have project entities associated"
        assert len(team_entities) > 0, "Should have team entities associated"
    
    @pytest.mark.asyncio
    async def test_entity_documents_mapping_excludes_topics(self, knowledge_graph, sample_metadata_with_topics):
        """Test that entity_documents mapping doesn't include topic entities."""
        file_path = "test-document.md"
        
        await knowledge_graph._process_document_entities(file_path, sample_metadata_with_topics)
        
        # Check entity_documents mapping
        topic_entities_in_mapping = [entity_id for entity_id in knowledge_graph.entity_documents.keys() 
                                   if entity_id.startswith("topic-")]
        
        assert len(topic_entities_in_mapping) == 0, f"Found topic entities in entity_documents mapping: {topic_entities_in_mapping}"
    


class TestKnowledgeGraphPerformanceImprovement:
    """Test that removing topic entities improves knowledge graph performance."""
    
    @pytest.fixture
    def knowledge_graph(self):
        return KnowledgeGraph()
    
    @pytest.mark.asyncio
    async def test_fewer_entities_processed(self, knowledge_graph):
        """Test that fewer entities are processed when topics are excluded."""
        metadata_with_many_categories = {
            "classification": {
                "categories": [
                    "api", "ui", "database", "security", "performance", 
                    "architecture", "testing", "deployment", "monitoring", "analytics"
                ]
            },
            "people": ["john-doe"],
            "projects": ["test-project"],
            "teams": ["engineering"]
        }
        
        file_path = "test-document.md"
        await knowledge_graph._process_document_entities(file_path, metadata_with_many_categories)
        
        # Should only have 4 entities (1 document, 1 person, 1 project, 1 team) instead of 14 with topics
        assert len(knowledge_graph.nodes) == 4, f"Expected 4 entities, got {len(knowledge_graph.nodes)}"
        
        # Verify performance improvement by checking entity counts
        entity_types = [node.type for node in knowledge_graph.nodes.values()]
        assert entity_types.count("topic") == 0, "Should have 0 topic entities"
        assert entity_types.count("document") == 1, "Should have 1 document entity"
        assert entity_types.count("person") == 1, "Should have 1 person entity"
        assert entity_types.count("project") == 1, "Should have 1 project entity"
        assert entity_types.count("team") == 1, "Should have 1 team entity"


class TestBackwardCompatibility:
    """Test that removing topic entities doesn't break existing functionality."""
    
    @pytest.fixture
    def knowledge_graph(self):
        return KnowledgeGraph()
    
    
    @pytest.mark.asyncio
    async def test_empty_categories_handled_gracefully(self, knowledge_graph):
        """Test that empty or missing categories don't cause issues."""
        test_cases = [
            {"classification": {"categories": []}},  # Empty categories
            {"classification": {}},  # No categories key
            {},  # No classification key
            {"people": ["john-doe"]}  # Only other metadata
        ]
        
        for i, metadata in enumerate(test_cases):
            file_path = f"test-doc-{i}.md"
            
            # Should not raise exceptions
            await knowledge_graph._process_document_entities(file_path, metadata)
            
            # Should not create any topic entities
            topic_nodes = [node for node in knowledge_graph.nodes.values() if node.type == "topic"]
            assert len(topic_nodes) == 0, f"Test case {i}: Found unexpected topic entities"