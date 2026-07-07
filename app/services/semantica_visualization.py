"""
Semantica Visualization — Graph data → Cytoscape.js adapter.

Replaces:
- graph/visualization_adapter.py (446 LOC)

Converts Semantica graph data (from GraphStore or in-memory caches)
into the Cytoscape.js format expected by the frontend knowledge explorer.
"""

import logging
import re
from typing import Any

from app.model_schemas.domain_config import DomainConfiguration
from app.services.semantica_config import (
    entity_type_to_label,
    relationship_type_to_neo4j,
)

logger = logging.getLogger(__name__)

# Strict regex: only allow alphanumeric characters and underscores in identifiers
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_cypher_identifier(value: str) -> str:
    """Validate that a string is safe to interpolate into a Cypher query.

    Only alphanumeric characters and underscores are allowed.
    Raises ValueError if the identifier is not safe.
    """
    if not _SAFE_IDENTIFIER_RE.match(value):
        raise ValueError(f"Unsafe Cypher identifier rejected: {value!r}")
    return value


class SemanticaVisualizationAdapter:
    """Converts graph data to Cytoscape.js format for frontend visualization."""

    def __init__(
        self,
        graph_store: Any,
        domain_config: DomainConfiguration | None = None,
    ):
        self.graph_store = graph_store
        self.domain = domain_config

    async def build_visualization_data(
        self,
        entity_types: list[str] | None = None,
        relationship_types: list[str] | None = None,
        include_semantic_edges: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        """Build Cytoscape.js-compatible visualization data.

        Args:
            entity_types: Optional filter for entity types to include.
            relationship_types: Optional filter for relationship types.
            include_semantic_edges: Whether to include meeting-derived edges.

        Returns:
            Dict with 'nodes' and 'edges' lists in Cytoscape format.
        """
        try:
            # Query all nodes from graph store
            nodes_data = await self._fetch_nodes(entity_types)
            edges_data = await self._fetch_edges(
                relationship_types, include_semantic_edges
            )

            # Convert to Cytoscape format
            cyto_nodes = []
            for node in nodes_data:
                cyto_nodes.append(self._node_to_cytoscape(node))

            # Build set of valid node IDs so we can drop orphan edges
            valid_node_ids = {n.get("id", "") for n in nodes_data}

            cyto_edges = []
            for edge in edges_data:
                source = edge.get("source", "")
                target = edge.get("target", "")
                # Only include edges whose both endpoints are in the node set
                if source not in valid_node_ids or target not in valid_node_ids:
                    continue
                cyto_edges.append(self._edge_to_cytoscape(edge))

            return {"nodes": cyto_nodes, "edges": cyto_edges}

        except Exception as e:
            logger.error(f"Failed to build visualization data: {e}")
            return {"nodes": [], "edges": []}

    async def _fetch_nodes(
        self,
        entity_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch nodes from the graph store."""
        try:
            if entity_types:
                labels = [_validate_cypher_identifier(entity_type_to_label(t)) for t in entity_types]
                nodes = []
                for label in labels:
                    raw = self.graph_store.execute_query(
                        f"MATCH (n:{label}:Entity) RETURN n",
                    )
                    rows = raw.get("records", []) if isinstance(raw, dict) else (raw or [])
                    nodes.extend([r.get("n", {}) for r in rows])
            else:
                raw = self.graph_store.execute_query(
                    "MATCH (n:Entity) RETURN n",
                )
                rows = raw.get("records", []) if isinstance(raw, dict) else (raw or [])
                nodes = [r.get("n", {}) for r in rows]

            return nodes

        except Exception as e:
            logger.error(f"Failed to fetch nodes: {e}")
            return []

    async def _fetch_edges(
        self,
        relationship_types: list[str] | None = None,
        include_semantic: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch edges from the graph store."""
        try:
            if relationship_types:
                edges = []
                for rel_type in relationship_types:
                    neo4j_type = _validate_cypher_identifier(relationship_type_to_neo4j(rel_type))
                    raw = self.graph_store.execute_query(
                        f"MATCH (a:Entity)-[r:{neo4j_type}]->(b:Entity) "
                        f"RETURN a.id AS source, b.id AS target, type(r) AS rel_type, properties(r) AS props",
                    )
                    rows = raw.get("records", []) if isinstance(raw, dict) else (raw or [])
                    edges.extend(rows)
            else:
                cypher = "MATCH (a:Entity)-[r]->(b:Entity) "
                if not include_semantic:
                    cypher += "WHERE NOT type(r) STARTS WITH 'SEMANTIC_' "
                cypher += "RETURN a.id AS source, b.id AS target, type(r) AS rel_type, properties(r) AS props"
                raw = self.graph_store.execute_query(cypher)
                edges = raw.get("records", []) if isinstance(raw, dict) else (raw or [])

            return edges

        except Exception as e:
            logger.error(f"Failed to fetch edges: {e}")
            return []

    def _node_to_cytoscape(self, node: dict[str, Any]) -> dict[str, Any]:
        """Convert a graph node to Cytoscape.js format."""
        node_id = node.get("id", "")
        name = node.get("name", node_id)
        entity_type = node.get("entity_type", "entity")

        # Extract all attributes
        skip_keys = {"id", "name", "entity_type", "canonical_name", "is_archived"}
        attributes = {
            k: v for k, v in node.items()
            if k not in skip_keys and v is not None
        }

        return {
            "id": node_id,
            "entity_type": entity_type,
            "attributes": {"name": name, **attributes},
            "metadata": {
                "file_path": node.get("file_path", ""),
                "canonical_name": node.get("canonical_name", name),
            },
        }

    def _edge_to_cytoscape(self, edge: dict[str, Any]) -> dict[str, Any]:
        """Convert a graph edge to Cytoscape.js format."""
        return {
            "source": edge.get("source", ""),
            "target": edge.get("target", ""),
            "relationship_type": edge.get("rel_type", "RELATED_TO"),
            "strength": edge.get("props", {}).get("strength", 1.0),
            "context": edge.get("props", {}).get("context", []),
        }

    def to_cytoscape_elements(
        self,
        nodes: list[dict],
        edges: list[dict],
    ) -> dict[str, list[dict]]:
        """Convert raw node/edge lists to Cytoscape elements format.

        This is a simpler conversion for when you already have the data
        and just need Cytoscape formatting.
        """
        cyto_nodes = [self._node_to_cytoscape(n) for n in nodes]
        cyto_edges = [self._edge_to_cytoscape(e) for e in edges]
        return {"nodes": cyto_nodes, "edges": cyto_edges}
