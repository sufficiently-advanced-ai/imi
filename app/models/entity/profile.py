"""
Models for entity.profile
"""

from ..base import BaseModel, Field, datetime


class PersonProfile(BaseModel):
    """Model for person profile data"""

    type: str = "person"
    id: str
    name: str
    role: str | None = None
    teams: list[str] = Field(default_factory=list)
    last_active: datetime
    related_files: list[str] = Field(default_factory=list)
    collaborators: list[str] = Field(default_factory=list)
    contexts: list[str] = Field(default_factory=list)

