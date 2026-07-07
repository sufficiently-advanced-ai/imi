"""
Knowledge Graph Service Package

Semantica-backed knowledge graph with Neo4j fallback.

Primary implementation: SemanticaKnowledge (app/services/semantica_knowledge.py)
Legacy fallback: Neo4jKnowledgeGraph (neo4j_graph.py)
In-memory fallback: KnowledgeGraph (builder.py) — used when Neo4j is unavailable

The factory function get_knowledge_graph() handles the selection automatically.
"""

# Core data models (used by all implementations)
# Legacy export for backward compatibility (builder.py still exists as fallback)
from .builder import KnowledgeGraph

# Factory (returns Semantica → Neo4j → in-memory fallback)
from .factory import clear_knowledge_graph_cache, get_knowledge_graph
from .models import GraphEdge, GraphNode

# Neo4j-specific exports (kept for backward compat)
from .neo4j_graph import Neo4jKnowledgeGraph
from .neo4j_models import build_node_properties, coerce_property_value
from .neo4j_schema import generate_schema_from_domain, initialize_schema_from_domain
from .signal_graph_writer import SignalGraphWriter

# Visualization adapter (works with all implementations)
from .visualization_adapter import GraphVisualizationAdapter

__all__ = [
    'GraphEdge',
    'GraphNode',
    'GraphVisualizationAdapter',
    'KnowledgeGraph',
    'Neo4jKnowledgeGraph',
    'SignalGraphWriter',
    'build_node_properties',
    'clear_knowledge_graph_cache',
    'coerce_property_value',
    'generate_schema_from_domain',
    'get_knowledge_graph',
    'initialize_schema_from_domain',
]
