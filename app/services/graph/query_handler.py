"""
Knowledge Graph Query Handler

Handles search, query, and retrieval operations for the knowledge graph.
"""

import logging
from collections import Counter, defaultdict
from typing import Any

from .models import GraphEdge, GraphNode

logger = logging.getLogger(__name__)


class GraphQueryHandler:
    """Handles query and search operations for the knowledge graph."""

    def __init__(self, nodes: dict[str, GraphNode], edges: dict[tuple, GraphEdge], document_entities: dict[str, set]):
        self.nodes = nodes
        self.edges = edges
        self.document_entities = document_entities

    def _find_edge_between(self, entity_a: str, entity_b: str) -> GraphEdge | None:
        """Find strongest edge between two entities, preferring A→B direction."""
        directional: list[GraphEdge] = []
        reverse: list[GraphEdge] = []
        for edge in self.edges.values():
            if edge.source == entity_a and edge.target == entity_b:
                directional.append(edge)
            elif edge.source == entity_b and edge.target == entity_a:
                reverse.append(edge)
        candidates = directional or reverse
        return max(candidates, key=lambda e: e.strength) if candidates else None

    async def find_related_entities(
        self, entity_id: str, max_results: int = 10
    ) -> list[dict[str, Any]]:
        """Find entities related to the given entity."""
        if entity_id not in self.nodes:
            return []

        # Get direct connections
        related = []
        node = self.nodes[entity_id]

        for connected_id in node.connections:
            if connected_id in self.nodes:
                connected_node = self.nodes[connected_id]

                # Find the edge between these entities
                edge = self._find_edge_between(entity_id, connected_id)

                related.append(
                    {
                        "entity": {
                            "id": connected_node.id,
                            "name": connected_node.name,
                            "type": connected_node.type,
                            "metadata": connected_node.metadata,
                        },
                        "relationship": {
                            "type": edge.relationship_type if edge else "unknown",
                            "strength": edge.strength if edge else 0.0,
                            "shared_documents": len(
                                set(node.documents) & set(connected_node.documents)
                            ),
                        },
                    }
                )

        # Sort by relationship strength and shared documents
        related.sort(
            key=lambda x: (
                x["relationship"]["strength"],
                x["relationship"]["shared_documents"],
            ),
            reverse=True,
        )

        return related[:max_results]

    async def find_contextual_documents(
        self, query_entities: list[str], max_results: int = 10
    ) -> list[dict[str, Any]]:
        """Find documents that are contextually relevant to the given entities."""
        document_scores = defaultdict(float)
        document_entity_counts = defaultdict(int)

        # Score documents based on entity presence and relationships
        for entity_id in query_entities:
            if entity_id not in self.nodes:
                continue

            node = self.nodes[entity_id]

            # Direct documents containing this entity
            for doc_path in node.documents:
                document_scores[doc_path] += 1.0
                document_entity_counts[doc_path] += 1

            # Documents containing related entities (with lower weight)
            for related_entity_id in node.connections:
                if related_entity_id in self.nodes:
                    related_node = self.nodes[related_entity_id]
                    edge = self._find_edge_between(entity_id, related_entity_id)
                    relationship_weight = edge.strength if edge else 0.1

                    for doc_path in related_node.documents:
                        document_scores[doc_path] += 0.3 * relationship_weight

        # Convert to result format
        results = []
        for doc_path, score in document_scores.items():
            # Skip document nodes from results
            if doc_path.startswith("doc:"):
                continue

            results.append(
                {
                    "path": doc_path,
                    "relevance_score": score,
                    "matching_entities": document_entity_counts[doc_path],
                    "total_entities": len(self.document_entities.get(doc_path, set())),
                }
            )

        # Sort by relevance score
        results.sort(key=lambda x: x["relevance_score"], reverse=True)

        return results[:max_results]

    async def search_by_topic(
        self, topic: str, max_results: int = 10
    ) -> list[dict[str, Any]]:
        """Search for documents and entities related to a topic with fuzzy matching."""
        if not topic or not topic.strip():
            return []

        topic_lower = topic.lower().strip()
        matching_entities = []

        logger.debug(f"Searching for topic: '{topic}' (normalized: '{topic_lower}')")

        # 1. Exact name match first (highest priority)
        exact_match = self.get_entity_by_name(topic)
        if exact_match:
            matching_entities.append(exact_match["id"])
            logger.debug(f"Found exact match: {exact_match['id']}")

        # 2. Fuzzy name matching for entities
        for entity_id, node in self.nodes.items():
            # Skip document entities for topic search
            if node.type == "document":
                continue

            node_name_lower = node.name.lower()

            # Full name substring match
            if topic_lower in node_name_lower and entity_id not in matching_entities:
                matching_entities.append(entity_id)
                logger.debug(f"Name substring match: {entity_id} ('{node.name}')")
                continue

            # Individual word matching for multi-word queries
            topic_words = topic_lower.split()
            name_words = node_name_lower.split()

            if len(topic_words) > 1:
                # All topic words must be found in the name
                if all(
                    any(topic_word in name_word for name_word in name_words)
                    for topic_word in topic_words
                ):
                    if entity_id not in matching_entities:
                        matching_entities.append(entity_id)
                        logger.debug(f"Multi-word match: {entity_id} ('{node.name}')")

        # 3. Use contextual document search to find related content
        if matching_entities:
            return await self.find_contextual_documents(matching_entities, max_results)
        else:
            logger.debug(f"No matching entities found for topic: '{topic}'")
            return []

    def get_entity_by_name(
        self, name: str, entity_type: str | None = None
    ) -> dict[str, Any] | None:
        """Find an entity by name with fuzzy matching (exact > starts-with > contains)."""
        name_lower = name.lower()
        best_match = None
        best_score = 0  # 3=exact, 2=starts-with, 1=contains

        for node in self.nodes.values():
            if entity_type and node.type != entity_type:
                continue
            node_name = node.name.lower()
            if node_name == name_lower:
                score = 3
            elif node_name.startswith(name_lower):
                score = 2
            elif name_lower in node_name:
                score = 1
            else:
                continue
            if score > best_score:
                best_score = score
                best_match = node

        if best_match:
            return {
                "id": best_match.id,
                "name": best_match.name,
                "type": best_match.type,
                "metadata": best_match.metadata,
                "document_count": len(best_match.documents),
                "connection_count": len(best_match.connections),
            }
        return None

    def get_graph_stats(self) -> dict[str, Any]:
        """Get statistics about the knowledge graph."""
        entity_counts = Counter(node.type for node in self.nodes.values())

        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "entity_counts": dict(entity_counts),
            "total_documents": len(self.document_entities),
            "connection_density": len(self.edges)
            / max(1, len(self.nodes) * (len(self.nodes) - 1) / 2),
        }

    def identify_test_data_nodes(self) -> set[str]:
        """Identify nodes that appear to be test data based on patterns."""
        test_nodes = set()
        for node_id, node in self.nodes.items():
            # Check exact name matches
            if node.name in [
                "Alpha Legal Reviews",
                "Test User",
                "Test Team",
                "Demo Project",
                "Example User",
                "Sample Data",
            ]:
                test_nodes.add(node_id)
                continue

            # Check for test patterns in names
            name_lower = node.name.lower()
            test_patterns = [
                "test",
                "demo",
                "example",
                "sample",
                "placeholder",
                "dummy",
                "fake",
            ]

            if any(pattern in name_lower for pattern in test_patterns):
                test_nodes.add(node_id)

        return test_nodes
