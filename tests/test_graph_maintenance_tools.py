"""Tests for Graph Maintenance Tools — CRUD operations on the knowledge graph.

Covers two layers:
1. Service layer: Neo4jKnowledgeGraph.{add,update,delete,merge}_node/edge
2. Tool layer: AgentTool subclasses that wrap the service layer

All tests mock Neo4j — no real database required.
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
    """Build a domain config with person + project entities and one relationship."""
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
    mock_git = MagicMock()
    mock_git.repo_path = "/tmp/test-repo"
    kg._git_ops = mock_git
    return kg


# ──────────────────────────────────────────────────────────────
# Service Layer: add_node
# ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_node_valid_entity_type(graph, mock_neo4j):
    """Happy path: add a person node."""
    mock_neo4j.execute_write = AsyncMock(return_value=[])

    result = await graph.add_node(entity_type="person", name="Alice Smith")

    assert result["id"] == "person-alice-smith"
    assert result["name"] == "Alice Smith"
    assert result["type"] == "person"
    # Node should be in cache
    assert "person-alice-smith" in graph.nodes
    assert graph.nodes["person-alice-smith"].name == "Alice Smith"
    # Neo4j write should have been called
    mock_neo4j.execute_write.assert_called_once()


@pytest.mark.asyncio
async def test_add_node_invalid_entity_type(graph):
    """Rejects unknown entity types."""
    with pytest.raises(ValueError, match="Invalid entity type 'unknown_type'"):
        await graph.add_node(entity_type="unknown_type", name="Test")


@pytest.mark.asyncio
async def test_add_node_generates_id(graph, mock_neo4j):
    """Auto-generates ID from name + type, sanitizing special chars."""
    mock_neo4j.execute_write = AsyncMock(return_value=[])

    result = await graph.add_node(entity_type="project", name="CRM Modernization")
    assert result["id"] == "project-crm-modernization"

    # Special characters are stripped
    result2 = await graph.add_node(entity_type="person", name="Alice (HR)")
    assert result2["id"] == "person-alice-hr"


@pytest.mark.asyncio
async def test_add_node_explicit_id(graph, mock_neo4j):
    """Uses explicit ID when provided."""
    mock_neo4j.execute_write = AsyncMock(return_value=[])

    result = await graph.add_node(
        entity_type="person",
        name="Bob",
        entity_id="person-custom-bob",
    )

    assert result["id"] == "person-custom-bob"


@pytest.mark.asyncio
async def test_add_node_with_properties(graph, mock_neo4j):
    """Passes through extra properties."""
    mock_neo4j.execute_write = AsyncMock(return_value=[])

    result = await graph.add_node(
        entity_type="person",
        name="Carol",
        properties={"role": "Engineer"},
    )

    assert result["properties"] == {"role": "Engineer"}
    assert graph.nodes["person-carol"].metadata == {"role": "Engineer"}


# ──────────────────────────────────────────────────────────────
# Service Layer: update_node
# ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_node_exists(graph, mock_neo4j):
    """Update properties on an existing node."""
    # Seed the node in Neo4j response
    mock_neo4j.execute_read = AsyncMock(return_value=[{
        "n": {
            "id": "person-alice",
            "name": "Alice",
            "entity_type": "person",
            "updated_at": "2026-01-01T00:00:00",
        }
    }])
    mock_neo4j.execute_write = AsyncMock(return_value=[])
    # Also seed in-memory cache
    graph.nodes["person-alice"] = GraphNode(
        id="person-alice", name="Alice", type="person", metadata={}
    )

    result = await graph.update_node(
        entity_id="person-alice",
        properties={"role": "Director"},
    )

    assert "role" in result["updated_fields"]
    assert graph.nodes["person-alice"].metadata["role"] == "Director"


@pytest.mark.asyncio
async def test_update_node_not_found(graph, mock_neo4j):
    """Raises error when node doesn't exist."""
    mock_neo4j.execute_read = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="not found"):
        await graph.update_node(entity_id="person-nonexistent", properties={"role": "x"})


