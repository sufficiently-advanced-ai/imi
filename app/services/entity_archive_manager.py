"""
Entity Archive Manager - Handles entity archiving, restoration, and merge rollback.

This service manages archived entities and stores merge history for rollback capability.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..git_ops import git_ops
from ..models import CanonicalEntity, EntityType

logger = logging.getLogger(__name__)


class EntityArchiveManager:
    """Manage entity archiving, restoration, and merge history."""

    def __init__(self, registry=None):
        self.registry = registry
        self.archive_path = os.path.join(git_ops.repo_path, ".entity_archive")
        self.merge_history_path = os.path.join(git_ops.repo_path, ".merge_history")

        # Create directories if they don't exist
        os.makedirs(self.archive_path, exist_ok=True)
        os.makedirs(self.merge_history_path, exist_ok=True)

    async def archive_entity(
        self,
        entity_id: str,
        entity: CanonicalEntity,
        archive_reason: str,
        archived_by: str = "system",
    ) -> dict[str, Any]:
        """Archive an entity by moving it to archived storage."""

        archive_data = {
            "entity": entity.model_dump(),
            "archived_at": datetime.utcnow().isoformat(),
            "archive_reason": archive_reason,
            "archived_by": archived_by,
            "original_id": entity_id,
        }

        # Save to archive file
        archive_file = os.path.join(self.archive_path, f"{entity_id}.json")
        with open(archive_file, "w") as f:
            json.dump(archive_data, f, indent=2)

        # Remove from active registry
        if self.registry:
            entity_type = self._get_entity_type(entity_id)
            if entity_type == EntityType.PERSON:
                self.registry.people.pop(entity_id, None)
            elif entity_type == EntityType.PROJECT:
                self.registry.projects.pop(entity_id, None)
            elif entity_type == EntityType.TEAM:
                self.registry.teams.pop(entity_id, None)

            self.registry.save()

        logger.info(f"Archived entity {entity_id}: {archive_reason}")

        return {
            "success": True,
            "archive_file": archive_file,
            "archived_at": archive_data["archived_at"],
        }

    async def restore_entity(self, entity_id: str) -> CanonicalEntity | None:
        """Restore an archived entity back to active registry."""

        archive_file = os.path.join(self.archive_path, f"{entity_id}.json")

        if not os.path.exists(archive_file):
            logger.error(f"Archive file not found for entity {entity_id}")
            return None

        # Load archived data
        with open(archive_file) as f:
            archive_data = json.load(f)

        entity_data = archive_data["entity"]
        entity_type = EntityType(entity_data["entity_type"])

        # Recreate entity object
        if entity_type == EntityType.PERSON:
            from ..models import CanonicalPerson

            entity = CanonicalPerson(**entity_data)
        elif entity_type == EntityType.PROJECT:
            from ..models import CanonicalProject

            entity = CanonicalProject(**entity_data)
        elif entity_type == EntityType.TEAM:
            from ..models import CanonicalTeam

            entity = CanonicalTeam(**entity_data)
        else:
            logger.error(f"Unknown entity type: {entity_type}")
            return None

        # Add back to registry
        if self.registry:
            if entity_type == EntityType.PERSON:
                self.registry.people[entity_id] = entity
            elif entity_type == EntityType.PROJECT:
                self.registry.projects[entity_id] = entity
            elif entity_type == EntityType.TEAM:
                self.registry.teams[entity_id] = entity

            self.registry.save()

        # Remove archive file
        os.remove(archive_file)

        logger.info(f"Restored entity {entity_id} from archive")

        return entity

    async def store_merge_history(
        self,
        source_entity_id: str,
        target_entity_id: str,
        source_entity: CanonicalEntity,
        target_entity_before: CanonicalEntity,
        merged_entity: CanonicalEntity,
        merge_strategy: str,
    ) -> str:
        """Store merge history for potential rollback."""

        rollback_token = str(uuid4())

        merge_record = {
            "rollback_token": rollback_token,
            "merge_timestamp": datetime.utcnow().isoformat(),
            "source_entity_id": source_entity_id,
            "target_entity_id": target_entity_id,
            "merge_strategy": merge_strategy,
            "entities": {
                "source": source_entity.model_dump(),
                "target_before": target_entity_before.model_dump(),
                "merged": merged_entity.model_dump(),
            },
        }

        # Save merge history
        history_file = os.path.join(self.merge_history_path, f"{rollback_token}.json")
        with open(history_file, "w") as f:
            json.dump(merge_record, f, indent=2)

        logger.info(f"Stored merge history with token {rollback_token}")

        return rollback_token

    async def rollback_merge(self, rollback_token: str) -> dict[str, Any]:
        """Rollback a merge operation using the stored history."""

        history_file = os.path.join(self.merge_history_path, f"{rollback_token}.json")

        if not os.path.exists(history_file):
            return {"success": False, "error": "Rollback token not found or expired"}

        # Load merge history
        with open(history_file) as f:
            merge_record = json.load(f)

        source_data = merge_record["entities"]["source"]
        target_before_data = merge_record["entities"]["target_before"]

        # Recreate entities
        entity_type = EntityType(source_data["entity_type"])

        if entity_type == EntityType.PERSON:
            from ..models import CanonicalPerson

            source_entity = CanonicalPerson(**source_data)
            target_entity = CanonicalPerson(**target_before_data)
        elif entity_type == EntityType.PROJECT:
            from ..models import CanonicalProject

            source_entity = CanonicalProject(**source_data)
            target_entity = CanonicalProject(**target_before_data)
        elif entity_type == EntityType.TEAM:
            from ..models import CanonicalTeam

            source_entity = CanonicalTeam(**source_data)
            target_entity = CanonicalTeam(**target_before_data)
        else:
            return {"success": False, "error": f"Unknown entity type: {entity_type}"}

        # Restore entities to registry
        if self.registry:
            source_id = merge_record["source_entity_id"]
            target_id = merge_record["target_entity_id"]

            if entity_type == EntityType.PERSON:
                self.registry.people[source_id] = source_entity
                self.registry.people[target_id] = target_entity
            elif entity_type == EntityType.PROJECT:
                self.registry.projects[source_id] = source_entity
                self.registry.projects[target_id] = target_entity
            elif entity_type == EntityType.TEAM:
                self.registry.teams[source_id] = source_entity
                self.registry.teams[target_id] = target_entity

            self.registry.save()

        # Remove history file
        os.remove(history_file)

        logger.info(f"Rolled back merge with token {rollback_token}")

        return {
            "success": True,
            "source_entity_id": merge_record["source_entity_id"],
            "target_entity_id": merge_record["target_entity_id"],
            "rolled_back_at": datetime.utcnow().isoformat(),
        }

    def get_archived_entities(self) -> list[dict[str, Any]]:
        """List all archived entities."""

        archived = []

        for filename in os.listdir(self.archive_path):
            if filename.endswith(".json"):
                archive_file = os.path.join(self.archive_path, filename)
                with open(archive_file) as f:
                    data = json.load(f)
                    archived.append(
                        {
                            "entity_id": data["original_id"],
                            "entity_name": data["entity"]["canonical_name"],
                            "entity_type": data["entity"]["entity_type"],
                            "archived_at": data["archived_at"],
                            "archive_reason": data["archive_reason"],
                        }
                    )

        return archived

    def _get_entity_type(self, entity_id: str) -> EntityType | None:
        """Determine entity type from ID."""
        if entity_id.startswith("person-"):
            return EntityType.PERSON
        elif entity_id.startswith("project-"):
            return EntityType.PROJECT
        elif entity_id.startswith("team-"):
            return EntityType.TEAM
        return None
