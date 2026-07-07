"""
Graph Visualization Adapter - Knowledge Graph Unification

This adapter bridges the unified KnowledgeGraph with the visual graph viewer,
replacing DomainGraphService as the data source for the graph visualization API.

The adapter:
1. Converts KnowledgeGraph nodes/edges to the format expected by EnhancedCytoscapeGraph
2. Applies domain-aware visual properties (colors, shapes, sizes)
3. Maintains backward compatibility with existing frontend code
"""

import logging
import re
from typing import Any

from app.model_schemas.domain_config import DomainConfiguration

from .models import GraphEdge, GraphNode

logger = logging.getLogger(__name__)


def _serialize_value(value: Any) -> Any:
    """Serialize Neo4j types and other non-JSON-safe values for the API response."""
    if value is None:
        return None
    # Handle neo4j.time.DateTime and similar
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)):
        return value
    # Fallback: convert to string
    return str(value)


class GraphVisualizationAdapter:
    """Adapts KnowledgeGraph data for the visual graph viewer.

    This adapter provides the same API shape as DomainGraphService but
    uses the unified KnowledgeGraph as its data source. This enables
    the graph viewer to display relationships discovered during meetings
    and other enrichment processes.
    """

    # Visual Enhancement Constants (matching DomainGraphService)
    MIN_NODE_SIZE = 10
    MAX_NODE_SIZE = 200
    MIN_OPACITY = 0.1
    MAX_OPACITY = 1.0
    MIN_EDGE_THICKNESS = 0.5
    MAX_EDGE_THICKNESS = 10.0

    # Default colors by entity type
    DEFAULT_COLORS = {
        "person": "#4ecdc4",
        "project": "#45b7d1",
        "organization": "#96ceb4",
        "team": "#feca57",
        "document": "#a0a0a0",
        "signal": "#F97316",  # Orange — distinct from all entity types
        "default": "#666",
    }

    # Edge colors by relationship type
    EDGE_COLORS = {
        "reports_to": "#ff6b6b",
        "works_with": "#4ecdc4",
        "manages": "#45b7d1",
        "collaborates_with": "#96ceb4",
        "discussed_topic": "#feca57",
        "co_occurrence": "#888888",
        "mentions": "#F97316",     # Orange, matching signal node
        "assigned_to": "#F97316",
        "default": "#666",
    }

    # Edge thickness by relationship type
    EDGE_THICKNESS = {
        "reports_to": 3.0,
        "manages": 3.5,
        "collaborates_with": 2.5,
        "discussed_topic": 2.0,
        "works_with": 2.0,
        "co_occurrence": 1.0,
        "mentions": 1.5,
        "assigned_to": 2.0,
        "default": 1.5,
    }

    # Edge styles by relationship type
    EDGE_STYLES = {
        "reports_to": "solid",
        "manages": "solid",
        "collaborates_with": "solid",
        "discussed_topic": "dashed",
        "works_with": "dashed",
        "co_occurrence": "dotted",
        "mentions": "dashed",      # Dashed to distinguish from entity relationships
        "assigned_to": "dashed",
        "default": "solid",
    }

    def __init__(
        self,
        knowledge_graph: Any,
        domain_config: DomainConfiguration | None = None
    ):
        """Initialize the adapter.

        Args:
            knowledge_graph: KnowledgeGraph or Neo4jKnowledgeGraph instance
                             (both expose .nodes, .edges, .semantic_edges)
            domain_config: Optional domain configuration for visual styling
        """
        self.kg = knowledge_graph
        self.domain_config = domain_config

        # Build dynamic color map from domain config if available
        if domain_config:
            self._domain_colors = self._build_domain_colors(domain_config)
        else:
            self._domain_colors = {}

    @staticmethod
    def _build_domain_colors(config: DomainConfiguration) -> dict[str, str]:
        """Generate colors for entity types from domain config.

        Assigns a visually distinct color to each entity type defined in
        the domain, so that switching domains automatically updates the
        color palette without hardcoding.
        """
        palette = [
            "#4ecdc4", "#45b7d1", "#96ceb4", "#feca57", "#ff6b6b",
            "#a29bfe", "#fd79a8", "#6c5ce7", "#00b894", "#e17055",
            "#0984e3", "#d63031",
        ]
        colors = {}
        for i, entity_id in enumerate(config.entities.keys()):
            colors[entity_id] = palette[i % len(palette)]
        return colors

    async def build_visualization_data(
        self,
        entity_types: list[str] | None = None,
        relationship_types: list[str] | None = None,
        display_config: dict[str, Any] | None = None,
        include_semantic_edges: bool = True,
        include_signals: bool = True,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Build graph data in format expected by EnhancedCytoscapeGraph.

        Args:
            entity_types: Optional filter for specific entity types
            relationship_types: Optional filter for specific relationship types
            display_config: Display configuration with colors, shapes, icons
            include_semantic_edges: Whether to include semantic edges from meetings
            include_signals: If False, omit Signal nodes and their MENTIONS/
                ASSIGNED_TO edges. Route-level callers pass False to keep the
                default graph view navigable; chat/internal callers leave True.
            limit: Max nodes to return, ranked by connection count. None = no cap.
                Route-level callers pass a cap (e.g. 500) so the UI stays fast.

        Returns:
            Dict with 'nodes', 'edges', and 'truncation' — the latter reports
            total_available_nodes (after type filtering, before limit) so the
            UI can surface 'Showing N of M'.
        """
        display_config = display_config or {}

        # Signal-specific relationships — filtered out along with signal nodes
        SIGNAL_EDGE_TYPES = {"mentions", "assigned_to"}

        # Ensure graph is built
        await self.kg.build_graph()

        # Pass 1: collect candidate nodes after type/signal/document filters.
        # Don't convert to visualization format yet — we may trim below.
        candidate_nodes: list[GraphNode] = []
        for _node_id, node in self.kg.nodes.items():
            if entity_types and node.type not in entity_types:
                continue

            # Documents clutter the view — only include if explicitly requested
            if node.type == "document" and "document" not in (entity_types or []):
                continue

            # Signals are off by default; 10-20 per meeting dominates the graph
            if node.type == "signal" and not include_signals:
                continue

            candidate_nodes.append(node)

        total_available_nodes = len(candidate_nodes)

        # Compute each candidate's *visible* degree before ranking. Raw
        # `len(n.connections)` counts every neighbour including signal-labeled
        # ones; if signals are hidden those edges are stripped below, so
        # signal-heavy entities would win the top-`limit` cut on inflated
        # scores and push core nodes out of the view. Ranking by visible
        # degree makes the truncation reflect what the user actually sees.
        if not include_signals:
            candidate_ids = {n.id for n in candidate_nodes}

            def _visible_degree(n: GraphNode) -> int:
                # Only neighbours that are themselves in the candidate set
                # (i.e. not hidden signals or document nodes) count.
                return sum(1 for c in n.connections if c in candidate_ids)
        else:
            def _visible_degree(n: GraphNode) -> int:
                return len(n.connections)

        # Rank by visible connection count desc, cap to `limit`. Most-connected
        # nodes are the structural backbone of the graph — dropping low-degree
        # nodes preserves the view's "shape" better than a random cap would.
        if limit is not None and len(candidate_nodes) > limit:
            candidate_nodes.sort(key=_visible_degree, reverse=True)
            candidate_nodes = candidate_nodes[:limit]
            truncated = True
        else:
            truncated = False

        nodes = [self._convert_node_to_visualization(n, display_config) for n in candidate_nodes]
        node_ids = {n["id"] for n in nodes}

        # Convert co-occurrence / domain edges from KnowledgeGraph
        edges = []
        edge_id_counter = 1

        for _edge_key, edge in self.kg.edges.items():
            if relationship_types and edge.relationship_type not in relationship_types:
                continue

            # Drop signal-relation edges when signals are excluded
            if not include_signals and edge.relationship_type in SIGNAL_EDGE_TYPES:
                continue

            # Edge endpoints must both be in our (possibly capped) node set
            if edge.source not in node_ids or edge.target not in node_ids:
                continue

            vis_edge = self._convert_edge_to_visualization(edge, f"edge-{edge_id_counter}")
            edges.append(vis_edge)
            edge_id_counter += 1

        # Include semantic edges (from meeting processing) if requested
        if include_semantic_edges and hasattr(self.kg, 'semantic_edges'):
            for _edge_key, semantic_edge in self.kg.semantic_edges.items():
                if relationship_types and semantic_edge.relationship_type not in relationship_types:
                    continue

                if not include_signals and semantic_edge.relationship_type in SIGNAL_EDGE_TYPES:
                    continue

                if semantic_edge.from_entity not in node_ids or semantic_edge.to_entity not in node_ids:
                    continue

                vis_edge = self._convert_semantic_edge_to_visualization(
                    semantic_edge, f"edge-{edge_id_counter}"
                )
                edges.append(vis_edge)
                edge_id_counter += 1

        logger.info(
            f"Built visualization with {len(nodes)} nodes and {len(edges)} edges "
            f"(include_signals={include_signals}, limit={limit}, "
            f"truncated={truncated}, total_available={total_available_nodes})"
        )
        return {
            "nodes": nodes,
            "edges": edges,
            "truncation": {
                "truncated": truncated,
                "total_available_nodes": total_available_nodes,
                "limit": limit,
            },
        }

    def convert_subgraph(
        self,
        nodes: dict[str, GraphNode],
        edges: dict[Any, GraphEdge],
        display_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convert a pre-built subgraph (nodes + edges) to visualization format.

        Used by the neighborhood endpoint: the kg.neighborhood() method runs
        focused Cypher queries and returns its own (nodes, edges) dicts rather
        than loading the whole graph into self.kg.nodes. This method styles
        them the same way build_visualization_data() does, so a neighborhood
        view is visually indistinguishable from a filtered full-graph view.
        """
        display_config = display_config or {}
        vis_nodes = [
            self._convert_node_to_visualization(n, display_config)
            for n in nodes.values()
        ]
        node_ids = {n["id"] for n in vis_nodes}
        vis_edges = []
        for idx, edge in enumerate(edges.values(), start=1):
            if edge.source not in node_ids or edge.target not in node_ids:
                continue
            vis_edges.append(self._convert_edge_to_visualization(edge, f"edge-{idx}"))
        return {"nodes": vis_nodes, "edges": vis_edges}

    def _convert_node_to_visualization(
        self,
        node: GraphNode,
        display_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert a GraphNode to visualization format.

        Args:
            node: The GraphNode to convert
            display_config: Display configuration

        Returns:
            Node dictionary in visualization format
        """
        entity_type = node.type

        # Build attributes from node metadata, serializing any non-JSON types
        attributes = {"name": node.name}
        if node.metadata:
            for key, value in node.metadata.items():
                if key not in ["id", "entity_type", "created_at", "updated_at"]:
                    attributes[key] = _serialize_value(value)

        # Get visual properties
        color = self._get_node_color(entity_type, display_config)
        shape = self._get_node_shape(entity_type, display_config)
        size = self._get_node_size(node)
        opacity = self._get_node_opacity(node)

        return {
            "id": node.id,
            "entity_type": entity_type,
            "entityType": entity_type,  # For frontend compatibility
            "name": node.name,
            "canonical_name": node.name,
            "attributes": attributes,
            "metadata": {
                "created_at": node.last_updated.isoformat() if node.last_updated else None,
                "updated_at": node.last_updated.isoformat() if node.last_updated else None,
                "documents": list(node.documents),
                "connection_count": len(node.connections),
            },
            # Visual properties
            "size": size,
            "color": color,
            "shape": shape,
            "opacity": opacity,
        }

    def _convert_edge_to_visualization(
        self,
        edge: GraphEdge,
        edge_id: str
    ) -> dict[str, Any]:
        """Convert a GraphEdge to visualization format.

        Args:
            edge: The GraphEdge to convert
            edge_id: Unique edge identifier

        Returns:
            Edge dictionary in visualization format
        """
        rel_type = edge.relationship_type

        return {
            "id": edge_id,
            "source": edge.source,
            "target": edge.target,
            "relationship_type": rel_type,
            "relationshipType": rel_type,  # For frontend compatibility
            "strength": edge.strength,
            "attributes": {
                "context": edge.context,
                "strength": edge.strength,
            },
            # Visual properties
            "thickness": self._get_edge_thickness(rel_type),
            "color": self._get_edge_color(rel_type),
            "style": self._get_edge_style(rel_type),
        }

    def _convert_semantic_edge_to_visualization(
        self,
        semantic_edge: Any,
        edge_id: str
    ) -> dict[str, Any]:
        """Convert a SemanticEdge to visualization format.

        Args:
            semantic_edge: The SemanticEdge to convert
            edge_id: Unique edge identifier

        Returns:
            Edge dictionary in visualization format
        """
        rel_type = semantic_edge.relationship_type

        return {
            "id": edge_id,
            "source": semantic_edge.from_entity,
            "target": semantic_edge.to_entity,
            "relationship_type": rel_type,
            "relationshipType": rel_type,  # For frontend compatibility
            "strength": semantic_edge.strength,
            "attributes": {
                "evidence": semantic_edge.evidence,
                "reasoning": semantic_edge.reasoning,
                "source": semantic_edge.source,
                "strength": semantic_edge.strength,
            },
            # Visual properties - semantic edges are slightly thicker
            "thickness": self._get_edge_thickness(rel_type) * 1.2,
            "color": self._get_edge_color(rel_type),
            "style": self._get_edge_style(rel_type),
        }

    # Visual property methods

    def _get_node_color(self, entity_type: str, display_config: dict[str, Any]) -> str:
        """Get color for node based on entity type.

        Priority: display_config > domain-derived > DEFAULT_COLORS > fallback
        """
        colors = display_config.get("colors", {})
        color = (
            colors.get(entity_type)
            or self._domain_colors.get(entity_type)
            or self.DEFAULT_COLORS.get(entity_type)
            or self.DEFAULT_COLORS["default"]
        )
        return self._validate_color(color)

    def _get_node_shape(self, entity_type: str, display_config: dict[str, Any]) -> str:
        """Get shape for node based on entity type."""
        shapes = display_config.get("shapes", {})
        return shapes.get(entity_type, "circle")

    def _get_node_size(self, node: GraphNode) -> int:
        """Calculate node size based on connections and importance."""
        base_size = 30

        # Increase size based on number of connections
        connection_count = len(node.connections)
        size = base_size + min(connection_count * 5, 50)

        # Increase size based on document appearances
        document_count = len(node.documents)
        size += min(document_count * 2, 20)

        # Check for importance in metadata
        importance = node.metadata.get("importance", 0)
        if isinstance(importance, (int, float)):
            size += min(importance * 10, 50)

        return max(self.MIN_NODE_SIZE, min(size, self.MAX_NODE_SIZE))

    def _get_node_opacity(self, node: GraphNode) -> float:
        """Get opacity for node."""
        opacity = 0.8

        # Reduce opacity for nodes with fewer connections
        if len(node.connections) == 0:
            opacity = 0.5

        # Check for active status
        if node.metadata.get("active") is False:
            opacity = 0.4

        return max(self.MIN_OPACITY, min(opacity, self.MAX_OPACITY))

    def _get_edge_thickness(self, relationship_type: str) -> float:
        """Get thickness for edge based on relationship type."""
        thickness = self.EDGE_THICKNESS.get(relationship_type, self.EDGE_THICKNESS["default"])
        return max(self.MIN_EDGE_THICKNESS, min(thickness, self.MAX_EDGE_THICKNESS))

    def _get_edge_color(self, relationship_type: str) -> str:
        """Get color for edge based on relationship type."""
        return self.EDGE_COLORS.get(relationship_type, self.EDGE_COLORS["default"])

    def _get_edge_style(self, relationship_type: str) -> str:
        """Get style for edge based on relationship type."""
        return self.EDGE_STYLES.get(relationship_type, self.EDGE_STYLES["default"])

    def _validate_color(self, color: str) -> str:
        """Validate and return a color value, defaulting if invalid."""
        if not color:
            return "#666"

        # Check for valid hex color
        if re.match(r"^#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?$", color):
            return color

        # Check for valid named colors
        valid_named_colors = {
            "red", "blue", "green", "yellow", "orange", "purple", "pink",
            "brown", "black", "white", "gray", "grey", "cyan", "magenta"
        }

        if color.lower() in valid_named_colors:
            return color.lower()

        return "#666"

    async def get_statistics(self) -> dict[str, Any]:
        """Get statistics about the graph.

        Assumes build_visualization_data() was already called, so the graph
        is populated. Skips redundant build_graph() call.

        Returns:
            Statistics dictionary with node/edge counts by type
        """

        # Count nodes by type
        nodes_by_type = {}
        for node in self.kg.nodes.values():
            entity_type = node.type
            nodes_by_type[entity_type] = nodes_by_type.get(entity_type, 0) + 1

        # Count edges by type
        edges_by_type = {}
        for edge in self.kg.edges.values():
            rel_type = edge.relationship_type
            edges_by_type[rel_type] = edges_by_type.get(rel_type, 0) + 1

        # Count semantic edges separately
        semantic_edges_count = 0
        if hasattr(self.kg, 'semantic_edges'):
            semantic_edges_count = len(self.kg.semantic_edges)
            for semantic_edge in self.kg.semantic_edges.values():
                rel_type = semantic_edge.relationship_type
                edges_by_type[rel_type] = edges_by_type.get(rel_type, 0) + 1

        return {
            "total_nodes": len(self.kg.nodes),
            "total_edges": len(self.kg.edges) + semantic_edges_count,
            "nodes_by_type": nodes_by_type,
            "edges_by_type": edges_by_type,
            "semantic_edges_count": semantic_edges_count,
            "domain": self.domain_config.id if self.domain_config else "default",
        }