# ──────────────────────────────────────────────────────────────
# Service Layer: delete_node
# ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_node_cascade(graph, mock_neo4j):
    """Cascade delete removes node + relationships."""
    # Seed entity in Neo4j
    mock_neo4j.execute_read = AsyncMock(side_effect=[
        # First call: get_entity_by_id
        [{"n": {"id": "person-alice", "name": "Alice", "entity_type": "person", "updated_at": "2026-01-01"}}],
        # Second call: count relationships
        [{"rel_count": 3}],
    ])
    mock_neo4j.execute_write = AsyncMock(return_value=[])
    graph.nodes["person-alice"] = GraphNode(
        id="person-alice", name="Alice", type="person"
    )

    result = await graph.delete_node(entity_id="person-alice", cascade=True)

    assert result["id"] == "person-alice"
    assert result["relationships_removed"] == 3
    assert "person-alice" not in graph.nodes


@pytest.mark.asyncio
async def test_delete_node_no_cascade_with_relationships(graph, mock_neo4j):
    """Fails when cascade=False and relationships exist."""
    mock_neo4j.execute_read = AsyncMock(side_effect=[
        [{"n": {"id": "person-alice", "name": "Alice", "entity_type": "person", "updated_at": "2026-01-01"}}],
        [{"rel_count": 2}],
    ])

    with pytest.raises(ValueError, match="has 2 relationships"):
        await graph.delete_node(entity_id="person-alice", cascade=False)


@pytest.mark.asyncio
async def test_delete_node_updates_cache(graph, mock_neo4j):
    """Verifies all caches are cleaned up after deletion."""
    mock_neo4j.execute_read = AsyncMock(side_effect=[
        [{"n": {"id": "person-alice", "name": "Alice", "entity_type": "person", "updated_at": "2026-01-01"}}],
        [{"rel_count": 0}],
    ])
    mock_neo4j.execute_write = AsyncMock(return_value=[])

    # Seed caches
    graph.nodes["person-alice"] = GraphNode(
        id="person-alice", name="Alice", type="person"
    )
    graph.nodes["person-bob"] = GraphNode(
        id="person-bob", name="Bob", type="person",
    )
    graph.nodes["person-bob"].connections.add("person-alice")
    edge_key = ("person-alice", "person-bob")
    graph.edges[edge_key] = GraphEdge(
        source="person-alice", target="person-bob",
        relationship_type="co_occurrence", strength=0.5,
    )
    graph.document_entities["doc.md"] = {"person-alice", "person-bob"}
    graph.entity_documents["person-alice"] = {"doc.md"}

    await graph.delete_node(entity_id="person-alice", cascade=True)

    assert "person-alice" not in graph.nodes
    assert edge_key not in graph.edges
    assert "person-alice" not in graph.document_entities.get("doc.md", set())
    assert "person-alice" not in graph.entity_documents
    assert "person-alice" not in graph.nodes["person-bob"].connections


@pytest.mark.asyncio
async def test_delete_node_not_found(graph, mock_neo4j):
    """Raises error when node doesn't exist."""
    mock_neo4j.execute_read = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="not found"):
        await graph.delete_node(entity_id="person-ghost")


# ──────────────────────────────────────────────────────────────
# Service Layer: add_edge
# ──────────────────────────────────────────────────────────────




@pytest.mark.asyncio
async def test_add_edge_invalid_type(graph):
    """Rejects unknown relationship types."""
    with pytest.raises(ValueError, match="Invalid relationship type"):
        await graph.add_edge(
            source_id="person-alice",
            target_id="project-crm",
            relationship_type="totally_fake",
        )


@pytest.mark.asyncio
async def test_add_edge_missing_source(graph, mock_neo4j):
    """Fails if source entity doesn't exist."""
    mock_neo4j.execute_read = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="Source entity.*not found"):
        await graph.add_edge(
            source_id="person-ghost",
            target_id="project-crm",
            relationship_type="has_projects",
        )


