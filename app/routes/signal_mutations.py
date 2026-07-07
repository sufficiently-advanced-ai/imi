"""
Signal Mutation Routes — HTTP endpoints for changing signal state.

Complements the read-only `signal_feed.py`. All writes delegate to
`chat_tools.update_signal`, which handles JSON persistence (source of truth),
git commit, and Neo4j sync — keeping the HTTP and MCP paths consistent.

Authentication: these endpoints are protected by `AuthenticationMiddleware`
(registered globally in `app/main.py`), which enforces session auth
on every non-public path. No per-route `Depends(get_current_user)` is
required — adding one would double-check the session.

Route order matters: static `/bulk/*` paths are declared before the
parameterized `/{signal_id}/*` handlers so FastAPI doesn't shadow them.

POST /api/signals/bulk/close         — bulk status=done
POST /api/signals/bulk/status        — bulk arbitrary status transition
POST /api/signals/{id}/close         — set status to "done"
POST /api/signals/{id}/reopen        — set status to "open"
POST /api/signals/{id}/in-progress   — set status to "in_progress"
POST /api/signals/{id}/update        — partial update (status/content/owner/due_date)
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.chat_tools import update_signal as _update_signal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signals", tags=["signals"])


class SignalUpdateRequest(BaseModel):
    status: str | None = Field(None, description="open, in_progress, or done")
    content: str | None = Field(None, description="New content text")
    owner_id: str | None = Field(None, description="Entity slug id of the owner")
    due_date: str | None = Field(None, description="YYYY-MM-DD")


class BulkSignalStatusRequest(BaseModel):
    signal_ids: list[str] = Field(..., min_length=1)
    status: str = Field(..., description="Target status: open, in_progress, done")


class BulkSignalCloseRequest(BaseModel):
    signal_ids: list[str] = Field(..., min_length=1)


class BulkSignalResult(BaseModel):
    signal_id: str
    success: bool
    error: str | None = None


class BulkSignalResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[BulkSignalResult]


_VALIDATION_HINTS = ("invalid", "must be", "provide at least")


def _classify_error(err: str) -> int:
    """Map a dict-result error string to an HTTP status code.

    chat_tools.update_signal catches `ValueError` (validation) and bare
    `Exception` (server) and flattens both into `error: str`, so we can't
    distinguish them structurally. This heuristic inspects the known
    validation messages produced by SignalStore and chat_tools.
    """
    lower = err.lower()
    if "not found" in lower:
        return 404
    if any(hint in lower for hint in _VALIDATION_HINTS):
        return 400
    return 500


def _result_to_http(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("success"):
        return result
    err = result.get("error", "unknown error")
    raise HTTPException(status_code=_classify_error(err), detail=err)


# ---------------------------------------------------------------------------
# Bulk routes FIRST so FastAPI doesn't match /bulk/* as /{signal_id}/*
# ---------------------------------------------------------------------------


@router.post("/bulk/close", response_model=BulkSignalResponse)
async def bulk_close_signals(body: BulkSignalCloseRequest) -> BulkSignalResponse:
    """Close multiple signals in one call.

    Processes sequentially; each update commits individually (matching the
    existing single-update path). Partial success is allowed — per-signal
    outcomes are returned.
    """
    return await _bulk_status_transition(body.signal_ids, "done")


@router.post("/bulk/status", response_model=BulkSignalResponse)
async def bulk_set_signal_status(
    body: BulkSignalStatusRequest,
) -> BulkSignalResponse:
    return await _bulk_status_transition(body.signal_ids, body.status)


# ---------------------------------------------------------------------------
# Per-signal routes
# ---------------------------------------------------------------------------


@router.post("/{signal_id}/close")
async def close_signal(signal_id: str) -> dict[str, Any]:
    return _result_to_http(await _update_signal(signal_id, status="done"))


@router.post("/{signal_id}/reopen")
async def reopen_signal(signal_id: str) -> dict[str, Any]:
    return _result_to_http(await _update_signal(signal_id, status="open"))


@router.post("/{signal_id}/in-progress")
async def mark_signal_in_progress(signal_id: str) -> dict[str, Any]:
    return _result_to_http(await _update_signal(signal_id, status="in_progress"))


@router.post("/{signal_id}/update")
async def update_signal_partial(
    signal_id: str, body: SignalUpdateRequest
) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(
            status_code=400, detail="Provide at least one field to update"
        )
    return _result_to_http(await _update_signal(signal_id, **payload))


async def _bulk_status_transition(
    signal_ids: list[str], status: str
) -> BulkSignalResponse:
    # Dedupe while preserving order — chat_tools.update_signal commits per
    # call, so the same ID twice would rewrite and re-commit a signal and
    # double-count totals.
    signal_ids = list(dict.fromkeys(signal_ids))
    results: list[BulkSignalResult] = []
    for sid in signal_ids:
        try:
            outcome = await _update_signal(sid, status=status)
        except Exception as e:
            logger.error(
                f"[BULK_SIGNAL] Unexpected failure for {sid}: {e}", exc_info=True
            )
            results.append(BulkSignalResult(signal_id=sid, success=False, error=str(e)))
            continue

        if outcome.get("success"):
            results.append(BulkSignalResult(signal_id=sid, success=True))
        else:
            results.append(
                BulkSignalResult(
                    signal_id=sid, success=False, error=outcome.get("error")
                )
            )

    succeeded = sum(1 for r in results if r.success)
    return BulkSignalResponse(
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )
