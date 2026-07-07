"""
Semantica Extraction — Entity extraction and deduplication via Semantica.

Replaces:
- domain_aware_entity_extractor.py
- entity_registry.py (extraction parts)

Uses Semantica's NERExtractor with Anthropic provider for LLM-based extraction,
plus DuplicateDetector for entity deduplication.
"""

import logging
from typing import Any

from app.model_schemas.domain_config import DomainConfiguration
from app.services.semantica_config import (
    make_entity_id,
)

logger = logging.getLogger(__name__)


class SemanticaExtraction:
    """Entity extraction and deduplication backed by Semantica."""

    def __init__(
        self,
        ner_extractor: Any,
        duplicate_detector: Any,
        domain_config: DomainConfiguration | None = None,
    ):
        self.ner = ner_extractor
        self.dedup = duplicate_detector
        self.domain = domain_config

    async def extract_entities(
        self,
        text: str,
        entity_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Extract entities from text using Semantica NER.

        Args:
            text: Input text to extract entities from.
            entity_types: Optional filter for entity types to extract.

        Returns:
            List of entity dicts with keys: id, name, type, confidence, metadata.
        """
        if not text or not text.strip():
            return []

        try:
            # Use Semantica NER extractor
            raw_entities = self.ner.extract_entities(text)

            # Convert Semantica Entity objects to our format
            entities = []
            for entity in raw_entities:
                entity_type = self._map_ner_label(entity.label)

                # Apply type filter
                if entity_types and entity_type not in entity_types:
                    continue

                # Domain-configured exclusions: drop terms that match this type's
                # ner_labels but are not real instances (e.g. frameworks/standards
                # bodies mis-tagged as ORG -> client).
                if self.domain and self.domain.entities:
                    edef = self.domain.entities.get(entity_type)
                    if edef is not None:
                        excluded = {x.strip().lower() for x in (getattr(edef, "ner_exclude", None) or [])}
                        if entity.text.strip().lower() in excluded:
                            continue

                entity_id = make_entity_id(entity_type, entity.text)
                entities.append({
                    "id": entity_id,
                    "name": entity.text,
                    "type": entity_type,
                    "confidence": entity.confidence,
                    "metadata": {
                        "source": "semantica_ner",
                        "start_char": entity.start_char,
                        "end_char": entity.end_char,
                        **(entity.metadata or {}),
                    },
                })

            logger.info(f"Extracted {len(entities)} entities from text ({len(text)} chars)")
            return entities

        except Exception as e:
            logger.error(f"Entity extraction failed: {e}")
            return []

    async def extract_entities_grouped(
        self,
        text: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Extract entities grouped by type (matches old extract_entities API).

        Returns:
            Dict mapping entity type → list of entity dicts.
            e.g. {"person": [...], "project": [...]}
        """
        entities = await self.extract_entities(text)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entity in entities:
            entity_type = entity["type"]
            if entity_type not in grouped:
                grouped[entity_type] = []
            grouped[entity_type].append(entity)
        return grouped

    async def deduplicate(
        self,
        entities: list[dict[str, Any]],
        existing_entities: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Deduplicate entities using Semantica's DuplicateDetector.

        Args:
            entities: New entities to deduplicate.
            existing_entities: Optional existing entities to check against.

        Returns:
            Deduplicated list of entities with merged metadata.
        """
        if not entities:
            return []

        try:
            if existing_entities:
                # Incremental dedup against existing entities
                duplicates = self.dedup.incremental_detect(
                    new_entities=entities,
                    existing_entities=existing_entities,
                )
            else:
                # Self-dedup within the batch
                duplicates = self.dedup.detect_duplicates(entities)

            # Build a map of duplicates to skip
            skip_ids = set()
            merge_map: dict[str, dict] = {}  # entity_name → primary entity

            for dup in duplicates:
                # Keep entity1 (higher confidence), skip entity2
                primary = dup.entity1
                duplicate = dup.entity2
                primary_name = primary.get("name", primary.get("text", ""))
                dup_name = duplicate.get("name", duplicate.get("text", ""))

                skip_ids.add(dup_name)
                if primary_name not in merge_map:
                    merge_map[primary_name] = primary

            # Filter out duplicates
            result = [e for e in entities if e.get("name") not in skip_ids]
            logger.info(
                f"Dedup: {len(entities)} → {len(result)} entities "
                f"({len(duplicates)} duplicates found)"
            )
            return result

        except Exception as e:
            logger.error(f"Deduplication failed: {e}")
            return entities  # Return unmodified on failure

    # Default NER label → generic type (used when the domain declares no ner_labels)
    _DEFAULT_LABEL_MAP = {
        "PERSON": "person", "PER": "person",
        "ORG": "organization", "ORGANIZATION": "organization",
        "GPE": "location", "LOC": "location", "LOCATION": "location",
        "PROJECT": "project", "PRODUCT": "project",
        "TEAM": "team", "GROUP": "team",
        "EVENT": "event", "DATE": "date", "MONEY": "financial", "TOPIC": "topic",
    }

    def _map_ner_label(self, label: str) -> str:
        """Map an NER label to a domain entity type.

        Domain config is authoritative: if any entity declares this label in its
        `ner_labels`, that entity type wins. Otherwise fall back to the default
        label map, then to the lowercased label.
        """
        upper = label.upper()

        # 1. Domain-declared ner_labels win
        if self.domain and self.domain.entities:
            for type_id, entity in self.domain.entities.items():
                labels = getattr(entity, "ner_labels", None) or []
                if upper in {lbl.upper() for lbl in labels}:
                    return type_id

        # 2. Default label map, then lowercased label
        return self._DEFAULT_LABEL_MAP.get(upper, label.lower())
