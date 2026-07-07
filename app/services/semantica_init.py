"""
Semantica Initialization — Sets up Semantica components on app startup.

Initializes:
- GraphStore (Neo4j backend, reusing existing connection)
- VectorStore (FAISS backend, in-memory)
- EmbeddingGenerator (FastEmbed with all-MiniLM-L6-v2)
- ContextGraph (decision intelligence)
- NERExtractor (LLM-backed entity extraction)
- DuplicateDetector (entity deduplication)

All components are created once and shared via SemanticaKnowledge facade.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Embedding dimensions for all-MiniLM-L6-v2
EMBEDDING_DIM = 384
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# FAISS index path for persistence
FAISS_INDEX_DIR = "/app/data/faiss"


def create_graph_store(
    uri: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> Any:
    """Create a Semantica GraphStore backed by Neo4j."""
    from semantica.graph_store import GraphStore

    from app.services.semantica_patches import apply_semantica_patches

    apply_semantica_patches()

    uri = uri or _get_neo4j_uri()
    username = username or _get_neo4j_username()
    password = password or _get_neo4j_password()

    store = GraphStore(
        backend="neo4j",
        uri=uri,
        username=username,
        password=password,
    )
    logger.info(f"Semantica GraphStore initialized (neo4j @ {uri})")
    return store


def create_vector_store(dimension: int = EMBEDDING_DIM) -> Any:
    """Create a Semantica VectorStore backed by FAISS."""
    from semantica.vector_store import VectorStore

    store = VectorStore(backend="faiss", dimension=dimension)
    logger.info(f"Semantica VectorStore initialized (faiss, dim={dimension})")
    return store


def create_embedding_generator() -> Any:
    """Create an embedding generator using FastEmbed."""
    from semantica.embeddings import EmbeddingGenerator

    generator = EmbeddingGenerator(config={
        "text": {
            "method": "fastembed",
            "model": EMBEDDING_MODEL,
        }
    })
    logger.info(f"Semantica EmbeddingGenerator initialized ({EMBEDDING_MODEL})")
    return generator


def create_context_graph() -> Any:
    """Create a ContextGraph for decision intelligence."""
    from semantica.context import ContextGraph

    graph = ContextGraph(
        advanced_analytics=True,
        enable_causality=True,
    )
    logger.info("Semantica ContextGraph initialized")
    return graph


def create_ner_extractor(api_key: str | None = None) -> Any:
    """Create an NER extractor using Anthropic Claude for LLM-based extraction."""
    from semantica.semantic_extract import NERExtractor

    api_key = api_key or _get_anthropic_key()
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is required for NER extraction. "
            "Set it in .env or environment variables."
        )
    extractor = NERExtractor(
        method="llm",
        provider="anthropic",
        llm_model="claude-haiku-4-5-20251001",
        min_confidence=0.7,
        post_process=True,
        api_key=api_key,
    )
    logger.info("Semantica NERExtractor initialized (anthropic/claude-haiku-4-5)")
    return extractor


def create_duplicate_detector(similarity_threshold: float = 0.8) -> Any:
    """Create a duplicate detector for entity deduplication."""
    from semantica.deduplication import DuplicateDetector

    detector = DuplicateDetector(
        similarity_threshold=similarity_threshold,
        confidence_threshold=0.6,
        use_clustering=True,
    )
    logger.info(f"Semantica DuplicateDetector initialized (threshold={similarity_threshold})")
    return detector


def create_graph_builder(graph_store: Any = None) -> Any:
    """Create a GraphBuilder for knowledge graph construction."""
    import os

    from semantica.kg import GraphBuilder

    _VALID_GRANULARITIES = {"day", "hour", "minute", "second"}
    raw = os.environ.get("TEMPORAL_GRANULARITY", "day").strip().lower()
    if raw not in _VALID_GRANULARITIES:
        logger.warning(
            f"Invalid TEMPORAL_GRANULARITY='{raw}', falling back to 'day'. "
            f"Valid values: {sorted(_VALID_GRANULARITIES)}"
        )
        raw = "day"
    temporal_granularity = raw

    builder = GraphBuilder(
        merge_entities=True,
        resolve_conflicts=True,
        enable_temporal=True,
        temporal_granularity=temporal_granularity,
        graph_store=graph_store,
    )
    logger.info(f"Semantica GraphBuilder initialized (temporal_granularity={temporal_granularity})")
    return builder


# ── Private helpers ──


def _get_neo4j_uri() -> str:
    import os
    return os.environ.get("NEO4J_URI", "bolt://localhost:7687")


def _get_neo4j_username() -> str:
    import os
    return os.environ.get("NEO4J_USERNAME", "neo4j")


def _get_neo4j_password() -> str:
    import os
    return os.environ.get("NEO4J_PASSWORD", "password")


def _get_anthropic_key() -> str:
    import os
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        logger.warning("ANTHROPIC_API_KEY not set — LLM extraction will fail")
    return key
