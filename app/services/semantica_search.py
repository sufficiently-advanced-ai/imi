"""
Semantica Search — Hybrid search implementation.

Replaces the substring-based search in chat_tools.py with Semantica's
vector + metadata hybrid search.

Provides:
- Semantic vector search (embedding similarity)
- Metadata filtering (entity type, date range, etc.)
- Keyword fallback for exact matches
- Combined ranking via SearchRanker
"""

import logging
import re
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class SemanticaSearch:
    """Hybrid search backed by Semantica VectorStore + graph queries."""

    def __init__(
        self,
        vector_store: Any,
        embedding_generator: Any,
        graph_store: Any = None,
    ):
        self.vector_store = vector_store
        self.embedder = embedding_generator
        self.graph_store = graph_store

    async def hybrid_search(
        self,
        query: str,
        entity_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Perform hybrid vector + keyword search.

        Args:
            query: Search query text.
            entity_types: Optional entity type filter.
            limit: Maximum results to return.

        Returns:
            List of search results with id, name, type, score, attributes.
        """
        if not query or not query.strip():
            return []

        try:
            # Generate query embedding
            query_embedding = self.embedder.generate_embeddings(
                query, data_type="text"
            )
            if isinstance(query_embedding, np.ndarray) and query_embedding.ndim > 1:
                query_embedding = query_embedding[0]

            # Build metadata filter — always exclude transcript chunks from
            # entity search, and optionally restrict to specific entity types.
            search_kwargs: dict[str, Any] = {}
            from semantica.vector_store import MetadataFilter
            mf = MetadataFilter()
            # Restrict to entity content (exclude transcript chunks)
            mf = mf.eq("content_type", "entity")
            if entity_types:
                # Filter by entity_type in metadata
                if len(entity_types) == 1:
                    mf = mf.eq("entity_type", entity_types[0])
                else:
                    # Multiple types — use OR filter
                    mf = mf.in_list("entity_type", entity_types)
            search_kwargs["filter"] = mf

            # Vector search
            results = self.vector_store.search_vectors(
                query_embedding,
                k=limit * 2,  # Over-fetch for re-ranking
                **search_kwargs,
            )

            # Convert to standard format
            search_results = []
            for result in results[:limit]:
                metadata = result.get("metadata", {})
                search_results.append({
                    "id": metadata.get("id", result.get("id", "")),
                    "name": metadata.get("name", ""),
                    "type": metadata.get("entity_type", ""),
                    "score": float(result.get("score", 0.0)),
                    "attributes": metadata.get("attributes", {}),
                    "file_path": metadata.get("file_path", ""),
                    "matched_attribute": metadata.get("matched_attribute", "semantic_similarity"),
                })

            # Fall back to graph search if vector search returned nothing
            if not search_results:
                return await self._fallback_search(query, entity_types, limit)

            return search_results

        except Exception as e:
            logger.error(f"Hybrid search failed: {e}")
            # Fallback to graph-based substring search
            return await self._fallback_search(query, entity_types, limit)

    async def search_transcripts(
        self,
        query: str,
        speaker: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search meeting transcripts using vector similarity.

        Args:
            query: Search text.
            speaker: Optional speaker name filter.
            date_from: Optional start date (ISO format).
            date_to: Optional end date (ISO format).
            max_results: Maximum results.

        Returns:
            List of transcript matches with meeting_id, speaker, text, score.
        """
        if not query or not query.strip():
            return []

        try:
            query_embedding = self.embedder.generate_embeddings(
                query, data_type="text"
            )
            if isinstance(query_embedding, np.ndarray) and query_embedding.ndim > 1:
                query_embedding = query_embedding[0]

            # Build metadata filter
            search_kwargs: dict[str, Any] = {}
            from semantica.vector_store import MetadataFilter
            mf = MetadataFilter()
            mf = mf.eq("content_type", "transcript")
            # Note: speaker is stored as comma-joined string for multi-speaker
            # chunks, so we filter post-search instead of using eq() which
            # requires an exact match.
            search_kwargs["filter"] = mf

            results = self.vector_store.search_vectors(
                query_embedding,
                k=max_results * 2,
                **search_kwargs,
            )

            # Filter by date range and format
            matches = []
            for result in results:
                metadata = result.get("metadata", {})
                result_date = metadata.get("date", "")

                # Apply date filters
                if date_from and result_date < date_from:
                    continue
                if date_to and result_date > date_to:
                    continue
                # Speaker filter (substring match for comma-joined multi-speaker chunks)
                if speaker and speaker.lower() not in metadata.get("speaker", "").lower():
                    continue

                matches.append({
                    "meeting_id": metadata.get("meeting_id", ""),
                    "meeting_title": metadata.get("meeting_title", ""),
                    "speaker": metadata.get("speaker", "Unknown"),
                    "text": metadata.get("text", ""),
                    "timestamp": metadata.get("timestamp", ""),
                    "date": result_date,
                    "score": float(result.get("score", 0.0)),
                    "file_path": metadata.get("file_path", ""),
                })

                if len(matches) >= max_results:
                    break

            return matches

        except Exception as e:
            logger.error(f"Transcript search failed: {e}")
            return []

    async def search_entities(
        self,
        query: str,
        entity_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search specifically for entities by name/attributes.

        Wraps hybrid_search with entity-specific filtering.
        """
        entity_types = [entity_type] if entity_type else None
        return await self.hybrid_search(query, entity_types=entity_types, limit=limit)

    async def index_entity(
        self,
        entity_id: str,
        name: str,
        entity_type: str,
        attributes: dict[str, Any],
        file_path: str = "",
    ) -> str | None:
        """Index an entity in the vector store for future search.

        Args:
            entity_id: Entity identifier.
            name: Entity display name.
            entity_type: Entity type slug.
            attributes: Entity attributes dict.
            file_path: Source file path.

        Returns:
            Vector ID if successful, None otherwise.
        """
        try:
            # Build searchable text from name + attributes
            text_parts = [name]
            for key, value in attributes.items():
                if isinstance(value, str) and value:
                    text_parts.append(f"{key}: {value}")
                elif isinstance(value, list):
                    text_parts.append(f"{key}: {', '.join(str(v) for v in value)}")

            text = " | ".join(text_parts)
            embedding = self.embedder.generate_embeddings(text, data_type="text")
            if isinstance(embedding, np.ndarray) and embedding.ndim > 1:
                embedding = embedding[0]

            metadata = {
                "id": entity_id,
                "name": name,
                "entity_type": entity_type,
                "content_type": "entity",
                "file_path": file_path,
                "attributes": attributes,
            }

            ids = self.vector_store.store_vectors(
                [embedding],
                metadata=[metadata],
            )
            return ids[0] if ids else None

        except Exception as e:
            logger.error(f"Failed to index entity {entity_id}: {e}")
            return None

    async def index_transcript_chunk(
        self,
        text: str,
        meeting_id: str,
        meeting_title: str = "",
        speaker: str = "Unknown",
        timestamp: str = "",
        date_str: str = "",
        file_path: str = "",
    ) -> str | None:
        """Index a transcript chunk in the vector store.

        Args:
            text: Transcript text content.
            meeting_id: Meeting/bot identifier.
            meeting_title: Meeting title.
            speaker: Speaker name.
            timestamp: Timestamp within meeting.
            date_str: Meeting date (ISO format).
            file_path: Source file path.

        Returns:
            Vector ID if successful, None otherwise.
        """
        if not text or not text.strip():
            return None

        try:
            embedding = self.embedder.generate_embeddings(text, data_type="text")
            if isinstance(embedding, np.ndarray) and embedding.ndim > 1:
                embedding = embedding[0]

            metadata = {
                "content_type": "transcript",
                "meeting_id": meeting_id,
                "meeting_title": meeting_title,
                "speaker": speaker,
                "text": text,
                "timestamp": timestamp,
                "date": date_str,
                "file_path": file_path,
            }

            ids = self.vector_store.store_vectors(
                [embedding],
                metadata=[metadata],
            )
            return ids[0] if ids else None

        except Exception as e:
            logger.error(f"Failed to index transcript chunk: {e}")
            return None

    async def index_signal(self, signal) -> str | None:
        """Index a signal with governance metadata (G3).

        Delegates to ``signal_retrieval.index_signal``; see that module for the
        metadata schema. Returns the vector id or None (on failure).
        """
        try:
            from app.services.signal_retrieval import index_signal

            return index_signal(self.vector_store, self.embedder, signal)
        except Exception as e:
            logger.error(
                f"Failed to index signal {getattr(signal, 'id', 'unknown')}: {e}"
            )
            return None

    async def search_signals_semantic(
        self, query: str, **kwargs
    ) -> list[dict[str, Any]]:
        """Governance-aware semantic search over indexed signals (G3).

        Delegates to ``signal_retrieval.search_signals_semantic`` (authority
        filter, lifecycle exclusion, optional recency blend).
        """
        if not query or not query.strip():
            return []
        try:
            from app.services.signal_retrieval import search_signals_semantic

            return search_signals_semantic(
                self.vector_store, self.embedder, query, **kwargs
            )
        except Exception as e:
            logger.error(f"Signal semantic search failed: {e}")
            return []

    async def _fallback_search(
        self,
        query: str,
        entity_types: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Fallback substring search when vector search is unavailable."""
        if not self.graph_store:
            return []

        try:
            # Use Neo4j CONTAINS for substring search
            cypher = "MATCH (n:Entity) WHERE toLower(n.name) CONTAINS toLower($query)"
            if entity_types:
                type_labels = " OR ".join(f"n:{_type_label(t)}" for t in entity_types)
                cypher += f" AND ({type_labels})"
            cypher += " RETURN n LIMIT $limit"

            raw = self.graph_store.execute_query(
                cypher, {"query": query, "limit": limit}
            )
            # execute_query returns {"success": bool, "records": [...]} —
            # iterate the records list, not the wrapper dict.
            rows = raw.get("records", []) if isinstance(raw, dict) else (raw or [])

            return [
                {
                    "id": r.get("n", {}).get("id", ""),
                    "name": r.get("n", {}).get("name", ""),
                    "type": r.get("n", {}).get("entity_type", ""),
                    "score": 0.5,  # Fixed score for fallback
                    "attributes": {},
                    "file_path": r.get("n", {}).get("file_path", ""),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Fallback search also failed: {e}")
            return []


_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _type_label(entity_type: str) -> str:
    """Convert entity type to Neo4j label format and validate."""
    label = "".join(w.capitalize() for w in entity_type.split("_"))
    if not _SAFE_IDENTIFIER_RE.match(label):
        raise ValueError(f"Unsafe Cypher identifier rejected: {label!r}")
    return label
