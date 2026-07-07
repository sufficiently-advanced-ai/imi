"""
Consolidated EntityService - Issue #395

This module consolidates functionality from:
- entity_brain.py (compatibility adapter)
- entity_brain_enhanced.py (dynamic registry support)
- entity_brain_refactored.py (domain-aware processing)

Features consolidated:
- Entity extraction from files and content
- Domain-aware entity processing
- Entity file creation and management
- Relationship management
- Claude integration for content analysis
- Backward compatibility with existing interfaces
- Migration utilities for legacy entities
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.domain.entities.services.entity_repository import EntityRepository

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Ensure INFO level logging for diagnostic messages
# Add handler to ensure logs output even with WARNING root logger
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(levelname)s: %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Constants for entity processing
VALID_ENTITY_TYPES = [
    "person",
    "project",
    "organization",
    "team",
    "account",
]  # Fallback types
MAX_CONTENT_LENGTH = 5000  # Maximum content length for Claude analysis
MAX_ENTITIES_PER_TYPE = 100  # Maximum entities to extract per type


class EntityService:
    """
    Unified entity management service.

    Consolidates functionality from multiple entity brain implementations
    into a single, comprehensive service.
    """

    def __init__(
        self,
        repository: EntityRepository | None = None,
        claude_client: Any | None = None,
        domain_id: str | None = None,
    ):
        """
        Initialize the entity service.

        Args:
            repository: EntityRepository instance (uses singleton if None)
            claude_client: Claude client for content analysis
            domain_id: Optional domain to load on initialization
        """
        from app.services.claude_client import get_claude_client

        self._repository = repository or EntityRepository()
        self._claude = claude_client or get_claude_client()
        self._current_domain_id = None
        self._domain_config = None  # Set when load_domain() succeeds — Issue #835
        self._domain_loading_task = None

        # Load domain if specified
        if domain_id:
            self._domain_loading_task = asyncio.create_task(
                self._load_domain_with_error_handling(domain_id)
            )

    async def _load_domain_with_error_handling(self, domain_id: str) -> None:
        """Load domain with proper error handling."""
        try:
            await self.load_domain(domain_id)
        except Exception as e:
            logger.error(
                f"Failed to load domain {domain_id} during initialization: {e}"
            )

    async def load_domain(self, domain_id: str) -> bool:
        """
        Load a domain configuration into the service.

        Args:
            domain_id: Domain configuration ID

        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            from app.core.dependencies import get_domain_config_service

            # Use the service registry singleton to ensure consistent domain config
            loader = get_domain_config_service()
            domain_config = await loader.load_domain(domain_id)

            if domain_config:
                await self._repository.register_domain(domain_config)
                self._current_domain_id = domain_id
                self._domain_config = (
                    domain_config  # Issue #835: store for prompt context
                )
                logger.info(f"Loaded domain '{domain_id}' into EntityService")
                return True
            else:
                # Clear stale state on failure — CodeRabbit review PR #839
                self._current_domain_id = None
                self._domain_config = None
                logger.error(f"Failed to load domain configuration: {domain_id}")
                return False

        except Exception as e:
            # Clear stale state on failure — CodeRabbit review PR #839
            self._current_domain_id = None
            self._domain_config = None
            logger.error(f"Error loading domain {domain_id}: {e}")
            return False

    def get_current_domain_id(self) -> str | None:
        """Get the currently loaded domain ID."""
        return self._current_domain_id

    # Entity extraction methods
    async def extract_entities(self, file_path: str) -> dict[str, list[str]]:
        """
        Extract all registered entity types from a file.

        Args:
            file_path: Path to the file to analyze

        Returns:
            Dictionary mapping entity types to lists of entity IDs
        """
        try:
            # Initialize result with all registered entity types
            entity_types = self.get_entity_types()
            result = {entity_type: [] for entity_type in entity_types}

            # Try metadata extraction first
            metadata_entities = await self._extract_entities_from_metadata(file_path)

            # Merge results
            for entity_type, entities in metadata_entities.items():
                if entity_type in result:
                    result[entity_type] = entities

            # If no entities found in metadata, try content extraction
            if not any(len(entities) > 0 for entities in result.values()):
                content_entities = await self._extract_entities_from_content(file_path)
                for entity_type, entities in content_entities.items():
                    if entity_type in result:
                        result[entity_type].extend(entities)
                        # Remove duplicates
                        result[entity_type] = list(set(result[entity_type]))

            # Log results
            total_entities = sum(len(entities) for entities in result.values())
            if total_entities > 0:
                logger.info(f"Extracted {total_entities} entities from {file_path}")
            else:
                logger.debug(f"No entities found in {file_path}")

            return result

        except Exception as e:
            logger.error(f"Error extracting entities from {file_path}: {e}")
            # Return empty results for all registered types
            entity_types = self.get_entity_types()
            return {entity_type: [] for entity_type in entity_types}

    async def _extract_entities_from_metadata(
        self, file_path: str
    ) -> dict[str, list[str]]:
        """Extract entities from file metadata/frontmatter."""
        from app.services.file_cache import file_cache

        entity_types = self.get_entity_types()
        result = {entity_type: [] for entity_type in entity_types}

        file = await file_cache.get_file(file_path)
        if not file:
            return result

        metadata, _ = self._extract_frontmatter_and_content(file.content)
        if not metadata:
            logger.debug(f"No metadata found in {file_path}")
            return result

        logger.debug(f"Metadata fields in {file_path}: {list(metadata.keys())}")

        # Dynamic entity extraction based on registry
        for entity_type in entity_types:
            entities_found = []

            # Check exact match
            if entity_type in metadata:
                entities_found.extend(self._extract_entity_list(metadata[entity_type]))

            # Check plural form
            plural_key = f"{entity_type}s"
            if plural_key in metadata and plural_key != entity_type:
                entities_found.extend(self._extract_entity_list(metadata[plural_key]))

            # Check alternative naming patterns
            entity_schema = self._repository.get_entity_schema(entity_type)
            if entity_schema:
                # Check by entity name (e.g., "Account" for "account" type)
                name_key = entity_schema.name.lower()
                if name_key in metadata:
                    entities_found.extend(self._extract_entity_list(metadata[name_key]))

                # Check plural of entity name
                name_plural = f"{name_key}s"
                if name_plural in metadata:
                    entities_found.extend(
                        self._extract_entity_list(metadata[name_plural])
                    )

            # Normalize all found entities
            for entity_name in entities_found:
                if entity_name:
                    entity_id = self.normalize_entity_id(entity_type, entity_name)
                    if entity_id not in result[entity_type]:
                        result[entity_type].append(entity_id)

        return result

    def _extract_entity_list(self, value: Any) -> list[str]:
        """Extract entity names from various value types."""
        if isinstance(value, list):
            return [str(item).strip() for item in value if item and str(item).strip()]
        elif isinstance(value, str) and value.strip():
            # Handle comma-separated values
            if "," in value:
                return [part.strip() for part in value.split(",") if part.strip()]
            else:
                return [value.strip()]
        else:
            return []

    async def _extract_entities_from_content(
        self, file_path: str
    ) -> dict[str, list[str]]:
        """Extract entities from file content using Claude."""
        from app.services.file_cache import file_cache

        entity_types = self.get_entity_types()
        result = {entity_type: [] for entity_type in entity_types}

        file = await file_cache.get_file(file_path)
        if not file or not file.content:
            return result

        # Generate extraction prompts
        extraction_prompts = self.get_entity_extraction_prompts()

        # Extract each entity type
        for entity_type, prompt in extraction_prompts.items():
            try:
                # Combine prompt with file content (truncated if too long)
                content = file.content[:MAX_CONTENT_LENGTH]
                full_prompt = f"{prompt}\n\nDocument content:\n{content}"

                # Call Claude for extraction - Haiku sufficient for NER
                from app.config import settings

                response = await self._claude.generate_message(
                    messages=[{"role": "user", "content": full_prompt}],
                    model=settings.CLAUDE_HAIKU_MODEL,
                    max_tokens=1000,
                    temperature=0.3,
                    operation="entity_extraction",
                )

                # Extract text from response
                if hasattr(response, "content"):
                    response_text = response.content[0].text
                elif isinstance(response, dict) and "content" in response:
                    response_text = response["content"][0]["text"]
                else:
                    response_text = str(response)

                # Use response_text instead of response for parsing
                response = response_text

                # Parse response for entity names
                entities = self._parse_entity_extraction_response(response, entity_type)
                result[entity_type] = entities

            except Exception as e:
                logger.error(f"Error extracting {entity_type} entities: {e}")

        return result

    async def extract_entities_from_content(self, content: str) -> dict[str, list[str]]:
        """
        Extract entities from content text using Claude.

        Args:
            content: Text content to analyze

        Returns:
            Dictionary mapping entity types to lists of entity IDs
        """
        entity_types = self.get_entity_types()
        result = {entity_type: [] for entity_type in entity_types}

        if not content.strip():
            return result

        # Generate extraction prompts
        extraction_prompts = self.get_entity_extraction_prompts()

        # Extract each entity type
        for entity_type, prompt in extraction_prompts.items():
            try:
                # Truncate content if too long
                truncated_content = content[:MAX_CONTENT_LENGTH]
                full_prompt = f"{prompt}\n\nContent:\n{truncated_content}"

                # Call Claude for extraction - Haiku sufficient for NER
                from app.config import settings

                response = await self._claude.generate_message(
                    messages=[{"role": "user", "content": full_prompt}],
                    model=settings.CLAUDE_HAIKU_MODEL,
                    max_tokens=1000,
                    temperature=0.3,
                    operation="entity_extraction",
                )

                # Extract text from response
                if hasattr(response, "content"):
                    response_text = response.content[0].text
                elif isinstance(response, dict) and "content" in response:
                    response_text = response["content"][0]["text"]
                else:
                    response_text = str(response)

                # Use response_text instead of response for parsing
                response = response_text

                # Parse response for entity names
                entities = self._parse_entity_extraction_response(response, entity_type)
                result[entity_type] = entities[:MAX_ENTITIES_PER_TYPE]  # Limit results

            except Exception as e:
                logger.error(
                    f"Error extracting {entity_type} entities from content: {e}"
                )

        return result

    def _parse_entity_extraction_response(
        self, response: str, entity_type: str
    ) -> list[str]:
        """Parse Claude's response to extract entity IDs."""
        entities = []

        # Look for common patterns in response
        patterns = [
            r"[-•]\s*([^(\n]+?)(?:\s*\([^)]+\))?$",  # Bullet points
            r"\d+\.\s*([^(\n]+?)(?:\s*\([^)]+\))?$",  # Numbered lists
            rf"{entity_type}s?:\s*([^(\n]+?)(?:\s*\([^)]+\))?$",  # Type prefix
        ]

        for line in response.split("\n"):
            line = line.strip()
            if line:
                for pattern in patterns:
                    match = re.search(pattern, line, re.MULTILINE | re.IGNORECASE)
                    if match:
                        entity_name = match.group(1).strip()
                        if entity_name:
                            entity_id = self.normalize_entity_id(
                                entity_type, entity_name
                            )
                            if entity_id not in entities:
                                entities.append(entity_id)
                        break

        return entities

    async def enrich_entities_from_transcript(
        self, transcript_text: str
    ) -> dict[str, list[str]]:
        """
        Extract and enrich entities from transcript text using Claude.

        Args:
            transcript_text: The transcript text to analyze

        Returns:
            Dictionary with entity types as keys and lists of entity names as values
        """
        # Get entity types from repository dynamically
        entity_types = self.get_entity_types()

        logger.info(
            f"[EntityService] Starting entity extraction from transcript ({len(transcript_text)} chars, {len(entity_types)} entity types)"
        )

        # Initialize result with empty lists for each entity type
        result = {entity_type: [] for entity_type in entity_types}

        # Return empty result for empty transcripts (skip Claude API call)
        if not transcript_text or not transcript_text.strip():
            logger.info("[EntityService] Empty transcript, skipping entity extraction")
            return result

        try:
            # Get existing entities context for better accuracy
            existing_entities_context = await self._get_existing_entities_context()

            # Build the prompt with dynamic entity types
            prompt_content = self._build_transcript_extraction_prompt(
                transcript_text, entity_types, existing_entities_context
            )

            # Call Claude for entity extraction
            from app.config import settings

            response = await self._claude.generate_message(
                messages=[{"role": "user", "content": prompt_content}],
                model=settings.CLAUDE_HAIKU_MODEL,
                max_tokens=2048,
                temperature=0.3,  # Low temperature for consistent extraction
                operation="transcript_entity_extraction",
            )

            # Parse the JSON response
            # Handle both dict and Message object response formats
            if hasattr(response, "content"):
                # Anthropic Message object
                response_text = response.content[0].text.strip()
            elif isinstance(response, dict) and "content" in response:
                # Dictionary format
                response_text = response["content"][0]["text"].strip()
            else:
                raise ValueError(f"Unexpected response format: {type(response)}")

            try:
                # The v2 prompt returns salience-labeled entities; parse with
                # the production parser, promote participant+subject (passing
                # mentions don't become entities here), and collapse to the
                # legacy {type: [names]} shape this method has always returned.
                from app.services.salient_entity_extractor import (
                    filter_salient_entities,
                    parse_salient_entities,
                    to_entities_mentioned,
                )

                labeled = parse_salient_entities(response_text, list(entity_types))
                promoted = filter_salient_entities(labeled, resolver=None)
                mentioned = to_entities_mentioned(promoted)
                for entity_type in entity_types:
                    if entity_type in mentioned:
                        result[entity_type] = mentioned[entity_type][
                            :MAX_ENTITIES_PER_TYPE
                        ]

            except Exception as e:
                logger.error(
                    f"[EntityService] Failed to parse Claude entity response: {e}"
                )
                # Avoid logging the full response: it can contain transcript
                # excerpts, names, and other sensitive meeting content. Log a
                # length + content hash so the failure is diagnosable without
                # leaking PII into application logs.
                import hashlib

                _digest = hashlib.sha256(
                    (response_text or "").encode("utf-8")
                ).hexdigest()[:12]
                logger.error(
                    "[EntityService] Unparseable response (len=%d, sha256=%s)",
                    len(response_text or ""),
                    _digest,
                )
                # Fall back to empty results rather than crashing

        except Exception as e:
            logger.error(f"[EntityService] Error during Claude entity extraction: {e}")
            # Re-raise the error so tests can catch and validate error handling
            raise RuntimeError(f"Entity extraction failed: {str(e)}") from e

        # Log successful extraction results
        total_entities = sum(len(entities) for entities in result.values())
        entity_summary = ", ".join(
            f"{len(result[et])} {et}" for et in entity_types if len(result[et]) > 0
        )
        logger.info(
            f"[EntityService] ✓ Successfully extracted {total_entities} total entities: {entity_summary or 'none found'}"
        )

        return result

    # File operations
    async def load_entity_file(self, entity_id: str) -> str | None:
        """
        Load an entity file content.

        Checks both domain-aware paths (e.g., people/jordan-reyes.md) and
        legacy paths (e.g., entities/person/person-jordan-reyes.md).

        Args:
            entity_id: ID of the entity or file path

        Returns:
            Content of the entity file or None if not found
        """
        from app.git_ops import GitOperationError, git_ops

        try:
            # Handle both entity IDs and file paths
            if entity_id.endswith(".md"):
                return await git_ops.read_file(entity_id)

            # Try domain-aware path first, with specific error handling
            # so we can fall back to legacy path on GitOperationError
            file_path = self._get_entity_file_path(entity_id)
            try:
                content = await git_ops.read_file(file_path)
                if content:
                    return content
            except GitOperationError:
                # Domain-aware path failed, try hyphenated variant
                # (e.g. focus_areas/ -> focus-areas/)
                hyphenated_path = file_path.replace("_", "-")
                if hyphenated_path != file_path:
                    try:
                        content = await git_ops.read_file(hyphenated_path)
                        if content:
                            return content
                    except GitOperationError:
                        pass
                logger.debug(
                    f"Entity not at domain path {file_path}, trying legacy path"
                )
                pass

            # Fall back to legacy path
            entity_type = self._get_entity_type_from_id(entity_id)
            legacy_path = f"entities/{entity_type}/{entity_id}.md"
            return await git_ops.read_file(legacy_path)

        except Exception as e:
            logger.error(f"Error loading entity file {entity_id}: {e}")
            return None

    async def save_entity_file(self, entity_id: str, content: str) -> None:
        """
        Save an entity file.

        If the file already exists at the legacy location, saves there to avoid
        creating duplicates. For new files, uses the domain-aware location.

        Args:
            entity_id: ID of the entity
            content: Content to save
        """
        import asyncio
        import os

        from app.git_ops import git_ops

        try:
            entity_type = self._get_entity_type_from_id(entity_id)

            # Check if file exists at legacy location
            # Use asyncio.to_thread to avoid blocking the event loop
            legacy_path = f"entities/{entity_type}/{entity_id}.md"
            legacy_full_path = os.path.join(git_ops.repo_path, legacy_path)

            legacy_exists = await asyncio.to_thread(os.path.exists, legacy_full_path)
            if legacy_exists:
                # Update in legacy location to avoid duplicates
                file_path = legacy_path
            else:
                # Use new domain-aware path
                file_path = self._get_entity_file_path(entity_id)
                domain_full_path = os.path.join(git_ops.repo_path, file_path)
                domain_exists = await asyncio.to_thread(os.path.exists, domain_full_path)
                if not domain_exists:
                    # Try hyphenated variant (e.g. focus_areas/ -> focus-areas/)
                    hyphenated_path = file_path.replace("_", "-")
                    if hyphenated_path != file_path:
                        hyph_full = os.path.join(git_ops.repo_path, hyphenated_path)
                        if await asyncio.to_thread(os.path.exists, hyph_full):
                            file_path = hyphenated_path

            await git_ops.commit_file(
                file_path,
                content,
                f"Update entity: {entity_type}/{entity_id}",
            )

            logger.debug(f"Saved entity file: {file_path}")

        except Exception as e:
            logger.error(f"Error saving entity file {entity_id}: {e}")

    async def update_entity_file(self, entity_id: str, content: str) -> None:
        """
        Update an entity file (alias for save_entity_file).

        Args:
            entity_id: ID of the entity
            content: Content to save
        """
        await self.save_entity_file(entity_id, content)

    async def update_entity_profile(
        self, entity_name: str, entity_type: str, updated_content: str
    ) -> None:
        """
        Update an entity's profile with defensive file creation.

        Args:
            entity_name: Name of the entity
            entity_type: Type of the entity
            updated_content: New content for the entity file
        """
        entity_id = self.normalize_entity_id(entity_type, entity_name)

        # Check if entity file exists
        existing_content = await self.load_entity_file(entity_id)

        if existing_content is None:
            # Entity file doesn't exist, create it defensively
            logger.info(f"Entity file not found for {entity_id}, creating defensively")

            # Create basic attributes from name
            attributes = {"name": entity_name, "canonical_name": entity_name}

            # Try to create the entity file properly with schema
            file_path = await self.create_entity_file(
                entity_type=entity_type, entity_id=entity_id, attributes=attributes
            )

            if file_path:
                logger.info(f"Defensively created entity file: {file_path}")
            else:
                # Fallback: just save the content directly
                logger.warning(
                    f"Could not create entity file properly, using fallback for {entity_id}"
                )
                await self.save_entity_file(entity_id, updated_content)
        else:
            # Entity exists, just update it
            await self.save_entity_file(entity_id, updated_content)

    # Entity creation and management
    async def create_entity_file(
        self,
        entity_type: str,
        entity_id: str,
        attributes: dict[str, Any],
        references: list[dict[str, str]] | None = None,
        relationships: dict[str, list[str]] | None = None,
    ) -> str | None:
        """
        Create an entity file with domain-aware structure.

        Args:
            entity_type: The entity type from registry
            entity_id: The normalized entity ID
            attributes: Entity attributes matching schema
            references: List of file references
            relationships: Initial relationships to other entities

        Returns:
            The file path if created successfully, None otherwise
        """
        try:
            # Validate entity against schema
            is_valid, errors = self._repository.validate_entity(entity_type, attributes)
            if not is_valid:
                logger.error(f"Entity validation failed: {errors}")
                return None

            # Get entity schema
            entity_schema = self._repository.get_entity_schema(entity_type)
            if not entity_schema:
                logger.error(f"Unknown entity type: {entity_type}")
                return None

            # Create entity using repository
            entity_data = await self._repository.create_entity(entity_type, attributes)
            if not entity_data:
                return None

            # Determine file path
            file_path = self._get_entity_file_path(entity_id)

            # Generate content
            content = await self._generate_entity_content(
                entity_schema,
                entity_type,
                entity_id,
                attributes,
                relationships,
                references,
            )

            # Commit the file
            from app.git_ops import git_ops

            await git_ops.commit_file(
                file_path,
                content,
                f"Create {entity_type} entity: {self._get_entity_display_name(attributes, entity_id)}",
            )

            logger.info(f"Created {entity_type} file: {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"Error creating entity file: {e}")
            return None

    # Relationship management
    async def update_entity_relationships(
        self,
        source_entity_type: str,
        source_entity_id: str,
        relationship_name: str,
        target_entity_id: str,
        bidirectional: bool = True,
    ) -> bool:
        """
        Update entity relationships with domain validation.

        Args:
            source_entity_type: Source entity type
            source_entity_id: Source entity ID
            relationship_name: Relationship name from schema
            target_entity_id: Target entity ID
            bidirectional: Whether to update inverse relationship

        Returns:
            True if successful, False otherwise
        """
        try:
            # Extract target entity type from ID
            target_entity_type = target_entity_id.split("-")[0]

            # Validate relationship
            if not self._repository.validate_relationship(
                source_entity_type, relationship_name, target_entity_type
            ):
                logger.error(
                    f"Invalid relationship: {source_entity_type}.{relationship_name} -> {target_entity_type}"
                )
                return False

            # Update source entity file
            from app.git_ops import git_ops
            from app.services.file_cache import file_cache

            source_path = self._get_entity_file_path(source_entity_id)
            source_file = await file_cache.get_file(source_path)
            if not source_file:
                logger.error(f"Source entity file not found: {source_path}")
                return False

            # Update metadata
            metadata, content = self._extract_frontmatter_and_content(
                source_file.content
            )
            if not metadata:
                metadata = {}

            # Add to relationship list
            if relationship_name not in metadata:
                metadata[relationship_name] = []
            if target_entity_id not in metadata[relationship_name]:
                metadata[relationship_name].append(target_entity_id)
                metadata["updated"] = datetime.utcnow().isoformat()

            # Rebuild file
            updated_content = f"---\n{yaml.dump(metadata, default_flow_style=False, sort_keys=False)}---\n{content}"

            # Commit update
            await git_ops.commit_file(
                source_path,
                updated_content,
                f"Add relationship: {source_entity_id} -> {target_entity_id}",
            )

            # Update inverse relationship if requested and defined
            if bidirectional:
                inverse_info = self._repository.get_inverse_relationship(
                    source_entity_type, relationship_name
                )
                if inverse_info:
                    await self.update_entity_relationships(
                        target_entity_type,
                        target_entity_id,
                        inverse_info["name"],
                        source_entity_id,
                        bidirectional=False,  # Prevent infinite recursion
                    )

            return True

        except Exception as e:
            logger.error(f"Error updating entity relationships: {e}")
            return False

    # Utility methods
    def normalize_entity_id(self, entity_type: str, raw_name: str) -> str:
        """
        Normalize entity name to consistent ID format.

        Args:
            entity_type: Type of entity from registry
            raw_name: Raw entity name

        Returns:
            Normalized ID with appropriate prefix
        """
        if not raw_name:
            raise ValueError("Entity name cannot be empty")

        # Get entity schema for intelligent normalization
        entity_schema = self._repository.get_entity_schema(entity_type)

        # Basic normalization
        name = raw_name.strip()

        if entity_schema:
            # Remove entity type name if it appears at the end
            entity_name_lower = entity_schema.name.lower()
            patterns = [
                rf"\s+{entity_name_lower}$",
                rf"\s+{entity_type}$",
            ]

            for pattern in patterns:
                name = re.sub(pattern, "", name, flags=re.IGNORECASE)

        # Normalize to lowercase and replace non-alphanumeric with hyphens
        normalized = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

        # Ensure it's not empty after normalization
        if not normalized:
            normalized = "unnamed"

        # Add entity type prefix
        return f"{entity_type}-{normalized}"

    def get_entity_extraction_prompts(self) -> dict[str, str]:
        """
        Generate entity extraction prompts based on registered entity types.

        Returns:
            Dictionary mapping entity types to extraction prompts
        """
        prompts = {}

        for entity_type in self.get_entity_types():
            entity_schema = self._repository.get_entity_schema(entity_type)
            if not entity_schema:
                # Fallback prompt for unknown types
                prompts[entity_type] = (
                    f"Extract {entity_type} entities from the document."
                )
                continue

            prompt = self._build_extraction_prompt(entity_schema, entity_type)
            prompts[entity_type] = prompt

        return prompts

    # Helper methods
    def get_entity_types(self) -> list[str]:
        """Get all registered entity types (compatibility method)."""
        types = self._repository.get_entity_types()
        if not types:
            # Fallback to default types if no domain is loaded
            return VALID_ENTITY_TYPES
        return types

    def _get_entity_type_from_id(self, entity_id: str) -> str:
        """Extract entity type from entity ID."""
        if "-" in entity_id:
            return entity_id.split("-")[0]
        return "unknown"

    def _get_entity_file_path(self, entity_id: str) -> str:
        """Generate file path for entity based on ID.

        Uses domain config plural directory (e.g., 'people' for person)
        when available, falling back to legacy 'entities/{type}/' structure.

        For domain-aware paths:
        - entity_id 'person-jordan-reyes' -> 'people/jordan-reyes.md'
        - Filename strips the entity type prefix for cleaner naming

        For legacy paths:
        - entity_id 'person-jordan-reyes' -> 'entities/person/person-jordan-reyes.md'
        - Keeps full entity_id to match existing convention
        """
        entity_type = self._get_entity_type_from_id(entity_id)

        # Try to get plural directory from domain config
        entity_schema = self._repository.get_entity_schema(entity_type)
        if entity_schema and entity_schema.plural:
            # Strip entity type prefix from filename for cleaner naming
            # e.g., 'person-jordan-reyes' -> 'jordan-reyes'
            prefix = f"{entity_type}-"
            if entity_id.startswith(prefix):
                filename = entity_id[len(prefix) :]
            else:
                filename = entity_id
            return f"{entity_schema.plural}/{filename}.md"

        # Fallback to legacy structure - keep full entity_id for compatibility
        return f"entities/{entity_type}/{entity_id}.md"

    def _get_entity_display_name(
        self, attributes: dict[str, Any], entity_id: str
    ) -> str:
        """Get display name for entity from attributes."""
        # Common name fields to check
        name_fields = ["name", "title", "canonical_name", "display_name"]

        for field in name_fields:
            if field in attributes and attributes[field]:
                return str(attributes[field])

        # Fallback to entity ID without prefix
        return entity_id.split("-", 1)[-1].replace("-", " ").title()

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
        except yaml.YAMLError as e:
            logger.warning(f"Failed to parse YAML frontmatter: {e}")
            return None, content

    async def _get_existing_entities_context(self) -> dict[str, list[str]] | None:
        """Get existing entities as context for better extraction accuracy."""
        try:
            # This could load from the repository or knowledge base
            # For now, return None to indicate no existing context
            return None
        except Exception as e:
            logger.warning(f"Failed to get existing entities context: {e}")
            return None

    def _build_transcript_extraction_prompt(
        self,
        transcript_text: str,
        entity_types: list[str],
        existing_entities: dict[str, list[str]] | None = None,
    ) -> str:
        """Build the prompt for transcript entity extraction."""
        # Load the prompt template
        prompt_template = self._load_prompt_template()

        # Build entity types context
        entity_type_list = "\n".join(f"- {entity_type}" for entity_type in entity_types)
        entity_types_context = f"<entity_types>{entity_type_list}</entity_types>"

        # Build domain context — Issue #835 (with repository fallback per PR #839 review)
        domain_cfg = self._domain_config
        if domain_cfg is None:
            from app.model_schemas.domain_config import DomainConfiguration

            repo_cfg = getattr(self._repository, "domain_config", None)
            if isinstance(repo_cfg, DomainConfiguration):
                domain_cfg = repo_cfg
        domain_context = ""
        if domain_cfg:
            from app.services.domain_prompt_context import serialize_domain_context

            domain_text = serialize_domain_context(domain_cfg)
            if domain_text:
                domain_context = f"<domain_context>\n{domain_text}\n</domain_context>\n"

        # Build existing entities context
        existing_entities_context = ""
        if existing_entities:
            context_lines = []
            for entity_type, entities in existing_entities.items():
                if entities:
                    entities_str = ", ".join(entities)
                    context_lines.append(f"{entity_type}: {entities_str}")

            if context_lines:
                existing_entities_context = f"""<existing_entities>
For reference, here are some existing entities in the knowledge base:
{chr(10).join(context_lines)}
Use this context to maintain consistency in entity extraction.
</existing_entities>"""

        # Replace placeholders in the template
        prompt_content = prompt_template.format(
            transcript_content=transcript_text,
            existing_entities_context=existing_entities_context,
            entity_types_context=entity_types_context,
            entity_type_list=entity_type_list,
        )

        # Prepend domain context to the prompt
        if domain_context:
            prompt_content = domain_context + prompt_content

        return prompt_content

    def _load_prompt_template(self) -> str:
        """Load the transcript entity extraction prompt template."""
        try:
            prompt_path = os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "..",
                "prompts",
                "transcript_entity_extract.xml",
            )
            with open(prompt_path, encoding="utf-8") as f:
                content = f.read()

            # Extract the content between <instructions> tags
            start_tag = "<instructions>"
            end_tag = "</instructions>"
            start_idx = content.find(start_tag)
            end_idx = content.find(end_tag)

            if start_idx != -1 and end_idx != -1:
                start_idx += len(start_tag)
                instructions = content[start_idx:end_idx].strip()

                # Include the transcript and context placeholders
                template = f"""<transcript>{{transcript_content}}</transcript>
{{existing_entities_context}}
{{entity_types_context}}

{instructions}"""
                return template
            else:
                logger.error("Could not find instructions tags in prompt template")
                return self._get_fallback_prompt_template()

        except Exception as e:
            logger.error(f"Failed to load prompt template: {e}")
            return self._get_fallback_prompt_template()

    def _get_fallback_prompt_template(self) -> str:
        """Get a fallback prompt template if loading fails."""
        return """<transcript>{transcript_content}</transcript>
{existing_entities_context}
{entity_types_context}

Analyze the transcript and extract all organizational entities mentioned. Focus on accuracy and avoid extracting AI products as people.

ENTITY TYPES TO EXTRACT:
{entity_type_list}

IMPORTANT RULES:
1. DO NOT extract AI product names (ChatGPT, Claude, Gemini, GPT-4, etc.) as people
2. Focus on real person names, organizations, and projects mentioned in the transcript
3. Extract speakers from the **Speaker**: format
4. Deduplicate entities - each entity should appear only once per type

Return your response as a JSON object with entity types as keys and arrays of entity names as values.
Return ONLY the JSON object, no additional text or formatting."""

    def _build_extraction_prompt(self, entity_schema: Any, entity_type: str) -> str:
        """Build extraction prompt for a specific entity type."""
        # Build domain context header — Issue #835 (with repository fallback per PR #839 review)
        domain_cfg = self._domain_config
        if domain_cfg is None:
            from app.model_schemas.domain_config import DomainConfiguration

            repo_cfg = getattr(self._repository, "domain_config", None)
            if isinstance(repo_cfg, DomainConfiguration):
                domain_cfg = repo_cfg
        domain_header = ""
        if domain_cfg:
            from app.services.domain_prompt_context import serialize_domain_context

            domain_text = serialize_domain_context(domain_cfg)
            if domain_text:
                domain_header = f"{domain_text}\n\n"

        # Build attribute descriptions
        required_attrs = []
        optional_attrs = []

        if hasattr(entity_schema, "attributes_dict") and entity_schema.attributes_dict:
            for attr_id, attr_schema in entity_schema.attributes_dict.items():
                attr_desc = f"{getattr(attr_schema, 'name', attr_id)} ({getattr(attr_schema, 'type', 'string')})"

                # Add enum values if applicable — fixed field name per PR #839 review
                enum_vals = getattr(attr_schema, "enum", None) or getattr(
                    attr_schema, "enum_values", None
                )
                if enum_vals:
                    attr_desc += f" - one of: {', '.join(enum_vals)}"

                if hasattr(attr_schema, "required") and attr_schema.required:
                    required_attrs.append(attr_desc)
                else:
                    optional_attrs.append(attr_desc)

        # Build relationship descriptions
        relationships = []
        if (
            hasattr(entity_schema, "relationships_dict")
            and entity_schema.relationships_dict
        ):
            for rel_id, rel_schema in entity_schema.relationships_dict.items():
                rel_desc = f"{getattr(rel_schema, 'name', rel_id)}"
                if hasattr(rel_schema, "target_entity"):
                    rel_desc += f" -> {rel_schema.target_entity}"
                relationships.append(rel_desc)

        # Generate prompt
        prompt = f"""{domain_header}Extract {entity_schema.name} entities from the document.

Entity Type: {entity_schema.name}
Description: {getattr(entity_schema, 'description', 'No description available')}

Required attributes to identify:
{chr(10).join(f'- {attr}' for attr in required_attrs) if required_attrs else '- None (only entity name required)'}

Optional attributes (extract if mentioned):
{chr(10).join(f'- {attr}' for attr in optional_attrs) if optional_attrs else '- None'}

Relationships to note:
{chr(10).join(f'- {rel}' for rel in relationships) if relationships else '- None'}

Return a list of {entity_type} entities found in the document, including any mentioned attributes.
Format each entity on a new line with attributes in parentheses if found.
Example: Entity Name (attribute1: value1, attribute2: value2)"""

        return prompt

    async def _generate_entity_content(
        self,
        entity_schema: Any,
        entity_type: str,
        entity_id: str,
        attributes: dict[str, Any],
        relationships: dict[str, list[str]] | None,
        references: list[dict[str, str]] | None,
    ) -> str:
        """Generate markdown content for entity file."""
        # Build frontmatter - use 'id' field to match loading code expectations
        frontmatter_dict = {
            "id": entity_id,
            "entity_type": entity_type,
            **attributes,
        }

        # Add empty relationship lists
        if (
            hasattr(entity_schema, "relationships_dict")
            and entity_schema.relationships_dict
        ):
            for rel_name in entity_schema.relationships_dict.keys():
                if relationships and rel_name in relationships:
                    frontmatter_dict[rel_name] = relationships[rel_name]
                else:
                    frontmatter_dict[rel_name] = []

        # Add references
        if references:
            frontmatter_dict["references"] = references

        # Generate content
        display_name = self._get_entity_display_name(attributes, entity_id)

        content = f"""---
{yaml.dump(frontmatter_dict, default_flow_style=False, sort_keys=False)}---

# {display_name}

## Overview

This is a {entity_schema.name} entity.

{getattr(entity_schema, 'description', '')}

## Details
"""

        # Add attribute sections
        if hasattr(entity_schema, "attributes_dict") and entity_schema.attributes_dict:
            for attr_id, attr_schema in entity_schema.attributes_dict.items():
                if attr_id in attributes and attributes[attr_id]:
                    value = attributes[attr_id]
                    attr_name = getattr(attr_schema, "name", attr_id)
                    content += f"\n### {attr_name}\n\n{value}\n"

        # Add relationship sections
        if (
            hasattr(entity_schema, "relationships_dict")
            and entity_schema.relationships_dict
        ):
            content += "\n## Relationships\n"
            for rel_name, rel_info in entity_schema.relationships_dict.items():
                rel_display_name = getattr(rel_info, "name", rel_name)
                content += f"\n### {rel_display_name}\n\n"
                if (
                    relationships
                    and rel_name in relationships
                    and relationships[rel_name]
                ):
                    for related_id in relationships[rel_name]:
                        content += f"- [[{related_id}]]\n"
                else:
                    content += f"*No {rel_display_name.lower()} linked yet.*\n"

        # Add references section
        if references:
            content += "\n## References\n\n"
            for ref in references:
                content += f"- [{ref['title']}]({ref['path']})"
                if "date" in ref:
                    content += f" ({ref['date']})"
                content += "\n"

        return content

    # Migration utilities
    async def migrate_legacy_entities(self) -> dict[str, int]:
        """
        Migrate legacy hardcoded entities to domain-aware structure.

        Returns:
            Dictionary with migration statistics
        """
        stats = {"migrated": 0, "skipped": 0, "errors": 0}

        # Define legacy type mapping
        legacy_mapping = {"people": "person", "projects": "project", "teams": "team"}

        # Check if current domain supports legacy types
        current_types = self.get_entity_types()

        for legacy_dir, legacy_type in legacy_mapping.items():
            if legacy_type not in current_types:
                logger.info(
                    f"Skipping {legacy_dir} - type '{legacy_type}' not in current domain"
                )
                continue

            # Process legacy directory
            repo_path = os.environ.get("GIT_REPO_PATH", "/app/repo")
            legacy_path = Path(repo_path) / "entities" / legacy_dir
            if legacy_path.exists():
                for file_path in legacy_path.glob("*.md"):
                    try:
                        from app.services.file_cache import file_cache

                        # Read legacy file
                        file = await file_cache.get_file(str(file_path))
                        if not file:
                            continue

                        # Extract metadata
                        metadata, content = self._extract_frontmatter_and_content(
                            file.content
                        )
                        if not metadata:
                            stats["skipped"] += 1
                            continue

                        # Create new entity with migrated data
                        entity_id = file_path.stem

                        # Map legacy attributes to new schema
                        attributes = self._map_legacy_attributes(metadata, legacy_type)

                        # Create new file
                        new_path = await self.create_entity_file(
                            entity_type=legacy_type,
                            entity_id=entity_id,
                            attributes=attributes,
                            references=metadata.get("references", []),
                        )

                        if new_path:
                            stats["migrated"] += 1
                            logger.info(f"Migrated {file_path} -> {new_path}")
                        else:
                            stats["errors"] += 1

                    except Exception as e:
                        logger.error(f"Error migrating {file_path}: {e}")
                        stats["errors"] += 1

        return stats

    def _map_legacy_attributes(
        self, metadata: dict[str, Any], entity_type: str
    ) -> dict[str, Any]:
        """Map legacy metadata to new entity schema attributes."""
        entity_schema = self._repository.get_entity_schema(entity_type)
        if not entity_schema:
            return {}

        attributes = {}

        # Map common fields
        common_mappings = {
            "name": ["name", "title", "canonical_name"],
            "description": ["description", "summary", "overview"],
            "status": ["status", "state"],
            "created": ["created", "created_at", "created_date"],
            "updated": ["updated", "updated_at", "modified"],
        }

        if hasattr(entity_schema, "attributes_dict") and entity_schema.attributes_dict:
            for attr_id, attr_schema in entity_schema.attributes_dict.items():
                # Check common mappings
                if attr_id in common_mappings:
                    for legacy_field in common_mappings[attr_id]:
                        if legacy_field in metadata:
                            attributes[attr_id] = metadata[legacy_field]
                            break

                # Direct mapping
                elif attr_id in metadata:
                    attributes[attr_id] = metadata[attr_id]

                # Set required fields to defaults if missing
                elif hasattr(attr_schema, "required") and attr_schema.required:
                    attr_type = getattr(attr_schema, "type", "string")
                    if attr_type == "string":
                        attributes[attr_id] = "Unknown"
                    elif (
                        attr_type == "enum"
                        and hasattr(attr_schema, "enum_values")
                        and attr_schema.enum_values
                    ):
                        attributes[attr_id] = attr_schema.enum_values[0]
                    elif attr_type == "boolean":
                        attributes[attr_id] = False
                    elif attr_type == "number":
                        attributes[attr_id] = 0

        return attributes
