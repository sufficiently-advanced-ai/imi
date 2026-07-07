"""
Entity File Service for unified entity storage.

This service handles all entity file operations including parsing markdown files
with YAML frontmatter, saving entities to files, and integrating with git_ops
for automatic commits. It serves as the single source of truth for entity data,
ensuring consistency between the entities interface and domain graph interface.
"""

import logging
import os
import re
from datetime import datetime
from typing import Any

import yaml

from app.core.dependencies import get_entity_repository
from app.git_ops import git_ops
from app.model_schemas.domain_config import DomainConfiguration

logger = logging.getLogger(__name__)

# Module-level cache for entities
# This cache is cleared when files are modified
_entity_cache: dict[str, dict[str, dict[str, Any]]] = {}


def clear_entity_cache(entity_type: str | None = None, entity_id: str | None = None):
    """Clear the entity cache, optionally for specific entity type or ID."""
    global _entity_cache

    if entity_type and entity_id:
        # Clear specific entity
        if entity_type in _entity_cache and entity_id in _entity_cache[entity_type]:
            del _entity_cache[entity_type][entity_id]
            logger.debug(f"Cleared cache for entity {entity_type}/{entity_id}")
    elif entity_type:
        # Clear all entities of a type
        if entity_type in _entity_cache:
            del _entity_cache[entity_type]
            logger.debug(f"Cleared cache for entity type {entity_type}")
    else:
        # Clear entire cache
        _entity_cache.clear()
        logger.info("Entity cache cleared")


