"""Unified review queue across governed record kinds (OB1 absorption Phase 2).

Externally visible paths (all under /api/memories):
    GET  /api/memories/review        — pending captures + agent memories
    POST /api/memories/{id}/review   — audited transition, kind auto-resolved

This is the human gate of the trust ladder: instruction-grade memory only
exists because someone acted here (ADR-002). Signals keep their existing
review surface (/api/decisions, update_signal); this queue covers the memory
record kinds introduced by the OB1 absorption.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

# Imported into module namespace so tests can monkeypatch.
from app.services.agent_memory_store import AgentMemoryStore
from app.services.capture_service import review_capture
from app.services.memory_capture import CaptureStore
from app.services.memory_inspector import inspect_memory
from app.services.memory_writeback import review_agent_memory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memories", tags=["memories"])


class ReviewQueueItem(BaseModel):
    id: str
    record_kind: Literal["capture", "agent_memory"]
    content: str
    summary: str | None = None
    memory_type: str | None = None
    source: str | None = None
    runtime_name: str | None = None
    task_id: str | None = None
    provenance_status: str
    created_at: str


class ReviewQueueResponse(BaseModel):
    items: list[ReviewQueueItem]
    total: int


class ReviewRequest(BaseModel):
    action: Literal["confirm", "reject", "evidence_only", "dispute", "supersede"]
    actor: str | None = None
    superseded_by: str | None = Field(
        None, description="Successor record id; required for action=supersede"
    )

    @model_validator(mode="after")
    def _supersede_requires_successor(self) -> ReviewRequest:
        if self.action == "supersede" and not self.superseded_by:
            raise ValueError("action=supersede requires superseded_by")
        return self


@router.get("/review", response_model=ReviewQueueResponse)
async def review_queue(
    kind: Literal["capture", "agent_memory"] | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """Pending records awaiting human review, newest first, across kinds."""
    items: list[ReviewQueueItem] = []

    if kind in (None, "capture"):
        for capture in CaptureStore().list(review_status="pending", limit=limit):
            items.append(
                ReviewQueueItem(
                    id=capture.id,
                    record_kind="capture",
                    content=capture.content,
                    summary=capture.summary,
                    source=capture.source,
                    provenance_status=capture.provenance_status,
                    created_at=capture.created_at,
                )
            )

    if kind in (None, "agent_memory"):
        for memory in AgentMemoryStore().list(review_status="pending", limit=limit):
            items.append(
                ReviewQueueItem(
                    id=memory.id,
                    record_kind="agent_memory",
                    content=memory.content,
                    summary=memory.summary,
                    memory_type=memory.memory_type,
                    runtime_name=memory.runtime_name,
                    task_id=memory.task_id,
                    provenance_status=memory.provenance_status,
                    created_at=memory.created_at,
                )
            )

    items.sort(key=lambda item: item.created_at, reverse=True)
    items = items[:limit]
    return ReviewQueueResponse(items=items, total=len(items))


@router.get("/{record_id}/inspector")
async def get_memory_inspector(record_id: str):
    """The trust surface: why this memory exists, its audit history, how it
    was used, and what it can influence. Works for deleted records too
    (answers from the audit trail)."""
    result = await inspect_memory(record_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Record {record_id!r} not found"
        )
    return result


@router.post("/{record_id}/review")
async def review_record(record_id: str, body: ReviewRequest):
    """Audited governance transition; record kind resolved automatically."""
    if CaptureStore().get(record_id) is not None:
        result = await review_capture(
            record_id,
            body.action,
            actor=body.actor,
            superseded_by=body.superseded_by,
        )
    elif AgentMemoryStore().get(record_id) is not None:
        result = await review_agent_memory(
            record_id,
            body.action,
            actor=body.actor,
            superseded_by=body.superseded_by,
        )
    else:
        raise HTTPException(
            status_code=404, detail=f"Record {record_id!r} not found"
        )

    if not result.get("success"):
        error = result.get("error", "unknown error")
        status_code = 404 if "not found" in error.lower() else 400
        raise HTTPException(status_code=status_code, detail=error)
    return result
