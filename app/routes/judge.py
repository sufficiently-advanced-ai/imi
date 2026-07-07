"""Judge REST API — the action-judgment surface (OB1 absorption Phase 4).

Externally visible paths (all under /api/judge):
    POST /api/judge/recall           — policy-aware recall before a decision
    POST /api/judge/decisions        — idempotent judgment write-back
    GET  /api/judge/decisions        — list judgment events (filters)
    GET  /api/judge/decisions/{id}   — single judgment event

URL style follows imi convention (/api/*, self-prefixed router); versioning
rides in the schema_version strings (imi.judge.*.v1), not the URL — a
deliberate deviation from OB1's /v1/ paths.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.judge import JudgeDecisionRequest, JudgeRecallRequest

# Imported into module namespace so tests can monkeypatch.
from app.services.judge_service import (
    get_judge_decision,
    judge_decide,
    judge_recall,
    list_judge_decisions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/judge", tags=["judge"])


class JudgeDecisionListResponse(BaseModel):
    decisions: list[dict] = Field(default_factory=list)
    total: int


@router.post("/recall")
async def post_judge_recall(body: JudgeRecallRequest):
    """Scoped, policy-aware recall: evidence memories + instruction-grade policy hits."""
    return await judge_recall(body)


@router.post("/decisions")
async def post_judge_decision(body: JudgeDecisionRequest):
    """Record a judge outcome (idempotent on action_id)."""
    result = await judge_decide(body)
    if not result.get("success"):
        raise HTTPException(
            status_code=400, detail=result.get("error", "judge decision failed")
        )
    return result


@router.get("/decisions")
async def get_judge_decisions(
    task_id: str | None = Query(None),
    decision: str | None = Query(None, description="allow|block|revise|escalate"),
    limit: int = Query(50, ge=1, le=500),
):
    """List judgment events, newest first."""
    decisions = await list_judge_decisions(
        task_id=task_id, decision=decision, limit=limit
    )
    return JudgeDecisionListResponse(decisions=decisions, total=len(decisions))


@router.get("/decisions/{decision_id}")
async def get_judge_decision_endpoint(decision_id: str):
    """Single judgment event."""
    result = await get_judge_decision(decision_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Judge decision {decision_id!r} not found"
        )
    return result
