"""
Models for entity.enrichment
"""


from ..api.core import EntityActivity
from ..base import BaseModel, Field


class OrganizationalContext(BaseModel):
    """Organizational context for an entity.

    Describes where an entity sits in the org hierarchy — hierarchy level,
    department, division, reporting chain, peers, and subordinates.

    NOTE: This model was originally defined in ``app.models.meeting.intelligence``
    where it was part of the meeting-stack.  It has been moved here
    (``app.models.entity.enrichment``) because it is used by the core
    entity-enrichment service (``app.services.entity_enrichment``) which must
    run in both ``kb`` and ``full`` deployment modes.

    The canonical import path is ``from app.models.entity.enrichment import
    OrganizationalContext``.  The meeting module re-exports it for backwards
    compatibility.
    """

    hierarchy_level: int = Field(
        ..., ge=0, description="Level in organizational hierarchy (0=top)"
    )
    department: str | None = Field(None, description="Department or division")
    division: str | None = Field(None, description="Higher-level division")
    reporting_chain: list[str] = Field(
        default_factory=list, description="Chain of command up to top"
    )
    peer_entities: list[str] = Field(
        default_factory=list, description="Entities at same level"
    )
    subordinate_entities: list[str] = Field(
        default_factory=list, description="Direct reports"
    )

    def get_organizational_depth(self) -> int:
        """Get depth in organization based on reporting chain"""
        return len(self.reporting_chain)

    def is_leadership_role(self) -> bool:
        """Check if this is a leadership position"""
        return self.hierarchy_level <= 3 or len(self.subordinate_entities) > 0


class EntityActivityResponse(BaseModel):
    """Response for entity activity endpoint"""

    activities: list[EntityActivity]
    total_count: int
    has_more: bool
    entity_id: str

