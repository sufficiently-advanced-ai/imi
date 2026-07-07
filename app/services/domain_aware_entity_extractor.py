"""Domain-Aware Entity Extractor Service - Issue #247

DEPRECATED: This module is superseded by SemanticaExtraction
(app/services/semantica_extraction.py). Kept as fallback only.
When SemanticaKnowledge is initialized, extraction uses Semantica NER
with Anthropic provider + deduplication v2 instead.
"""

import logging
import re
from typing import Any

import yaml

from app.model_schemas.domain_config import DomainConfiguration
from app.services.file_cache import file_cache

logger = logging.getLogger(__name__)


class DomainAwareEntityExtractor:
    """Extract entities from files based on domain configuration."""

    # Standard field mappings for common entity types
    # These provide sensible defaults that can be extended by domain configs
    STANDARD_FIELD_MAPPINGS = {
        "person": [
            "people",
            "person",
            "participants",
            "attendees",
            "members",
            "authors",
        ],
        "project": ["projects", "project", "initiatives"],
        "team": ["teams", "team", "departments", "department"],
        "account": ["accounts", "account"],
        "contact": ["contacts", "contact"],
        "organization": ["organizations", "organization"],
        "interaction": ["interactions", "interaction"],
    }

    # Standard suffix removals for entity types
    STANDARD_SUFFIX_REMOVALS = {
        "person": [r"\s*\([^)]+\)\s*$"],  # Remove role info in parentheses
        "project": [
            r"\s*(project|program)$"
        ],  # Keep "initiative" as it's often part of the name
        "team": [r"\s*(team|department|group|division)$"],
        "account": [
            r"\s*(corporation|inc|llc|ltd)\.?$"
        ],  # Keep "corp" as it's often part of the name
        "organization": [
            r"\s*(corporation|inc|llc|ltd)\.?$"
        ],  # Keep "corp" as it's often part of the name
    }

    def _extract_frontmatter_and_content(
        self, content: str
    ) -> tuple[dict[str, Any] | None, str]:
        """Extract frontmatter metadata and content from markdown."""
        if not content.startswith("---\n"):
            return None, content

        end_idx = content.find("\n---\n", 4)
        if end_idx == -1:
            return None, content

        try:
            yaml_content = content[4:end_idx]
            metadata = yaml.safe_load(yaml_content)
            body_content = content[end_idx + 5 :]
            return metadata, body_content
        except yaml.YAMLError:
            return None, content

    def _get_entity_field_mappings(
        self, domain_config: DomainConfiguration
    ) -> dict[str, list[str]]:
        """
        Generate field mappings for entity types based on domain configuration.

        Returns a dictionary mapping entity type to list of metadata field names
        that should be checked for that entity type.
        """
        if not domain_config:
            return {}

        mappings = {}

        # For each entity type in the domain, create field mappings
        for entity_type in domain_config.entities.keys():
            # Start with standard mappings if available
            if entity_type in self.STANDARD_FIELD_MAPPINGS:
                mappings[entity_type] = self.STANDARD_FIELD_MAPPINGS[entity_type].copy()
            else:
                # For custom entity types, generate sensible defaults
                mappings[entity_type] = [
                    entity_type + "s",  # plural form
                    entity_type,  # singular form
                ]

        return mappings

    def normalize_entity_id(
        self, entity_type: str, raw_name: str, domain_config: DomainConfiguration
    ) -> str:
        """
        Normalize entity name to consistent ID format.

        Args:
            entity_type: Type of entity from domain config
            raw_name: Raw entity name
            domain_config: Domain configuration

        Returns:
            Normalized ID with appropriate prefix
        """
        # Use entity type as prefix
        prefix = entity_type

        # Clean the name
        name = raw_name.strip()

        # Apply standard suffix removals if available
        if entity_type in self.STANDARD_SUFFIX_REMOVALS:
            for pattern in self.STANDARD_SUFFIX_REMOVALS[entity_type]:
                name = re.sub(pattern, "", name, flags=re.IGNORECASE)

        # Normalize to lowercase and replace non-alphanumeric with hyphens
        normalized = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

        # Add prefix
        return f"{prefix}-{normalized}"

    async def extract_entities_from_metadata(
        self, file_path: str, domain_config: DomainConfiguration | None
    ) -> dict[str, list[str]]:
        """
        Extract entities from file metadata based on domain configuration.

        Args:
            file_path: Path to the file to analyze
            domain_config: Domain configuration defining entity types

        Returns:
            Dictionary mapping entity types to lists of normalized entity IDs
        """
        # Handle missing domain config
        if not domain_config:
            logger.warning(
                f"No domain configuration provided for entity extraction from {file_path}"
            )
            return {}

        # Initialize result with all entity types from domain
        result = {entity_type: [] for entity_type in domain_config.entities.keys()}

        # Get file content
        file = await file_cache.get_file(file_path)
        if not file:
            logger.debug(f"File not found: {file_path}")
            return result

        # Extract metadata
        metadata, _ = self._extract_frontmatter_and_content(file.content)
        if not metadata:
            logger.debug(f"No metadata found in {file_path}")
            return result

        logger.debug(f"Metadata fields in {file_path}: {list(metadata.keys())}")

        # Get field mappings for this domain
        field_mappings = self._get_entity_field_mappings(domain_config)

        # Extract entities for each type
        for entity_type, field_names in field_mappings.items():
            extracted_ids = set()  # Use set to avoid duplicates

            for field_name in field_names:
                if field_name in metadata:
                    values = metadata[field_name]

                    # Handle list values
                    if isinstance(values, list):
                        for value in values:
                            if isinstance(value, str):
                                entity_id = self.normalize_entity_id(
                                    entity_type, value, domain_config
                                )
                                extracted_ids.add(entity_id)

                    # Handle single string values
                    elif isinstance(values, str):
                        entity_id = self.normalize_entity_id(
                            entity_type, values, domain_config
                        )
                        extracted_ids.add(entity_id)

            # Convert set to sorted list for consistent output
            result[entity_type] = sorted(list(extracted_ids))

        # Log extraction results
        total_entities = sum(len(entities) for entities in result.values())
        if total_entities > 0:
            logger.info(f"Extracted {total_entities} entities from {file_path}")
            for entity_type, entities in result.items():
                if entities:
                    logger.debug(f"  {entity_type}: {entities}")

        return result
