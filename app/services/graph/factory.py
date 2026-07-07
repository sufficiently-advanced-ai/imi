"""
Knowledge Graph Factory

Provides factory function for creating the global knowledge graph instance.

get_knowledge_graph() returns the legacy Neo4jKnowledgeGraph (or in-memory fallback)
for backward compatibility with visualization and route handlers.

get_semantica_knowledge() returns the SemanticaKnowledge instance for
search, extraction, decisions, and new capabilities.
"""

import logging

logger = logging.getLogger(__name__)

# --- Domain graph API response cache (TTL-based) ---
_graph_response_cache: dict[str, dict] = {}
_graph_response_cache_time: dict[str, float] = {}
GRAPH_RESPONSE_CACHE_TTL = 60  # seconds


def get_graph_response_cache() -> tuple[dict[str, dict], dict[str, float]]:
    """Return the response cache dicts for the domain graph API."""
    return _graph_response_cache, _graph_response_cache_time


def invalidate_graph_response_cache():
    """Clear the domain graph API response cache."""
    _graph_response_cache.clear()
    _graph_response_cache_time.clear()
    logger.info("Domain graph response cache invalidated")

# Global knowledge graph instance - use lazy initialization to avoid circular dependencies
_knowledge_graph = None
_using_fallback = False  # Track if we fell back to in-memory builder


def get_knowledge_graph():
    """Get the knowledge graph instance for the current tenant.

    Tenant-scoped accessor (Phase 4.1): resolves the current tenant's container
    and returns its graph. In single-tenant mode this is the one default
    container, whose ``GraphBackend`` delegates to
    ``_resolve_default_knowledge_graph()`` below — i.e. behavior is unchanged.

    NOTE: This always returns the LEGACY graph (not SemanticaKnowledge).
    Use get_semantica_knowledge() for Semantica-specific features.
    """
    from app.core.tenancy.context import current_tenant

    return current_tenant().graph


def _resolve_default_knowledge_graph():
    """Legacy lazy-init of the process-global knowledge graph singleton.

    This is the original ``get_knowledge_graph`` body, retained verbatim and
    reached through the single-tenant ``DefaultGraphBackend``. It keeps the
    module-global ``_knowledge_graph`` / ``_using_fallback`` state so the
    existing ``reset_knowledge_graph`` / ``clear_knowledge_graph_cache`` helpers
    continue to operate on it unchanged.
    """
    global _knowledge_graph, _using_fallback

    # If we have a Neo4j-backed graph, return it
    if _knowledge_graph is not None and not _using_fallback:
        return _knowledge_graph

    # Try to create/upgrade to Neo4j-backed graph
    try:
        from app.core.domain_config import get_domain_config
        from app.neo4j_client import get_neo4j_client

        from .neo4j_graph import Neo4jKnowledgeGraph

        neo4j_client = get_neo4j_client()
        # Only use Neo4j if the client has been initialized (driver exists)
        if neo4j_client.is_initialized:
            domain = get_domain_config()
            _knowledge_graph = Neo4jKnowledgeGraph(
                neo4j_client=neo4j_client,
                domain_config=domain,
            )
            if _using_fallback:
                logger.info("Upgraded from in-memory fallback to Neo4j-backed knowledge graph")
            else:
                logger.info("Using Neo4j-backed knowledge graph")
            _using_fallback = False
            return _knowledge_graph
        else:
            raise RuntimeError("Neo4j not initialized")
    except Exception as e:
        if _knowledge_graph is not None:
            # Already have a fallback, keep using it
            return _knowledge_graph
        # First-time fallback to old in-memory builder
        logger.warning(f"Neo4j unavailable ({e}), falling back to in-memory graph")
        _using_fallback = True
        from .builder import KnowledgeGraph
        try:
            from app.domain.entities.services import get_entity_repository
            _knowledge_graph = KnowledgeGraph(registry=get_entity_repository())
        except Exception:
            logger.exception("Failed to load entity repository; falling back to basic KnowledgeGraph")
            _knowledge_graph = KnowledgeGraph()

    return _knowledge_graph


def get_semantica_knowledge():
    """Get the SemanticaKnowledge instance directly.

    Returns the unified Semantica-backed knowledge layer for:
    - Hybrid vector search
    - Entity extraction + deduplication
    - Decision intelligence (precedents, causal chains)
    - Temporal queries
    - Provenance tracking

    Returns None if Semantica is not initialized.
    """
    try:
        from app.core.service_registry import get_registry
        registry = get_registry()
        if registry.has("semantica_knowledge"):
            return registry.get("semantica_knowledge")
    except Exception as e:
        logger.debug(f"Semantica registry lookup failed: {e}")
    return None


def clear_knowledge_graph_cache():
    """Clear the knowledge graph cache.

    Call this when entities are created, updated, or deleted to ensure
    the graph is rebuilt on next access.
    """
    global _knowledge_graph
    if _knowledge_graph is not None:
        _knowledge_graph.invalidate_cache()
        logger.info("Knowledge graph cache cleared")
    else:
        logger.debug("Knowledge graph cache clear called, but graph not yet initialized")
    # Always invalidate the domain graph API response cache
    invalidate_graph_response_cache()


def reset_knowledge_graph():
    """Reset the knowledge graph singleton so it is recreated on next access.

    Call this when the active domain changes so the graph picks up the new
    domain entity types and schema.
    """
    global _knowledge_graph, _using_fallback
    _knowledge_graph = None
    _using_fallback = False
    invalidate_graph_response_cache()


def is_multi_tenant_graph_backend() -> bool:
    """Return True when the installed GraphBackend is multi-tenant (scoped).

    Used to guard wipe-and-rebuild operations — a wipe against a shared graph
    while other tenants are live is destructive, so callers (rebuild
    orchestrator, tenant graph wipe) check this first.

    Returns False if the container factory has not been initialised yet (lazy
    default), since the lazy default is always the single-tenant bundle.
    """
    try:
        from app.core.tenancy.backends.neo4j_scoped import ScopedNeo4jGraphBackend
        from app.core.tenancy.factory import get_container_factory

        factory = get_container_factory()
        backends = factory._backends  # None until first access or install_backends
        if backends is None:
            return False
        return isinstance(backends.graph, ScopedNeo4jGraphBackend)
    except Exception:
        return False
