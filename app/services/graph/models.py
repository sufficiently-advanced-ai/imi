"""
Knowledge Graph Data Models

Contains the core data structures for the knowledge graph:
- GraphNode: Represents an entity node
- GraphEdge: Represents relationships between entities
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class GraphNode:
    """Represents an entity node in the knowledge graph."""

    id: str
    name: str
    type: str  # person, project, team, document, topic
    metadata: dict[str, Any] = field(default_factory=dict)
    connections: set[str] = field(default_factory=set)
    documents: set[str] = field(default_factory=set)
    last_updated: datetime = field(default_factory=datetime.utcnow)


@dataclass
class GraphEdge:
    """Represents a relationship between two entities."""

    source: str
    target: str
    relationship_type: str  # collaboration, hierarchy, dependency, co_occurrence
    strength: float  # 0.0 to 1.0
    context: list[str] = field(
        default_factory=list
    )  # Documents where relationship appears
    created: datetime = field(default_factory=datetime.utcnow)