@pytest.mark.asyncio
async def test_add_edge_missing_target(graph, mock_neo4j):
    """Fails if target entity doesn't exist."""
    mock_neo4j.execute_read = AsyncMock(side_effect=[
        # Source exists
        [{"n": {"id": "person-alice", "name": "Alice", "entity_type": "person", "updated_at": "2026-01-01"}}],
        # Target does not
        [],
    ])

    with pytest.raises(ValueError, match="Target entity.*not found"):
        await graph.add_edge(
            source_id="person-alice",
            target_id="project-ghost",
            relationship_type="has_projects",
        )


# ──────────────────────────────────────────────────────────────
# Service Layer: update_edge
# ──────────────────────────────────────────────────────────────




@pytest.mark.asyncio
async def test_update_edge_not_found(graph, mock_neo4j):
    """Raises error when edge doesn't exist."""
    mock_neo4j.execute_read = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="not found"):
        await graph.update_edge(
            source_id="person-alice",
            target_id="project-crm",
            relationship_type="has_projects",
            properties={"strength": 0.8},
        )


# ──────────────────────────────────────────────────────────────
# Service Layer: delete_edge
# ──────────────────────────────────────────────────────────────




@pytest.mark.asyncio
async def test_delete_edge_not_found(graph, mock_neo4j):
    """Raises error when edge doesn't exist."""
    mock_neo4j.execute_read = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="not found"):
        await graph.delete_edge(
            source_id="person-alice",
            target_id="project-crm",
            relationship_type="has_projects",
        )


# ──────────────────────────────────────────────────────────────
# Service Layer: merge_nodes
# ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_merge_nodes_primary_wins(graph, mock_neo4j):
    """Primary properties take precedence."""
    mock_neo4j.execute_read = AsyncMock(side_effect=[
        # Primary exists
        [{"n": {"id": "person-alice", "name": "Alice", "entity_type": "person",
                "role": "Director", "updated_at": "2026-01-01"}}],
        # Duplicate exists
        [{"n": {"id": "person-alice-smith", "name": "Alice Smith", "entity_type": "person",
                "role": "Manager", "department": "Engineering", "updated_at": "2026-01-01"}}],
        # Relationship count
        [{"rel_count": 1}],
        # Outgoing rels
        [],
        # Incoming rels
        [],
        # Aliases
        [{"aliases": None}],
    ])
    mock_neo4j.execute_write = AsyncMock(return_value=[])
    graph.nodes["person-alice"] = GraphNode(
        id="person-alice", name="Alice", type="person",
        metadata={"role": "Director"},
    )
    graph.nodes["person-alice-smith"] = GraphNode(
        id="person-alice-smith", name="Alice Smith", type="person",
        metadata={"role": "Manager", "department": "Engineering"},
    )

    result = await graph.merge_nodes(
        primary_id="person-alice",
        duplicate_id="person-alice-smith",
        strategy="primary_wins",
    )

    assert result["id"] == "person-alice"
    assert "person-alice-smith" in result["aliases"]
    # Primary's role should win
    assert result["properties"]["role"] == "Director"
    # Duplicate's unique fields should be preserved
    assert result["properties"]["department"] == "Engineering"
    assert "person-alice-smith" not in graph.nodes


@pytest.mark.asyncio
async def test_merge_nodes_duplicate_wins(graph, mock_neo4j):
    """Duplicate properties take precedence."""
    mock_neo4j.execute_read = AsyncMock(side_effect=[
        [{"n": {"id": "person-alice", "name": "Alice", "entity_type": "person",
                "role": "Director", "updated_at": "2026-01-01"}}],
        [{"n": {"id": "person-alice-smith", "name": "Alice Smith", "entity_type": "person",
                "role": "VP", "updated_at": "2026-01-01"}}],
        [{"rel_count": 0}],
        [],
        [],
        [{"aliases": None}],
    ])
    mock_neo4j.execute_write = AsyncMock(return_value=[])
    graph.nodes["person-alice"] = GraphNode(
        id="person-alice", name="Alice", type="person",
        metadata={"role": "Director"},
    )
    graph.nodes["person-alice-smith"] = GraphNode(
        id="person-alice-smith", name="Alice Smith", type="person",
        metadata={"role": "VP"},
    )

    result = await graph.merge_nodes(
        primary_id="person-alice",
        duplicate_id="person-alice-smith",
        strategy="duplicate_wins",
    )

    assert result["properties"]["role"] == "VP"


