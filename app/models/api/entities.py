"""
Entity management and enrichment models.

This module contains models for entity management, search, enrichment,
bulk operations, and import/export functionality.
"""

from datetime import UTC
from typing import TYPE_CHECKING, Any, Optional

from ..base import BaseModel, Field, datetime, field_validator
from ..entity.registry import CanonicalEntity
from ..entity.relationships import RelationshipType
from ..types import EntityType

if TYPE_CHECKING:
    from ..entity.enrichment import OrganizationalContext
    from ..entity.search import SuggestedEntity


class EntityExtraction(BaseModel):
    """Extracted entity with validation metadata - Issue #58"""

    entity_type: EntityType
    raw_text: str = Field(..., description="Exact text from document")
    canonical_id: str | None = Field(
        None, description="Canonical entity ID if validated"
    )
    confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="Confidence score for extraction"
    )
    context: str = Field(..., description="Surrounding context from document")
    suggested_by: str = Field(
        ..., description="Source of suggestion: claude, registry, or manual"
    )

    class Config:
        extra = "allow"  # Allow additional fields like disambiguation_reason




class CanonicalPerson(CanonicalEntity):
    """Canonical representation of a person entity"""

    titles: list[str] = Field(default_factory=list, description="Job titles and roles")
    email: str | None = Field(default=None, description="Primary email address")
    phone: str | None = Field(default=None, description="Primary phone number")
    departments: list[str] = Field(
        default_factory=list, description="Associated departments"
    )

    @field_validator("id")
    @classmethod
    def validate_person_id(cls, v: str) -> str:
        if not v.startswith("person-"):
            raise ValueError("Person ID must start with 'person-'")
        return v




class CanonicalProject(CanonicalEntity):
    """Canonical representation of a project entity"""

    status: str = Field(default="active", description="Project status")
    teams: list[str] = Field(
        default_factory=list, description="Teams working on this project"
    )
    start_date: datetime | None = Field(
        default=None, description="Project start date"
    )
    end_date: datetime | None = Field(default=None, description="Project end date")
    objectives: list[str] = Field(
        default_factory=list, description="Project objectives"
    )

    @field_validator("id")
    @classmethod
    def validate_project_id(cls, v: str) -> str:
        if not v.startswith("project-"):
            raise ValueError("Project ID must start with 'project-'")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        valid_statuses = ["planning", "active", "completed", "on-hold", "cancelled"]
        if v not in valid_statuses:
            raise ValueError(f"Status must be one of: {', '.join(valid_statuses)}")
        return v

    def add_team(self, team_id: str) -> None:
        """Add team to project if not already present"""
        if team_id not in self.teams:
            self.teams.append(team_id)




class CanonicalTeam(CanonicalEntity):
    """Canonical representation of a team entity"""

    department: str | None = Field(
        default=None, description="Department this team belongs to"
    )
    division: str | None = Field(
        default=None, description="Division this team belongs to"
    )
    parent_team: str | None = Field(
        default=None, description="Parent team ID if hierarchical"
    )
    members: list[str] = Field(
        default_factory=list, description="Team member person IDs"
    )
    lead: str | None = Field(default=None, description="Team lead person ID")

    @field_validator("id")
    @classmethod
    def validate_team_id(cls, v: str) -> str:
        if not v.startswith("team-"):
            raise ValueError("Team ID must start with 'team-'")
        return v

    def is_child_of(self, team_id: str) -> bool:
        """Check if this team is a child of the specified team"""
        return self.parent_team == team_id

    def add_member(self, person_id: str) -> None:
        """Add member to team if not already present"""
        if person_id not in self.members:
            self.members.append(person_id)

    def remove_member(self, person_id: str) -> None:
        """Remove member from team"""
        self.members = [m for m in self.members if m != person_id]




# Entity Search & Discovery Models - Issue #60


class EntitySearchResult(BaseModel):
    """Individual entity search result"""

    entity_id: str
    entity_type: EntityType
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    confidence_score: float
    match_type: str  # exact, partial, fuzzy, alias
    last_activity: datetime
    document_count: int
    relevance_score: float = 0.0

    @field_validator("match_type")
    @classmethod
    def validate_match_type(cls, v: str) -> str:
        valid_types = ["exact", "partial", "fuzzy", "alias"]
        if v not in valid_types:
            raise ValueError(f"Match type must be one of: {', '.join(valid_types)}")
        return v




class EntityAutocompleteResponse(BaseModel):
    """Response for entity autocomplete endpoint"""

    suggestions: list["SuggestedEntity"]
    query: str
    total_matches: int




