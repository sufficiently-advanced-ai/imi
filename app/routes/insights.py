"""Routes for insights API endpoints."""

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..domain.entities.services import get_entity_repository
from ..services.agent_tools import AgentToolRegistry
from ..services.knowledge_graph import KnowledgeGraph

router = APIRouter()
logger = logging.getLogger(__name__)

# Import the dependency function
from .agent_tools import get_tool_registry  # noqa: E402 — dependency import after router setup


async def get_insights_from_tools(
    registry: AgentToolRegistry,
    type_filter: str | None = None,
    min_confidence: float | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    """Get insights from agent tools with filtering and pagination."""
    try:
        print(f"DEBUG: get_insights_from_tools called with registry: {registry}")
        print(f"DEBUG: registry type: {type(registry)}")

        # Use the generate_insights tool to get insights
        insights_tool = registry.get_tool("generate_insights")
        if not insights_tool:
            return {"insights": [], "total": 0}

        # Execute tool to get insights
        # For now, we'll return mock data - in production this would call the actual tool
        all_insights = []

        # Generate mock insights for demo
        for i in range(1500):
            insight_type = ["opportunity", "risk", "prediction"][i % 3]
            confidence = 0.95 - (i * 0.005)
            timestamp = datetime.now() - timedelta(days=i % 30)

            insight = {
                "id": f"insight-{i}",
                "type": insight_type,
                "description": f"AI discovered pattern indicating {insight_type} #{i}",
                "confidence": confidence,
                "entities": [f"person-{i % 10}", f"project-{i % 5}"],
                "sources": [f"meeting-{i % 20}.md", f"doc-{i % 15}.md"],
                "timestamp": timestamp.isoformat(),
                "metadata": {
                    "category": "strategic" if i % 2 == 0 else "operational",
                    "impact": "high" if confidence > 0.8 else "medium",
                    "value": f"${(i + 1) * 100}K"
                    if insight_type == "opportunity"
                    else None,
                },
            }

            # Apply filters
            if type_filter and insight["type"] != type_filter:
                continue
            if min_confidence and insight["confidence"] < min_confidence:
                continue
            if start_date and timestamp < start_date:
                continue
            if end_date and timestamp > end_date:
                continue

            all_insights.append(insight)

        # Apply pagination
        total = len(all_insights)
        paginated_insights = all_insights[offset : offset + limit]

        return {
            "insights": paginated_insights,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    except Exception as e:
        import traceback

        logger.error(f"Error getting insights: {str(e)}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


async def get_connections_graph(
    entity_type: str | None = None, depth: int = 2
) -> dict[str, Any]:
    """Get knowledge graph connections."""
    try:
        # Initialize and build knowledge graph with registry for proper entity resolution
        registry = get_entity_repository()
        kg = KnowledgeGraph(registry=registry)
        await kg.build_graph()

        # Get the actual graph nodes and edges
        nodes = []
        edges = []

        # Transform nodes to expected format
        for node_id, node in kg.nodes.items():
            # Skip document nodes - we don't want to show documents as nodes
            if node.type == "document":
                continue

            # Filter by entity type if specified
            if entity_type and node.type != entity_type:
                continue

            nodes.append(
                {
                    "id": node_id,
                    "type": node.type,
                    "label": node.name,
                    "metadata": {
                        **node.metadata,
                        "last_updated": node.last_updated.isoformat()
                        if node.last_updated
                        else None,
                        "document_count": len(node.documents),
                    },
                }
            )

        # Transform edges to expected format
        for _edge_key, edge in kg.edges.items():
            # Only include edges where both nodes are in our filtered set
            node_ids = {n["id"] for n in nodes}
            if edge.source in node_ids and edge.target in node_ids:
                edges.append(
                    {
                        "source": edge.source,
                        "target": edge.target,
                        "type": edge.relationship_type,
                        "strength": edge.strength,
                    }
                )

        return {"nodes": nodes, "edges": edges}

    except Exception as e:
        logger.error(f"Error getting connections: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


async def get_timeline_data(
    start_date: str | None = None,
    end_date: str | None = None,
    entity: str | None = None,
    aggregation: str = "day",
) -> dict[str, Any]:
    """Get timeline data for patterns."""
    try:
        timeline = []

        # Generate timeline data
        if not start_date:
            start = datetime.now() - timedelta(days=30)
        else:
            start = datetime.fromisoformat(start_date)

        if not end_date:
            end = datetime.now()
        else:
            end = datetime.fromisoformat(end_date)

        current = start
        while current <= end:
            patterns = []

            # Generate patterns for this date
            for j in range(3):
                pattern = {
                    "id": f"pattern-{current.strftime('%Y%m%d')}-{j}",
                    "type": "escalation" if j % 2 == 0 else "opportunity",
                    "description": f"Pattern detected on {current.strftime('%Y-%m-%d')}",
                    "entities": [f"entity-{j}", entity] if entity else [f"entity-{j}"],
                    "confidence": 0.85 - (j * 0.1),
                }

                if not entity or entity in pattern["entities"]:
                    patterns.append(pattern)

            if patterns:
                timeline.append(
                    {
                        "date": current.strftime("%Y-%m-%d"),
                        "patterns": patterns,
                        "count": len(patterns),
                    }
                )

            # Move to next period based on aggregation
            if aggregation == "day":
                current += timedelta(days=1)
            elif aggregation == "week":
                current += timedelta(weeks=1)
            elif aggregation == "month":
                # Approximate month as 30 days
                current += timedelta(days=30)
            else:
                current += timedelta(days=1)

        return {"timeline": timeline}

    except Exception as e:
        logger.error(f"Error getting timeline: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/insights")
async def get_insights(
    insight_type: str | None = Query(
        None, alias="type", description="Filter by insight type"
    ),
    min_confidence: float | None = Query(
        None, description="Minimum confidence score"
    ),
    start_date: datetime | None = Query(
        None, description="Start date for filtering"
    ),
    end_date: datetime | None = Query(None, description="End date for filtering"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(20, ge=1, le=1000, description="Items per page"),
    registry: AgentToolRegistry = Depends(get_tool_registry),
):
    """Get paginated insights with optional filtering."""
    try:
        print("DEBUG: get_insights endpoint called")
        print(f"DEBUG: registry parameter: {registry}")
        print(f"DEBUG: registry class: {registry.__class__.__name__}")
        return await get_insights_from_tools(
            registry=registry,
            type_filter=insight_type,
            min_confidence=min_confidence,
            start_date=start_date,
            end_date=end_date,
            offset=offset,
            limit=limit,
        )
    except Exception as e:
        import traceback

        print(f"ERROR in get_insights endpoint: {str(e)}")
        print(traceback.format_exc())
        raise


@router.get("/api/insights/connections")
async def get_connections(
    entity_type: str | None = Query(None, description="Filter by entity type"),
    depth: int = Query(2, ge=1, le=5, description="Connection depth limit"),
):
    """Get knowledge graph connections."""
    return await get_connections_graph(entity_type=entity_type, depth=depth)


@router.get("/api/insights/timeline")
async def get_timeline(
    start_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    entity: str | None = Query(None, description="Filter by entity"),
    aggregation: str = Query(
        "day", pattern="^(day|week|month)$", description="Aggregation period"
    ),
):
    """Get timeline view of patterns."""
    return await get_timeline_data(
        start_date=start_date, end_date=end_date, entity=entity, aggregation=aggregation
    )
