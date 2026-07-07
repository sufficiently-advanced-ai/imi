"""
Models for domain.config
"""


from ..agent.patterns import IntelligencePattern
from ..base import BaseModel, Field

# Import shared types from central module
from ..types import DomainEntityRelationship, ExtractionPriority, SuccessMetric


class DomainEntityType(BaseModel):
    """Definition of an entity type in a domain configuration."""

    id: str
    name: str
    plural: str
    attributes: dict[str, str] = Field(default_factory=dict)
    relationships: list["DomainEntityRelationship"] = Field(default_factory=list)




class DomainConfiguration(BaseModel):
    """Complete domain configuration."""

    id: str
    name: str
    version: str = "1.0.0"
    entities: dict[str, DomainEntityType] = Field(default_factory=dict)
    intelligence_patterns: dict[str, "IntelligencePattern"] = Field(default_factory=dict)
    extraction_priorities: dict[str, "ExtractionPriority"] = Field(default_factory=dict)
    success_metrics: dict[str, "SuccessMetric"] = Field(default_factory=dict)

