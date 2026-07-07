"""LLM metadata enrichment for captures (Phase 1 of the OB1 absorption).

Ports OB1's ``extractMetadata`` (server/index.ts): a captured thought is
classified into type/topics/people/action_items/dates_mentioned with a cheap
Haiku call. Enrichment is strictly best-effort — every failure mode (no
client, API error, empty or unparseable response) returns FALLBACK_METADATA
so capture persistence is never blocked (persist-first ordering; the
embeddings do the heavy lifting when metadata is off, per the OB1 design).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_HAIKU_MODEL = settings.CLAUDE_HAIKU_MODEL

# OB1's thought-type taxonomy; unknown types coerce to "observation".
CAPTURE_TYPES = frozenset(
    {
        "observation",
        "task",
        "idea",
        "reference",
        "person_note",
        "decision",
        "lesson",
        "meeting",
        "journal",
    }
)

FALLBACK_METADATA: dict[str, Any] = {
    "type": "observation",
    "topics": ["uncategorized"],
    "people": [],
    "action_items": [],
    "dates_mentioned": [],
}

_SYSTEM_PROMPT = (
    "You classify short captured thoughts for a personal knowledge system. "
    "Respond with ONLY a JSON object with keys: "
    '"type" (one of: observation, task, idea, reference, person_note, '
    "decision, lesson, meeting, journal), "
    '"topics" (list of 1-5 short lowercase tags), '
    '"people" (list of person names mentioned), '
    '"action_items" (list of concrete follow-ups, empty if none), '
    '"dates_mentioned" (list of ISO dates referenced, empty if none). '
    "No prose, no markdown fences."
)


def _extract_response_text(response) -> str | None:
    """Extract text content from a Claude API response (.content[0].text)."""
    if hasattr(response, "content") and response.content:
        first = response.content[0]
        if hasattr(first, "text"):
            return first.text
    return None


def _parse_json_object(text: str) -> dict | None:
    """Parse a JSON object from raw text, tolerating fences and prose."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_str_list(value: Any) -> list[str]:
    """Coerce an LLM field to a list of strings (None → [], scalar → [str])."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _coerce_metadata(raw: dict) -> dict[str, Any]:
    """Normalize a parsed LLM object into the enrichment metadata shape."""
    raw_type = str(raw.get("type", "")).strip().lower()
    return {
        "type": raw_type if raw_type in CAPTURE_TYPES else "observation",
        "topics": _as_str_list(raw.get("topics")),
        "people": _as_str_list(raw.get("people")),
        "action_items": _as_str_list(raw.get("action_items")),
        "dates_mentioned": _as_str_list(raw.get("dates_mentioned")),
    }


async def enrich_capture(content: str, claude_client=None) -> dict[str, Any]:
    """Classify a capture's text into metadata; degrade to fallback on failure."""
    if claude_client is None:
        return dict(FALLBACK_METADATA)

    try:
        response = await claude_client.generate_message(
            messages=[{"role": "user", "content": f"Classify this thought:\n\n{content}"}],
            model=_HAIKU_MODEL,
            max_tokens=500,
            temperature=0.0,
            system=_SYSTEM_PROMPT,
            operation="capture_enrichment",
        )
    except Exception as e:
        logger.warning("[CAPTURE] Enrichment LLM call failed (non-fatal): %s", e)
        return dict(FALLBACK_METADATA)

    text = _extract_response_text(response)
    if not text:
        logger.warning("[CAPTURE] Empty enrichment response (non-fatal)")
        return dict(FALLBACK_METADATA)

    parsed = _parse_json_object(text)
    if parsed is None:
        logger.warning("[CAPTURE] Unparseable enrichment response (non-fatal)")
        return dict(FALLBACK_METADATA)

    return _coerce_metadata(parsed)