# Entity Profile & Activity Models - Issue #60


class EntityActivity(BaseModel):
    """Entity activity record"""

    entity_id: str
    activity_type: str  # meeting, commit, document, etc.
    activity_date: datetime
    description: str
    document_path: str
    relevance_score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)




class ProfileEntityRelationship(BaseModel):
    """Entity relationship information for profiles"""

    entity_id: str
    relationship_type: str
    strength: float
    last_interaction: datetime | None = None
    interaction_count: int = 0




class EntityInsight(BaseModel):
    """Generated insight about an entity"""

    insight_type: str
    content: str
    confidence: float
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    supporting_evidence: list[str] = Field(default_factory=list)




class EntityStatistics(BaseModel):
    """Statistics about an entity"""

    total_mentions: int = 0
    recent_mentions: int = 0
    document_count: int = 0
    activity_count: int = 0
    relationship_count: int = 0
    last_activity: datetime | None = None
    quality_score: float = 0.0
    completeness_score: float = 0.0




class EnrichedEntity(BaseModel):
    """Entity with enriched data"""

    entity: CanonicalEntity
    total_mentions: int = 0
    recent_mentions: int = 0
    key_relationships: list[dict[str, Any]] = Field(default_factory=list)
    activity_summary: dict[str, int] = Field(default_factory=dict)
    last_activity: datetime | None = None




class EntityProfileResponse(BaseModel):
    """Response for entity profile endpoint"""

    entity: dict[str, Any]  # Serialized entity
    statistics: EntityStatistics
    recent_activity: list[EntityActivity] = Field(default_factory=list)
    top_relationships: list[ProfileEntityRelationship] = Field(default_factory=list)
    insights: list[EntityInsight] = Field(default_factory=list)
    narrative_profile: str | None = Field(
        default=None, description="Full narrative profile content from entity markdown file"
    )




class EntityDocumentsResponse(BaseModel):
    """Response for entity documents endpoint"""

    documents: list[dict[str, Any]]
    total_count: int
    entity_id: str




# Entity Management Models - Issue #60


class EntityMergeRequest(BaseModel):
    """Request to merge two entities.

    The duplicate (``target_entity_id``) is merged into the survivor and then
    archived. By default the path ``entity_id`` is the survivor; pass
    ``primary_id`` to state explicitly which of the two entities survives
    (it must be one of the path id or ``target_entity_id``).
    """

    target_entity_id: str
    primary_id: str | None = None
    keep_canonical_name: str | None = None
    merge_strategy: str = "confidence"  # confidence, manual, oldest
    preview: bool = False
    enable_rollback: bool = False

    @field_validator("merge_strategy")
    @classmethod
    def validate_merge_strategy(cls, v: str) -> str:
        valid_strategies = ["confidence", "manual", "oldest"]
        if v not in valid_strategies:
            raise ValueError(
                f"Merge strategy must be one of: {', '.join(valid_strategies)}"
            )
        return v




class EntityMergeResponse(BaseModel):
    """Response for entity merge operation"""

    success: bool
    merged_entity_id: str | None = None
    merge_summary: dict[str, Any] = Field(default_factory=dict)
    rollback_token: str | None = None
    rollback_enabled: bool = False
    preview: bool = False
    merge_impact: dict[str, Any] | None = None




class EntityAliasRequest(BaseModel):
    """Request to add alias to entity"""

    alias: str
    reason: str | None = None

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Alias cannot be empty")
        if len(v) > 255:
            raise ValueError("Alias too long (max 255 characters)")
        return v




class EntityAliasResponse(BaseModel):
    """Response for alias operations"""

    success: bool
    current_aliases: list[str]
    message: str | None = None




class EntityUpdateRequest(BaseModel):
    """Request to update entity fields"""

    canonical_name: str | None = None
    confidence: float | None = None
    email: str | None = None
    phone: str | None = None
    titles: list[str] | None = None
    departments: list[str] | None = None
    status: str | None = None
    teams: list[str] | None = None
    objectives: list[str] | None = None
    department: str | None = None
    division: str | None = None
    members: list[str] | None = None
    lead: str | None = None
    version: int | None = None  # For optimistic concurrency control




class EntityUpdateResponse(BaseModel):
    """Response for entity update"""

    success: bool
    entity: dict[str, Any]
    version: int




class EntityArchiveResponse(BaseModel):
    """Response for entity archive operation"""

    success: bool
    entity_id: str
    archived_at: datetime
    archive_reason: str
    relationships_preserved: int = 0




# Bulk Operation Models - Issue #60


