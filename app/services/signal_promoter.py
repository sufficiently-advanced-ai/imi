"""
Signal Promoter — Extracts signals from an observation and links them to entities.

Supports two extraction modes:
  1. LLM-powered (preferred): Uses Claude Haiku to produce deduplicated, quality-
     filtered signals with per-signal entity attribution and meaningful confidence.
  2. Regex fallback: Parses markdown section headers — no dedup, flat confidence.

The LLM path is used when a claude_client is provided; otherwise regex is used.

NOTE: Decision recording is now also handled by SemanticaDecisions
(app/services/semantica_decisions.py) when available. Decisions extracted
by this promoter are written through to Semantica's ContextGraph for
precedent search and causal chain analysis.
"""

import json
import logging
import re
import uuid
from datetime import UTC, datetime

from app.models.observation import Observation
from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.entity_utils import ensure_entity_id_format, get_active_entity_types

logger = logging.getLogger(__name__)

# Use configured Haiku model for cheap structured extraction
from app.config import settings  # noqa: E402 — after module docstring constants

_HAIKU_MODEL = settings.CLAUDE_HAIKU_MODEL

# Decisions below this confidence are tagged tier="candidate" rather than
# confirmed. Mirrors the decision_detector confidence_threshold used by the
# live-meeting workflows that enable it (hosted live-meeting workflows = 0.7).
# This batch path has no workflow context, so the value is fixed here.
DECISION_CANDIDATE_THRESHOLD = 0.7

# The system prompt lives in app/prompts/signal_promote.xml so the eval suite
# (scripts/run_evals.py --task signals) measures the shipping prompt. When the
# file cannot be loaded, _SYSTEM_PROMPT is None and extraction falls back to
# the regex path.
try:
    from app.services.prompt_loader import load_prompt

    _SYSTEM_PROMPT: str | None = load_prompt("signal_promote")
except (FileNotFoundError, OSError) as _prompt_err:  # pragma: no cover - missing/unreadable template
    # Expected I/O failure: degrade gracefully to the regex extraction path.
    # Unexpected errors (ImportError, bad-name ValueError, programming bugs)
    # are left to propagate so a real regression surfaces instead of silently
    # disabling the LLM path.
    logging.getLogger(__name__).error(
        "Failed to load signal_promote prompt template: %s", _prompt_err
    )
    _SYSTEM_PROMPT = None


