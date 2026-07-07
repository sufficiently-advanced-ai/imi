"""
Central types module to prevent circular imports.

All shared enums, types, and base models that are used across
multiple model files should be defined here. This prevents
circular import dependencies between model modules.
"""


from .base import BaseModel, Enum, Field

# Shared Enums - Previously duplicated across modules

class CorrelationType(str, Enum):
    """Enumeration of correlation types"""
    EXACT_MATCH = "exact_match"
    PARTIAL_MATCH = "partial_match"
    NO_MATCH = "no_match"


class EntityType(str, Enum):
    """Types of entities in the knowledge base"""
    PERSON = "person"
    PROJECT = "project"
    TEAM = "team"


class MetricType(str, Enum):
    """Types of success metrics"""
    PERCENTAGE = "percentage"
    COUNT = "count"
    SCORE = "score"
    CURRENCY = "currency"
    TIME = "time"


class ObjectiveStatus(str, Enum):
    """Status of objective execution"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class PatternType(str, Enum):
    """Types of intelligence patterns"""
    RISK_DETECTION = "risk_detection"
    OPPORTUNITY_DETECTION = "opportunity_detection"
    SENTIMENT_ANALYSIS = "sentiment_analysis"
    COMPLIANCE_CHECK = "compliance_check"
    PERFORMANCE_INDICATOR = "performance_indicator"


class ProcessingStatus(str, Enum):
    """Status of processing operations"""
    SUCCESS = "success"
    PARTIAL = "partial_success"
    FAILED = "failed"


class RelationshipType(str, Enum):
    """Types of relationships between entities"""
    WORKS_WITH = "works_with"
    REPORTS_TO = "reports_to"
    MANAGES = "manages"
    COLLABORATES_WITH = "collaborates_with"
    MEMBER_OF = "member_of"
    OWNS = "owns"
    PARTICIPATES_IN = "participates_in"
    DEPENDS_ON = "depends_on"
    CONTRIBUTES_TO = "contributes_to"


class TaskStatus(str, Enum):
    """Status of background tasks"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Shared Base Models - Previously duplicated or causing circular imports

class DomainEntityRelationship(BaseModel):
    """Relationship between entity types in domain configuration"""
    target_entity: str
    relationship_type: str
    cardinality: str = "many-to-many"
    inverse_name: str | None = None


class ExtractionPriority(BaseModel):
    """Entity extraction priorities for different source types"""
    source_type: str
    priorities: dict[str, str]  # entity_type -> priority (high/medium/low)
    patterns: list[str] = Field(default_factory=list)  # pattern IDs to apply


class PatternTrigger(BaseModel):
    """Trigger condition for an intelligence pattern"""
    condition: str
    weight: float = 1.0


class SuccessMetric(BaseModel):
    """Success metric definition for a domain"""
    id: str
    name: str
    description: str
    metric_type: MetricType
    calculation: str
    target_value: float
    current_value: float = 0.0
    unit: str = ""
