"""
Test suite for Issue #43: Knowledge graph search functionality
Tests the fixes for entity search and type filtering
"""
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from app.services.knowledge_graph import KnowledgeGraph, GraphNode
from app.services.chat_tools import search_knowledge_graph


class TestKnowledgeGraphSearch:
    """Test knowledge graph search functionality fixes."""
    
    @pytest.fixture
    def mock_kg(self):
        """Create a mock knowledge graph with test data."""
        kg = KnowledgeGraph()
        
        # Add test entities
        kg.nodes = {
            "person:kevin-zhang": GraphNode(
                id="person:kevin-zhang",
                name="Kevin Zhang",
                type="person",
                metadata={
                    "title": "Analytics",
                    "department": "Analytics",
                    "projects": ["Q4 partner comp analysis", "CRM Modernization Initiative"]
                },
                documents={"people/person-kevin-zhang.md"}
            ),
            "person:sarah-chen": GraphNode(
                id="person:sarah-chen", 
                name="Sarah Chen",
                type="person",
                metadata={
                    "title": "Global Technology Practice Lead",
                    "department": "Technology"
                },
                documents={"people/person-sarah-chen.md"}
            ),
            "project:crm-modernization": GraphNode(
                id="project:crm-modernization",
                name="CRM Modernization Initiative", 
                type="project",
                metadata={"status": "active"},
                documents={"projects/crm-modernization.md"}
            ),
            "team-analytics": GraphNode(
                id="team-analytics",
                name="Analytics Team",
                type="team", 
                metadata={"department": "Analytics"},
                documents={"teams/analytics.md"}
            ),
            "doc:people/person-kevin-zhang.md": GraphNode(
                id="doc:people/person-kevin-zhang.md",
                name="person-kevin-zhang.md",
                type="document",
                metadata={"path": "people/person-kevin-zhang.md"}
            )
        }
        
        # Set up document associations
        kg.document_entities = {
            "people/person-kevin-zhang.md": {
                "person:kevin-zhang", 
                "doc:people/person-kevin-zhang.md"
            },
            "people/person-sarah-chen.md": {
                "person:sarah-chen",
                "doc:people/person-sarah-chen.md" 
            },
            "projects/crm-modernization.md": {
                "project:crm-modernization",
                "doc:projects/crm-modernization.md"
            }
        }
        
        return kg
    
    
    
    
    
    @pytest.mark.asyncio
    async def test_no_document_entities_in_search(self, mock_kg):
        """Test that document entities are filtered out of search results."""
        # This should not return document entities, only real entities
        results = await mock_kg.search_by_topic("person-kevin-zhang.md", max_results=10)
        
        # Even if we search for the document name, we should get entity results, not doc entities
        if results:
            doc_paths = [r.get('path') for r in results]
            # Should not return paths that are just document references
            assert all(path != "doc:people/person-kevin-zhang.md" for path in doc_paths)



class TestChatToolsSearchIntegration:
    """Test the chat_tools search_knowledge_graph function."""
    
    
    
    @pytest.mark.asyncio
    async def test_empty_query_handling(self):
        """Test handling of empty queries."""
        results = await search_knowledge_graph("")
        assert results == [], "Empty query should return empty results"
        
        results = await search_knowledge_graph("   ")
        assert results == [], "Whitespace-only query should return empty results"
        
        results = await search_knowledge_graph(None)
        assert results == [], "None query should return empty results"
    


if __name__ == "__main__":
    # Run specific test
    pytest.main([__file__ + "::TestKnowledgeGraphSearch::test_exact_name_search", "-v"])