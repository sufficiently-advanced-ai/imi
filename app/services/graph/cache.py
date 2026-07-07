"""
Knowledge Graph Caching and Persistence

Handles caching, loading, and persistence of the knowledge graph to improve performance.
"""

import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from .models import GraphEdge, GraphNode

logger = logging.getLogger(__name__)


def _serialize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Serialize metadata dict, converting date/datetime objects to ISO strings."""
    if not metadata:
        return metadata

    result = {}
    for key, value in metadata.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, date):
            result[key] = value.isoformat()
        elif isinstance(value, dict):
            result[key] = _serialize_metadata(value)
        elif isinstance(value, list):
            result[key] = [
                v.isoformat() if isinstance(v, (date, datetime)) else v
                for v in value
            ]
        else:
            result[key] = value
    return result


class GraphCache:
    """Handles caching and persistence for knowledge graph data."""

    def __init__(self, cache_path: str):
        self._cache_path = cache_path

    def is_cache_valid(self, last_build: datetime | None) -> bool:
        """Check if the cache is still valid (within 24 hours)."""
        if not last_build:
            return False

        cache_age = datetime.utcnow() - last_build
        return cache_age.total_seconds() < 24 * 3600  # 24 hours

    def invalidate_cache(self) -> None:
        """Invalidate the cache by deleting the cache file."""
        logger.info("Invalidating knowledge graph cache")
        if os.path.exists(self._cache_path):
            try:
                os.remove(self._cache_path)
                logger.debug(f"Removed cache file: {self._cache_path}")
            except Exception as e:
                logger.warning(f"Failed to remove cache file: {e}")

    async def save_to_cache(
        self,
        nodes: dict[str, GraphNode],
        edges: dict[tuple, GraphEdge],
        document_entities: dict[str, set],
        last_build: datetime | None
    ) -> None:
        """Save the knowledge graph to cache file."""
        try:
            cache_data = {
                "nodes": {
                    node_id: {
                        "id": node.id,
                        "name": node.name,
                        "type": node.type,
                        "metadata": _serialize_metadata(node.metadata),
                        "connections": list(node.connections),
                        "documents": list(node.documents),
                        "last_updated": node.last_updated.isoformat(),
                    }
                    for node_id, node in nodes.items()
                },
                "edges": {
                    f"{edge.source}|{edge.relationship_type}|{edge.target}": {
                        "source": edge.source,
                        "target": edge.target,
                        "relationship_type": edge.relationship_type,
                        "strength": edge.strength,
                        "context": edge.context,
                        "created": edge.created.isoformat(),
                    }
                    for edge in edges.values()
                },
                "document_entities": {
                    doc_path: list(entities)
                    for doc_path, entities in document_entities.items()
                },
                "last_build": last_build.isoformat() if last_build else None,
            }

            with open(self._cache_path, "w") as f:
                json.dump(cache_data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save knowledge graph cache: {e}")

    async def load_from_cache(
        self
    ) -> tuple[dict[str, GraphNode], dict[tuple, GraphEdge], dict[str, set], datetime | None]:
        """Load the knowledge graph from cache file."""
        nodes = {}
        edges = {}
        document_entities = {}
        last_build = None
        defaultdict(set)

        try:
            if not os.path.exists(self._cache_path):
                return nodes, edges, document_entities, last_build

            with open(self._cache_path) as f:
                cache_data = json.load(f)

            # Restore nodes
            for node_id, node_data in cache_data.get("nodes", {}).items():
                nodes[node_id] = GraphNode(
                    id=node_data["id"],
                    name=node_data["name"],
                    type=node_data["type"],
                    metadata=node_data["metadata"],
                    connections=set(node_data["connections"]),
                    documents=set(node_data["documents"]),
                    last_updated=datetime.fromisoformat(node_data["last_updated"]),
                )

            # Restore edges
            for edge_key, edge_data in cache_data.get("edges", {}).items():
                parts = edge_key.split("|")
                if len(parts) == 3:
                    # New format: source|rel_type|target
                    edge_tuple = (parts[0], parts[1], parts[2])
                elif len(parts) == 2:
                    # Legacy format: source|target
                    source, target = parts[0], parts[1]
                    edge_tuple = (source, edge_data.get("relationship_type", "unknown"), target)
                else:
                    logger.warning("Skipping malformed edge key in cache: %r", edge_key)
                    continue
                edges[edge_tuple] = GraphEdge(
                    source=edge_data["source"],
                    target=edge_data["target"],
                    relationship_type=edge_data["relationship_type"],
                    strength=edge_data["strength"],
                    context=edge_data["context"],
                    created=datetime.fromisoformat(edge_data["created"]),
                )

            # Restore document associations
            document_entities = {
                doc_path: set(entities)
                for doc_path, entities in cache_data.get(
                    "document_entities", {}
                ).items()
            }

            if cache_data.get("last_build"):
                last_build = datetime.fromisoformat(cache_data["last_build"])

        except Exception as e:
            logger.error(f"Failed to load knowledge graph cache: {e}")
            # Return empty data on corruption
            nodes.clear()
            edges.clear()
            document_entities.clear()

        return nodes, edges, document_entities, last_build
