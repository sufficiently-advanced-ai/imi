"""Conflict Candidates REST API — R3.5 (Sprint 4).

Externally visible paths (all under /api/conflicts):
    GET  /api/conflicts/candidates              — list pending conflict candidates
    POST /api/conflicts/candidates/confirm      — confirm: flip both signals' conflicts_with
    POST /api/conflicts/candidates/dismiss      — dismiss: flip status only, no edge write

A "candidate" is a pending conflict relationship that was auto-detected by the
LLM conflict detector (Sprint 4, S4-1/S4-2) and stored in
``signal.metadata["conflict_candidates"]``. Each candidate entry carries
``status`` which starts as "pending" and transitions to "confirmed" or "dismissed"
via the two mutation endpoints.

IMPORTANT — governance separation (PRD R3.5):
    Confirming a conflict does NOT go through ``apply_review`` or the governance
    axis (provenance_status / review_status). The ``conflicting`` lifecycle state
    is computed from ``metadata.conflicts_with`` (a plain list of IDs), not from
    any governance field. This is intentional: conflict detection is a semantic
    annotation, orthogonal to the trust/authority ladder managed by
    signal_governance.py.

    Human confirmation (POST /confirm) simply:
      1. Appends each signal's ID to the other's ``metadata.conflicts_with`` list
         (deduplicated).
      2. Flips the candidate's ``status`` to "confirmed".
      3. Persists BOTH signals' meeting files in ONE git commit (deduplicated paths
         when both signals live in the same meeting file).
      4. Best-effort writes a CONFLICTS_WITH edge to Neo4j.

Prefix convention: router self-prefixes with /api (mirrors decisions.py,
supersession.py).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.git_ops import git_ops

# Import update_signal at module level so tests can monkeypatch it.
# NOTE: this import exists for the monkeypatch test only; confirm does NOT call it.
from app.services.chat_tools import update_signal  # noqa: F401
from app.services.signal_store import signal_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conflicts", tags=["conflicts"])


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class ConflictCandidateItem(BaseModel):
    """A flattened conflict candidate as returned by GET /candidates."""

    signal_id: str
    signal_content: str
    other_signal_id: str
    other_content: str
    rationale: str
    confidence: float
    speakers: list[str]
    proposed_at: str


class ConfirmConflictRequest(BaseModel):
    signal_id: str
    other_signal_id: str
    actor: str | None = None


class DismissConflictRequest(BaseModel):
    signal_id: str
    other_signal_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_pending_candidates() -> list[ConflictCandidateItem]:
    """Scan the signal store and return all *pending* conflict candidates."""
    items: list[ConflictCandidateItem] = []
    for batch in signal_store.load_all():
        for sig in batch.signals:
            candidates = sig.metadata.get("conflict_candidates")
            if not candidates:
                continue
            for cand in candidates:
                if cand.get("status") != "pending":
                    continue
                items.append(
                    ConflictCandidateItem(
                        signal_id=sig.id,
                        signal_content=sig.content,
                        other_signal_id=cand["other_signal_id"],
                        other_content=cand.get("other_content", ""),
                        rationale=cand.get("rationale", ""),
                        confidence=cand.get("confidence", 0.0),
                        speakers=cand.get("speakers", []),
                        proposed_at=cand.get("proposed_at", ""),
                    )
                )
    return items


def _find_conflict_candidate(signal_id: str, other_signal_id: str):
    """Locate the signal and its conflict candidate dict in the store.

    Returns (signal, container, candidate_dict) or raises HTTPException.
    Verifies:
      - signal exists
      - a conflict_candidates entry pointing at other_signal_id exists
    """
    lookup = signal_store.find_signal_by_id(signal_id)
    if lookup is None:
        raise HTTPException(
            status_code=404,
            detail=f"Signal '{signal_id}' not found",
        )
    signal, container = lookup

    candidates = signal.metadata.get("conflict_candidates", [])
    match = next(
        (c for c in candidates if c.get("other_signal_id") == other_signal_id),
        None,
    )
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No conflict candidate linking signal={signal_id!r} "
                f"to other={other_signal_id!r}"
            ),
        )

    return signal, container, match


def _assert_pending(candidate: dict) -> None:
    """Raise 409 if the candidate is not in 'pending' status."""
    current_status = candidate.get("status", "pending")
    if current_status != "pending":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Candidate is already '{current_status}'; "
                "only 'pending' candidates may be actioned"
            ),
        )


async def _write_edge_best_effort(
    signal_id: str,
    other_signal_id: str,
    *,
    actor: str | None,
    confirmed_at: str,
    tenant_id: str | None = None,
) -> None:
    """Best-effort write of CONFLICTS_WITH edge to Neo4j. Never raises."""
    try:
        from app.neo4j_client import get_neo4j_client
        from app.services.graph.signal_graph_writer import SignalGraphWriter

        client = get_neo4j_client()
        if client:
            writer = SignalGraphWriter(client)
            await writer.write_conflicts_with_edge(
                signal_id,
                other_signal_id,
                confirmed_at=confirmed_at,
                actor=actor,
                tenant_id=tenant_id,
            )
    except Exception as e:
        logger.warning("[CONFLICTS] Neo4j edge write failed (best-effort): %s", e)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/candidates", response_model=list[ConflictCandidateItem])
async def list_candidates():
    """Return all pending conflict candidates across the signal store.

    Only candidates whose ``status == "pending"`` are included.
    Confirmed and dismissed candidates are omitted.
    """
    return _iter_pending_candidates()


@router.post("/candidates/confirm")
async def confirm_candidate(body: ConfirmConflictRequest):
    """Confirm a conflict candidate.

    Appends each signal's ID to the other's ``metadata.conflicts_with`` (deduplicated),
    flips the candidate's status to "confirmed", persists BOTH signals in ONE git
    commit (deduplicating paths when both signals share a meeting file), then
    best-effort writes a CONFLICTS_WITH edge to Neo4j.

    NOTE: does NOT call apply_review or touch the governance axis (PRD R3.5).
    The ``conflicting`` lifecycle state is derived from ``metadata.conflicts_with``.

    Returns 404 if either signal ID is unknown or no matching candidate exists.
    Returns 409 if the candidate is not in 'pending' status.
    """
    sig_a, container_a, candidate = _find_conflict_candidate(
        body.signal_id, body.other_signal_id
    )
    _assert_pending(candidate)

    # Verify the other signal exists
    lookup_b = signal_store.find_signal_by_id(body.other_signal_id)
    if lookup_b is None:
        raise HTTPException(
            status_code=404,
            detail=f"Signal '{body.other_signal_id}' not found",
        )
    sig_b, container_b = lookup_b

    # 1. Append conflicts_with to both signals (deduplicated)
    cw_a: list = list(sig_a.metadata.get("conflicts_with") or [])
    if body.other_signal_id not in cw_a:
        cw_a.append(body.other_signal_id)

    cw_b: list = list(sig_b.metadata.get("conflicts_with") or [])
    if body.signal_id not in cw_b:
        cw_b.append(body.signal_id)

    # 2. Flip the candidate status to "confirmed"
    candidate["status"] = "confirmed"

    # Build updated metadata dicts (signal.metadata is read-only via Pydantic;
    # use model_copy to produce a new instance with updated metadata)
    meta_a = dict(sig_a.metadata or {})
    meta_a["conflicts_with"] = cw_a
    # preserve candidate list (already mutated in-place via the dict reference)
    new_sig_a = sig_a.model_copy(update={"metadata": meta_a})

    meta_b = dict(sig_b.metadata or {})
    meta_b["conflicts_with"] = cw_b
    new_sig_b = sig_b.model_copy(update={"metadata": meta_b})

    # 3. Persist both signals — ORDER MATTERS for crash safety.
    #
    # Write sig_b (the OTHER signal, whose conflicts_with is extended) FIRST,
    # then sig_a (the CANDIDATE-CARRYING signal, whose status is flipped to
    # "confirmed") LAST.  If the process dies between the two writes, sig_a
    # still reads as "pending", so the human can re-confirm (idempotent:
    # the conflicts_with append is deduplicated, and sig_b's write is safe
    # to repeat).  Writing sig_a last ensures the status flip is only visible
    # once both signals are durable.
    signal_store.replace_signal(new_sig_b, container_b)
    signal_store.replace_signal(new_sig_a, container_a)

    # 4. Git commit — ONE commit for BOTH meeting files (deduplicate paths)
    path_a = signal_store.relative_path(container_a.bot_id)
    path_b = signal_store.relative_path(container_b.bot_id)
    commit_paths = list(dict.fromkeys([path_a, path_b]))  # preserves order, dedupes

    committed = False
    try:
        await git_ops.commit_and_push(
            commit_paths,
            f"conflict: confirm {body.signal_id[:8]} <-> {body.other_signal_id[:8]}",
        )
        committed = True
    except Exception as e:
        logger.warning(
            "[CONFLICTS] Git commit failed (best-effort, mutation persisted): %s", e
        )

    # 5. Best-effort Neo4j edge
    confirmed_at = datetime.now(UTC).isoformat()
    await _write_edge_best_effort(
        body.signal_id,
        body.other_signal_id,
        actor=body.actor,
        confirmed_at=confirmed_at,
        tenant_id=sig_a.tenant_id or sig_b.tenant_id,
    )

    logger.info(
        "[CONFLICTS] Confirmed: %s <-> %s actor=%s committed=%s",
        body.signal_id,
        body.other_signal_id,
        body.actor,
        committed,
    )
    return {
        "confirmed": True,
        "committed": committed,
        "signal_id": body.signal_id,
        "other_signal_id": body.other_signal_id,
    }


@router.post("/candidates/dismiss")
async def dismiss_candidate(body: DismissConflictRequest):
    """Dismiss a conflict candidate without confirming it.

    Flips ``status`` to ``"dismissed"`` on the candidate entry in signal_a's
    metadata. The other signal is NOT touched (no conflicts_with added, no
    edge written, no governance transition).

    Returns 404 if the signal or candidate pair is not found.
    Returns 409 if the candidate is not in 'pending' status.
    """
    sig_a, container_a, candidate = _find_conflict_candidate(
        body.signal_id, body.other_signal_id
    )
    _assert_pending(candidate)

    # Flip status only — never touch the other signal or write an edge
    candidate["status"] = "dismissed"

    meta_a = dict(sig_a.metadata or {})
    # The candidate dict was found via .metadata["conflict_candidates"] —
    # since we mutated it in-place through the list reference, meta_a already
    # contains the updated status. But we need to model_copy to persist.
    # Re-assign to ensure the updated candidate list is captured.
    candidates_list = meta_a.get("conflict_candidates", [])
    for i, c in enumerate(candidates_list):
        if c.get("other_signal_id") == body.other_signal_id:
            candidates_list[i] = candidate
            break
    meta_a["conflict_candidates"] = candidates_list

    new_sig_a = sig_a.model_copy(update={"metadata": meta_a})
    signal_store.replace_signal(new_sig_a, container_a)

    # Commit just the one signal file
    path_a = signal_store.relative_path(container_a.bot_id)
    committed = False
    try:
        await git_ops.commit_and_push(
            [path_a],
            f"conflict: dismiss {body.signal_id[:8]} <-> {body.other_signal_id[:8]}",
        )
        committed = True
    except Exception as e:
        logger.warning("[CONFLICTS] Git commit failed for dismiss (best-effort): %s", e)

    logger.info(
        "[CONFLICTS] Dismissed: %s <-> %s committed=%s",
        body.signal_id,
        body.other_signal_id,
        committed,
    )
    return {
        "dismissed": True,
        "signal_id": body.signal_id,
        "other_signal_id": body.other_signal_id,
    }
