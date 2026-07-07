"""
Domain Graph Enhancement Routes for Issue #269.

Provides API endpoints for enhanced graph functionality including
node details, graph controls, and export capabilities.

Uses unified KnowledgeGraph via GraphVisualizationAdapter which includes
semantic relationships discovered during meeting processing.
"""

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.core.domain_config import get_domain_config
from app.services.graph import GraphVisualizationAdapter, get_knowledge_graph

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/domain-graph", tags=["domain-graph-enhancements"])


class ZoomRequest(BaseModel):
    zoom_level: float


class LayoutRequest(BaseModel):
    layout_type: str


class NodeActionRequest(BaseModel):
    action: str


@router.get("/node/{node_id}")
async def get_node_details(
    node_id: str,
    x_domain_id: str = Header(None, alias="X-Domain-ID"),
):
    """Get detailed information about a specific node."""
    try:
        domain_config = get_domain_config()
        if x_domain_id and x_domain_id != domain_config.id:
            raise HTTPException(status_code=409, detail=f"Requested domain '{x_domain_id}' does not match active domain '{domain_config.id}'.")

        # Build graph using legacy adapter (reliable for visualization)
        try:
            knowledge_graph = get_knowledge_graph()
            adapter = GraphVisualizationAdapter(knowledge_graph, domain_config)
            graph_data = await adapter.build_visualization_data(include_semantic_edges=True)
        except Exception as e:
            logger.error(f"Failed to build enhanced domain graph: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to build graph for domain {x_domain_id}: {str(e)}",
            )

        # Validate graph data structure
        if not graph_data or "nodes" not in graph_data:
            logger.error("Invalid graph data structure returned")
            raise HTTPException(status_code=500, detail="Invalid graph data structure")

        # Find the specific node
        node_data = None
        for node in graph_data.get("nodes", []):
            if node.get("id") == node_id:
                node_data = node
                break

        if not node_data:
            raise HTTPException(status_code=404, detail=f"Node {node_id} not found")

        # Calculate metrics for the node
        connections = []
        for edge in graph_data.get("edges", []):
            if edge.get("source") == node_id:
                connections.append(
                    {
                        "target": edge.get("target"),
                        "relationship": edge.get("relationshipType", "related"),
                    }
                )
            elif edge.get("target") == node_id:
                connections.append(
                    {
                        "target": edge.get("source"),
                        "relationship": edge.get("relationshipType", "related"),
                    }
                )

        # Enhance with connection count metrics
        node_data["metrics"] = {
            "degree_centrality": len(connections),
            "connection_count": len(connections),
        }
        node_data["connections"] = connections

        return node_data

    except Exception as e:
        logger.error(f"Error getting node details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/node/{node_id}/connections")
async def get_connected_nodes(
    node_id: str,
    x_domain_id: str = Header(None, alias="X-Domain-ID"),
):
    """Get nodes connected to a specific node."""
    try:
        domain_config = get_domain_config()
        if x_domain_id and x_domain_id != domain_config.id:
            raise HTTPException(status_code=409, detail=f"Requested domain '{x_domain_id}' does not match active domain '{domain_config.id}'.")

        # Build graph using legacy adapter (reliable for visualization)
        try:
            knowledge_graph = get_knowledge_graph()
            adapter = GraphVisualizationAdapter(knowledge_graph, domain_config)
            graph_data = await adapter.build_visualization_data(include_semantic_edges=True)
        except Exception as e:
            logger.error(f"Failed to build enhanced domain graph: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to build graph for domain {x_domain_id}: {str(e)}",
            )

        # Validate graph data structure
        if not graph_data or "edges" not in graph_data or "nodes" not in graph_data:
            logger.error("Invalid graph data structure returned")
            raise HTTPException(status_code=500, detail="Invalid graph data structure")

        # Find connected nodes
        connected_nodes = []
        relationships = []
        node_ids = set()

        for edge in graph_data.get("edges", []):
            if edge.get("source") == node_id:
                target_id = edge.get("target")
                if target_id not in node_ids:
                    node_ids.add(target_id)
                    relationships.append(
                        {
                            "source": node_id,
                            "target": target_id,
                            "type": edge.get("relationshipType", "related"),
                        }
                    )
            elif edge.get("target") == node_id:
                source_id = edge.get("source")
                if source_id not in node_ids:
                    node_ids.add(source_id)
                    relationships.append(
                        {
                            "source": source_id,
                            "target": node_id,
                            "type": edge.get("relationshipType", "related"),
                        }
                    )

        # Get node details for connected nodes
        for node in graph_data.get("nodes", []):
            if node.get("id") in node_ids:
                connected_nodes.append(
                    {
                        "id": node.get("id"),
                        "entityType": node.get("entityType"),
                        "name": node.get("name", node.get("canonical_name", "")),
                    }
                )

        return {"connected_nodes": connected_nodes, "relationships": relationships}

    except Exception as e:
        logger.error(f"Error getting connected nodes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/controls/zoom")
async def set_zoom_level(
    request: ZoomRequest, x_domain_id: str = Header(alias="X-Domain-ID")
):
    """Set the zoom level for the graph."""
    try:
        return {"zoom_level": request.zoom_level, "success": True}
    except Exception as e:
        logger.error(f"Error setting zoom level: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/controls/layout")
async def set_layout_type(
    request: LayoutRequest, x_domain_id: str = Header(alias="X-Domain-ID")
):
    """Set the layout algorithm for the graph."""
    try:
        valid_layouts = ["force-directed", "hierarchical", "circular", "grid"]
        if request.layout_type not in valid_layouts:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid layout type. Must be one of: {valid_layouts}",
            )

        return {"layout": request.layout_type, "success": True}
    except HTTPException:
        raise  # Re-raise HTTPException without wrapping
    except Exception as e:
        logger.error(f"Error setting layout type: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Note: Export functionality is now handled on the frontend using Cytoscape's built-in export capabilities
# The frontend can export the graph directly as PNG or SVG without server-side processing


@router.post("/node/{node_id}/action")
async def perform_node_action(
    node_id: str,
    request: NodeActionRequest,
    x_domain_id: str = Header(alias="X-Domain-ID"),
):
    """Perform an action on a node."""
    try:
        valid_actions = [
            "filter_by_node",
            "hide_node",
            "expand_connections",
            "focus_node",
        ]
        if request.action not in valid_actions:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid action. Must be one of: {valid_actions}",
            )

        return {"action": request.action, "node_id": node_id, "success": True}
    except HTTPException:
        raise  # Re-raise HTTPException without wrapping
    except Exception as e:
        logger.error(f"Error performing node action: {e}")
        raise HTTPException(status_code=500, detail=str(e))
