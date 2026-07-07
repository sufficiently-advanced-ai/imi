"""
Batch upload service for processing multiple files
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fastapi import UploadFile

from app.domain.entities.services import EntityRepository, EntityService
from app.git_ops import git_ops
from app.models import EntityType, MetadataResponse
from app.services.metadata import analyze_metadata_with_entities

logger = logging.getLogger(__name__)


class BatchPhase(str, Enum):
    """Phases of batch processing"""

    VALIDATING = "validating"
    SAVING_FILES = "saving_files"
    ENTITY_EXTRACTION = "entity-extraction"
    DEDUPLICATION = "deduplication"
    PROFILE_GENERATION = "profile-generation"
    COMMITTING = "committing"
    COMPLETE = "complete"


@dataclass
class BatchStatus:
    """Status of a batch upload"""

    batch_id: str
    status: str = "pending"
    phase: BatchPhase = BatchPhase.VALIDATING
    files_completed: int = 0
    total_files: int = 0
    entities_found: dict[str, set[str]] = field(
        default_factory=lambda: {"people": set(), "projects": set(), "teams": set()}
    )
    current_file: str = ""
    is_complete: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class BatchResult:
    """Result of batch processing"""

    batch_id: str
    files_processed: int = 0
    successful_files: list[str] = field(default_factory=list)
    failed_files: list[dict[str, str]] = field(default_factory=list)
    entities_found: dict[str, list[str]] = field(default_factory=dict)
    partial_success: bool = False
    commit_sha: str = ""

    def to_user_message(self) -> str:
        """Generate user-friendly message"""
        if not self.failed_files:
            return f"Successfully processed all {len(self.successful_files)} files"
        else:
            return f"Processed {len(self.successful_files)} files. {len(self.failed_files)} failed."


class BatchUploadService:
    """Service for handling batch file uploads"""

    def __init__(self):
        self.entity_service = EntityService()
        # Import here to avoid circular imports
        from app.services.entity_deduplication import EntityDeduplicator

        self.deduplicator = EntityDeduplicator()
        self._semaphore = asyncio.Semaphore(5)  # Max 5 concurrent operations

    async def process_batch(
        self, batch_id: str, files: list[UploadFile]
    ) -> BatchResult:
        """
        Process multiple files with intelligent batching

        Args:
            batch_id: Unique identifier for this batch
            files: List of uploaded files

        Returns:
            BatchResult with processing outcome
        """
        result = BatchResult(batch_id=batch_id)

        try:
            # Phase 1: Save all files to repo
            logger.info(f"Batch {batch_id}: Saving {len(files)} files")
            saved_files = await self._save_files_to_repo(files)
            result.successful_files = saved_files

            # Phase 2: Extract entities from all files
            logger.info(f"Batch {batch_id}: Extracting entities")
            all_entities = await self._extract_entities_batch(saved_files)

            # Phase 3: Deduplicate entities
            logger.info(f"Batch {batch_id}: Deduplicating entities")
            unique_entities = self._deduplicate_entities(all_entities)

            # Convert to result format
            result.entities_found = {
                "people": [
                    e.name for e in unique_entities if e.type == EntityType.PERSON
                ],
                "projects": [
                    e.name for e in unique_entities if e.type == EntityType.PROJECT
                ],
                "teams": [e.name for e in unique_entities if e.type == EntityType.TEAM],
            }

            # Phase 4: Generate/update profiles
            logger.info(f"Batch {batch_id}: Updating entity profiles")
            await self._update_entity_profiles(unique_entities)

            # Phase 5: Single git commit
            logger.info(f"Batch {batch_id}: Committing changes")
            commit_sha = await self._commit_batch_changes(batch_id, saved_files)
            result.commit_sha = commit_sha

            result.files_processed = len(saved_files)

        except Exception as e:
            logger.exception(f"Batch {batch_id} processing error: {str(e)}")
            result.partial_success = True
            raise

        return result

    async def _save_files_to_repo(self, files: list[UploadFile]) -> list[str]:
        """Save files to repository and return paths"""
        saved_paths = []

        for file in files:
            try:
                # Files should already be saved by the upload endpoint
                # This is a placeholder for the actual implementation
                saved_paths.append(file.filename)
            except Exception as e:
                logger.error(f"Failed to save {file.filename}: {str(e)}")

        return saved_paths

    async def _extract_entities_batch(self, files: list[str]) -> list[Any]:
        """Extract entities from multiple files concurrently"""
        entities = []

        async def extract_with_limit(file_path: str):
            async with self._semaphore:
                try:
                    result = await self.entity_service.extract_entities(file_path)
                    return result
                except Exception as e:
                    logger.error(f"Entity extraction failed for {file_path}: {str(e)}")
                    return []

        # Process files concurrently
        tasks = [extract_with_limit(f) for f in files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Flatten results
        for result in results:
            if not isinstance(result, Exception) and result:
                if isinstance(result, list):
                    entities.extend(result)
                elif isinstance(result, dict):
                    # Convert dict format to entity objects
                    for entity_type, names in result.items():
                        if isinstance(names, list):
                            for name in names:
                                entities.append(
                                    Entity(
                                        name=name,
                                        type=EntityType[entity_type.upper()]
                                        if entity_type.upper() in EntityType.__members__
                                        else EntityType.PERSON,
                                    )
                                )

        return entities

    def _deduplicate_entities(self, entities: list[Any]) -> list[Any]:
        """Deduplicate entities intelligently"""
        return self.deduplicator.deduplicate(entities)

    async def _update_entity_profiles(self, entities: list[Any]):
        """Update or create profiles for entities"""
        # Group entities by type
        people = [e for e in entities if e.type == EntityType.PERSON]
        projects = [e for e in entities if e.type == EntityType.PROJECT]
        teams = [e for e in entities if e.type == EntityType.TEAM]

        # Update profiles
        if people:
            for person in people:
                entity_id = self.entity_service.normalize_entity_id(
                    "person", person.name
                )
                await self.entity_service.create_entity_file(
                    entity_type="person",
                    entity_id=entity_id,
                    attributes={"name": person.name, "email": person.email or None},
                )
                # Optionally update profile metadata
                # await self.entity_service.update_entity_profile(entity_id, {"last_seen_in_batch": True})

        # Handle projects and teams similarly when implemented
        # TODO: Implement project and team profile handling
        if projects:
            pass  # Project handling not yet implemented
        if teams:
            pass  # Team handling not yet implemented

    async def _commit_batch_changes(
        self, batch_id: str, uploaded_files: list[str]
    ) -> str:
        """Commit all changes in a single git commit"""
        # Get all modified files
        modified_files = uploaded_files.copy()

        # Add entity profile files
        entity_files = self._get_modified_entity_files()
        modified_files.extend(entity_files)

        # Create commit message
        commit_message = f"Batch upload ({batch_id}): Added {len(uploaded_files)} files"

        # Commit and push
        try:
            result = await git_ops.commit_and_push(modified_files, commit_message)
            return result
        except Exception as e:
            logger.error(f"Git commit failed for batch {batch_id}: {str(e)}")
            raise

    def _get_modified_entity_files(self) -> list[str]:
        """Get list of modified entity profile files"""
        # This would check for modified person/project/team files
        # For now, return empty list
        return []


# Entity class for deduplication
@dataclass
class Entity:
    """Entity representation for deduplication"""

    name: str
    type: EntityType
    email: str = ""
    context: dict[str, Any] = field(default_factory=dict)


async def process_batch_with_entities(
    file_paths: list[str], entity_repository: EntityRepository
) -> list[MetadataResponse]:
    """Process batch of files with entity-aware metadata generation - Issue #58

    Args:
        file_paths: List of file paths to process
        entity_repository: Entity repository for validation

    Returns:
        List of metadata responses
    """
    results = []

    for path in file_paths:
        try:
            # Generate entity-aware metadata
            result = await analyze_metadata_with_entities(path)
            results.append(result)

            # Extract any new entities found
            if hasattr(result.metadata, "entity_extractions"):
                for extraction in result.metadata.entity_extractions:
                    # Register unknown entities with lower confidence
                    if extraction.confidence == 0.0 and extraction.canonical_id is None:
                        if extraction.entity_type == EntityType.PERSON:
                            await entity_repository.create_entity(
                                "person",
                                {"name": extraction.raw_text, "confidence": 0.5},
                            )
                        elif extraction.entity_type == EntityType.PROJECT:
                            await entity_repository.create_entity(
                                "project",
                                {"name": extraction.raw_text, "confidence": 0.5},
                            )
                        elif extraction.entity_type == EntityType.TEAM:
                            await entity_repository.create_entity(
                                "team", {"name": extraction.raw_text, "confidence": 0.5}
                            )
        except Exception as e:
            logger.error(f"Failed to process {path} with entity awareness: {str(e)}")
            # Continue with other files

    return results
