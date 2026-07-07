"""
Models for agent.patterns
"""


from ..base import BaseModel, Field

# Import shared types from central module
from ..types import PatternTrigger, PatternType


class IntelligencePattern(BaseModel):
    """Intelligence pattern for domain-specific analysis."""

    id: str
    name: str
    description: str
    pattern_type: PatternType
    triggers: list[PatternTrigger]
    priority: str = "medium"
    actions: list[str] = Field(default_factory=list)

