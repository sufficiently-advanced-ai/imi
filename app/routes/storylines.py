"""Storylines and activity REST API (``app/services/storylines.py``).

    GET   /api/storylines                      — list, with evidence counts per window
    POST  /api/storylines                      — propose one (status: proposed)
    GET   /api/storylines/{id}                 — one, with recent evidence; ?as_of= for past state
    GET   /api/storylines/{id}/evidence        — evidence by occurred_at (?since=&until=&stance=)
    GET   /api/storylines/{id}/history         — state versions and status transitions
    PATCH /api/storylines/{id}                 — reviewer fields only
    GET   /api/activity?type=topic&window=14   — per-entity evidence, last window vs prior

Status and seed status are never request fields: the server sets them (seed
file, lifecycle). Every read is one Neo4j aggregate query.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.services import storylines
from app.services.storylines import StorylineError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["storylines"])


def _actor(label: str | None) -> str:
    cleaned = (label or "").strip()[:80]
    return f"rest:{cleaned}" if cleaned else "rest"


class StorylineUpdate(BaseModel):
    # extra="allow" so a server-owned field is refused with a clear 422 by the
    # service rather than silently dropped.
    model_config = ConfigDict(extra="allow")

    state: str | None = None
    direction: str | None = None
    confidence: str | None = None
    would_change: str | None = None
    open_question: str | None = None
    actor: str | None = Field(None, description="Who is writing (audit label)")


class StorylineProposal(BaseModel):
    name: str
    thesis: str | None = None
    description: str | None = None
    open_question: str | None = None
    would_change: str | None = None
    stakes: list[str] | None = None
    actor: str | None = Field(None, description="Who is proposing (audit label)")


async def _call(coro) -> Any:
    try:
        return await coro
    except StorylineError as e:
        message = str(e)
        raise HTTPException(status_code=404 if message.startswith("no storyline") else 422,
                            detail=message) from e


@router.get("/storylines")
async def list_storylines(
    status: str | None = Query(None, description="proposed | active | dormant | closed"),
    window: int = Query(14, ge=1, le=storylines.MAX_WINDOW_DAYS, description="Days per count window"),
    include_retired: bool = Query(False, description="Include storylines removed from the seed file"),
):
    return await _call(storylines.list_storylines(status, window, include_retired=include_retired))


@router.post("/storylines", status_code=201)
async def propose_storyline(body: StorylineProposal):
    return await _call(storylines.propose_storyline(
        body.name, thesis=body.thesis, description=body.description,
        open_question=body.open_question, would_change=body.would_change, stakes=body.stakes,
        actor=_actor(body.actor),
    ))


@router.get("/storylines/{storyline_id}")
async def get_storyline(
    storyline_id: str,
    as_of: str | None = Query(None, description="ISO-8601: review fields as they stood then"),
    evidence: int = Query(10, ge=0, le=storylines.MAX_EVIDENCE, description="Recent evidence items"),
    window: int = Query(14, ge=1, le=storylines.MAX_WINDOW_DAYS),
):
    return await _call(storylines.get_storyline(storyline_id, as_of=as_of, evidence=evidence,
                                                window_days=window))


@router.get("/storylines/{storyline_id}/evidence")
async def storyline_evidence(
    storyline_id: str,
    since: str | None = Query(None, description="ISO-8601, inclusive, by occurred_at"),
    until: str | None = Query(None, description="ISO-8601, exclusive, by occurred_at"),
    stance: str | None = Query(None, description="supports | challenges | context"),
    max_results: int = Query(50, ge=1, le=storylines.MAX_EVIDENCE),
):
    return await _call(storylines.storyline_evidence(storyline_id, since=since, until=until,
                                                     stance=stance, max_results=max_results))


@router.get("/storylines/{storyline_id}/history")
async def storyline_history(storyline_id: str, max_results: int = Query(100, ge=1, le=1000)):
    return await _call(storylines.storyline_history(storyline_id, max_results=max_results))


@router.patch("/storylines/{storyline_id}")
async def update_storyline(storyline_id: str, body: StorylineUpdate):
    fields = body.model_dump(exclude_unset=True)
    actor = _actor(fields.pop("actor", None))
    return await _call(storylines.update_storyline(storyline_id, fields, actor=actor))


@router.get("/activity")
async def activity(
    type: str = Query(..., description="Entity type, e.g. topic, technology, organization, storyline"),
    window: int = Query(14, ge=1, le=storylines.MAX_WINDOW_DAYS, description="Days per count window"),
    max_results: int = Query(100, ge=1, le=1000),
):
    return await _call(storylines.activity(type, window, max_results=max_results))
