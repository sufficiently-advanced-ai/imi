"""Captures REST API — the G4 capture loop surface (OB1 absorption Phase 1).

Externally visible paths (all under /api/captures):
    POST /api/captures                — capture a thought (persist→enrich→index→commit)
    GET  /api/captures                — list captures (review_status/source filters)
    GET  /api/captures/{capture_id}   — single capture record
    POST /api/captures/{capture_id}/review — audited governance transition

Prefix convention: routers self-prefix with /api (mirrors decisions.py);
main.py calls include_router with NO extra prefix. Governance fields are
never request parameters (ADR-002 server-injected) — the review action is
the only governance entry point.

NOTE: /api/memory is owned by app/routes/memory.py (the org-memory agent);
captures deliberately live at /api/captures.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

# Imported into module namespace so tests can monkeypatch (decisions.py pattern).
from app.services.capture_service import capture_and_persist, review_capture
from app.services.memory_capture import CaptureStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/captures", tags=["captures"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CaptureRequest(BaseModel):
    content: str = Field(..., min_length=1, description="The thought to capture")
    source: str = Field("manual", description="Capture source: web, mail, manual, rss")
    source_id: str | None = Field(
        None, description="External id (URL, message id) for idempotent re-capture"
    )
    tags: list[str] | None = None
    source_date: str | None = None
    actor: str | None = Field(None, description="Who captured (for the audit row)")


class CaptureRecord(BaseModel):
    id: str
    content: str
    source: str
    source_id: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    enrichment: dict = Field(default_factory=dict)
    related_record_ids: list[str] = Field(default_factory=list)
    provenance_status: str
    review_status: str
    can_use_as_evidence: bool
    can_use_as_instruction: bool
    superseded_by: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    tenant_id: str | None = None
    created_at: str


class CaptureListResponse(BaseModel):
    captures: list[CaptureRecord]
    total: int


class ReviewRequest(BaseModel):
    action: Literal["confirm", "reject", "evidence_only", "dispute", "supersede"]
    actor: str | None = None
    superseded_by: str | None = Field(
        None, description="Successor capture id; required for action=supersede"
    )

    @model_validator(mode="after")
    def _supersede_requires_successor(self) -> ReviewRequest:
        if self.action == "supersede" and not self.superseded_by:
            raise ValueError("action=supersede requires superseded_by")
        return self


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("")
async def create_capture(body: CaptureRequest):
    """Capture a thought: persist-first, then enrich/index/commit best-effort."""
    result = await capture_and_persist(
        body.content,
        source=body.source,
        source_id=body.source_id,
        tags=body.tags,
        source_date=body.source_date,
        actor=body.actor,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=500, detail=result.get("error", "capture failed")
        )
    return result


@router.get("", response_model=CaptureListResponse)
async def list_captures(
    review_status: str | None = Query(None, description="Review status filter"),
    source: str | None = Query(None, description="Capture source filter"),
    limit: int = Query(50, ge=1, le=500),
):
    """List captures, newest first. ``total`` is the full match count."""
    store = CaptureStore()
    records = store.list(review_status=review_status, source=source, limit=limit)
    captures = [CaptureRecord(**m.model_dump()) for m in records]
    total = store.count(review_status=review_status, source=source)
    return CaptureListResponse(captures=captures, total=total)


@router.get("/{capture_id}", response_model=CaptureRecord)
async def get_capture(capture_id: str):
    """Single capture record."""
    memory = CaptureStore().get(capture_id)
    if memory is None:
        raise HTTPException(
            status_code=404, detail=f"Capture {capture_id!r} not found"
        )
    return CaptureRecord(**memory.model_dump())


@router.post("/{capture_id}/review")
async def review_capture_endpoint(capture_id: str, body: ReviewRequest):
    """Apply an audited governance transition (the only governance entry point)."""
    result = await review_capture(
        capture_id,
        body.action,
        actor=body.actor,
        superseded_by=body.superseded_by,
    )
    if not result.get("success"):
        error = result.get("error", "unknown error")
        status_code = 404 if "not found" in error.lower() else 400
        raise HTTPException(status_code=status_code, detail=error)
    return result
