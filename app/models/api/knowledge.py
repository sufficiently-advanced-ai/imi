"""
Knowledge and document operation models.

This module contains models for document management, knowledge base operations,
metadata handling, and diff explanations.
"""

from datetime import UTC
from typing import Any, Literal

from ..base import BaseModel, Field, datetime
from ..types import ProcessingStatus

# Import EntityExtraction from entities module for use in EnhancedDocumentMetadata
from .entities import EntityExtraction


class File(BaseModel):
    path: str
    content: str
    created_at: datetime | None = None
    modified_at: datetime | None = None




class KnowledgeResponse(BaseModel):
    files: list[File]
    total: int = 0
    page: int = 0
    limit: int = 100




class QueryRequest(BaseModel):
    question: str
    context_files: list[str] | None = None
    prompt_type: str = "search"




class QueryResponse(BaseModel):
    answer: str
    model: str
    prompt_tokens: int | None = 0
    completion_tokens: int | None = 0
    confidence: str | None = None
    sources: list[str] | None = None
    # New fields for ChatAgent
    response: str | None = None  # Alias for answer for backward compatibility
    context_used: list[str] | None = None  # Files that were actually used
    tool_calls: list[dict[str, Any]] | None = None  # Tool usage tracking




class DocumentMetadata(BaseModel):
    """Document metadata schema"""

    type: str = Field(
        ..., description="Document type (e.g., meeting_notes, documentation)"
    )
    created: datetime = Field(default_factory=lambda: datetime.now(UTC))
    modified: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = Field(
        default="auto", description="Source of metadata (auto or manual)"
    )
    classification: dict[str, float | list[str]] = Field(
        default_factory=lambda: {"confidence": 0.0, "categories": []},
        description="Document classification information including categories and confidence",
    )
    summary: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "key_points": [],
            "action_items": [],
            "participants": [],
        },
        description="Document summary including key points, action items, and participants",
    )
    references: dict[str, list[str]] = Field(
        default_factory=lambda: {"related_docs": [], "context_files": []},
        description="References to related documents and context files",
    )

    class Config:
        extra = "allow"  # Allow additional fields not in the schema




class DigestRequest(BaseModel):
    """Request model for /digest endpoint"""

    date: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y%m%d"),
        pattern="^\\d{8}$",
        description="Date in YYYYMMDD format",
    )
    force_refresh: bool = Field(
        default=False, description="Force regeneration of existing digest"
    )




class DigestResponse(BaseModel):
    """Response model for /digest endpoint"""

    digest_file: str  # Path to generated digest file
    processed_files: list[str]  # Files included in digest
    created: bool  # Whether new digest was created




class ProcessingResult(BaseModel):
    """Generic result model for processing operations"""

    success: bool
    message: str
    data: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)




class UploadResponse(BaseModel):
    """Response model for file upload endpoint"""

    status: ProcessingStatus
    filename: str
    path: str
    metadata: DocumentMetadata | None = None
    errors: list[str] = Field(default_factory=list)
    message: str
    upload_id: str | None = None




class DiffExplanationRequest(BaseModel):
    """Request model for diff explanation endpoint"""

    old_content: str
    new_content: str
    file_path: str
    current_commit: str
    previous_commit: str
    context_files: list[str] | None = None
    force_refresh: bool = False




class DiffExplanationResponse(BaseModel):
    """Response model for diff explanation endpoint"""

    explanation: str
    file_path: str




class EnhancedDocumentMetadata(DocumentMetadata):
    """Document metadata with entity awareness - Issue #58"""

    entity_extractions: list[EntityExtraction] = Field(default_factory=list)
    entity_confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="Overall entity extraction confidence"
    )
    validation_status: Literal["pending", "validated", "needs_review"] = Field(
        "pending", description="Entity validation status"
    )




class MetadataResponse(BaseModel):
    path: str
    metadata: DocumentMetadata | EnhancedDocumentMetadata
