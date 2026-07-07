"""Async CRUD over recall traces / usage feedback / memory events (Phase 3).

All functions take an AsyncSession and do NOT commit — the caller owns the
transaction boundary (route handlers and memory_recall commit; tests inspect
mid-transaction state freely).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.user_models.memory_ops_models import (
    MemoryEvent,
    MemoryRecallItem,
    MemoryRecallTrace,
)

logger = logging.getLogger(__name__)


async def record_recall(
    session: AsyncSession,
    *,
    request_id: str,
    query: str,
    authority: str,
    surface: str,
    schema_version: str,
    runtime_name: str | None = None,
    runtime_version: str | None = None,
    task_id: str | None = None,
    flow_id: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
    scope: dict | None = None,
    response_policy: dict | None = None,
    items: list[dict[str, Any]] | None = None,
) -> MemoryRecallTrace:
    """Persist a recall trace with its per-item ranking snapshots."""
    trace = MemoryRecallTrace(
        request_id=request_id,
        query=query,
        authority=authority,
        surface=surface,
        schema_version=schema_version,
        runtime_name=runtime_name,
        runtime_version=runtime_version,
        task_id=task_id,
        flow_id=flow_id,
        workspace_id=workspace_id,
        project_id=project_id,
        request_scope=scope or {},
        response_policy=response_policy or {},
    )
    session.add(trace)
    await session.flush()  # assign trace.id (and surface UNIQUE violations)

    for item in items or []:
        session.add(
            MemoryRecallItem(
                trace_id=trace.id,
                record_id=item["record_id"],
                record_kind=item["record_kind"],
                rank=item["rank"],
                similarity=item.get("similarity"),
                ranking_score=item.get("ranking_score"),
                use_policy_snapshot=item.get("use_policy_snapshot", {}),
            )
        )
    await session.flush()

    session.add(
        MemoryEvent(
            event_type="recall_requested",
            trace_id=request_id,
            runtime_name=runtime_name,
            task_id=task_id,
            payload={"query": query, "returned": len(items or [])},
        )
    )
    await session.flush()
    return trace


def _trace_dict(trace: MemoryRecallTrace, items: list[MemoryRecallItem]) -> dict:
    return {
        "request_id": trace.request_id,
        "query": trace.query,
        "authority": trace.authority,
        "surface": trace.surface,
        "schema_version": trace.schema_version,
        "runtime_name": trace.runtime_name,
        "runtime_version": trace.runtime_version,
        "task_id": trace.task_id,
        "flow_id": trace.flow_id,
        "tenant_id": trace.tenant_id,
        "request_scope": trace.request_scope,
        "response_policy": trace.response_policy,
        "created_at": trace.created_at.isoformat() if trace.created_at else None,
        "items": [
            {
                "record_id": item.record_id,
                "record_kind": item.record_kind,
                "rank": item.rank,
                "similarity": item.similarity,
                "ranking_score": item.ranking_score,
                "returned": item.returned,
                "used": item.used,
                "used_as": item.used_as,
                "ignored_reason": item.ignored_reason,
                "use_policy_snapshot": item.use_policy_snapshot,
            }
            for item in items
        ],
    }


async def get_trace_with_items(
    session: AsyncSession, request_id: str
) -> dict | None:
    """Load a trace + items (rank order) as a plain dict, or None."""
    trace = (
        await session.execute(
            select(MemoryRecallTrace).where(
                MemoryRecallTrace.request_id == request_id
            )
        )
    ).scalar_one_or_none()
    if trace is None:
        return None
    items = (
        (
            await session.execute(
                select(MemoryRecallItem)
                .where(MemoryRecallItem.trace_id == trace.id)
                .order_by(MemoryRecallItem.rank)
            )
        )
        .scalars()
        .all()
    )
    return _trace_dict(trace, list(items))


async def apply_usage(
    session: AsyncSession,
    request_id: str,
    used_memory_ids: list[str] | None = None,
    ignored: list[dict] | None = None,
) -> int:
    """Record which recalled memories were used/ignored. Returns rows touched."""
    trace = (
        await session.execute(
            select(MemoryRecallTrace).where(
                MemoryRecallTrace.request_id == request_id
            )
        )
    ).scalar_one_or_none()
    if trace is None:
        return 0

    touched = 0
    for record_id in used_memory_ids or []:
        result = await session.execute(
            update(MemoryRecallItem)
            .where(
                MemoryRecallItem.trace_id == trace.id,
                MemoryRecallItem.record_id == record_id,
            )
            .values(used=True)
        )
        if result.rowcount:
            touched += result.rowcount
            session.add(
                MemoryEvent(
                    event_type="memory_used",
                    record_id=record_id,
                    trace_id=request_id,
                    runtime_name=trace.runtime_name,
                    task_id=trace.task_id,
                )
            )

    for entry in ignored or []:
        record_id = entry.get("memory_id")
        result = await session.execute(
            update(MemoryRecallItem)
            .where(
                MemoryRecallItem.trace_id == trace.id,
                MemoryRecallItem.record_id == record_id,
            )
            .values(used=False, ignored_reason=entry.get("reason"))
        )
        if result.rowcount:
            touched += result.rowcount
            session.add(
                MemoryEvent(
                    event_type="memory_ignored",
                    record_id=record_id,
                    trace_id=request_id,
                    runtime_name=trace.runtime_name,
                    task_id=trace.task_id,
                    payload={"reason": entry.get("reason")},
                )
            )

    await session.flush()
    return touched


async def usage_stats(session: AsyncSession, record_id: str) -> dict:
    """Aggregate recall usage for one record across all traces."""
    items = (
        (
            await session.execute(
                select(MemoryRecallItem).where(
                    MemoryRecallItem.record_id == record_id
                )
            )
        )
        .scalars()
        .all()
    )
    last_returned = max((i.created_at for i in items), default=None)
    return {
        "times_returned": len(items),
        "times_used": sum(1 for i in items if i.used is True),
        "times_ignored": sum(1 for i in items if i.used is False),
        "last_returned_at": last_returned.isoformat() if last_returned else None,
    }


async def list_traces(
    session: AsyncSession,
    *,
    task_id: str | None = None,
    surface: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List recent traces (without items), newest first."""
    query = select(MemoryRecallTrace).order_by(MemoryRecallTrace.created_at.desc())
    if task_id:
        query = query.where(MemoryRecallTrace.task_id == task_id)
    if surface:
        query = query.where(MemoryRecallTrace.surface == surface)
    traces = (await session.execute(query.limit(limit))).scalars().all()
    return [_trace_dict(trace, []) | {"items": None} for trace in traces]


async def log_event(
    session: AsyncSession,
    event_type: str,
    *,
    record_id: str | None = None,
    record_kind: str | None = None,
    trace_id: str | None = None,
    actor_kind: str = "system",
    actor_label: str | None = None,
    runtime_name: str | None = None,
    task_id: str | None = None,
    payload: dict | None = None,
) -> MemoryEvent:
    """Append one operational event."""
    event = MemoryEvent(
        event_type=event_type,
        record_id=record_id,
        record_kind=record_kind,
        trace_id=trace_id,
        actor_kind=actor_kind,
        actor_label=actor_label,
        runtime_name=runtime_name,
        task_id=task_id,
        payload=payload or {},
    )
    session.add(event)
    await session.flush()
    return event
