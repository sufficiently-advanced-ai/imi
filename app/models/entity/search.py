"""
Models for entity.search
"""

from typing import Any

from ..api.core import EntityType
from ..base import BaseModel, Field, datetime, field_validator


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




class SuggestedEntity(BaseModel):
    """Entity suggestion response - Issue #58"""

    id: str
    canonical_name: str
    entity_type: EntityType
    confidence: float
    aliases: list[str] = Field(default_factory=list)
    match_context: str | None = None
    relevance_score: float = 0.0
    context: dict[str, Any] | None = None
    match_reason: str | None = None




class EntitySuggestionRequest(BaseModel):
    """Request for entity suggestions - Issue #58"""

    text: str = Field(..., description="Text to analyze for entities")
    entity_types: list[EntityType] | None = None
    include_context: bool = Field(False, description="Include entity context")
    min_confidence: float = Field(0.0, ge=0.0, le=1.0)
    limit: int | None = Field(None, ge=1, le=100)




class EntitySuggestionResponse(BaseModel):
    """Response with entity suggestions - Issue #58"""

    suggestions: dict[str, list[SuggestedEntity]]
    response_time_ms: float




class EntitySearchResponse(BaseModel):
    """Response for entity search endpoint"""

    results: list[EntitySearchResult]
    total_results: int
    query: str
    suggestions: list[str] = Field(default_factory=list)
    search_time_ms: float = 0.0

