"""Decisions REST API — Issue #954, Tasks 5 + 8 + 9.

Externally visible paths (all under /api/decisions):
    GET  /api/decisions/stats               — aggregated stats headline
    GET  /api/decisions/constitution        — download constitution.md (404 before export)
    POST /api/decisions/constitution/export — render + commit constitution artifact
    POST /api/decisions/audit/export        — render + commit decision audit artifact (R4.2)
    GET  /api/decisions                     — filtered, paged list
    GET  /api/decisions/{decision_id}       — single decision with lineage + audit
    POST /api/decisions/{decision_id}/review — human review (confirm/reject/evidence_only)

Declaration order is critical: /stats, /constitution, and /audit/export must come
before /{decision_id} so FastAPI does not swallow them as path-parameter captures.

Prefix convention: routers self-prefix with /api (mirrors signal_feed.py which
uses prefix="/api/signals"); main.py calls include_router with NO extra prefix.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.git_ops import git_ops

# Import service functions into module namespace so tests can monkeypatch them
# without having to reach into app.services.decision_view directly.
from app.services.chat_tools import update_signal
from app.services.constitution import (
    CONSTITUTION_RELATIVE_PATH,
    export_constitution,
)
from app.services.decision_audit_artifact import export_decision_audit
from app.services.decision_view import (
    compute_decision_stats,
    get_decision,
    list_decisions,
)
from app.services.staleness_evaluator import run_staleness_evaluation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class GovernanceLadder(BaseModel):
    position: str = Field(..., description="'instruction' | 'evidence' | 'blocked'")
    provenance_status: str
    review_status: str
    can_use_as_evidence: bool
    can_use_as_instruction: bool


class DecisionResponse(BaseModel):
    id: str
    content: str
    state: str
    state_reason: str
    age_days: int | None = None
    review_status: str
    provenance_status: str
    can_use_as_evidence: bool
    can_use_as_instruction: bool
    owner: str | None = None
    owner_id: str | None = None
    client_id: str | None = None
    source_meeting_id: str
    source_meeting_title: str | None = None
    source_timestamp: str
    superseded_by: str | None = None
    tenant_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class DecisionListResponse(BaseModel):
    decisions: list[DecisionResponse]
    total: int
    counts_by_state: dict[str, int]


class LineageEntry(BaseModel):
    id: str
    content: str
    state: str
    source_timestamp: str
    relation: str


class AuditEntry(BaseModel):
    action: str
    gate_response: str | None = None
    actor: str | None = None
    reasoning: str
    created_at: str


class DecisionDetailResponse(DecisionResponse):
    lineage: list[LineageEntry] = Field(default_factory=list)
    audit_history: list[AuditEntry] = Field(default_factory=list)
    governance_ladder: GovernanceLadder


class DecisionStatsResponse(BaseModel):
    meetings: int
    decisions: int
    counts_by_state: dict[str, int]
    stale: int
    superseded: int
    headline: str


class ConstitutionExportResponse(BaseModel):
    path: str
    committed: bool
    counts_by_state: dict[str, int]


class AuditExportResponse(BaseModel):
    path: str
    committed: bool
    headline: str


class StateTransitionEntry(BaseModel):
    signal_id: str
    from_state: str = Field(..., alias="from")
    to_state: str = Field(..., alias="to")
    reason: str
    at: str

    model_config = {"populate_by_name": True}


class StalenessEvaluateResponse(BaseModel):
    evaluated: int
    transitions: list[StateTransitionEntry]
    committed: bool
    first_run: bool


# ---------------------------------------------------------------------------
# Endpoints — ORDER IS CRITICAL (stats, constitution, audit/export,
# staleness/evaluate must all come before /{id})
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=DecisionStatsResponse)
async def get_decision_stats():
    """Aggregated decision statistics across all meetings."""
    stats = compute_decision_stats()
    return DecisionStatsResponse(**stats)


def _read_file(path: str) -> str:
    """Sync helper: read a file and return its text content."""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@router.get("/constitution")
async def get_constitution():
    """Download the constitution artifact as text/markdown.

    Returns 404 if the constitution has not been exported yet (POST /constitution/export first).
    """
    full_path = os.path.join(git_ops.repo_path, CONSTITUTION_RELATIVE_PATH)
    if not os.path.isfile(full_path):
        raise HTTPException(
            status_code=404,
            detail="Constitution not yet exported — POST /api/decisions/constitution/export first",
        )
    content = await asyncio.to_thread(_read_file, full_path)
    return PlainTextResponse(content=content, media_type="text/markdown; charset=utf-8")


@router.post("/constitution/export", response_model=ConstitutionExportResponse)
async def post_constitution_export():
    """Render and commit the constitution artifact.

    Loads all decision signals, filters to active/stale/superseded states, and
    writes ``constitution/constitution.md`` to the repo. Attempts to commit and
    push via git_ops; returns ``committed: false`` if git fails (file is always
    written).
    """
    result = await export_constitution(commit=True)
    return ConstitutionExportResponse(**result)


@router.post("/audit/export", response_model=AuditExportResponse)
async def post_audit_export():
    """Render and commit the decision audit artifact (R4.2).

    Computes decision stats across all meetings and writes a dated Markdown
    artifact ``constitution/decision-audit-{YYYY-MM-DD}.md`` to the repo.
    Stale and superseded decisions appear as bulleted lists with source links.
    Attempts to commit and push via git_ops; returns ``committed: false`` if
    git fails (file is always written).
    """
    result = await export_decision_audit(commit=True)
    return AuditExportResponse(**result)


@router.post("/staleness/evaluate", response_model=StalenessEvaluateResponse)
async def post_staleness_evaluate():
    """Run the staleness/zombie evaluation job.

    Evaluates the lifecycle state of all decision-type signals, diffs against
    the previous state snapshot (``constitution/state-snapshot.json``), appends
    any transitions to ``constitution/state-transitions.jsonl``, and commits
    both files via git_ops.

    Returns ``first_run: true`` on the initial seed (no prior snapshot existed).
    Returns ``committed: false`` if git commit fails or there were no changes to
    commit (no transitions and not a first run).
    """
    result = await run_staleness_evaluation(commit=True)
    return StalenessEvaluateResponse(
        evaluated=result["evaluated"],
        transitions=[StateTransitionEntry(**t) for t in result["transitions"]],
        committed=result["committed"],
        first_run=result["first_run"],
    )


@router.get("", response_model=DecisionListResponse)
async def list_decisions_endpoint(
    state: str | None = Query(None, description="Lifecycle state filter"),
    owner_id: str | None = Query(None, description="Filter by owner entity slug"),
    client_id: str | None = Query(None, description="Filter by client ID"),
    date_from: str | None = Query(
        None, description="ISO timestamp lower bound (inclusive)"
    ),
    date_to: str | None = Query(
        None, description="ISO timestamp upper bound (inclusive)"
    ),
    limit: int = Query(50, ge=1, le=500, description="Max decisions to return"),
):
    """Return a filtered, paged list of decisions.

    ``total`` and ``counts_by_state`` reflect the full matching set before
    truncation; only ``decisions`` is limited.
    """
    try:
        result = list_decisions(
            state=state,
            owner_id=owner_id,
            client_id=client_id,
            date_from=date_from,
            date_to=date_to,
            max_results=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return DecisionListResponse(
        decisions=[DecisionResponse(**d) for d in result["decisions"]],
        total=result["total"],
        counts_by_state=result["counts_by_state"],
    )


@router.get("/{decision_id}", response_model=DecisionDetailResponse)
async def get_decision_endpoint(decision_id: str):
    """Get a single decision enriched with lineage, audit history, and governance ladder."""
    result = get_decision(decision_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Decision {decision_id!r} not found"
        )

    # Coerce nested structures to their Pydantic models
    lineage = [LineageEntry(**entry) for entry in result.get("lineage", [])]
    audit_history = [AuditEntry(**row) for row in result.get("audit_history", [])]
    governance_ladder = GovernanceLadder(**result["governance_ladder"])

    return DecisionDetailResponse(
        **{
            k: v
            for k, v in result.items()
            if k not in ("lineage", "audit_history", "governance_ladder")
        },
        lineage=lineage,
        audit_history=audit_history,
        governance_ladder=governance_ladder,
    )


class ReviewRequest(BaseModel):
    action: Literal["confirm", "reject", "evidence_only"]
    actor: str | None = None


@router.post("/{decision_id}/review")
async def review_decision(decision_id: str, body: ReviewRequest):
    """Apply a human review action to a candidate decision.

    Delegates to ``chat_tools.update_signal`` — the governance chokepoint that
    composes ``apply_review`` + audit record, persists to the SignalStore, and
    syncs the graph. ``supersede`` and ``dispute`` are deliberately rejected at
    the schema layer: supersession has its own candidate flow (with a required
    successor id) and dispute belongs to the conflicts flow.
    """
    # Pre-check: the id must resolve to an existing *decision*. update_signal
    # accepts any signal type, so without this a non-decision signal could be
    # reviewed through a /decisions URL.
    if get_decision(decision_id) is None:
        raise HTTPException(
            status_code=404, detail=f"Decision {decision_id!r} not found"
        )

    result = await update_signal(
        decision_id, review_action=body.action, actor=body.actor
    )
    if not result.get("success"):
        # With existence pre-checked, remaining failures are validation errors
        # (mutual-exclusion guard or apply_review ValueError) — user-correctable.
        error = result.get("error", "unknown error")
        status_code = 404 if "not found" in error.lower() else 400
        raise HTTPException(status_code=status_code, detail=error)

    new_state = None
    try:
        detail = get_decision(decision_id)
        if detail:
            new_state = detail.get("state")
    except Exception:  # state echo is best-effort; the transition succeeded
        logger.warning(
            "[DECISIONS] post-review state lookup failed for %s", decision_id
        )

    return {
        "reviewed": True,
        "decision_id": decision_id,
        "action": body.action,
        "new_state": new_state,
    }
