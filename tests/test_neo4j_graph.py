"""Tests for Neo4j Knowledge Graph Service.

Source: app/services/graph/neo4j_graph.py

This is the core business logic layer. Tests are grouped by concern:
- Metadata extraction
- Entity type resolution
- Entity ID derivation
- File ingestion
- Query methods
- Semantic relationships
- Sync & cache management
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.model_schemas.domain_config import (
    DomainAttribute,
    DomainConfiguration,
    DomainEntity,
    DomainRelationship,
)
from app.services.graph.models import GraphEdge, GraphNode
from app.services.graph.neo4j_graph import Neo4jKnowledgeGraph


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────


def _make_domain_config() -> DomainConfiguration:
    """Build a realistic DomainConfiguration with person + project entities."""
    return DomainConfiguration(
        id="test_domain",
        name="Test Domain",
        entities={
            "person": DomainEntity(
                name="person",
                description="A person",
                plural="people",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                    DomainAttribute(name="role", type="string", required=False),
                ],
                relationships=[
                    DomainRelationship(
                        type="has_projects",
                        target="project",
                        cardinality="one-to-many",
                    ),
                ],
            ),
            "project": DomainEntity(
                name="project",
                description="A project",
                plural="projects",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                ],
                relationships=[],
            ),
        },
    )


@pytest.fixture
def mock_neo4j():
    """AsyncMock Neo4j client with default empty returns."""
    client = AsyncMock()
    client.execute_read = AsyncMock(return_value=[])
    client.execute_write = AsyncMock(return_value=[])
    client.execute_many = AsyncMock(
        return_value={"succeeded": 0, "failed": 0, "errors": []}
    )
    return client


@pytest.fixture
def domain():
    return _make_domain_config()


@pytest.fixture
def graph(mock_neo4j, domain):
    """Neo4jKnowledgeGraph with mocked dependencies."""
    kg = Neo4jKnowledgeGraph(neo4j_client=mock_neo4j, domain_config=domain)
    # Override git_ops to avoid real filesystem
    mock_git = MagicMock()
    mock_git.repo_path = "/tmp/test-repo"
    kg._git_ops = mock_git
    return kg


# ──────────────────────────────────────────────────────────────
# Metadata Extraction
# ──────────────────────────────────────────────────────────────


class TestExtractMetadata:
    def test_yaml_frontmatter(self, graph):
        content = "---\nname: Tom\ntype: person\n---\nSome body text"
        result = graph._extract_metadata(content)
        assert result == {"name": "Tom", "type": "person"}

    def test_no_frontmatter(self, graph):
        content = "Just plain text without frontmatter"
        result = graph._extract_metadata(content)
        assert result is None

    def test_invalid_yaml_falls_back(self, graph):
        """Invalid YAML should attempt fallback to frontmatter service.

        The code catches yaml.YAMLError, so we give it content that
        actually triggers a YAML parse error.
        """
        # "\t" at the start of a value is invalid YAML
        content = "---\nkey:\t{invalid\n---\nbody"
        with patch(
            "app.services.frontmatter.frontmatter.extract_all",
            return_value={"name": "fallback"},
        ):
            result = graph._extract_metadata(content)
            assert result == {"name": "fallback"}

    def test_multiline_frontmatter(self, graph):
        content = "---\nname: Alice\nrole: Engineer\nage: 30\n---\n# About Alice"
        result = graph._extract_metadata(content)
        assert result["name"] == "Alice"
        assert result["role"] == "Engineer"
        assert result["age"] == 30


# ──────────────────────────────────────────────────────────────
# Entity Type Resolution
# ──────────────────────────────────────────────────────────────


class TestResolveEntityType:
    def test_from_metadata_type(self, graph):
        result = graph._resolve_entity_type("", "person", "some/path.md")
        assert result == "person"

    def test_from_id_prefix(self, graph):
        result = graph._resolve_entity_type("person-tom", "", "some/path.md")
        assert result == "person"

    def test_from_file_path(self, graph):
        result = graph._resolve_entity_type("", "", "entities/person/tom.md")
        assert result == "person"

    def test_unknown_type_returns_none(self, graph):
        result = graph._resolve_entity_type("", "widget", "docs/readme.md")
        assert result is None

    def test_type_takes_priority_over_id(self, graph):
        """Explicit type field should win even if ID has a different prefix."""
        result = graph._resolve_entity_type("project-alpha", "person", "some/path.md")
        assert result == "person"

    def test_no_domain_returns_none(self, mock_neo4j):
        """Without domain config, always returns None."""
        kg = Neo4jKnowledgeGraph(neo4j_client=mock_neo4j, domain_config=None)
        result = kg._resolve_entity_type("person-tom", "person", "entities/person/tom.md")
        assert result is None


# ──────────────────────────────────────────────────────────────
# Entity ID Derivation
# ──────────────────────────────────────────────────────────────


class TestDeriveEntityId:
    def test_from_name(self, graph):
        result = graph._derive_entity_id("person", "some/path.md", {"name": "Tom Smith"})
        assert result == "person-tom-smith"

    def test_from_filename(self, graph):
        result = graph._derive_entity_id("person", "entities/person/tom-williams.md", {})
        assert result == "person-tom-williams"

    def test_from_filename_adds_prefix(self, graph):
        """If filename doesn't start with entity type prefix, it's added."""
        result = graph._derive_entity_id("person", "entities/tom.md", {})
        assert result == "person-tom"

    def test_from_filename_keeps_existing_prefix(self, graph):
        result = graph._derive_entity_id("person", "entities/person-tom.md", {})
        assert result == "person-tom"


class TestNormalizeTargetId:
    def test_adds_prefix_for_plain_name(self, graph):
        result = graph._normalize_target_id("alpha", "project")
        assert result == "project-alpha"

    def test_keeps_existing_prefix(self, graph):
        result = graph._normalize_target_id("project-alpha", "project")
        assert result == "project-alpha"

    def test_normalizes_spaces_to_dashes(self, graph):
        result = graph._normalize_target_id("Tom Smith", "person")
        assert result == "person-tom-smith"

    def test_keeps_person_prefix(self, graph):
        result = graph._normalize_target_id("person-tom-smith", "person")
        assert result == "person-tom-smith"


# ──────────────────────────────────────────────────────────────
# File Ingestion
# ──────────────────────────────────────────────────────────────


class TestIngestFile:
    @pytest.mark.asyncio
    async def test_ingest_entity_file(self, graph, mock_neo4j):
        """Entity file should create a node via execute_write with correct label."""
        metadata = {
            "id": "person-tom",
            "type": "person",
            "name": "Tom Williams",
            "role": "Engineer",
        }

        await graph._ingest_file("entities/person/tom.md", metadata)

        # Should have called execute_write at least once for the node MERGE
        assert mock_neo4j.execute_write.await_count >= 1
        first_call = mock_neo4j.execute_write.call_args_list[0]
        query = first_call[0][0]
        assert "Entity:Person" in query
        assert "MERGE" in query

    @pytest.mark.asyncio
    async def test_ingest_entity_creates_relationships(self, graph, mock_neo4j):
        """Entity with relationship targets should create relationship edges."""
        metadata = {
            "id": "person-tom",
            "type": "person",
            "name": "Tom",
            "has_projects": ["alpha", "beta"],
        }

        await graph._ingest_file("entities/person/tom.md", metadata)

        # Count calls: 1 entity node + 2 stub targets + 2 relationships + document links
        write_calls = mock_neo4j.execute_write.call_args_list
        relationship_calls = [
            c for c in write_calls
            if "HAS_PROJECTS" in str(c)
        ]
        assert len(relationship_calls) == 2

    @pytest.mark.asyncio
    async def test_ingest_entity_creates_stub_targets(self, graph, mock_neo4j):
        """Relationship targets should create stub nodes."""
        metadata = {
            "id": "person-tom",
            "type": "person",
            "name": "Tom",
            "has_projects": ["alpha"],
        }

        await graph._ingest_file("entities/person/tom.md", metadata)

        write_calls = mock_neo4j.execute_write.call_args_list
        stub_calls = [
            c for c in write_calls
            if "stub" in str(c[0]) and "Project" in str(c[0])
        ]
        assert len(stub_calls) >= 1

    @pytest.mark.asyncio
    async def test_ingest_document_file(self, graph, mock_neo4j):
        """Non-entity file should create a Document node."""
        metadata = {"title": "Meeting Notes", "date": "2026-01-15"}

        await graph._ingest_file("meetings/2026-01-15.md", metadata)

        write_calls = mock_neo4j.execute_write.call_args_list
        doc_calls = [c for c in write_calls if "Document" in str(c[0])]
        assert len(doc_calls) >= 1

    @pytest.mark.asyncio
    async def test_ingest_extracts_entity_references(self, graph, mock_neo4j):
        """Metadata with 'people' field should create entity references."""
        metadata = {
            "title": "Weekly Standup",
            "people": ["Tom Williams", "Alice Chen"],
        }

        await graph._ingest_file("meetings/standup.md", metadata)

        # Should have created stub entities for references
        write_calls = mock_neo4j.execute_write.call_args_list
        person_calls = [c for c in write_calls if "Person" in str(c[0])]
        assert len(person_calls) >= 2

    @pytest.mark.asyncio
    async def test_ingest_updates_document_entities(self, graph, mock_neo4j):
        """After ingestion, document_entities dict should be populated."""
        metadata = {
            "id": "person-tom",
            "type": "person",
            "name": "Tom",
        }

        await graph._ingest_file("entities/person/tom.md", metadata)
        assert "entities/person/tom.md" in graph.document_entities


# ──────────────────────────────────────────────────────────────
# Query Methods
# ──────────────────────────────────────────────────────────────


class TestFindRelatedEntities:
    @pytest.mark.asyncio
    async def test_returns_formatted_list(self, graph, mock_neo4j):
        mock_neo4j.execute_read = AsyncMock(return_value=[
            {
                "related": {
                    "id": "project-alpha",
                    "name": "Alpha",
                    "entity_type": "project",
                    "canonical_name": "alpha",
                    "updated_at": "2026-01-01T00:00:00",
                },
                "rel_type": "HAS_PROJECTS",
                "strength": 0.8,
            }
        ])

        results = await graph.find_related_entities("person-tom")

        assert len(results) == 1
        assert results[0]["entity"]["id"] == "project-alpha"
        assert results[0]["entity"]["name"] == "Alpha"
        assert results[0]["relationship"]["type"] == "has_projects"
        assert results[0]["relationship"]["strength"] == 0.8

    @pytest.mark.asyncio
    async def test_empty_results(self, graph, mock_neo4j):
        mock_neo4j.execute_read = AsyncMock(return_value=[])
        results = await graph.find_related_entities("person-nobody")
        assert results == []


class TestSearchByTopic:
    @pytest.mark.asyncio
    async def test_fulltext_search(self, graph, mock_neo4j):
        """Full-text search → contextual documents pipeline."""
        # First call: fulltext search returns entity IDs
        # Second call: find_contextual_documents returns documents
        mock_neo4j.execute_read = AsyncMock(side_effect=[
            # fulltext search results
            [{"id": "person-tom", "name": "Tom", "type": "person", "score": 1.5}],
            # contextual documents results
            [{"path": "entities/person/tom.md", "entity_count": 1, "relevance_score": 1.0}],
        ])

        results = await graph.search_by_topic("Tom")
        assert len(results) == 1
        assert results[0]["path"] == "entities/person/tom.md"

    @pytest.mark.asyncio
    async def test_empty_topic(self, graph, mock_neo4j):
        results = await graph.search_by_topic("")
        assert results == []
        mock_neo4j.execute_read.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_whitespace_topic(self, graph, mock_neo4j):
        results = await graph.search_by_topic("   ")
        assert results == []

    @pytest.mark.asyncio
    async def test_force_refresh_rebuilds(self, graph, mock_neo4j):
        """force_refresh=True should trigger build_graph."""
        graph.build_graph = AsyncMock(return_value={})
        mock_neo4j.execute_read = AsyncMock(return_value=[])

        await graph.search_by_topic("test", force_refresh=True)
        graph.build_graph.assert_awaited_once_with(force_rebuild=True)


class TestGetEntityByName:
    def test_found(self, graph):
        """When a node matches by name, return its dict representation."""
        graph.nodes["person-tom"] = GraphNode(
            id="person-tom",
            name="Tom Williams",
            type="person",
            metadata={"role": "Engineer"},
        )

        result = graph.get_entity_by_name("Tom Williams")
        assert result is not None
        assert result["id"] == "person-tom"
        assert result["name"] == "Tom Williams"
        assert result["type"] == "person"

    def test_case_insensitive(self, graph):
        graph.nodes["person-tom"] = GraphNode(
            id="person-tom", name="Tom Williams", type="person"
        )

        result = graph.get_entity_by_name("tom williams")
        assert result is not None
        assert result["id"] == "person-tom"

    def test_not_found(self, graph):
        result = graph.get_entity_by_name("Nobody")
        assert result is None

    def test_filter_by_type(self, graph):
        graph.nodes["person-tom"] = GraphNode(
            id="person-tom", name="Tom", type="person"
        )

        assert graph.get_entity_by_name("Tom", entity_type="person") is not None
        assert graph.get_entity_by_name("Tom", entity_type="project") is None


# ──────────────────────────────────────────────────────────────
# Semantic Relationships
# ──────────────────────────────────────────────────────────────


class TestCreateSemanticRelationship:
    @pytest.mark.asyncio
    async def test_writes_to_neo4j_and_cache(self, graph, mock_neo4j):
        """Creates relationship in Neo4j and adds to semantic_edges cache."""
        mock_domain_service = MagicMock()
        mock_domain = _make_domain_config()
        mock_domain_service.get_active_domain.return_value = mock_domain

        with patch(
            "app.core.domain_config.domain_config_service.get_domain_config_service",
            return_value=mock_domain_service,
        ):
            edge = await graph.create_semantic_relationship(
                from_entity_id="person-tom",
                to_entity_id="project-alpha",
                relationship_type="has_projects",
                strength=0.85,
                evidence="Tom leads project Alpha",
                reasoning="Mentioned as project lead",
                source="meeting-notes",
            )

        assert edge.from_entity == "person-tom"
        assert edge.strength == 0.85

        # Check in-memory cache
        key = ("person-tom", "project-alpha", "has_projects")
        assert key in graph.semantic_edges

        # Verify Neo4j write was called
        assert mock_neo4j.execute_write.await_count >= 1

    @pytest.mark.asyncio
    async def test_empty_entity_id_raises(self, graph):
        with pytest.raises(ValueError, match="cannot be empty"):
            await graph.create_semantic_relationship(
                from_entity_id="",
                to_entity_id="project-alpha",
                relationship_type="has_projects",
                strength=0.8,
                evidence="evidence",
                reasoning="reasoning",
                source="test",
            )

    @pytest.mark.asyncio
    async def test_invalid_strength_raises(self, graph):
        mock_domain_service = MagicMock()
        mock_domain_service.get_active_domain.return_value = _make_domain_config()

        with (
            patch(
                "app.core.domain_config.domain_config_service.get_domain_config_service",
                return_value=mock_domain_service,
            ),
            pytest.raises(ValueError, match="between 0.0 and 1.0"),
        ):
            await graph.create_semantic_relationship(
                from_entity_id="person-tom",
                to_entity_id="project-alpha",
                relationship_type="has_projects",
                strength=1.5,
                evidence="evidence",
                reasoning="reasoning",
                source="test",
            )


class TestQuerySemanticRelationships:
    def _populate_semantic_edges(self, graph):
        """Add some semantic edges for testing."""
        from app.services.knowledge_graph import SemanticEdge
        import time

        edges = [
            SemanticEdge(
                from_entity="person-tom",
                to_entity="project-alpha",
                relationship_type="has_projects",
                strength=0.9,
                evidence="Tom leads Alpha",
                reasoning="Project lead",
                source="meeting",
                created_at=time.time(),
            ),
            SemanticEdge(
                from_entity="person-alice",
                to_entity="project-beta",
                relationship_type="has_projects",
                strength=0.7,
                evidence="Alice works on Beta",
                reasoning="Contributor",
                source="meeting",
                created_at=time.time(),
            ),
            SemanticEdge(
                from_entity="person-tom",
                to_entity="project-beta",
                relationship_type="has_projects",
                strength=0.5,
                evidence="Tom advises on Beta",
                reasoning="Advisor",
                source="document",
                created_at=time.time(),
            ),
        ]
        for edge in edges:
            key = (edge.from_entity, edge.to_entity, edge.relationship_type)
            graph.semantic_edges[key] = edge

    def test_returns_all(self, graph):
        self._populate_semantic_edges(graph)
        results = graph.query_semantic_relationships()
        assert len(results) == 3

    def test_filter_by_type(self, graph):
        self._populate_semantic_edges(graph)
        results = graph.query_semantic_relationships(relationship_type="has_projects")
        assert len(results) == 3  # all are has_projects

    def test_filter_by_min_strength(self, graph):
        self._populate_semantic_edges(graph)
        results = graph.query_semantic_relationships(min_strength=0.8)
        assert len(results) == 1
        assert results[0].from_entity == "person-tom"
        assert results[0].to_entity == "project-alpha"

    def test_filter_by_from_entity(self, graph):
        self._populate_semantic_edges(graph)
        results = graph.query_semantic_relationships(from_entity="person-tom")
        assert len(results) == 2

    def test_filter_by_to_entity(self, graph):
        self._populate_semantic_edges(graph)
        results = graph.query_semantic_relationships(to_entity="project-beta")
        assert len(results) == 2

    def test_sorted_by_strength(self, graph):
        self._populate_semantic_edges(graph)
        results = graph.query_semantic_relationships()
        strengths = [r.strength for r in results]
        assert strengths == sorted(strengths, reverse=True)
class TestInvalidateCache:
    def test_clears_last_build_but_keeps_stale_data(self, graph):
        """invalidate_cache clears last_build to trigger rebuild on next access,
        but keeps stale node/edge data available so readers see valid data
        during the rebuild window."""
        graph.nodes["person-tom"] = GraphNode(
            id="person-tom", name="Tom", type="person"
        )
        graph.edges[("a", "b")] = GraphEdge(
            source="a", target="b", relationship_type="co_occurrence", strength=0.5
        )
        graph.document_entities["doc.md"] = {"person-tom"}
        graph.entity_documents["person-tom"] = {"doc.md"}
        from datetime import datetime
        graph.last_build = datetime.utcnow()

        graph.invalidate_cache()

        # last_build cleared → next build_graph() will rebuild
        assert graph.last_build is None
        # Stale data preserved so concurrent readers see something during rebuild
        assert len(graph.nodes) == 1
        assert len(graph.edges) == 1
        assert len(graph.document_entities) == 1
        assert len(graph.entity_documents) == 1