class BulkMergeOperation(BaseModel):
    """Single merge operation in bulk request"""

    source_entity_id: str
    target_entity_id: str
    keep_canonical_name: str | None = None
    merge_strategy: str = "confidence"




class BulkMergeResponse(BaseModel):
    """Response for bulk merge operations"""

    total_operations: int
    successful: int
    failed: int
    results: list[dict[str, Any]]
    transaction_mode: bool = False




class BulkValidationRequest(BaseModel):
    """Request for bulk entity validation"""

    entity_ids: list[str]
    validation_rules: list[str] = Field(
        default_factory=lambda: ["completeness", "consistency", "uniqueness"]
    )
    strict_mode: bool = False
    include_suggestions: bool = False




class BulkValidationResponse(BaseModel):
    """Response for bulk validation"""

    total_validated: int
    validation_results: list[dict[str, Any]]
    performance_metrics: dict[str, Any] | None = None




class EnrichmentOptions(BaseModel):
    """Options for entity enrichment"""

    sources: list[str] = Field(default_factory=list)  # linkedin, github, etc.
    fields: list[str] = Field(default_factory=list)  # specific fields to enrich
    confidence_threshold: float = 0.7
    overwrite_existing: bool = False
    async_processing: bool = False
    track_sources: bool = True




class BulkEnrichmentRequest(BaseModel):
    """Request for bulk entity enrichment"""

    entity_ids: list[str]
    enrichment_options: EnrichmentOptions




class BulkEnrichmentResponse(BaseModel):
    """Response for bulk enrichment"""

    total_enriched: int
    enrichment_results: list[dict[str, Any]]
    job_id: str | None = None
    status: str = "completed"
    status_url: str | None = None




class ImportResponse(BaseModel):
    """Response for entity import"""

    success: bool
    imported: dict[str, int]  # entity_type -> count
    total_imported: int
    merged: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)
    validation_errors: list[dict[str, Any]] = Field(default_factory=list)




class ExportRequest(BaseModel):
    """Request for entity export"""

    export_format: str = "json"  # json, csv, xlsx
    entity_types: list[EntityType] = Field(default_factory=list)
    include_relationships: bool = False
    filter_confidence: float | None = None
    filter_department: str | None = None




class ExportResponse(BaseModel):
    """Response for entity export"""

    format: str
    entities: dict[str, Any]
    export_metadata: dict[str, Any]




class EnrichedEntityRelationship(BaseModel):
    """Represents an enriched relationship between two entities"""

    target_entity_id: str = Field(..., description="ID of the target entity")
    relationship_type: RelationshipType = Field(..., description="Type of relationship")
    strength: float = Field(
        ..., ge=0.0, le=1.0, description="Strength of relationship (0-1)"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in relationship (0-1)"
    )
    evidence_documents: list[str] = Field(
        default_factory=list, description="Documents supporting this relationship"
    )
    first_detected: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_confirmed: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def create_inverse(self, source_entity_id: str) -> "EnrichedEntityRelationship":
        """Create the inverse relationship"""
        return EnrichedEntityRelationship(
            target_entity_id=source_entity_id,
            relationship_type=self.relationship_type.get_inverse(),
            strength=self.strength,
            confidence=self.confidence,
            evidence_documents=list(self.evidence_documents),
            first_detected=self.first_detected,
            last_confirmed=self.last_confirmed,
        )




class EnrichedEntityWithContext(BaseModel):
    """Entity with enriched relationship and context data"""

    base_entity: CanonicalPerson | CanonicalProject | CanonicalTeam = Field(
        ..., description="Base entity information"
    )
    relationships: list[EnrichedEntityRelationship] = Field(
        default_factory=list, description="All relationships"
    )
    organizational_context: Optional["OrganizationalContext"] = Field(
        None, description="Organizational position"
    )
    last_enriched: datetime = Field(default_factory=lambda: datetime.now(UTC))
    enrichment_version: str = Field(
        default="1.0", description="Version of enrichment logic"
    )

    def get_relationships_by_type(
        self, relationship_type: RelationshipType
    ) -> list[EnrichedEntityRelationship]:
        """Get all relationships of a specific type"""
        return [
            r for r in self.relationships if r.relationship_type == relationship_type
        ]

    def get_average_relationship_strength(
        self, relationship_type: RelationshipType | None = None
    ) -> float:
        """Calculate average strength of relationships"""
        filtered_rels = self.relationships
        if relationship_type:
            filtered_rels = [
                r for r in filtered_rels if r.relationship_type == relationship_type
            ]

        if not filtered_rels:
            return 0.0

        return sum(r.strength for r in filtered_rels) / len(filtered_rels)
