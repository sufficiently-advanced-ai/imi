"""Tests for recall traces / usage feedback storage (Phase 3 of the OB1 absorption).

High-churn operational data lives in SQL (SQLite default, Postgres hosted) —
never in the git corpus. Covers: trace+items persistence, the
UNIQUE(trace_id, record_id) guarantee, usage feedback flips, and the
append-only memory_events log.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.user_models.memory_ops_models import (
    MemoryEvent,
    MemoryRecallItem,
    MemoryRecallTrace,
)
from app.services.recall_trace_store import (
    apply_usage,
    get_trace_with_items,
    log_event,
    record_recall,
)


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _items():
    return [
        {
            "record_id": "sig-1",
            "record_kind": "signal",
            "rank": 0,
            "similarity": 0.9,
            "ranking_score": 1.2,
            "use_policy_snapshot": {"can_use_as_instruction": True},
        },
        {
            "record_id": "cap-1",
            "record_kind": "capture",
            "rank": 1,
            "similarity": 0.7,
            "ranking_score": 0.8,
            "use_policy_snapshot": {"can_use_as_instruction": False},
        },
    ]


@pytest.mark.asyncio
async def test_record_recall_persists_trace_and_items(maker):
    async with maker() as session:
        await record_recall(
            session,
            request_id="req-1",
            query="what framework",
            authority="evidence",
            surface="agent_recall",
            schema_version="imi.memory.recall.v1",
            runtime_name="openclaw",
            task_id="task-1",
            scope={"tenant_only": True},
            items=_items(),
        )
        await session.commit()

    async with maker() as session:
        trace = await get_trace_with_items(session, "req-1")

    assert trace is not None
    assert trace["query"] == "what framework"
    assert trace["runtime_name"] == "openclaw"
    assert [i["record_id"] for i in trace["items"]] == ["sig-1", "cap-1"]
    assert trace["items"][0]["ranking_score"] == pytest.approx(1.2)
    assert trace["items"][0]["used"] is None  # no feedback yet


@pytest.mark.asyncio
async def test_unknown_trace_returns_none(maker):
    async with maker() as session:
        assert await get_trace_with_items(session, "nope") is None


@pytest.mark.asyncio
async def test_duplicate_record_in_trace_rejected(maker):
    items = _items()
    items.append(dict(items[0]))  # same record_id twice
    async with maker() as session:
        with pytest.raises(IntegrityError):
            await record_recall(
                session,
                request_id="req-2",
                query="q",
                authority="evidence",
                surface="agent_recall",
                schema_version="imi.memory.recall.v1",
                items=items,
            )
            await session.commit()


@pytest.mark.asyncio
async def test_apply_usage_flips_items_and_logs_events(maker):
    async with maker() as session:
        await record_recall(
            session,
            request_id="req-3",
            query="q",
            authority="evidence",
            surface="agent_recall",
            schema_version="imi.memory.recall.v1",
            items=_items(),
        )
        await session.commit()

    async with maker() as session:
        updated = await apply_usage(
            session,
            "req-3",
            used_memory_ids=["sig-1"],
            ignored=[{"memory_id": "cap-1", "reason": "off-topic"}],
        )
        await session.commit()
    assert updated == 2

    async with maker() as session:
        trace = await get_trace_with_items(session, "req-3")
        by_id = {i["record_id"]: i for i in trace["items"]}
        assert by_id["sig-1"]["used"] is True
        assert by_id["cap-1"]["used"] is False
        assert by_id["cap-1"]["ignored_reason"] == "off-topic"

        events = (await session.execute(select(MemoryEvent))).scalars().all()
        event_types = {e.event_type for e in events}
        assert "memory_used" in event_types
        assert "memory_ignored" in event_types


@pytest.mark.asyncio
async def test_apply_usage_unknown_trace_returns_zero(maker):
    async with maker() as session:
        assert await apply_usage(session, "nope", used_memory_ids=["x"]) == 0


@pytest.mark.asyncio
async def test_log_event_appends(maker):
    async with maker() as session:
        await log_event(
            session,
            "memory_written",
            record_id="mem-1",
            record_kind="agent_memory",
            actor_kind="agent",
            runtime_name="openclaw",
            payload={"count": 3},
        )
        await session.commit()

    async with maker() as session:
        events = (await session.execute(select(MemoryEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "memory_written"
    assert events[0].payload == {"count": 3}
    assert events[0].tenant_id == "default"
