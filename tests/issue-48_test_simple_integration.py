"""
Simple integration test for Issue #48: Metadata-driven Knowledge Graph

Tests the simplified, metadata-driven approach to knowledge graph processing.
"""

import pytest
import asyncio
from app.services.knowledge_graph import KnowledgeGraph


class TestSimpleKnowledgeGraphIntegration:
    """Test simplified knowledge graph processing."""
    
    @pytest.fixture
    def knowledge_graph(self):
        """Create a fresh KnowledgeGraph instance."""
        return KnowledgeGraph()
    
    @pytest.mark.asyncio
    async def test_metadata_driven_processing(self, knowledge_graph):
        """Test that knowledge graph only processes explicit metadata fields."""
        metadata = {
            "people": ["John Doe", "Jane Smith"],
            "projects": ["backend-api", "frontend-redesign"],
            "teams": ["engineering", "design"],
            "classification": {
                "categories": ["api design", "user interface", "security"]  # Should be ignored
            },
            "summary": {
                "participants": ["Bob Wilson"],  # Legacy support
                "key_points": ["Engineering team met", "Design group discussed"]  # Should be ignored
            }
        }
        
        await knowledge_graph._process_document_entities("test.md", metadata)
        
        # Verify entities created from explicit fields only
        all_nodes = list(knowledge_graph.nodes.values())
        
        # Should have: 1 document + 3 people + 2 projects + 2 teams = 8 entities
        expected_count = 8
        assert len(all_nodes) == expected_count, f"Expected {expected_count} entities, got {len(all_nodes)}"
        
        # Verify no topic entities
        topic_nodes = [node for node in all_nodes if node.type == "topic"]
        assert len(topic_nodes) == 0, "Should not create any topic entities"
        
        # Verify people entities (including legacy participants)
        person_nodes = [node for node in all_nodes if node.type == "person"]
        person_names = [node.name for node in person_nodes]
        assert "John Doe" in person_names
        assert "Jane Smith" in person_names
        assert "Bob Wilson" in person_names  # From legacy participants
        
        # Verify project entities (only from explicit projects field)
        project_nodes = [node for node in all_nodes if node.type == "project"]
        project_names = [node.name for node in project_nodes]
        assert "backend-api" in project_names
        assert "frontend-redesign" in project_names
        
        # Verify team entities (only from explicit teams field)
        team_nodes = [node for node in all_nodes if node.type == "team"]
        team_names = [node.name for node in team_nodes]
        assert "engineering" in team_names
        assert "design" in team_names
    
    @pytest.mark.asyncio
    async def test_null_safety(self, knowledge_graph):
        """Test that malformed metadata is handled safely."""
        test_cases = [
            None,  # Null metadata
            {},  # Empty metadata
            {"people": None},  # Null field
            {"people": "not-a-list"},  # Wrong type
            {"people": ["", "  ", "Valid Person"]},  # Empty/whitespace values
            {"projects": [None, "", "Valid Project"]},  # Mixed null/empty/valid
        ]
        
        for i, metadata in enumerate(test_cases):
            # Should not raise exceptions
            await knowledge_graph._process_document_entities(f"test-{i}.md", metadata)
        
        # Should have created some valid entities without crashing
        all_nodes = list(knowledge_graph.nodes.values())
        assert len(all_nodes) > 0, "Should create at least document entities"
        
        # Should not have any topic entities
        topic_nodes = [node for node in all_nodes if node.type == "topic"]
        assert len(topic_nodes) == 0, "Should not create topic entities from malformed data"
    
    @pytest.mark.asyncio
    async def test_topic_migration_cleanup(self, knowledge_graph):
        """Test that existing topic entities are cleaned up during processing."""
        file_path = "legacy-document.md"
        
        # Manually create legacy topic entities
        from app.services.knowledge_graph import GraphNode
        topic_id = "topic:legacy-topic"
        knowledge_graph.nodes[topic_id] = GraphNode(
            id=topic_id,
            name="Legacy Topic",
            type="topic",
            metadata={}
        )
        knowledge_graph.entity_documents[topic_id].add(file_path)
        knowledge_graph.document_entities[file_path] = {topic_id}
        
        # Verify topic exists
        assert topic_id in knowledge_graph.nodes
        
        # Process document with new logic
        await knowledge_graph._process_document_entities(file_path, {"type": "document"})
        
        # Verify topic is removed
        assert topic_id not in knowledge_graph.nodes, "Legacy topic should be removed"
        
        # Verify document still exists
        doc_id = f"doc:{file_path}"
        assert doc_id in knowledge_graph.nodes, "Document should still exist"
    
    @pytest.mark.asyncio
    async def test_deduplication(self, knowledge_graph):
        """Test that duplicate entities are handled correctly."""
        metadata = {
            "people": ["John Doe", "john doe", "JOHN DOE"],  # Same person, different cases
            "projects": ["api-project", "API-Project"],  # Same project, different cases
            "summary": {
                "participants": ["John Doe", "Jane Smith"]  # John Doe appears in both
            }
        }
        
        await knowledge_graph._process_document_entities("dedup-test.md", metadata)
        
        # Should deduplicate based on normalized IDs
        person_nodes = [node for node in knowledge_graph.nodes.values() if node.type == "person"]
        person_ids = [node.id for node in person_nodes]
        
        # Should have unique person IDs
        assert len(person_ids) == len(set(person_ids)), "Person IDs should be unique"
        
        # Should have John Doe only once
        john_doe_count = len([node for node in person_nodes if "john-doe" in node.id])
        assert john_doe_count == 1, f"Should have John Doe only once, got {john_doe_count}"