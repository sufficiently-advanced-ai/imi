"""
Knowledge Graph Service - Backward Compatibility Wrapper

This module provides backward compatibility for the refactored knowledge graph.
All functionality has been moved to the app.services.graph package.

DEPRECATED: Import from app.services.graph instead:
  from app.services.graph import KnowledgeGraph, GraphNode, GraphEdge, get_knowledge_graph
"""

import warnings
from dataclasses import dataclass

# Import everything from the new modular structure
from .graph import GraphEdge, GraphNode, KnowledgeGraph, Neo4jKnowledgeGraph, get_knowledge_graph

# Issue deprecation warning
warnings.warn(
    "Importing from app.services.knowledge_graph is deprecated. "
    "Use 'from app.services.graph import KnowledgeGraph' instead.",
    DeprecationWarning,
    stacklevel=2
)


@dataclass(slots=True)
class SemanticEdge:
    """Semantic relationship with evidence and reasoning."""
    from_entity: str  # Source entity ID
    to_entity: str  # Target entity ID
    relationship_type: str  # Domain-defined relationship type
    strength: float  # 0.0-1.0 confidence score
    evidence: str  # Quote or supporting text
    reasoning: str  # Why this relationship was inferred
    source: str  # Where it came from
    created_at: float  # Unix timestamp


# Re-export everything for backward compatibility
__all__ = ['GraphEdge', 'GraphNode', 'KnowledgeGraph', 'Neo4jKnowledgeGraph', 'SemanticEdge', 'get_knowledge_graph']
