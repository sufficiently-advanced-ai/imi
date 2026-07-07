"""Supersession Candidates REST API — R2.4 + R4.4.

Externally visible paths (all under /api/supersession):
    GET  /api/supersession/candidates              — list pending candidates
    POST /api/supersession/candidates/confirm      — confirm: apply governance + flip status
    POST /api/supersession/candidates/dismiss      — dismiss: flip status only, no governance

A "candidate" is a pending supersession relationship that was auto-detected at
ingest time and stored in ``signal.metadata["supersession_candidates"]``.  Each
candidate entry carries ``status`` which starts as "pending" and transitions to
"confirmed" or "dismissed" via the two mutation endpoints.

Prefix convention: router self-prefixes with /api (mirrors decisions.py).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Import update_signal at module level so tests can monkeypatch it.
from app.services.chat_tools import update_signal
from app.services.signal_store import signal_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/supersession", tags=["supersession"])


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class CandidateItem(BaseModel):
    """A flattened supersession candidate as returned by GET /candidates."""

    new_signal_id: str
    new_content: str
    old_signal_id: str
    old_content: str
    matched_entities: list[str]
    reason: str
    confidence: float
    proposed_at: str


class ConfirmRequest(BaseModel):
    new_signal_id: str
    old_signal_id: str
    actor: str | None = None


class DismissRequest(BaseModel):
    new_signal_id: str
    old_signal_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_pending_candidates() -> list[CandidateItem]:
    """Scan the signal store and return all *pending* supersession candidates."""
    items: list[CandidateItem] = []
    for batch in signal_store.load_all():
        for sig in batch.signals:
            candidates = sig.metadata.get("supersession_candidates")
            if not candidates:
                continue
            for cand in candidates:
                if cand.get("status") != "pending":
                    continue
                items.append(
                    CandidateItem(
                        new_signal_id=sig.id,
                        new_content=sig.content,
                        old_signal_id=cand["old_signal_id"],
                        old_content=cand["old_content"],
                        matched_entities=cand.get("matched_entities", []),
                        reason=cand.get("reason", ""),
                        confidence=cand.get("confidence", 0.0),
                        proposed_at=cand.get("proposed_at", ""),
                    )
                )
    return items


def _find_candidate(
    new_signal_id: str,
    old_signal_id: str,
):
    """Locate the new signal + its candidate dict in the store.

    Returns (new_signal, container, candidate_dict) or raises HTTPException.
    """
    lookup = signal_store.find_signal_by_id(new_signal_id)
    if lookup is None:
        raise HTTPException(
            status_code=404,
            detail=f"Signal '{new_signal_id}' not found",
        )
    new_signal, container = lookup

    candidates = new_signal.metadata.get("supersession_candidates", [])
    match = next(
        (c for c in candidates if c.get("old_signal_id") == old_signal_id),
        None,
    )
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No supersession candidate linking new={new_signal_id!r} "
                f"to old={old_signal_id!r}"
            ),
        )

    # Verify the old signal exists too
    if signal_store.find_signal_by_id(old_signal_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Old signal '{old_signal_id}' not found",
        )

    return new_signal, container, match


def _assert_pending(candidate: dict) -> None:
    """Raise 409 if the candidate is not in 'pending' status.

    Prevents /confirm from re-running governance on already-confirmed pairs
    and /dismiss from overwriting confirmed pairs.
    """
    current_status = candidate.get("status", "pending")
    if current_status != "pending":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Candidate is already '{current_status}'; "
                "only 'pending' candidates may be actioned"
            ),
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/candidates", response_model=list[CandidateItem])
async def list_candidates():
    """Return all pending supersession candidates across the signal store.

    Only candidates whose ``status == "pending"`` are included.
    Confirmed and dismissed candidates are omitted.
    """
    return _iter_pending_candidates()


@router.post("/candidates/confirm")
async def confirm_candidate(body: ConfirmRequest):
    """Confirm a supersession candidate.

    Applies the governance transition (``review_action="supersede"``) to the
    old signal via ``chat_tools.update_signal``, then flips the candidate's
    status on the *new* signal's metadata to ``"confirmed"``.

    Returns 404 if either signal ID is unknown or no matching candidate exists.
    """
    new_signal, container, candidate = _find_candidate(
        body.new_signal_id, body.old_signal_id
    )
    _assert_pending(candidate)

    # Apply governance transition to the old signal
    result = await update_signal(
        body.old_signal_id,
        review_action="supersede",
        superseded_by=body.new_signal_id,
        actor=body.actor,
    )

    if not result.get("success"):
        logger.error(
            "[SUPERSESSION] confirm failed for old=%s new=%s: %s",
            body.old_signal_id,
            body.new_signal_id,
            result.get("error"),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Governance transition failed: {result.get('error')}",
        )

    # Flip candidate status to "confirmed" on the new signal
    candidate["status"] = "confirmed"
    signal_store.replace_signal(new_signal, container)

    logger.info(
        "[SUPERSESSION] Confirmed: old=%s superseded_by new=%s actor=%s",
        body.old_signal_id,
        body.new_signal_id,
        body.actor,
    )
    return {
        "confirmed": True,
        "new_signal_id": body.new_signal_id,
        "old_signal_id": body.old_signal_id,
    }


@router.post("/candidates/dismiss")
async def dismiss_candidate(body: DismissRequest):
    """Dismiss a supersession candidate without applying governance.

    Flips ``status`` to ``"dismissed"`` on the candidate entry in the new
    signal's metadata.  The old signal is **not** touched (no governance
    transition, no audit record).

    Returns 404 if either signal ID is unknown or no matching candidate exists.
    """
    new_signal, container, candidate = _find_candidate(
        body.new_signal_id, body.old_signal_id
    )
    _assert_pending(candidate)

    # Flip candidate status only — never call update_signal / apply_review
    candidate["status"] = "dismissed"
    signal_store.replace_signal(new_signal, container)

    logger.info(
        "[SUPERSESSION] Dismissed: old=%s new=%s",
        body.old_signal_id,
        body.new_signal_id,
    )
    return {
        "dismissed": True,
        "new_signal_id": body.new_signal_id,
        "old_signal_id": body.old_signal_id,
    }
