"""
Pydantic models for the Master Ingestion Endpoint (Issue #863).

Defines request/response schemas for content ingestion, job status tracking,
and processing results.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ContentSource(str, Enum):
    """Supported content source platforms."""
    FIREFLIES = "fireflies"
    OTTER = "otter"
    FATHOM = "fathom"
    GRAIN = "grain"
    PLAUD = "plaud"
    LOCAL_RECORDING = "local_recording"
    SLACK = "slack"
    EMAIL = "email"
    DOCUMENT = "document"
    OTHER = "other"


class ContentType(str, Enum):
    """Classified content types after ingestion."""
    CALL_TRANSCRIPT = "call_transcript"
    SLACK_THREAD = "slack_thread"
    EMAIL_THREAD = "email_thread"
    DOCUMENT = "document"
    NOTES = "notes"


class IngestRequest(BaseModel):
    """Request body for POST /api/ingest."""
    content: str = Field(..., min_length=1, description="Raw text content (required)")
    source: ContentSource | None = Field(
        None, description="Source hint — skips LLM classification"
    )
    source_id: str | None = Field(
        None, description="External ID for idempotency"
    )
    title: str | None = Field(None, description="Subject or title")
    participants: list[str] | None = Field(
        None, description="Known participants"
    )
    timestamp: datetime | None = Field(
        None, description="Content creation time"
    )
    metadata: dict[str, Any] | None = Field(
        None, description="Passthrough metadata"
    )


class IngestResponse(BaseModel):
    """Response from POST /api/ingest."""
    job_id: str
    status: str  # "accepted" | "duplicate"
    content_type: str | None = None
    poll_url: str  # /api/ingest/{job_id}/status


class IngestResult(BaseModel):
    """Processing results after pipeline completion."""
    entities_extracted: int = 0
    relationships_created: int = 0
    decisions_found: int = 0
    insights_generated: int = 0
    graph_nodes_created: list[str] = Field(default_factory=list)
    content_hash: str = ""
    processing_time_ms: int = 0


class IngestJobStatus(BaseModel):
    """Status of an ingestion job."""
    job_id: str
    status: str  # pending/running/completed/failed
    content_type: str | None = None
    phases_completed: list[str] = Field(default_factory=list)
    current_phase: str | None = None
    result: IngestResult | None = None
    error: str | None = None
