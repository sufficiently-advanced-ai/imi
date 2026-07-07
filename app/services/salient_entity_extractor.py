"""Salience-aware transcript entity extraction (entity extraction v2).

Runs the transcript_entity_extract.xml prompt and post-filters by salience:
the PROMPT labels (participant / subject / mention), the CODE decides what
becomes a graph node. Keeping the filter in code means recall errors are
recoverable (the full labeled list is preserved on the observation) and the
threshold can move without prompt churn.

Promotion rule:
  - participant, subject  -> promoted to entities_mentioned / graph nodes
  - mention               -> promoted ONLY if it resolves to an entity that
                             already exists in the graph (a passing mention of
                             a known client should still link — but a passing
                             mention never mints a new node)
"""

from __future__ import annotations

import html
import json
import logging
import re

from app.config import settings
from app.services.entity_utils import is_valid_entity_name
from app.services.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

VALID_SALIENCE = {"participant", "subject", "mention"}
PROMOTED_SALIENCE = {"participant", "subject"}

# Non-anchored: the model sometimes appends commentary after the closing
# fence ("**Note:** I excluded..."), which must not cost a whole meeting's
# entities. Match the first fenced block wherever it sits.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def build_salient_extraction_prompt(
    transcript: str,
    entity_types: list[str],
    existing_entities: dict[str, list[str]] | None = None,
) -> str:
    """Assemble the v2 extraction prompt (mirrors the historical
    EntityService._build_transcript_extraction_prompt template shape)."""
    instructions = load_prompt("transcript_entity_extract")
    # Escape interpolated fields: transcript/entity text is embedded into
    # XML-like tags, so a stray "</transcript>" or "<" could break the prompt
    # structure or steer the model. html.escape covers &, <, > (and quotes).
    entity_type_list = "\n".join(f"- {html.escape(t)}" for t in entity_types)

    existing_context = ""
    if existing_entities:
        lines = [
            f"{html.escape(etype)}: {', '.join(html.escape(n) for n in names)}"
            for etype, names in existing_entities.items()
            if names
        ]
        if lines:
            existing_context = (
                "<existing_entities>\n"
                "For reference, here are some existing entities in the knowledge base:\n"
                + "\n".join(lines)
                + "\nUse this context to keep canonical names consistent.\n"
                "</existing_entities>"
            )

    template = (
        "<transcript>{transcript_content}</transcript>\n"
        "{existing_entities_context}\n"
        "{entity_types_context}\n\n"
        f"{instructions}"
    )
    return template.format(
        transcript_content=html.escape(transcript),
        existing_entities_context=existing_context,
        entity_types_context=f"<entity_types>{entity_type_list}</entity_types>",
        entity_type_list=entity_type_list,
    )


