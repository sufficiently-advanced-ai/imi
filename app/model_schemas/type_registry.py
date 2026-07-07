"""Type Registry Models — Issue #877

Graduated typing for the domain graph. Tracks provisional types created
at runtime alongside the canonical types declared in `config/domains/*.yaml`.

- canonical: declared in domain YAML; full display config + typed queries
- provisional: user-created via the editor; flagged on instances and here
- aliased: a provisional type an admin mapped onto a canonical name
- deprecated: was canonical, no longer recommended but preserved for old data
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TypeStatus(str, Enum):
    CANONICAL = "canonical"
    PROVISIONAL = "provisional"
    ALIASED = "aliased"
    DEPRECATED = "deprecated"


class TypeKind(str, Enum):
    ENTITY = "entity"
    RELATIONSHIP = "relationship"
    ATTRIBUTE = "attribute"


class TypeEntry(BaseModel):
    """A single row in the type registry."""

    name: str = Field(..., description="Type name, e.g. 'reports_to_inverse'")
    kind: TypeKind
    status: TypeStatus
    domain_id: str = Field(..., description="Domain id this type belongs to")
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO timestamp",
    )
    created_by: str = Field(
        default="domain_yaml",
        description="User id or 'domain_yaml' for canonical types",
    )
    usage_count: int = Field(default=0, description="Running instance count")
    aliased_to: str | None = Field(
        default=None,
        description="Target canonical name when status is aliased",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Type-specific context. For relationships: "
            "{source_type, target_type}. For attributes: {entity_type}."
        ),
    )
