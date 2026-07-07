"""
Models for entity.registry
"""

from ..base import BaseModel, Field, datetime


class CanonicalEntity(BaseModel):
    """Base model for canonical entities in the registry"""

    id: str = Field(..., description="Unique identifier for the entity")
    canonical_name: str = Field(
        ..., description="The canonical/official name for this entity"
    )
    aliases: list[str] = Field(
        default_factory=list, description="Alternative names and variations"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Confidence in this entity's accuracy"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen: datetime = Field(default_factory=datetime.utcnow)

    def add_alias(self, alias: str) -> None:
        """Add new alias if not already present"""
        normalized_alias = alias.strip()
        if (
            normalized_alias
            and not self.has_alias(normalized_alias)
            and normalized_alias.lower() != self.canonical_name.lower()
        ):
            self.aliases.append(normalized_alias)

    def remove_alias(self, alias: str) -> None:
        """Remove alias from list"""
        self.aliases = [a for a in self.aliases if a.lower() != alias.lower()]

    def has_alias(self, alias: str) -> bool:
        """Check if entity has specific alias (case insensitive)"""
        return any(a.lower() == alias.lower() for a in self.aliases)

    def update_last_seen(self) -> None:
        """Update last seen timestamp to current time"""
        self.last_seen = datetime.utcnow()




# Meeting State Models - Issue #121


class EntityReference(BaseModel):
    """Reference to an entity in the registry"""

    name: str
    registry_id: str | None = None
    entity_type: str  # person, project, organization