@pytest.mark.asyncio
async def test_merge_nodes_merge_all(graph, mock_neo4j):
    """Merge all: primary wins on scalar conflicts, duplicate fills gaps."""
    mock_neo4j.execute_read = AsyncMock(side_effect=[
        [{"n": {"id": "person-alice", "name": "Alice", "entity_type": "person",
                "role": "Director", "updated_at": "2026-01-01"}}],
        [{"n": {"id": "person-alice-smith", "name": "Alice Smith", "entity_type": "person",
                "role": "Manager", "department": "Sales", "updated_at": "2026-01-01"}}],
        [{"rel_count": 0}],
        [],
        [],
        [{"aliases": None}],
    ])
    mock_neo4j.execute_write = AsyncMock(return_value=[])
    graph.nodes["person-alice"] = GraphNode(
        id="person-alice", name="Alice", type="person",
        metadata={"role": "Director"},
    )
    graph.nodes["person-alice-smith"] = GraphNode(
        id="person-alice-smith", name="Alice Smith", type="person",
        metadata={"role": "Manager", "department": "Sales"},
    )

    result = await graph.merge_nodes(
        primary_id="person-alice",
        duplicate_id="person-alice-smith",
        strategy="merge_all",
    )

    # merge_all: primary wins on scalar conflicts, duplicate fills gaps
    assert result["properties"]["role"] == "Director"  # primary wins
    assert result["properties"]["department"] == "Sales"  # duplicate fills gap




@pytest.mark.asyncio
async def test_merge_nodes_tracks_alias(graph, mock_neo4j):
    """Alias array should contain the duplicate ID."""
    mock_neo4j.execute_read = AsyncMock(side_effect=[
        [{"n": {"id": "person-alice", "name": "Alice", "entity_type": "person", "updated_at": "2026-01-01"}}],
        [{"n": {"id": "person-al", "name": "Al", "entity_type": "person", "updated_at": "2026-01-01"}}],
        [{"rel_count": 0}],
        [],
        [],
        # Existing aliases
        [{"aliases": ["person-old-alias"]}],
    ])
    mock_neo4j.execute_write = AsyncMock(return_value=[])
    graph.nodes["person-alice"] = GraphNode(id="person-alice", name="Alice", type="person")
    graph.nodes["person-al"] = GraphNode(id="person-al", name="Al", type="person")

    result = await graph.merge_nodes(
        primary_id="person-alice",
        duplicate_id="person-al",
    )

    assert "person-al" in result["aliases"]
    assert "person-old-alias" in result["aliases"]




@pytest.mark.asyncio
async def test_merge_nodes_invalid_strategy(graph):
    """Rejects invalid merge strategies."""
    with pytest.raises(ValueError, match="Invalid merge strategy"):
        await graph.merge_nodes(
            primary_id="person-alice",
            duplicate_id="person-bob",
            strategy="invalid",
        )


@pytest.mark.asyncio
async def test_merge_nodes_primary_not_found(graph, mock_neo4j):
    """Raises error when primary doesn't exist."""
    mock_neo4j.execute_read = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="Primary node.*not found"):
        await graph.merge_nodes(primary_id="person-ghost", duplicate_id="person-bob")


@pytest.mark.asyncio
async def test_merge_nodes_duplicate_not_found(graph, mock_neo4j):
    """Raises error when duplicate doesn't exist."""
    mock_neo4j.execute_read = AsyncMock(side_effect=[
        [{"n": {"id": "person-alice", "name": "Alice", "entity_type": "person", "updated_at": "2026-01-01"}}],
        [],
    ])

    with pytest.raises(ValueError, match="Duplicate node.*not found"):
        await graph.merge_nodes(primary_id="person-alice", duplicate_id="person-ghost")


# ──────────────────────────────────────────────────────────────
# Tool Layer Tests
# ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_graph_service():
    """A mock Neo4jKnowledgeGraph for tool-layer tests."""
    service = AsyncMock()
    return service


