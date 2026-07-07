"""Domain Configuration Models - Issue #156"""

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class PatternType(str, Enum):
    """Types of intelligence patterns."""

    RISK = "risk"
    OPPORTUNITY = "opportunity"
    ESCALATION = "escalation"
    COMMITMENT = "commitment"
    DECISION = "decision"
    INSIGHT = "insight"


class MetricType(str, Enum):
    """Types of success metrics."""

    COUNT = "count"
    PERCENTAGE = "percentage"
    TIME = "time"
    RATIO = "ratio"
    SCORE = "score"


class DomainAttribute(BaseModel):
    """Attribute definition for domain entities."""

    name: str = Field(..., description="Attribute name in snake_case")
    type: str = Field(
        ..., description="Attribute type: string, number, date, boolean, datetime, enum"
    )
    required: bool = Field(
        default=False, description="Whether this attribute is required"
    )
    enum: list[str] | None = Field(
        default=None, description="Allowed values for enum type"
    )
    unit: str | None = Field(
        default=None, description="Unit of measurement for number types"
    )

    @field_validator("name")
    @classmethod
    def validate_attribute_name(cls, v: str) -> str:
        """Validate attribute name follows snake_case convention."""
        import re

        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                "Attribute name must be snake_case starting with lowercase letter"
            )
        return v

    @field_validator("type")
    @classmethod
    def validate_attribute_type(cls, v: str) -> str:
        """Validate attribute type is supported."""
        valid_types = ["string", "number", "date", "boolean", "datetime", "enum"]
        if v not in valid_types:
            raise ValueError(f"Attribute type must be one of: {', '.join(valid_types)}")
        return v

    @field_validator("enum")
    @classmethod
    def validate_enum_values(
        cls, v: list[str] | None, values
    ) -> list[str] | None:
        """Validate enum values are provided when type is enum."""
        if values.data.get("type") == "enum" and not v:
            raise ValueError("Enum type requires enum values to be specified")
        return v


class DomainRelationship(BaseModel):
    """Relationship definition between domain entities."""

    type: str = Field(..., description="Relationship type name")
    target: str = Field(..., description="Target entity type")
    cardinality: str = Field(..., description="Relationship cardinality")
    inverse_name: str | None = Field(
        default=None, description="Name of the inverse relationship"
    )

    @property
    def name(self) -> str:
        """Alias for type to match EntityRegistry expectations."""
        return self.type

    @property
    def target_entity(self) -> str:
        """Alias for target to match EntityRegistry expectations."""
        return self.target

    @field_validator("cardinality")
    @classmethod
    def validate_cardinality(cls, v: str) -> str:
        """Validate cardinality value."""
        valid_cardinalities = [
            "one_to_one",
            "one_to_many",
            "many_to_one",
            "many_to_many",
            "one-to-one",
            "one-to-many",
            "many-to-one",
            "many-to-many",
        ]
        if v not in valid_cardinalities:
            raise ValueError(
                f"Cardinality must be one of: {', '.join(valid_cardinalities)}"
            )
        # Normalize to hyphenated format
        return v.replace("_", "-")