class SignalPromoter:
    """Extract signals from an Observation and resolve entity references."""

    def __init__(self, claude_client=None, knowledge_graph=None):
        self._claude_client = claude_client
        self._kg = knowledge_graph

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def promote(self, observation: Observation) -> MeetingSignals | None:
        """Main entry point: extract signals, resolve entities, return container.

        Does NOT write to disk — the caller is responsible for persistence/git.
        Returns None if the observation has no extractable body content.
        """
        body = observation.content or ""
        if not body.strip():
            return None

        # Resolve entities from observation metadata
        entity_refs = self._resolve_entities_from_state(observation)

        # Choose extraction method based on available claude_client
        if self._claude_client:
            signals = await self._extract_signals_with_llm(observation, entity_refs)
        else:
            signals = self._extract_signals_regex(observation, entity_refs)

        if not signals:
            return None

        signals = self._apply_client_scope(signals)

        return MeetingSignals(
            meeting_id=observation.observation_id,
            bot_id=observation.external_id,
            meeting_title=observation.title,
            extracted_at=datetime.now(UTC).isoformat(),
            signal_count=len(signals),
            signals=signals,
        )

    # ------------------------------------------------------------------
    # LLM-powered signal extraction
    # ------------------------------------------------------------------

    async def _extract_signals_with_llm(
        self, observation: Observation, entity_refs: list[EntityRef]
    ) -> list[Signal]:
        """Extract signals using Claude Haiku for dedup + quality filtering."""
        if not _SYSTEM_PROMPT:
            logger.warning("[SIGNALS] No system prompt available, falling back to regex")
            return self._extract_signals_regex(observation, entity_refs)
        body = observation.content or ""
        meeting_id = observation.external_id  # legacy: Signal.source_meeting_id takes external_id (was bot_id)
        meeting_title = observation.title
        timestamp = observation.observed_at.isoformat()
        participants = observation.participants or []

        # Build entity context for the prompt
        entity_context = [{"name": ref.name, "type": ref.type} for ref in entity_refs]

        user_prompt = (
            "Extract structured signals from this meeting summary.\n\n"
            f"MEETING SUMMARY:\n{body}\n\n"
            f"KNOWN ENTITIES:\n{json.dumps(entity_context, indent=2)}\n\n"
            "For each signal return: type, content, confidence, entities (names), "
            "owner (action_items only), status (action_items only).\n"
            "Return [] if no substantive signals exist."
        )

        try:
            response = await self._claude_client.generate_message(
                messages=[{"role": "user", "content": user_prompt}],
                model=_HAIKU_MODEL,
                max_tokens=2000,
                temperature=0.1,
                system=_SYSTEM_PROMPT,
                operation="signal_extraction",
            )

            # Extract text from response
            response_text = self._extract_response_text(response)
            if not response_text:
                logger.warning("[SIGNALS] Empty LLM response, falling back to regex")
                return self._extract_signals_regex(observation, entity_refs)

            # Parse JSON response
            raw_signals = self._parse_llm_signals(response_text)
            if raw_signals is None:
                logger.warning("[SIGNALS] Failed to parse LLM JSON, falling back to regex")
                return self._extract_signals_regex(observation, entity_refs)

            # Convert raw LLM output to Signal objects
            signals: list[Signal] = []
            for pos, raw in enumerate(raw_signals):
                try:
                    if not isinstance(raw, dict):
                        continue

                    signal_type = raw.get("type", "").strip().lower()
                    if signal_type not in ("decision", "action_item", "key_point", "insight"):
                        continue

                    content = raw.get("content", "").strip()
                    if not content or len(content) < 10:
                        continue

                    confidence = raw.get("confidence", 0.8)
                    if not isinstance(confidence, (int, float)) or confidence < 0.5:
                        continue

                    # Resolve per-signal entities from names Claude returned
                    signal_entity_names = raw.get("entities") or []
                    if not isinstance(signal_entity_names, list):
                        signal_entity_names = []
                    signal_entities = self._resolve_signal_entities(signal_entity_names, entity_refs)

                    # Owner for action items
                    owner_ref = None
                    owner_value = raw.get("owner")
                    if signal_type == "action_item" and isinstance(owner_value, str) and owner_value.strip():
                        owner_ref = self._resolve_owner(owner_value.strip(), entity_refs)

                    signal_status = None
                    if signal_type == "action_item":
                        signal_status = raw.get("status", "open")
                        if signal_status not in ("open", "in_progress", "done"):
                            signal_status = "open"

                    signal_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_DNS,
                            f"{signal_type}:{meeting_id}:{pos}:{content[:100]}",
                        )
                    )

                    # Two-tier decisions: below the candidate threshold a
                    # decision is a CANDIDATE, not a confirmed decision.
                    signal_metadata = {}
                    if (
                        signal_type == "decision"
                        and confidence < DECISION_CANDIDATE_THRESHOLD
                    ):
                        signal_metadata["tier"] = "candidate"
                        logger.info(
                            "[SIGNALS] Tagging decision as candidate "
                            "(confidence %.2f < %.2f) for %s",
                            confidence,
                            DECISION_CANDIDATE_THRESHOLD,
                            meeting_id,
                        )

                    signals.append(
                        Signal(
                            id=signal_id,
                            type=signal_type,
                            content=content,
                            metadata=signal_metadata,
                            source_meeting_id=meeting_id,
                            source_meeting_title=meeting_title,
                            source_timestamp=timestamp,
                            participants=participants,
                            entities=signal_entities,
                            confidence=round(confidence, 2),
                            status=signal_status,
                            owner=owner_ref,
                            position=pos,
                            # G2 wiring: explicit provenance for LLM path (PRD §10.1)
                            provenance_status="inferred",
                            review_status="pending",
                        )
                    )
                except Exception as item_err:
                    logger.warning("[SIGNALS] Skipping malformed LLM signal item %d: %s", pos, item_err)
                    continue

            logger.info(
                "[SIGNALS] LLM extracted %d signals (from %d raw) for %s",
                len(signals),
                len(raw_signals),
                meeting_id,
            )
            return signals

        except Exception as e:
            logger.error(
                "[SIGNALS] LLM extraction failed for %s: %s, falling back to regex",
                meeting_id,
                e,
                exc_info=True,
            )
            return self._extract_signals_regex(observation, entity_refs)

    @staticmethod
    def _extract_response_text(response) -> str | None:
        """Extract text content from a Claude API response."""
        if hasattr(response, "content") and response.content:
            if len(response.content) > 0 and hasattr(response.content[0], "text"):
                return response.content[0].text
        return None

    @staticmethod
    def _parse_llm_signals(text: str) -> list[dict] | None:
        """Parse JSON array from LLM response text with error recovery."""
        text = text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            logger.warning("[SIGNALS] LLM returned non-array JSON: %s", type(parsed))
            return None
        except json.JSONDecodeError as e:
            # Try to find a JSON array within the text
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
            logger.warning("[SIGNALS] Could not parse LLM response as JSON: %s", e)
            return None

    def _resolve_signal_entities(self, entity_names: list, entity_refs: list[EntityRef]) -> list[EntityRef]:
        """Match entity names from LLM output against pre-resolved EntityRef list.

        Uses case-insensitive word-overlap matching to avoid false positives
        (e.g. "Sam" should not match "Samantha"). Deduplicates by entity ID.
        """
        if not entity_names:
            return []

        matched: list[EntityRef] = []
        seen_ids: set[str] = set()

        for name in entity_names:
            if not isinstance(name, str) or not name.strip():
                continue
            name_lower = name.strip().lower()
            name_words = set(name_lower.split())

            # Try exact match first, then word-overlap match
            best_match: EntityRef | None = None
            for ref in entity_refs:
                ref_lower = ref.name.lower()
                if name_lower == ref_lower:
                    best_match = ref
                    break
                # Require at least one shared word (e.g. "Sarah" matches
                # "Sarah Chen", but "Sam" won't match "Samantha")
                ref_words = set(ref_lower.split())
                if name_words & ref_words:
                    best_match = ref
                    # Don't break — keep looking for exact match

            if best_match and best_match.id not in seen_ids:
                seen_ids.add(best_match.id)
                matched.append(best_match)

        return matched

    # ------------------------------------------------------------------
    # Regex-based signal extraction (fallback)
    # ------------------------------------------------------------------

    def _extract_signals_regex(self, observation: Observation, entity_refs: list[EntityRef]) -> list[Signal]:
        """Extract all signals from an observation using regex, preserving document order.

        This is the original extraction method kept as a fallback when no
        claude_client is available or when the LLM call fails.
        """
        raw_signals: list[tuple[int, Signal]] = []
        body = observation.content or ""
        meeting_id = observation.external_id  # legacy: Signal.source_meeting_id takes external_id (was bot_id)
        meeting_title = observation.title
        timestamp = observation.observed_at.isoformat()
        participants = observation.participants or []

        # --- Decisions ---
        for pattern in [
            r"Decisions Made",
            r"Decisions",
            r"Strategic Decisions",
            r"Technical Decisions",
        ]:
            offset = self._find_section_offset(body, pattern)
            section = self._extract_section(body, pattern)
            if section:
                items = self._parse_list_items(section)
                for idx, item in enumerate(items):
                    raw_signals.append(
                        (
                            offset + idx,
                            Signal(
                                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"decision:{meeting_id}:{idx}:{item[:100]}")),
                                type="decision",
                                content=item,
                                source_meeting_id=meeting_id,
                                source_meeting_title=meeting_title,
                                source_timestamp=timestamp,
                                participants=participants,
                                entities=entity_refs,
                                # G2 wiring: explicit provenance for regex path (PRD §10.1)
                                provenance_status="observed",
                                review_status="pending",
                            ),
                        )
                    )

        # --- Action Items ---
        for pattern in [
            r"Action Items and Next Steps",
            r"Action Items",
            r"Next Steps",
            r"Immediate Actions.*",
            r"Recommended Best Practices.*",
        ]:
            offset = self._find_section_offset(body, pattern)
            section = self._extract_section(body, pattern)
            if section:
                items = self._parse_list_items(section)
                for idx, item in enumerate(items):
                    owner_name = self._extract_action_item_owner(item)
                    status = self._extract_action_item_status(item)
                    owner_ref = self._resolve_owner(owner_name, entity_refs) if owner_name else None
                    raw_signals.append(
                        (
                            offset + idx,
                            Signal(
                                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"action:{meeting_id}:{idx}:{item[:100]}")),
                                type="action_item",
                                content=item,
                                source_meeting_id=meeting_id,
                                source_meeting_title=meeting_title,
                                source_timestamp=timestamp,
                                participants=participants,
                                entities=entity_refs,
                                status=status,
                                owner=owner_ref,
                                # G2 wiring: explicit provenance for regex path (PRD §10.1)
                                provenance_status="observed",
                                review_status="pending",
                            ),
                        )
                    )

        # --- Key Points ---
        for pattern in [
            r"Key Discussion Points",
            r"Key Points",
            r"Key Takeaways",
            r"Key Themes",
        ]:
            offset = self._find_section_offset(body, pattern)
            section = self._extract_section(body, pattern)
            if section:
                items = self._parse_list_items(section)
                for idx, item in enumerate(items[:5]):
                    raw_signals.append(
                        (
                            offset + idx,
                            Signal(
                                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"keypoint:{meeting_id}:{idx}:{item[:100]}")),
                                type="key_point",
                                content=item,
                                source_meeting_id=meeting_id,
                                source_meeting_title=meeting_title,
                                source_timestamp=timestamp,
                                participants=participants,
                                entities=entity_refs,
                                # G2 wiring: explicit provenance for regex path (PRD §10.1)
                                provenance_status="observed",
                                review_status="pending",
                            ),
                        )
                    )

        # --- Insights ---
        for pattern in [
            r"Important Insights.*",
            r"Insights",
            r"Important Insights and Conclusions",
        ]:
            offset = self._find_section_offset(body, pattern)
            section = self._extract_section(body, pattern)
            if section:
                items = self._parse_list_items(section)
                for idx, item in enumerate(items[:3]):
                    raw_signals.append(
                        (
                            offset + idx,
                            Signal(
                                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"insight:{meeting_id}:{idx}:{item[:100]}")),
                                type="insight",
                                content=item,
                                source_meeting_id=meeting_id,
                                source_meeting_title=meeting_title,
                                source_timestamp=timestamp,
                                participants=participants,
                                entities=entity_refs,
                                # G2 wiring: explicit provenance for regex path (PRD §10.1)
                                provenance_status="observed",
                                review_status="pending",
                            ),
                        )
                    )

        # Sort by document position and assign position index
        raw_signals.sort(key=lambda t: t[0])
        signals = []
        for pos, (_, signal) in enumerate(raw_signals):
            signal.position = pos
            signals.append(signal)

        return signals

    # ------------------------------------------------------------------
    # Markdown parsing helpers (exact copies from signal_feed.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_section(body: str, section_pattern: str) -> str:
        """Extract content between a section header and the next ## header."""
        pattern = rf"##\s+{section_pattern}\s*\n(.*?)(?=\n##\s|\Z)"
        match = re.search(pattern, body, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _parse_list_items(section_text: str) -> list[str]:
        """Parse bullet points and numbered items from a section."""
        items = []
        for line in section_text.split("\n"):
            line = line.strip()
            if not line or line.startswith("##") or line.startswith("---"):
                continue
            if re.match(
                r"^(no |none |not |n/a\b|no specific|no explicit|no formal)",
                line,
                re.IGNORECASE,
            ):
                continue
            # Bullet points: - item, * item, - [x] item, - [ ] item
            bullet_match = re.match(r"^[-*]\s+(\[.\]\s+)?(.*)", line)
            if bullet_match:
                content = bullet_match.group(2).strip()
                if content and len(content) > 5:
                    items.append(content)
                continue
            # Numbered items: 1. item, 1) item
            num_match = re.match(r"^\d+[.)]\s+(.*)", line)
            if num_match:
                content = num_match.group(1).strip()
                if content and len(content) > 5:
                    items.append(content)
                continue
            # Bold items: **Title**: Description
            bold_match = re.match(r"^\*\*(.+?)\*\*:?\s*(.*)", line)
            if bold_match:
                title = bold_match.group(1).strip()
                desc = bold_match.group(2).strip()
                content = f"{title}: {desc}" if desc else title
                if content and len(content) > 5:
                    items.append(content)
        return items

    @staticmethod
    def _extract_action_item_owner(text: str) -> str | None:
        """Try to extract an owner/assignee from action item text."""
        assigned_match = re.search(r"assigned:?\s*(\w[\w\s]*\w)", text, re.IGNORECASE)
        if assigned_match:
            return assigned_match.group(1).strip()
        to_match = re.match(r"^(\w[\w\s]{1,30}?)\s+to\s+\w", text, re.IGNORECASE)
        if to_match:
            name = to_match.group(1).strip()
            if name.lower() not in (
                "need",
                "plan",
                "want",
                "going",
                "continue",
                "start",
                "begin",
                "try",
                "ensure",
                "complete",
            ):
                return name
        return None

    @staticmethod
    def _extract_action_item_status(text: str) -> str:
        """Determine action item status from markdown checkbox."""
        return "done" if "[x]" in text.lower() else "open"

    @staticmethod
    def _find_section_offset(body: str, section_pattern: str) -> int:
        """Find the byte offset of a section header in the body for ordering."""
        match = re.search(rf"##\s+{section_pattern}", body, re.IGNORECASE)
        return match.start() if match else len(body)

    # ------------------------------------------------------------------
    # Entity resolution
    # ------------------------------------------------------------------

    def _resolve_entity(self, entity_type: str, name: str) -> EntityRef:
        """Resolve a single entity name to an EntityRef with deterministic slug ID.

        Uses ensure_entity_id_format for the slug, and optionally verifies
        the entity exists in the knowledge graph.
        """
        slug_id = ensure_entity_id_format(entity_type, name)

        # Optionally verify against knowledge graph (synchronous, in-memory cache)
        if self._kg:
            try:
                entity = self._kg.get_entity_by_name(name, entity_type)
                if entity:
                    # Use the graph's canonical ID if available
                    return EntityRef(
                        id=entity.get("id", slug_id),
                        type=entity_type,
                        name=entity.get("name", name),
                    )
            except Exception as e:
                # Graph lookup failed — fall back to slug-only
                logger.debug(f"[SIGNALS] Graph lookup failed for {entity_type}/{name}: {e}")

        return EntityRef(id=slug_id, type=entity_type, name=name)

    def _resolve_entities_from_state(self, observation: Observation) -> list[EntityRef]:
        """Convert entities_mentioned dict to a flat list of EntityRef objects.

        observation.entities_mentioned is shaped like:
            {"person": ["Sarah Chen", "David Kim"], "project": ["CRM Modernization"]}

        Deduplicates by entity ID to prevent the same entity appearing twice
        (e.g. when "participants" type entities overlap with "person" entities).
        """
        refs: list[EntityRef] = []
        seen_ids: set[str] = set()
        entities_mentioned = observation.entities_mentioned or {}

        active_types = get_active_entity_types()
        for entity_type, names in entities_mentioned.items():
            if entity_type not in active_types:
                logger.debug(f"[SIGNALS] Skipping non-entity key '{entity_type}' in entities_mentioned")
                continue
            for name in names:
                try:
                    ref = self._resolve_entity(entity_type, name)
                    if ref.id not in seen_ids:
                        seen_ids.add(ref.id)
                        refs.append(ref)
                except Exception as e:
                    logger.warning(f"[SIGNALS] Failed to resolve entity {entity_type}/{name}: {e}")
                    continue

        return refs

    @staticmethod
    def _client_type_ids() -> set[str]:
        """Entity type IDs treated as the 'client' scope for the active domain."""
        try:
            from app.core.domain_config.domain_config_service import get_domain_config_service
            domain = get_domain_config_service().get_active_domain()
            if domain and domain.entities:
                # 'client' if present, else 'account' (relabel mode), else nothing
                return {t for t in ("client", "account") if t in domain.entities}
        except Exception as e:
            logger.warning("[SIGNALS] Failed to load domain config for client types: %s", e, exc_info=True)
        return {"client", "account"}

    def _apply_client_scope(self, signals: list[Signal]) -> list[Signal]:
        """Set client_id on each signal: prefer the signal's own client entity,
        else fall back to the meeting's single dominant client (if unambiguous)."""
        client_types = self._client_type_ids()

        def own_client(sig: Signal) -> str | None:
            client_ids = {ref.id for ref in sig.entities if ref.type in client_types}
            return next(iter(client_ids)) if len(client_ids) == 1 else None

        # Meeting-level dominant client = the unique client referenced across signals
        meeting_clients = {cid for sig in signals if (cid := own_client(sig))}
        fallback = next(iter(meeting_clients)) if len(meeting_clients) == 1 else None

        for sig in signals:
            sig.client_id = own_client(sig) or fallback
        return signals

    def _resolve_owner(self, owner_name: str, entity_refs: list[EntityRef]) -> EntityRef | None:
        """Match an action item owner name against already-resolved entity refs.

        First tries fuzzy match against existing refs (avoids duplicate lookups),
        then falls back to fresh resolution as a person entity.
        """
        owner_lower = owner_name.lower()

        # Try matching against already-resolved person entities (word boundary match)
        for ref in entity_refs:
            if ref.type == "person" and (owner_lower == ref.name.lower() or owner_lower in ref.name.lower().split()):
                return ref

        # Fall back to fresh resolution
        return self._resolve_entity("person", owner_name)
