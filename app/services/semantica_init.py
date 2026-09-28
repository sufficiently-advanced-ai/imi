"""
Semantica Initialization — Sets up Semantica components on app startup.

Initializes:
- GraphStore (Neo4j backend, reusing existing connection)
- VectorStore (FAISS backend, in-memory)
- EmbeddingGenerator (FastEmbed with all-MiniLM-L6-v2)
- ContextGraph (decision intelligence)
- ClaudeNERExtractor (entity extraction routed through ClaudeClient)
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


class ClaudeNERExtractor:
    """LLM entity extraction through ``ClaudeClient.generate_message``
    (operation ``semantica_ner``), so it follows ``config/inference.yaml``
    routing like every other model call — the subscription backend, not a
    separate Anthropic API key. Replaces Semantica's ``NERExtractor(method=
    "llm", provider="anthropic", api_key=...)``, which called the API directly
    and, when that key was stale, fell back to a pattern extractor that
    returned hundreds of junk spans per transcript.

    Returns objects with Semantica's Entity shape (text, label, confidence,
    start_char, end_char, metadata) so ``SemanticaExtraction`` is unchanged.
    Never raises: returns [] when the call or the parse fails.
    """

    OPERATION = "semantica_ner"
    LABELS = ("PERSON", "ORG", "PROJECT", "PRODUCT", "TEAM", "EVENT", "TOPIC", "LOCATION")
    MAX_CHARS = 100_000

    def __init__(self, client: Any = None, min_confidence: float = 0.7, labels: tuple[str, ...] | None = None):
        self._client = client
        self.min_confidence = min_confidence
        self.labels = tuple(labels or self.LABELS)

    def _prompt(self, text: str) -> str:
        return (
            "Extract the named entities from the text below. Only specific, named things: "
            "people, organisations, projects, products, teams, events, topics, places. Not "
            "roles, pronouns, or generic nouns.\n"
            f"Use one of these labels: {', '.join(self.labels)}.\n"
            'Return JSON only: {"entities": [{"text": "...", "label": "...", "confidence": 0.0}]}\n'
            "Use the name exactly as written in the text; list each entity once.\n\n"
            f"Text:\n{text[: self.MAX_CHARS]}"
        )

    async def aextract_entities(self, text: str) -> list[Any]:
        import json
        import re
        from types import SimpleNamespace

        if not (text or "").strip():
            return []
        try:
            from app.config import settings

            client = self._client
            if client is None:
                from app.services.claude_client import get_claude_client

                client = get_claude_client()
            response = await client.generate_message(
                messages=[{"role": "user", "content": self._prompt(text)}],
                model=settings.CLAUDE_HAIKU_MODEL,
                max_tokens=4000,
                temperature=0.0,
                operation=self.OPERATION,
            )
            raw = ""
            if hasattr(response, "content") and response.content:
                raw = getattr(response.content[0], "text", "") or ""
            elif isinstance(response, dict) and response.get("content"):
                first = response["content"][0]
                raw = first.get("text", "") if isinstance(first, dict) else ""
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(match.group(0)) if match else {}
        except Exception as e:
            logger.warning("[SEMANTICA] NER extraction failed: %s", e)
            return []
        out, seen = [], set()
        for item in data.get("entities", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("text") or "").strip()
            label = str(item.get("label") or "").strip().upper()
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            if not name or not label or confidence < self.min_confidence or (name.lower(), label) in seen:
                continue
            seen.add((name.lower(), label))
            start = text.find(name)
            out.append(SimpleNamespace(
                text=name, label=label, confidence=confidence,
                start_char=max(start, 0), end_char=max(start, 0) + len(name) if start >= 0 else 0,
                metadata={"extraction_method": "claude_client", "operation": self.OPERATION},
            ))
        return out


def create_ner_extractor(client: Any = None) -> Any:
    """Entity extractor routed through ClaudeClient (see ClaudeNERExtractor)."""
    extractor = ClaudeNERExtractor(client=client, min_confidence=0.7)
    logger.info("Semantica NER extractor initialized (ClaudeClient, operation=semantica_ner)")
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
