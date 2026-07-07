"""
Ingest Classifier — Content type classification for the ingestion pipeline.

Handles two modes:
1. Source hint mapping: Known sources (fireflies, otter, etc.) map directly to content types
2. LLM classification: When no source hint, uses Claude to classify content

Falls back to "document" on any classification failure.
"""

import hashlib
import logging

logger = logging.getLogger(__name__)

# Source platform → content type mapping
_SOURCE_TO_TYPE = {
    "fireflies": "call_transcript",
    "otter": "call_transcript",
    "fathom": "call_transcript",
    "grain": "call_transcript",
    "plaud": "call_transcript",
    "local_recording": "call_transcript",
    "slack": "slack_thread",
    "email": "email_thread",
    "document": "document",
    "other": "document",
}

# Valid content types the classifier can return
_VALID_TYPES = {"call_transcript", "slack_thread", "email_thread", "document", "notes"}

_CLASSIFY_SYSTEM_PROMPT = """You are a content classifier. Given raw text, classify it into exactly one of these types:
- call_transcript: Meeting or call recording transcription (has speakers, dialogue)
- slack_thread: Slack or chat thread (has usernames, short messages, timestamps)
- email_thread: Email conversation (has to/from/subject headers, formal structure)
- document: Formal document, report, or article (structured prose)
- notes: Informal notes, bullet points, or quick captures

Respond with ONLY the type name, nothing else."""


def compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of content for deduplication."""
    return hashlib.sha256(content.encode()).hexdigest()


class IngestClassifier:
    """Classifies content type for the ingestion pipeline."""

    def __init__(self, claude_client):
        self._claude = claude_client

    def map_source_to_type(self, source: str) -> str:
        """Map a source platform name to a content type.

        Args:
            source: Source platform identifier (fireflies, otter, slack, etc.)

        Returns:
            Content type string. Defaults to "document" for unknown sources.
        """
        return _SOURCE_TO_TYPE.get(source.lower(), "document")

    async def classify(self, content: str, source_hint: str | None = None) -> str:
        """Classify content into a content type.

        If source_hint is provided, maps directly without LLM call.
        Otherwise, calls Claude for classification.

        Args:
            content: Raw text content to classify
            source_hint: Optional source platform name

        Returns:
            Content type string (e.g., "call_transcript", "document")
        """
        if source_hint:
            content_type = self.map_source_to_type(source_hint)
            logger.info(f"[INGEST_CLASSIFY] Source hint '{source_hint}' → '{content_type}'")
            return content_type

        return await self._classify_with_llm(content)

    async def _classify_with_llm(self, content: str) -> str:
        """Use Claude to classify content type.

        Falls back to "document" on any failure.
        """
        if not self._claude:
            logger.warning("[INGEST_CLASSIFY] Claude client not available, falling back to 'document'")
            return "document"

        try:
            # Truncate content for classification (first 2000 chars is enough)
            sample = content[:2000]

            response = await self._claude.generate_message(
                messages=[{"role": "user", "content": sample}],
                system=_CLASSIFY_SYSTEM_PROMPT,
                max_tokens=20,
                temperature=0.0,
                operation="ingest_classify",
            )

            raw = response.content[0].text.strip().lower()

            if raw in _VALID_TYPES:
                logger.info(f"[INGEST_CLASSIFY] LLM classified as '{raw}'")
                return raw

            logger.warning(f"[INGEST_CLASSIFY] LLM returned invalid type '{raw}', falling back to 'document'")
            return "document"

        except Exception as e:
            logger.warning(f"[INGEST_CLASSIFY] LLM classification failed: {e}, falling back to 'document'")
            return "document"