def parse_salient_extraction(response_text: str, entity_types: list[str]) -> dict:
    """Parse the model's JSON into {"entities": [...], "meeting_title": str|None}."""
    cleaned = (response_text or "").strip()
    fence = _FENCE_RE.search(cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        # strict=False tolerates literal newlines/control chars in strings.
        data = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        # Prose around the object (preamble or trailing notes): fall back to
        # the outermost {...} span, same tolerance as the finalization parser.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            logger.warning("[SALIENT-EXTRACT] No JSON object found in response")
            return {"entities": [], "meeting_title": None}
        try:
            data = json.loads(match.group(), strict=False)
        except json.JSONDecodeError as e:
            logger.warning("[SALIENT-EXTRACT] Could not parse extraction JSON: %s", e)
            return {"entities": [], "meeting_title": None}
    if not isinstance(data, dict):
        return {"entities": [], "meeting_title": None}

    title = (data.get("meeting_title") or "").strip().rstrip(".")
    # Prompt contract (transcript_entity_extract.xml) caps titles at 8 words;
    # reject longer model output.
    if not title or len(title.split()) > 8 or title.lower() in (
        "untitled meeting",
        "meeting summary",
    ):
        title = None

    return {
        "entities": _parse_entity_items(data.get("entities"), entity_types),
        "meeting_title": title,
    }


def parse_salient_entities(response_text: str, entity_types: list[str]) -> list[dict]:
    """Parse the model's JSON into validated labeled-entity dicts."""
    return parse_salient_extraction(response_text, entity_types)["entities"]


def _parse_entity_items(items, entity_types: list[str]) -> list[dict]:
    if not isinstance(items, list):
        return []

    valid_types = set(entity_types)
    labeled: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        etype = (raw.get("type") or "").strip().lower()
        canonical = (raw.get("canonical_name") or raw.get("name_heard") or "").strip()
        salience = (raw.get("salience") or "").strip().lower()
        if etype not in valid_types or not canonical:
            continue
        if not is_valid_entity_name(canonical):
            logger.debug(
                "[SALIENT-EXTRACT] Dropping junk-named entity: %s/%r",
                etype,
                canonical,
            )
            continue
        if salience not in VALID_SALIENCE:
            salience = "mention"  # unlabeled = least privileged
        aliases = raw.get("aliases_heard") or []
        if not isinstance(aliases, list):
            aliases = []
        confidence = raw.get("confidence")
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        labeled.append(
            {
                "type": etype,
                "name_heard": (raw.get("name_heard") or canonical).strip(),
                "canonical_name": canonical,
                "aliases_heard": [a.strip() for a in aliases if isinstance(a, str)],
                "salience": salience,
                "role": raw.get("role"),
                "confidence": round(float(confidence), 2),
                "evidence": (raw.get("evidence") or "").strip(),
            }
        )
    return labeled


def filter_salient_entities(labeled: list[dict], resolver=None) -> list[dict]:
    """Apply the promotion rule. resolver is an EntityResolver (or None —
    then mentions are dropped outright)."""
    promoted: list[dict] = []
    for entity in labeled:
        if entity["salience"] in PROMOTED_SALIENCE:
            promoted.append(entity)
            continue
        if resolver is not None:
            try:
                resolved = resolver.resolve(entity["type"], entity["canonical_name"])
                if resolved.matched_via != "new":
                    promoted.append(
                        {**entity, "canonical_name": resolved.canonical_name}
                    )
                    continue
            except Exception as e:
                logger.debug(
                    "[SALIENT-EXTRACT] Mention resolution failed for %r: %s",
                    entity["canonical_name"],
                    e,
                )
        logger.debug(
            "[SALIENT-EXTRACT] Dropping passing mention: %s/%r",
            entity["type"],
            entity["canonical_name"],
        )
    return promoted


def to_entities_mentioned(promoted: list[dict]) -> dict[str, list[str]]:
    """Collapse promoted entities to the Observation.entities_mentioned shape."""
    mentioned: dict[str, list[str]] = {}
    for entity in promoted:
        names = mentioned.setdefault(entity["type"], [])
        if entity["canonical_name"] not in names:
            names.append(entity["canonical_name"])
    return mentioned


async def extract_salient_entities(
    claude_client,
    transcript: str,
    entity_types: list[str],
    existing_entities: dict[str, list[str]] | None = None,
) -> dict:
    """Run the v2 extraction prompt over a transcript.

    Returns {"entities": [...full labeled list, pre-filter...],
    "meeting_title": str|None}; callers apply filter_salient_entities."""
    if not (transcript or "").strip():
        return {"entities": [], "meeting_title": None}
    prompt = build_salient_extraction_prompt(transcript, entity_types, existing_entities)
    response = await claude_client.generate_message(
        messages=[{"role": "user", "content": prompt}],
        model=settings.CLAUDE_HAIKU_MODEL,
        max_tokens=3000,
        temperature=0.2,
        operation="salient_entity_extraction",
    )
    text = None
    if hasattr(response, "content") and response.content:
        # Anthropic Message object
        first = response.content[0]
        if hasattr(first, "text"):
            text = first.text
    elif isinstance(response, dict) and response.get("content"):
        # Dict-shaped payload (mocks/adapters): {"content": [{"text": ...}]}
        first = response["content"][0]
        if isinstance(first, dict):
            text = first.get("text")
    return parse_salient_extraction(text or "", entity_types)