class TestAddNodeTool:

    @pytest.mark.asyncio
    async def test_validation_error(self, mock_graph_service):
        from app.services.tools.graph_node_tools import AddNodeTool

        mock_graph_service.add_node = AsyncMock(
            side_effect=ValueError("Invalid entity type 'bad'")
        )

        tool = AddNodeTool(None, None, None)
        with patch("app.services.tools.graph_node_tools._get_graph_service", return_value=mock_graph_service):
            result = await tool.execute({
                "entity_type": "bad",
                "name": "Test",
            })

        assert result.success is False
        assert "Invalid entity type" in result.error
class TestAddEdgeTool:
    @pytest.mark.asyncio
    async def test_happy_path(self, mock_graph_service):
        from app.services.tools.graph_edge_tools import AddEdgeTool

        mock_graph_service.add_edge = AsyncMock(return_value={
            "source": "person-alice", "target": "project-crm",
            "relationship_type": "has_projects", "properties": {},
        })

        tool = AddEdgeTool(None, None, None)
        with patch("app.services.tools.graph_edge_tools._get_graph_service", return_value=mock_graph_service):
            result = await tool.execute({
                "source_id": "person-alice",
                "target_id": "project-crm",
                "relationship_type": "has_projects",
            })

        assert result.success is True
        assert result.data["edge"]["source"] == "person-alice"
        assert result.data["created"] is True

    @pytest.mark.asyncio
    async def test_invalid_type(self, mock_graph_service):
        from app.services.tools.graph_edge_tools import AddEdgeTool

        mock_graph_service.add_edge = AsyncMock(
            side_effect=ValueError("Invalid relationship type 'fake'")
        )

        tool = AddEdgeTool(None, None, None)
        with patch("app.services.tools.graph_edge_tools._get_graph_service", return_value=mock_graph_service):
            result = await tool.execute({
                "source_id": "a",
                "target_id": "b",
                "relationship_type": "fake",
            })

        assert result.success is False
        assert "Invalid relationship type" in result.error


class TestUpdateEdgeTool:

    @pytest.mark.asyncio
    async def test_not_found(self, mock_graph_service):
        from app.services.tools.graph_edge_tools import UpdateEdgeTool

        mock_graph_service.update_edge = AsyncMock(
            side_effect=ValueError("Edge not found")
        )

        tool = UpdateEdgeTool(None, None, None)
        with patch("app.services.tools.graph_edge_tools._get_graph_service", return_value=mock_graph_service):
            result = await tool.execute({
                "source_id": "a", "target_id": "b",
                "relationship_type": "has_projects",
                "properties": {"x": 1},
            })

        assert result.success is False
        assert "not found" in result.error


class TestDeleteEdgeTool:
    @pytest.mark.asyncio
    async def test_happy_path(self, mock_graph_service):
        from app.services.tools.graph_edge_tools import DeleteEdgeTool

        mock_graph_service.delete_edge = AsyncMock(return_value={
            "source": "person-alice", "target": "project-crm",
            "relationship_type": "has_projects",
        })

        tool = DeleteEdgeTool(None, None, None)
        with patch("app.services.tools.graph_edge_tools._get_graph_service", return_value=mock_graph_service):
            result = await tool.execute({
                "source_id": "person-alice",
                "target_id": "project-crm",
                "relationship_type": "has_projects",
            })

        assert result.success is True
        assert result.data["deleted_edge"]["source"] == "person-alice"
        assert result.data["success"] is True

    @pytest.mark.asyncio
    async def test_not_found(self, mock_graph_service):
        from app.services.tools.graph_edge_tools import DeleteEdgeTool

        mock_graph_service.delete_edge = AsyncMock(
            side_effect=ValueError("Edge not found")
        )

        tool = DeleteEdgeTool(None, None, None)
        with patch("app.services.tools.graph_edge_tools._get_graph_service", return_value=mock_graph_service):
            result = await tool.execute({
                "source_id": "a", "target_id": "b",
                "relationship_type": "has_projects",
            })

        assert result.success is False
        assert "not found" in result.error