class EntityFileService:
    """Service for managing entity files as the single source of truth."""

    def __init__(self, domain_config: DomainConfiguration | None = None):
        """
        Initialize the entity file service.

        Args:
            domain_config: Domain configuration for entity validation
        """
        self.domain_config = domain_config
        self.entity_registry = get_entity_repository()
        self.git_ops = git_ops

        # Register domain if provided
        if domain_config:
            self.entity_registry.load_domain_config(domain_config)

    def _get_entity_directory(self, entity_type: str) -> str:
        """
        Get the directory path for a given entity type.

        Checks multiple locations in priority order:
        1. Domain config plural form (e.g., "people" for person)
        2. Legacy entities/{type} structure (e.g., "entities/person")
        3. Default pluralization

        Args:
            entity_type: The entity type (e.g., 'person', 'project')

        Returns:
            Directory path relative to repo root
        """
        # Map entity types to directory names
        # Use plural form if available in domain config
        if self.domain_config and entity_type in self.domain_config.entities:
            entity_config = self.domain_config.entities[entity_type]
            primary_dir = entity_config.plural or f"{entity_type}s"

            # Check if primary directory exists and has files
            primary_path = os.path.join(self.git_ops.repo_path, primary_dir)
            if os.path.isdir(primary_path) and any(
                f.endswith('.md') for f in os.listdir(primary_path) if os.path.isfile(os.path.join(primary_path, f))
            ):
                return primary_dir

            # Fallback to legacy entities/{type} structure
            legacy_dir = f"entities/{entity_type}"
            legacy_path = os.path.join(self.git_ops.repo_path, legacy_dir)
            if os.path.isdir(legacy_path) and any(
                f.endswith('.md') for f in os.listdir(legacy_path) if os.path.isfile(os.path.join(legacy_path, f))
            ):
                logger.debug(f"Using legacy directory for {entity_type}: {legacy_dir}")
                return legacy_dir

            # Return primary even if empty (will be created)
            return primary_dir

        # Default pluralization
        type_to_dir = {
            "person": "people",
            "project": "projects",
            "team": "teams",
            "account": "accounts",
            "contact": "contacts",
            "company": "companies",
            "opportunity": "opportunities",
            "engagement": "engagements",
        }

        primary_dir = type_to_dir.get(entity_type, f"{entity_type}s")

        # Check if primary directory exists and has files
        primary_path = os.path.join(self.git_ops.repo_path, primary_dir)
        if os.path.isdir(primary_path) and any(
            f.endswith('.md') for f in os.listdir(primary_path) if os.path.isfile(os.path.join(primary_path, f))
        ):
            return primary_dir

        # Fallback to legacy entities/{type} structure
        legacy_dir = f"entities/{entity_type}"
        legacy_path = os.path.join(self.git_ops.repo_path, legacy_dir)
        if os.path.isdir(legacy_path) and any(
            f.endswith('.md') for f in os.listdir(legacy_path) if os.path.isfile(os.path.join(legacy_path, f))
        ):
            logger.debug(f"Using legacy directory for {entity_type}: {legacy_dir}")
            return legacy_dir

        return primary_dir

    def get_entity_path(self, entity_type: str, entity_id: str) -> str:
        """Return the relative file path for a given entity type and ID.

        This is the public API for resolving entity file paths — use this
        instead of accessing ``_get_entity_directory`` directly.

        Returns:
            Relative path like ``people/jane-doe.md``
        """
        dir_name = self._get_entity_directory(entity_type)
        filename = entity_id.replace(f"{entity_type}-", "") + ".md"
        return f"{dir_name}/{filename}"

    def _generate_entity_id(self, entity_type: str, attributes: dict[str, Any]) -> str:
        """
        Generate a consistent entity ID.

        Args:
            entity_type: The entity type
            attributes: Entity attributes

        Returns:
            Generated entity ID
        """
        # Use name attribute if available
        name = attributes.get("name", "")
        if name:
            # Convert to lowercase, replace spaces with hyphens
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            return f"{entity_type}-{slug}"

        # Fallback to timestamp-based ID
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return f"{entity_type}-{timestamp}"

    def _parse_markdown_file(self, file_path: str) -> tuple[dict[str, Any | None, str]]:
        """
        Parse a markdown file with YAML frontmatter.

        Args:
            file_path: Full path to the markdown file

        Returns:
            Tuple of (metadata dict, content string) or None if error
        """
        try:
            if not os.path.exists(file_path):
                return None

            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            # Look for YAML frontmatter
            if "---" in content:
                lines = content.split("\n")
                start_idx = None
                end_idx = None

                for i, line in enumerate(lines):
                    if line.strip() == "---":
                        if start_idx is None:
                            start_idx = i
                        elif end_idx is None:
                            end_idx = i
                            break

                if start_idx is not None and end_idx is not None:
                    # Extract YAML content
                    yaml_lines = lines[start_idx + 1 : end_idx]
                    yaml_content = "\n".join(yaml_lines)

                    try:
                        metadata = yaml.safe_load(yaml_content) or {}
                        body_content = "\n".join(
                            lines[:start_idx] + lines[end_idx + 1 :]
                        ).strip()
                        return metadata, body_content
                    except yaml.YAMLError as e:
                        logger.error(f"YAML parse error in {file_path}: {e}")

            # No frontmatter found, return empty metadata
            return {}, content

        except Exception as e:
            logger.error(f"Error parsing markdown file {file_path}: {e}")
            return None

    def _extract_relationships_from_metadata(
        self, metadata: dict[str, Any], entity_type: str
    ) -> tuple[dict[str, Any], set]:
        """
        Extract relationship fields from metadata based on domain schema.

        Args:
            metadata: Entity metadata containing attributes and possibly relationships
            entity_type: The type of entity

        Returns:
            Tuple of (relationships dict, set of extracted field names)
        """
        relationships = {}
        extracted_fields = set()

        # Check if we have domain config with entity schema
        if not self.domain_config or entity_type not in self.domain_config.entities:
            return relationships, extracted_fields

        entity_config = self.domain_config.entities[entity_type]

        # Check each relationship type defined for this entity
        for relationship in entity_config.relationships:
            relationship_type = relationship.type
            target_entity_type = relationship.target

            # Look for this relationship in various field patterns
            # These patterns match what the domain graph service looks for
            possible_fields = [
                relationship_type,  # e.g., "belongs_to_account"
                f"{target_entity_type}_id",  # e.g., "account_id"
                f"{target_entity_type}_ids",  # e.g., "account_ids"
                f"{relationship_type}_id",  # e.g., "belongs_to_account_id"
                f"{relationship_type}_ids",  # e.g., "belongs_to_account_ids"
            ]

            # Check if any of these fields exist in metadata
            for field in possible_fields:
                if field in metadata:
                    value = metadata[field]

                    # Store the relationship value under the relationship type name
                    # This matches what the RelationshipManager expects
                    if value is not None:
                        relationships[relationship_type] = value
                        extracted_fields.add(field)  # Track which field was extracted
                        break  # Found this relationship, move to next

        return relationships, extracted_fields

    async def _build_reverse_relationships(
        self, entities: dict[str, dict[str, Any]], entity_type: str
    ) -> None:
        """
        Build reverse relationships efficiently using indexed lookups.

        This method builds a relationship index for O(1) lookups instead of O(n²) scanning.
        For example, if projects have 'belongs_to_account', this will populate
        'has_projects' on the account.

        TODO: Performance optimization opportunities:
        1. Lazy loading - only build reverse relationships when accessed
        2. Batch processing - process relationships in chunks for large datasets
        3. Caching - cache computed relationships to avoid recomputation
        4. Circular dependency detection - prevent infinite loops with circular relationships

        Args:
            entities: Dictionary of entities to update with reverse relationships
            entity_type: The type of entities being processed
        """
        if not self.domain_config or entity_type not in self.domain_config.entities:
            return

        entity_config = self.domain_config.entities[entity_type]

        # Build index of inverse relationships for efficient lookup
        inverse_relationship_map = self._build_inverse_relationship_map(entity_type)

        # Process each relationship defined for this entity type
        for relationship in entity_config.relationships:
            relationship_type = relationship.type
            target_entity_type = relationship.target
            cardinality = relationship.cardinality

            # Determine if this is a "has" relationship (reverse lookup needed)
            is_reverse_relationship = relationship_type.startswith(
                "has_"
            ) or cardinality in ["one_to_many", "one-to-many"]

            if (
                is_reverse_relationship
                and relationship_type in inverse_relationship_map
            ):
                inverse_rel_info = inverse_relationship_map[relationship_type]

                # Use cached relationship index if available
                await self._apply_reverse_relationships_from_index(
                    entities,
                    entity_type,
                    relationship_type,
                    target_entity_type,
                    inverse_rel_info["inverse_name"],
                    cardinality,
                )

    def _build_inverse_relationship_map(
        self, entity_type: str
    ) -> dict[str, dict[str, Any]]:
        """
        Build a map of inverse relationships for efficient lookup.

        Args:
            entity_type: The entity type to build the map for

        Returns:
            Map of relationship_type to inverse relationship info
        """
        inverse_map = {}

        if not self.domain_config or entity_type not in self.domain_config.entities:
            return inverse_map

        entity_config = self.domain_config.entities[entity_type]

        for relationship in entity_config.relationships:
            target_type = relationship.target

            # Find inverse relationship in target entity config
            if target_type in self.domain_config.entities:
                target_config = self.domain_config.entities[target_type]

                inverse_rel = None

                # Prefer the explicitly declared inverse. When two relationships
                # connect the same pair of entity types (e.g. account
                # has_contacts/managed_by both target person), a first-match scan
                # is ambiguous and can pick the wrong reverse name. The declared
                # inverse_name disambiguates deterministically.
                if relationship.inverse_name:
                    inverse_rel = target_config.relationships_dict.get(
                        relationship.inverse_name
                    )

                # Fall back to the first relationship pointing back to this
                # entity type when no inverse_name is declared (legacy configs).
                if inverse_rel is None:
                    for target_rel in target_config.relationships:
                        if target_rel.target == entity_type:
                            inverse_rel = target_rel
                            break

                if inverse_rel is not None:
                    inverse_map[relationship.type] = {
                        "inverse_name": inverse_rel.type,
                        "target_type": target_type,
                    }

        return inverse_map

    async def _apply_reverse_relationships_from_index(
        self,
        entities: dict[str, dict[str, Any]],
        entity_type: str,
        relationship_type: str,
        target_entity_type: str,
        inverse_relationship: str,
        cardinality: str,
    ) -> None:
        """
        Apply reverse relationships using an efficient indexed approach.

        Args:
            entities: Entities to update
            entity_type: Source entity type
            relationship_type: Relationship type name
            target_entity_type: Target entity type
            inverse_relationship: Name of the inverse relationship
            cardinality: Relationship cardinality
        """
        # Build relationship index for O(1) lookups
        relationship_index = {}

        # First pass: build index of all target entities and their relationships
        target_entities = await self._load_entities_of_type(
            target_entity_type, skip_reverse=True
        )

        for target_id, target_entity in target_entities.items():
            target_relationships = target_entity.get("relationships", {})

            if inverse_relationship in target_relationships:
                referenced_id = target_relationships[inverse_relationship]

                # Handle both single and multiple references
                referenced_ids = (
                    referenced_id
                    if isinstance(referenced_id, list)
                    else [referenced_id]
                    if referenced_id
                    else []
                )

                for ref_id in referenced_ids:
                    if ref_id not in relationship_index:
                        relationship_index[ref_id] = []
                    relationship_index[ref_id].append(target_id)

        # Second pass: apply relationships using the index (O(1) lookup per entity)
        for entity_id in entities:
            if entity_id in relationship_index:
                if "relationships" not in entities[entity_id]:
                    entities[entity_id]["relationships"] = {}

                # Set the relationship value based on cardinality
                related_ids = relationship_index[entity_id]

                if cardinality in [
                    "one_to_many",
                    "one-to-many",
                    "many_to_many",
                    "many-to-many",
                ]:
                    entities[entity_id]["relationships"][relationship_type] = (
                        related_ids
                    )
                else:
                    # For one-to-one, use the first (should be only) related entity
                    entities[entity_id]["relationships"][relationship_type] = (
                        related_ids[0] if related_ids else None
                    )

    def _format_markdown_file(self, metadata: dict[str, Any], content: str) -> str:
        """
        Format entity data as markdown with YAML frontmatter.

        Args:
            metadata: Entity metadata/attributes
            content: Markdown content body

        Returns:
            Formatted markdown string
        """
        # Ensure required metadata fields
        if "created_at" not in metadata:
            metadata["created_at"] = datetime.utcnow().isoformat()
        metadata["updated_at"] = datetime.utcnow().isoformat()

        # Format YAML frontmatter
        yaml_str = yaml.dump(metadata, default_flow_style=False, sort_keys=False)

        # Combine with content
        return f"---\n{yaml_str}---\n\n{content}"

    async def load_all_entities(
        self, entity_type: str | None = None
    ) -> dict[str, dict[str, Any]]:
        """
        Load all entities from files, optionally filtered by type.

        Args:
            entity_type: Optional entity type to filter by

        Returns:
            Dictionary mapping entity IDs to entity data
        """
        entities = {}

        # Determine which entity types to load
        if entity_type:
            entity_types = [entity_type]
        elif self.domain_config:
            entity_types = list(self.domain_config.entities.keys())
        elif self.entity_registry and self.entity_registry.domain_config:
            # Get domain config from entity registry (may have been loaded at startup)
            entity_types = list(self.entity_registry.domain_config.entities.keys())
            logger.debug(f"Using entity types from entity_registry: {entity_types}")
        else:
            # Default entity types - only used if no domain is loaded anywhere
            entity_types = ["person", "project", "team", "account", "contact"]
            logger.warning("No domain config available, using default entity types")

        for etype in entity_types:
            # Check cache first
            if etype in _entity_cache:
                entities.update(_entity_cache[etype])
                continue

            # Load from files
            type_entities = await self._load_entities_of_type(etype)
            entities.update(type_entities)

            # Cache the loaded entities
            _entity_cache[etype] = type_entities

        return entities

    async def _load_entities_of_type(
        self, entity_type: str, skip_reverse: bool = False
    ) -> dict[str, dict[str, Any]]:
        """
        Load all entities of a specific type from files.

        Loads from both the primary directory (domain config plural) and the
        legacy entities/{type} directory, merging results.

        Args:
            entity_type: The entity type to load
            skip_reverse: Skip building reverse relationships (to avoid infinite recursion)

        Returns:
            Dictionary mapping entity IDs to entity data
        """
        entities = {}

        # Get primary directory for this entity type
        dir_name = self._get_entity_directory(entity_type)

        # Build list of directories to check (primary + legacy)
        directories_to_check = [dir_name]

        # Add legacy directory if different from primary
        legacy_dir = f"entities/{entity_type}"
        if legacy_dir != dir_name:
            directories_to_check.append(legacy_dir)

        # Load from all directories
        for current_dir_name in directories_to_check:
            dir_path = os.path.join(self.git_ops.repo_path, current_dir_name)

            # Create primary directory if it doesn't exist (only for first/primary dir)
            if current_dir_name == dir_name:
                os.makedirs(dir_path, exist_ok=True)
            elif not os.path.isdir(dir_path):
                # Skip legacy directories that don't exist
                continue

            # Load all markdown files in the directory.
            try:
                dir_filenames = sorted(os.listdir(dir_path))
            except Exception as e:
                logger.error(
                    f"Error listing entities of type {entity_type} from {current_dir_name}: {e}"
                )
                dir_filenames = []

            for filename in dir_filenames:
                if not filename.endswith(".md"):
                    continue

                # Isolate parsing per file: a single malformed record (e.g. a
                # file whose frontmatter ``relationships`` is a bare list rather
                # than a {type: [ids]} mapping) must not abort the whole
                # directory load and silently drop every file after it.
                try:
                    file_path = os.path.join(dir_path, filename)
                    result = self._parse_markdown_file(file_path)

                    if not result:
                        continue

                    metadata, content = result

                    # Extract entity ID
                    entity_id = metadata.get("id")
                    if not entity_id:
                        # Generate from filename
                        entity_id = f"{entity_type}-{filename.replace('.md', '')}"

                    # Skip if we already have this entity (primary takes precedence)
                    if entity_id in entities:
                        continue

                    # Ensure entity_type is in metadata
                    if "entity_type" not in metadata:
                        metadata["entity_type"] = entity_type

                    # Build entity structure
                    entity = {
                        "id": entity_id,
                        "entity_type": entity_type,
                        "attributes": {},
                        "relationships": {},
                        "created_at": metadata.get(
                            "created_at", datetime.utcnow().isoformat()
                        ),
                        "updated_at": metadata.get(
                            "updated_at", datetime.utcnow().isoformat()
                        ),
                        "is_archived": metadata.get("is_archived", False),
                        "file_path": os.path.join(current_dir_name, filename),
                    }

                    # Extract relationships from metadata based on domain schema
                    extracted_relationships, extracted_fields = (
                        self._extract_relationships_from_metadata(metadata, entity_type)
                    )

                    # Separate attributes from metadata
                    skip_keys = {
                        "id",
                        "entity_type",
                        "created_at",
                        "updated_at",
                        "is_archived",
                        "relationships",
                    }
                    # Also skip any fields that were identified as relationships
                    skip_keys.update(extracted_fields)

                    for key, value in metadata.items():
                        if key not in skip_keys:
                            entity["attributes"][key] = value

                    # Handle relationships - merge extracted and explicit. Only a
                    # mapping can be spread/merged; some legacy or AI-authored
                    # files store ``relationships`` as a list of bare target IDs,
                    # which would raise "'list' object is not a mapping". When it
                    # isn't a dict, fall back to the schema-extracted relationships.
                    explicit_relationships = metadata.get("relationships")
                    if isinstance(explicit_relationships, dict):
                        # Explicit relationships take precedence
                        entity["relationships"] = {
                            **extracted_relationships,
                            **explicit_relationships,
                        }
                    else:
                        if explicit_relationships is not None:
                            logger.warning(
                                "Ignoring non-mapping 'relationships' (%s) in %s/%s; "
                                "using schema-extracted relationships instead.",
                                type(explicit_relationships).__name__,
                                current_dir_name,
                                filename,
                            )
                        entity["relationships"] = extracted_relationships

                    # Add content if present
                    if content:
                        entity["content"] = content

                    entities[entity_id] = entity

                except Exception as e:
                    logger.error(
                        f"Error loading entity file {current_dir_name}/{filename} "
                        f"(type {entity_type}): {e}"
                    )
                    continue

        # Build reverse relationships unless skipped
        if not skip_reverse:
            await self._build_reverse_relationships(entities, entity_type)

        return entities

    async def get_entity(self, entity_id: str) -> dict[str, Any | None]:
        """
        Get a single entity by ID.

        Args:
            entity_id: The entity ID

        Returns:
            Entity data or None if not found
        """
        # Try to determine entity type from ID
        if "-" in entity_id:
            entity_type = entity_id.split("-")[0]

            # Check cache first
            if entity_type in _entity_cache and entity_id in _entity_cache[entity_type]:
                return _entity_cache[entity_type][entity_id]

            # Load entities of this type
            type_entities = await self._load_entities_of_type(entity_type)
            if entity_id in type_entities:
                # Update cache
                if entity_type not in _entity_cache:
                    _entity_cache[entity_type] = {}
                _entity_cache[entity_type][entity_id] = type_entities[entity_id]
                return type_entities[entity_id]

        # Fallback: search all entity types
        all_entities = await self.load_all_entities()
        return all_entities.get(entity_id)

    async def save_entity(
        self, entity: dict[str, Any], commit_message: str | None = None
    ) -> bool:
        """
        Save an entity to a markdown file.

        Args:
            entity: Entity data to save
            commit_message: Optional git commit message

        Returns:
            True if successful, False otherwise
        """
        try:
            entity_id = entity["id"]
            entity_type = entity["entity_type"]

            # Get file path
            dir_name = self._get_entity_directory(entity_type)
            dir_path = os.path.join(self.git_ops.repo_path, dir_name)
            os.makedirs(dir_path, exist_ok=True)

            # Generate filename from entity ID
            filename = entity_id.replace(f"{entity_type}-", "") + ".md"
            file_path = os.path.join(dir_path, filename)
            relative_path = os.path.join(dir_name, filename)

            # Prepare metadata
            metadata = {
                "id": entity_id,
                "entity_type": entity_type,
                "created_at": entity.get("created_at", datetime.utcnow().isoformat()),
                "updated_at": datetime.utcnow().isoformat(),
                "is_archived": entity.get("is_archived", False),
            }

            # Add attributes to metadata
            if "attributes" in entity:
                metadata.update(entity["attributes"])

            # Add relationships if present
            if "relationships" in entity and entity["relationships"]:
                metadata["relationships"] = entity["relationships"]

            # Get content
            content = entity.get("content", "")
            if not content and "attributes" in entity:
                # Generate basic content from attributes
                name = entity["attributes"].get("name", entity_id)
                content = f"# {name}\n\nEntity ID: {entity_id}\n"

            # Format and write file
            markdown_content = self._format_markdown_file(metadata, content)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            # Clear cache for this entity
            clear_entity_cache(entity_type, entity_id)

            # Commit to git if requested
            # TODO: Transaction safety issue - file is written before git commit
            # If git commit fails, filesystem and git repo will be inconsistent
            # Consider implementing atomic operations:
            # 1. Write to temp file first
            # 2. Attempt git add/commit on temp file
            # 3. Only move to final location if git succeeds
            # 4. Or implement rollback mechanism to delete file on git failure
            if commit_message:
                try:
                    await self.git_ops.commit_and_push([relative_path], commit_message)
                    logger.info(f"Committed entity {entity_id} to git")
                except Exception as e:
                    logger.error(f"Failed to commit entity {entity_id}: {e}")
                    # Don't fail the save operation if commit fails
                    # WARNING: This leaves filesystem and git out of sync

            logger.info(f"Saved entity {entity_id} to {relative_path}")
            return True

        except Exception as e:
            logger.error(f"Error saving entity: {e}")
            return False

    async def delete_entity(
        self,
        entity_id: str,
        soft_delete: bool = True,
        commit_message: str | None = None,
    ) -> bool:
        """
        Delete an entity (soft or hard delete).

        Args:
            entity_id: The entity ID to delete
            soft_delete: If True, mark as archived; if False, delete file
            commit_message: Optional git commit message

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get the entity
            entity = await self.get_entity(entity_id)
            if not entity:
                logger.warning(f"Entity {entity_id} not found for deletion")
                return False

            if soft_delete:
                # Soft delete: mark as archived
                entity["is_archived"] = True
                entity["deleted_at"] = datetime.utcnow().isoformat()
                return await self.save_entity(entity, commit_message)
            else:
                # Hard delete: remove file
                file_path = entity.get("file_path")
                if file_path:
                    full_path = os.path.join(self.git_ops.repo_path, file_path)
                    if os.path.exists(full_path):
                        os.remove(full_path)

                        # Clear cache
                        entity_type = entity["entity_type"]
                        clear_entity_cache(entity_type, entity_id)

                        # Commit deletion
                        if commit_message:
                            try:
                                await self.git_ops.commit_and_push(
                                    [file_path], commit_message
                                )
                            except Exception as e:
                                logger.error(
                                    f"Failed to commit deletion of {entity_id}: {e}"
                                )

                        logger.info(f"Deleted entity file {file_path}")
                        return True

                logger.warning(f"Entity {entity_id} file not found")
                return False

        except Exception as e:
            logger.error(f"Error deleting entity {entity_id}: {e}")
            return False

    async def list_entities(
        self,
        entity_type: str | None = None,
        include_archived: bool = False,
        filters: dict[str, Any | None] = None,
    ) -> list[dict[str, Any]]:
        """
        List entities with optional filtering.

        Args:
            entity_type: Optional entity type filter
            include_archived: Whether to include archived entities
            filters: Additional filters to apply

        Returns:
            List of entities matching the criteria
        """
        # Load all entities
        all_entities = await self.load_all_entities(entity_type)

        # Convert to list and apply filters
        entity_list = []
        for _entity_id, entity in all_entities.items():
            # Skip archived if not requested
            if not include_archived and entity.get("is_archived", False):
                continue

            # Apply additional filters
            if filters:
                match = True
                for key, value in filters.items():
                    # Check in attributes
                    if key in entity.get("attributes", {}):
                        if entity["attributes"][key] != value:
                            match = False
                            break
                    # Check in top-level fields
                    elif key in entity:
                        if entity[key] != value:
                            match = False
                            break

                if not match:
                    continue

            entity_list.append(entity)

        return entity_list
