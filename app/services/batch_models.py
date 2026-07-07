"""
Batch processing models to avoid circular imports
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.models import EntityType


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
class Entity:
    """Entity representation for deduplication"""

    name: str
    type: EntityType
    email: str = ""
    context: dict[str, Any] = field(default_factory=dict)


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

    def dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            "batch_id": self.batch_id,
            "status": self.status,
            "phase": self.phase,
            "files_completed": self.files_completed,
            "total_files": self.total_files,
            "entities_found": {
                k: list(v) if isinstance(v, set) else v
                for k, v in self.entities_found.items()
            },
            "current_file": self.current_file,
            "is_complete": self.is_complete,
            "errors": self.errors,
        }


@dataclass
class BatchResult:
    """Result of batch processing"""

    batch_id: str
    files_processed: int = 0
    entities_extracted: dict[str, int] = field(default_factory=dict)
    commit_hash: str = ""
    errors: list[str] = field(default_factory=list)
