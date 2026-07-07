"""Agent memory REST API — writeback, recall, traces (OB1 absorption P2+P3).

Externally visible paths (all under /api/agent-memory):
    POST /api/agent-memory/writeback                  — persist a typed memory batch
    POST /api/agent-memory/recall                     — unified governed recall
    POST /api/agent-memory/recall/{request_id}/usage  — used/ignored feedback
    GET  /api/agent-memory/recall-traces              — list recent traces
    GET  /api/agent-memory/recall-traces/{request_id} — one trace with items
    GET  /api/agent-memory/memories                   — filtered list (newest first)
    GET  /api/agent-memory/memories/{id}              — single record

The writeback schema (imi.memory.writeback.v1) clamps provenance to
observed/inferred/generated at validation time — an agent surface can never
mint instruction-grade memory (ADR-002). Unsafe content (unsafe_reasons gate)
rejects the whole batch with 422.

Review endpoints live in memories_review.py (/api/memories) because they span
record kinds (captures + agent memories).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

# Imported into module namespace so tests can monkeypatch (decisions.py pattern).
from app.services.agent_memory_store import AgentMemoryStore
from app.services.memory_recall import RecallRequest, recall
from app.services.memory_writeback import WritebackRequest, writeback
from app.services.recall_trace_store import (
    apply_usage,
    get_trace_with_items,
    list_traces,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-memory", tags=["agent-memory"])


def _session_factory():
    """Async session factory for the memory-ops tables (lazy, monkeypatchable)."""
    from app.database import create_database_session, get_database_config

    return create_database_session(get_database_config())


class MemoryListResponse(BaseModel):
    memories: list[dict] = Field(default_factory=list)
    total: int


class UsageRequest(BaseModel):
    used_memory_ids: list[str] = Field(default_factory=list)
    ignored: list[dict] = Field(
        default_factory=list, description="[{memory_id, reason}]"
    )


@router.post("/writeback")
async def post_writeback(body: WritebackRequest):
    """Persist a writeback batch (atomic on safety, idempotent on replay)."""
    result = await writeback(body)
    if not result.get("success"):
        if "rejected" in result:
            raise HTTPException(status_code=422, detail=result)
        raise HTTPException(
            status_code=400, detail=result.get("error", "writeback failed")
        )
    return result


@router.post("/recall")
async def post_recall(body: RecallRequest):
    """Unified governed recall over signals + captures + agent memories."""
    return await recall(body)


@router.post("/recall/{request_id}/usage")
async def post_recall_usage(request_id: str, body: UsageRequest):
    """Report which recalled memories were used/ignored (closes the loop)."""
    factory = _session_factory()
    async with factory() as session:
        trace = await get_trace_with_items(session, request_id)
        if trace is None:
            raise HTTPException(
                status_code=404, detail=f"Recall trace {request_id!r} not found"
            )
        updated = await apply_usage(
            session,
            request_id,
            used_memory_ids=body.used_memory_ids,
            ignored=body.ignored,
        )
        await session.commit()
    return {"request_id": request_id, "updated": updated}


@router.get("/recall-traces")
async def get_recall_traces(
    task_id: str | None = Query(None),
    surface: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """List recent recall traces (without items), newest first."""
    factory = _session_factory()
    async with factory() as session:
        traces = await list_traces(
            session, task_id=task_id, surface=surface, limit=limit
        )
    return {"traces": traces, "total": len(traces)}


@router.get("/recall-traces/{request_id}")
async def get_recall_trace(request_id: str):
    """Debug a recall: what was asked, returned, and (if reported) used."""
    factory = _session_factory()
    async with factory() as session:
        trace = await get_trace_with_items(session, request_id)
    if trace is None:
        raise HTTPException(
            status_code=404, detail=f"Recall trace {request_id!r} not found"
        )
    return trace


@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    memory_type: str | None = Query(None),
    review_status: str | None = Query(None),
    runtime_name: str | None = Query(None),
    task_id_prefix: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """List agent memories, newest first. ``total`` is the full match count."""
    store = AgentMemoryStore()
    records = store.list(
        memory_type=memory_type,
        review_status=review_status,
        runtime_name=runtime_name,
        task_id_prefix=task_id_prefix,
        limit=limit,
    )
    memories = [m.model_dump() for m in records]
    total = store.count(
        memory_type=memory_type,
        review_status=review_status,
        runtime_name=runtime_name,
        task_id_prefix=task_id_prefix,
    )
    return MemoryListResponse(memories=memories, total=total)


@router.get("/memories/{memory_id}")
async def get_memory(memory_id: str):
    """Single agent memory record (source refs + artifacts embedded)."""
    memory = AgentMemoryStore().get(memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail=f"Memory {memory_id!r} not found")
    return memory.model_dump()
