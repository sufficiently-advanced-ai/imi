"""
Memory API routes - Organizational Memory Agent endpoints.

Provides the main API interface for the organizational memory capabilities
described in GitHub issue #5.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..agents.memory_agent import memory_agent
from ..services.knowledge_graph import get_knowledge_graph

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryQueryRequest(BaseModel):
    """Request model for memory queries."""

    question: str
    context_hint: str | None = None
    max_documents: int = 10


class MemoryQueryResponse(BaseModel):
    """Response model for memory queries."""

    answer: str
    confidence: str
    sources: list[str]
    related_entities: list[dict[str, Any]]
    connection_analysis: dict[str, Any]
    query_metadata: dict[str, Any]


class ContextSurfaceRequest(BaseModel):
    """Request model for context surfacing."""

    current_document: str
    work_context: str | None = None


class ContextSurfaceResponse(BaseModel):
    """Response model for context surfacing."""

    related_documents: list[dict[str, Any]]
    related_entities: list[dict[str, Any]]
    suggestions: list[str]
    context_analysis: str
    document_entities: list[dict[str, Any]]


class TopicSearchResponse(BaseModel):
    """Response model for topic search."""

    documents: list[dict[str, Any]]
    related_entities: list[dict[str, Any]]
    topic_analysis: dict[str, Any]


class EntityEvolutionResponse(BaseModel):
    """Response model for entity evolution tracking."""

    entity: dict[str, Any] | None
    timeline: list[dict[str, Any]]
    evolution_analysis: dict[str, Any]
    current_connections: int
    related_entities: list[dict[str, Any]]
    error: str | None = None


class GraphStatsResponse(BaseModel):
    """Response model for knowledge graph statistics."""

    total_nodes: int
    total_edges: int
    entity_counts: dict[str, int]
    total_documents: int
    last_build: str | None
    connection_density: float


class GraphVisualizationNode(BaseModel):
    """Node model for graph visualization."""

    id: str
    label: str
    type: str
    metadata: dict[str, Any]


class GraphVisualizationEdge(BaseModel):
    """Edge model for graph visualization."""

    source: str
    target: str
    type: str
    strength: float


class GraphVisualizationResponse(BaseModel):
    """Response model for graph visualization data."""

    nodes: list[GraphVisualizationNode]
    edges: list[GraphVisualizationEdge]


@router.post("/query", response_model=MemoryQueryResponse)
async def query_organizational_memory(request: MemoryQueryRequest):
    """
    Main organizational memory query endpoint.

    This fulfills the core requirement from GitHub issue #5:
    "Perfect recall and connection of all organizational knowledge"

    Example Query: "What did we decide about pricing in Q3?"
    """
    try:
        result = await memory_agent.query_memory(
            question=request.question,
            context_hint=request.context_hint,
            max_documents=request.max_documents,
        )

        return MemoryQueryResponse(**result)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Memory query failed: {str(e)}")


@router.post("/context", response_model=ContextSurfaceResponse)
async def surface_context(request: ContextSurfaceRequest):
    """
    Surface relevant context during work sessions.

    Analyzes the current document and suggests related documents,
    entities, and context that might be relevant to the user's work.
    """
    try:
        result = await memory_agent.surface_context(
            current_document=request.current_document, work_context=request.work_context
        )

        return ContextSurfaceResponse(**result)

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Context surfacing failed: {str(e)}"
        )


@router.get("/search/{topic}", response_model=TopicSearchResponse)
async def search_by_topic(
    topic: str,
    max_results: int = Query(default=10, ge=1, le=50),
    force_refresh: bool = Query(
        default=False,
        description="Force refresh the knowledge graph cache before searching",
    ),
):
    """
    Search for all documents and entities related to a specific topic.

    Enables auto-organization by topic as required in the GitHub issue.
    """
    try:
        # Force graph rebuild if requested
        if force_refresh:
            await get_knowledge_graph().build_graph(force_rebuild=True)

        result = await memory_agent.find_by_topic(topic, max_results)
        return TopicSearchResponse(**result)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Topic search failed: {str(e)}")


@router.get("/entity/{entity_name}/evolution", response_model=EntityEvolutionResponse)
async def track_entity_evolution(
    entity_name: str,
    entity_type: str | None = Query(
        default=None, enum=["person", "project", "team", "topic"]
    ),
):
    """
    Track knowledge evolution over time for a specific entity.

    Shows how knowledge about people, projects, or topics has evolved
    through the git history and document changes.
    """
    try:
        result = await memory_agent.track_entity_evolution(entity_name, entity_type)
        return EntityEvolutionResponse(**result)

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Entity evolution tracking failed: {str(e)}"
        )


@router.get("/entity/{entity_name}/related")
async def get_related_entities(
    entity_name: str,
    entity_type: str | None = Query(
        default=None, enum=["person", "project", "team", "topic"]
    ),
    max_results: int = Query(default=10, ge=1, le=50),
    force_refresh: bool = Query(
        default=False,
        description="Force refresh the knowledge graph cache before searching",
    ),
):
    """
    Find entities related to the specified entity.

    Discovers connections between people, projects, teams, and topics
    based on co-occurrence and relationship patterns.
    """
    try:
        # Build knowledge graph if needed (force rebuild if requested)
        await get_knowledge_graph().build_graph(force_rebuild=force_refresh)

        # Find the entity
        entity = get_knowledge_graph().get_entity_by_name(entity_name, entity_type)
        if not entity:
            raise HTTPException(
                status_code=404, detail=f"Entity '{entity_name}' not found"
            )

        # Get related entities
        related = await get_knowledge_graph().find_related_entities(
            entity["id"], max_results
        )

        return {
            "entity": entity,
            "related_entities": related,
            "total_connections": len(related),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Related entity search failed: {str(e)}"
        )


@router.get("/graph/stats", response_model=GraphStatsResponse)
async def get_knowledge_graph_stats():
    """
    Get statistics about the knowledge graph.

    Provides metrics for connection density score and overall graph health
    as specified in the GitHub issue KPIs.
    """
    try:
        await get_knowledge_graph().build_graph()
        stats = get_knowledge_graph()._get_graph_stats()
        return GraphStatsResponse(**stats)

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get graph statistics: {str(e)}"
        )


@router.post("/graph/rebuild")
async def rebuild_knowledge_graph(
    force: bool = Query(default=False),
    clean: bool = Query(default=False, description="Wipe Neo4j before rebuilding"),
):
    """
    Rebuild the knowledge graph from all document metadata.

    Useful for ensuring the graph is up-to-date with the latest
    document changes and entity extractions.

    Set clean=true to wipe Neo4j first (removes stale data from previous builds).
    Requires force=true when using clean=true.
    """
    try:
        if clean and not force:
            raise HTTPException(
                status_code=400,
                detail="clean=true requires force=true to confirm destructive rebuild",
            )

        stats = await get_knowledge_graph().build_graph(force_rebuild=force, clean=clean)

        return {
            "status": "success",
            "message": "Knowledge graph rebuilt successfully",
            "stats": stats,
            "performance": {
                "meets_connection_density_target": stats["connection_density"] > 0.8,
                "connection_density_score": stats["connection_density"] * 100,
            },
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to rebuild knowledge graph: {str(e)}"
        )


@router.get("/graph/entities")
async def list_entities(
    entity_type: str | None = Query(
        default=None, enum=["person", "project", "team", "topic", "document"]
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """
    List entities in the knowledge graph with pagination.

    Enables browsing of auto-organized information by type.
    """
    try:
        await get_knowledge_graph().build_graph()

        # Filter entities by type if specified
        entities = []
        for node in get_knowledge_graph().nodes.values():
            if entity_type is None or node.type == entity_type:
                entities.append(
                    {
                        "id": node.id,
                        "name": node.name,
                        "type": node.type,
                        "document_count": len(node.documents),
                        "connection_count": len(node.connections),
                        "last_updated": node.last_updated.isoformat(),
                        "metadata": node.metadata,
                    }
                )

        # Sort by connection count (most connected first)
        entities.sort(key=lambda x: x["connection_count"], reverse=True)

        # Apply pagination
        total = len(entities)
        paginated_entities = entities[offset : offset + limit]

        return {
            "entities": paginated_entities,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
            "summary": {
                "total_entities": total,
                "entity_type_filter": entity_type,
                "most_connected": paginated_entities[0]["name"]
                if paginated_entities
                else None,
            },
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to list entities: {str(e)}"
        )


@router.get("/documents/{document_path:path}/connections")
async def get_document_connections(document_path: str):
    """
    Get all entities and connections for a specific document.

    Shows how a document fits into the broader organizational knowledge.
    """
    try:
        await get_knowledge_graph().build_graph()

        # Get entities in this document
        doc_entities = get_knowledge_graph().document_entities.get(document_path, set())

        if not doc_entities:
            return {
                "document": document_path,
                "entities": [],
                "connections": [],
                "analysis": "No entities found in this document",
            }

        # Get detailed information about each entity
        entities = []
        connections = []

        for entity_id in doc_entities:
            if entity_id.startswith("doc:"):
                continue  # Skip document nodes

            if entity_id in get_knowledge_graph().nodes:
                node = get_knowledge_graph().nodes[entity_id]
                entities.append(
                    {
                        "id": node.id,
                        "name": node.name,
                        "type": node.type,
                        "total_documents": len(node.documents),
                        "total_connections": len(node.connections),
                    }
                )

                # Get connections between entities in this document
                for other_entity_id in doc_entities:
                    if other_entity_id != entity_id and not other_entity_id.startswith(
                        "doc:"
                    ):
                        # Find strongest edge between these two entities
                        candidates = [
                            ev for ev in get_knowledge_graph().edges.values()
                            if (ev.source == entity_id and ev.target == other_entity_id)
                            or (ev.source == other_entity_id and ev.target == entity_id)
                        ]
                        edge = max(candidates, key=lambda e: e.strength) if candidates else None
                        if edge:
                            connections.append(
                                {
                                    "source": get_knowledge_graph()
                                    .nodes[entity_id]
                                    .name,
                                    "target": get_knowledge_graph()
                                    .nodes[other_entity_id]
                                    .name,
                                    "relationship_type": edge.relationship_type,
                                    "strength": edge.strength,
                                }
                            )

        return {
            "document": document_path,
            "entities": entities,
            "connections": connections,
            "analysis": {
                "total_entities": len(entities),
                "total_connections": len(connections),
                "most_connected_entity": max(
                    entities, key=lambda x: x["total_connections"]
                )["name"]
                if entities
                else None,
                "entity_types": list(set(e["type"] for e in entities)),
            },
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get document connections: {str(e)}"
        )


@router.get("/graph/visualization", response_model=GraphVisualizationResponse)
async def get_graph_visualization(
    include_documents: bool = Query(
        default=False, description="Include document nodes in visualization"
    ),
):
    """
    Get knowledge graph data formatted for visualization.

    Returns nodes and edges in the format expected by the frontend
    ConnectionGraph component. This endpoint serves real knowledge graph
    data instead of mock data.

    By default, document nodes are filtered out to show only meaningful
    entity relationships (people, projects, teams).
    """
    try:
        # Build/update the knowledge graph
        await get_knowledge_graph().build_graph()

        # Transform nodes for visualization, filtering documents unless requested
        visualization_nodes = []
        filtered_node_ids = set()

        for node in get_knowledge_graph().nodes.values():
            # Skip document nodes unless explicitly requested
            if node.type == "document" and not include_documents:
                continue

            visualization_nodes.append(
                GraphVisualizationNode(
                    id=node.id,
                    label=node.name
                    if node.name
                    else node.id,  # Fallback to ID if name is empty
                    type=node.type,
                    metadata={
                        "documentCount": len(node.documents),
                        "connectionCount": len(node.connections),
                        **node.metadata,  # Include original metadata
                    },
                )
            )
            filtered_node_ids.add(node.id)

        # Transform edges for visualization, filtering those connected to hidden nodes
        visualization_edges = []
        for edge in get_knowledge_graph().edges.values():
            # Only include edges where both nodes are in the filtered set
            if edge.source in filtered_node_ids and edge.target in filtered_node_ids:
                visualization_edges.append(
                    GraphVisualizationEdge(
                        source=edge.source,
                        target=edge.target,
                        type=edge.relationship_type,
                        strength=edge.strength,
                    )
                )

        return GraphVisualizationResponse(
            nodes=visualization_nodes, edges=visualization_edges
        )

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to get graph visualization data: {str(e)}"
        )


@router.post("/graph/cleanup-test-data")
async def cleanup_test_data(rebuild: bool = Query(default=False)):
    """
    Remove test data from the knowledge graph cache.

    This endpoint identifies and removes nodes that appear to be test data
    based on naming patterns (e.g., "Alpha Legal Reviews", "Test User").

    Args:
        rebuild: If True, rebuild the graph after cleanup
    """
    try:
        # Ensure graph is loaded
        await get_knowledge_graph().build_graph()

        # Perform cleanup
        removed_count = await get_knowledge_graph().cleanup_test_data(rebuild=rebuild)

        # Get updated stats
        stats = get_knowledge_graph()._get_graph_stats() if rebuild else None

        return {
            "status": "success",
            "message": f"Removed {removed_count} test data nodes",
            "removed_count": removed_count,
            "stats": stats,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to cleanup test data: {str(e)}"
        )