class DomainEntity(BaseModel):
    """Entity definition within a domain."""

    name: str = Field(..., description="Entity type name")
    description: str = Field(..., description="Entity description")
    plural: str = Field(..., description="Plural form of entity name")
    label: str | None = Field(default=None, description="Display name (e.g., 'Client'). Falls back to name.")
    plural_label: str | None = Field(default=None, description="Display plural (e.g., 'Clients'). Falls back to plural.")
    icon: str | None = Field(default=None, description="Lucide icon name for navigation (e.g., 'building-2', 'users', 'briefcase')")
    attributes: list[DomainAttribute] = Field(default_factory=list)
    relationships: list[DomainRelationship] = Field(default_factory=list)
    ner_labels: list[str] = Field(
        default_factory=list,
        description="NER labels (e.g. 'ORG', 'PERSON') that map to this entity type during extraction",
    )
    ner_exclude: list[str] = Field(
        default_factory=list,
        description="Entity names that match this type's ner_labels but should NOT be treated as this type (e.g. standards bodies/frameworks mis-tagged as ORG). Case-insensitive exact match.",
    )

    @field_validator("ner_labels", "ner_exclude")
    @classmethod
    def _clean_ner_lists(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in v or []:
            s = (item or "").strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
        return out

    @property
    def attributes_dict(self) -> dict[str, DomainAttribute]:
        """Get attributes as a dictionary keyed by name."""
        return {attr.name: attr for attr in self.attributes}

    @property
    def relationships_dict(self) -> dict[str, DomainRelationship]:
        """Get relationships as a dictionary keyed by type."""
        return {rel.type: rel for rel in self.relationships}


class PatternTrigger(BaseModel):
    """Trigger condition for intelligence patterns."""

    entity: str = Field(..., description="Entity type this trigger applies to")
    condition: str = Field(..., description="Condition expression")
    weight: float = Field(default=1.0, description="Weight of this trigger (0-1)")


class IntelligencePattern(BaseModel):
    """Intelligence pattern definition."""

    name: str
    pattern_type: PatternType = Field(..., description="Type of pattern")
    triggers: list[PatternTrigger]
    priority: str = Field(default="medium")
    actions: list[str] = Field(default_factory=list)

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        """Validate priority level."""
        valid_priorities = ["high", "medium", "low"]
        if v not in valid_priorities:
            raise ValueError(f"Priority must be one of: {', '.join(valid_priorities)}")
        return v

    def get_required_entities(self) -> list[str]:
        """Get list of entity types required by this pattern."""
        return list(set(trigger.entity for trigger in self.triggers))


class ExtractionPriority(BaseModel):
    """Extraction priority configuration."""

    pattern: str
    priority: str

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        """Validate priority level."""
        valid_priorities = ["high", "medium", "low"]
        if v not in valid_priorities:
            raise ValueError(f"Priority must be one of: {', '.join(valid_priorities)}")
        return v


class SuccessMetric(BaseModel):
    """Success metric definition."""

    name: str
    type: MetricType = Field(..., description="Type of metric")
    calculation: str | None = None
    range: list[int | float] | None = None
    unit: str | None = None
    target: int | float | None = None


class NavItem(BaseModel):
    """Override for a single nav item label."""

    label: str = Field(..., description="Display label (e.g., 'Clients' instead of 'Entities')")
    description: str | None = Field(default=None, description="Tooltip/description override")


class NavGroup(BaseModel):
    """Override for a nav group and its items."""

    label: str | None = Field(default=None, description="Group header override")
    items: dict[str, NavItem] = Field(default_factory=dict, description="Keyed by route path (e.g., '/entities')")


class UILabels(BaseModel):
    """UI terminology and labeling configuration."""

    app_name: str = Field(default="imi", description="Sidebar header name")
    nav_groups: dict[str, NavGroup] = Field(default_factory=dict, description="Keyed by group id")
    entity_label: str = Field(default="Entities", description="Generic label for the entities section")
    graph_label: str = Field(default="Domain Graph", description="Label for the graph page")
    terminology: dict[str, str] = Field(default_factory=dict, description="Generic term overrides")


class DomainConfiguration(BaseModel):
    """Complete domain configuration schema."""

    id: str = Field(..., description="Domain ID in snake_case")
    name: str = Field(..., description="Human-readable domain name")
    version: str = Field(default="1.0.0", description="Schema version")
    entities: dict[str, DomainEntity] = Field(default_factory=dict)
    intelligence_patterns: dict[str, IntelligencePattern] = Field(default_factory=dict)
    extraction_priorities: dict[str, ExtractionPriority] = Field(default_factory=dict)
    success_metrics: list[SuccessMetric] = Field(default_factory=list)
    ui: UILabels | None = Field(default=None, description="UI terminology and labeling configuration")

    @field_validator("id")
    @classmethod
    def validate_domain_id(cls, v: str) -> str:
        """Validate domain ID format."""
        import re

        if not v:
            raise ValueError("Domain ID cannot be empty")
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                "Domain ID must be snake_case starting with lowercase letter"
            )
        return v

    def has_circular_references(self) -> bool:
        """Check if the domain has circular entity relationships."""
        # Build adjacency list
        graph = {}
        for entity_name, entity in self.entities.items():
            graph[entity_name] = [rel.target for rel in entity.relationships]

        # DFS to detect cycles
        def has_cycle(node: str, visited: set, rec_stack: set) -> bool:
            visited.add(node)
            rec_stack.add(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor, visited, rec_stack):
                        return True
                elif neighbor in rec_stack:
                    return True

            rec_stack.remove(node)
            return False

        visited = set()
        for node in graph:
            if node not in visited:
                if has_cycle(node, visited, set()):
                    return True
        return False

    @model_validator(mode="after")
    def validate_inverse_names(self) -> "DomainConfiguration":
        """Validate that inverse_name references are consistent.

        For every relationship with an inverse_name:
        1. The target entity must exist
        2. The target entity must have a relationship with that name
        3. That inverse relationship must point back to the source entity
        4. The pair must be symmetric (inverse's inverse_name == this rel's type)
        """
        errors: list[str] = []

        for entity_name, entity in self.entities.items():
            for rel in entity.relationships:
                if not rel.inverse_name:
                    continue

                # Check target entity exists
                target_entity = self.entities.get(rel.target)
                if not target_entity:
                    errors.append(
                        f"{entity_name}.{rel.type}: target entity '{rel.target}' "
                        f"not found for inverse_name '{rel.inverse_name}'"
                    )
                    continue

                # Check inverse relationship exists on target
                inverse_rel = target_entity.relationships_dict.get(rel.inverse_name)
                if not inverse_rel:
                    errors.append(
                        f"{entity_name}.{rel.type}: inverse_name '{rel.inverse_name}' "
                        f"not found on target entity '{rel.target}'"
                    )
                    continue

                # Check inverse points back to source entity
                if inverse_rel.target != entity_name:
                    errors.append(
                        f"{entity_name}.{rel.type}: inverse '{rel.target}.{rel.inverse_name}' "
                        f"targets '{inverse_rel.target}', expected '{entity_name}'"
                    )
                    continue

                # Check symmetry: inverse's inverse_name should be this rel's type
                if inverse_rel.inverse_name and inverse_rel.inverse_name != rel.type:
                    errors.append(
                        f"{entity_name}.{rel.type}: asymmetric inverse — "
                        f"'{rel.target}.{rel.inverse_name}' has inverse_name "
                        f"'{inverse_rel.inverse_name}', expected '{rel.type}'"
                    )

        if errors:
            raise ValueError(
                "Invalid inverse_name references:\n  " + "\n  ".join(errors)
            )

        return self
