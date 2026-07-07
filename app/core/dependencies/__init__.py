"""
Core Dependencies

Shared dependency injection for services used across modules.
Provides singleton instances and lazy initialization patterns.
"""

from app.git_ops import git_ops
from app.services.claude_client import get_claude_client
from app.services.file_cache import file_cache


def get_git_ops():
    """Get the git operations service."""
    return git_ops


def get_claude_client_service():
    """Get the Claude client service."""
    return get_claude_client()


def get_file_cache_service():
    """Get the file cache service."""
    return file_cache


# Lazy initialization for services that might have circular dependencies
_entity_registry: object | None = None


def get_entity_repository():
    """Get the entity registry service with lazy initialization."""
    global _entity_registry
    if _entity_registry is None:
        from app.domain.entities.services import get_entity_repository
        _entity_registry = get_entity_repository()
    return _entity_registry


# Semantica knowledge layer
def get_semantica_knowledge():
    """Get SemanticaKnowledge service instance if initialized."""
    from app.core.service_registry import get_registry
    registry = get_registry()
    if registry.has("semantica_knowledge"):
        return registry.get("semantica_knowledge")
    return None


def initialize_semantica(settings=None):
    """Initialize SemanticaKnowledge and register in service registry."""
    import logging
    _logger = logging.getLogger(__name__)
    from app.core.service_registry import get_registry
    registry = get_registry()

    if registry.has("semantica_knowledge"):
        return registry.get("semantica_knowledge")

    try:
        from app.core.domain_config import get_domain_config
        from app.services.semantica_init import (
            create_context_graph,
            create_duplicate_detector,
            create_embedding_generator,
            create_graph_store,
            create_ner_extractor,
            create_vector_store,
        )
        from app.services.semantica_knowledge import SemanticaKnowledge

        domain = get_domain_config()

        graph_store = create_graph_store()
        vector_store = create_vector_store()
        embedding_generator = create_embedding_generator()
        context_graph = create_context_graph()
        ner_extractor = create_ner_extractor()
        duplicate_detector = create_duplicate_detector()

        sk = SemanticaKnowledge(
            graph_store=graph_store,
            vector_store=vector_store,
            embedding_generator=embedding_generator,
            context_graph=context_graph,
            ner_extractor=ner_extractor,
            duplicate_detector=duplicate_detector,
            domain_config=domain,
        )

        registry.register_instance("semantica_knowledge", sk)
        _logger.info("SemanticaKnowledge initialized and registered")
        return sk

    except Exception as e:
        _logger.warning(f"Failed to initialize Semantica: {e}")
        return None


# Domain config service - use service registry directly for singleton pattern
# This ensures all code paths use the same instance via the central registry
def get_domain_config_service():
    """Get the domain config service from the service registry.

    IMPORTANT: Uses the service registry pattern to ensure a single instance
    is used across the entire application, regardless of import path.
    """
    from app.core.service_registry import get_registry

    registry = get_registry()

    if not registry.has("domain_config"):
        from app.core.domain_config import DomainConfigService

        def factory():
            return DomainConfigService()

        registry.register_factory("domain_config", factory)

    return registry.get("domain_config")

