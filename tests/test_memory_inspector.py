"""Tests for the memory inspector (Phase 5 of the OB1 absorption).

The trust surface: for any governed record, answer OB1's inspector questions —
why does this memory exist (provenance/sources), what created it (audit
history), how was it used (recall/judge usage aggregates), and what can it
influence (authority flags + supersession lineage). A deleted record still
answers from its audit trail (the audit-survives-hard-delete guarantee made
visible).
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.agent_memory import AgentMemory
from app.services.agent_memory_store import AgentMemoryStore
from app.services.memory_capture import CaptureStore
from app.services.memory_governance import capture_audit_store, review_record_with_audit
from app.services.memory_inspector import inspect_memory
from app.services.recall_trace_store import apply_usage, record_recall


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _capture_store(tmp_path) -> CaptureStore:
    return CaptureStore(capture_dir=tmp_path / "memory" / "captures", repo_root=tmp_path)


def _memory_store(tmp_path) -> AgentMemoryStore:
    return AgentMemoryStore(agent_dir=tmp_path / "memory" / "agent", repo_root=tmp_path)


async def _seed_usage(maker, record_id, kind):
    async with maker() as session:
        await record_recall(
            session,
            request_id="req-i1",
            query="q",
            authority="evidence",
            surface="agent_recall",
            schema_version="imi.memory.recall.v1",
            items=[
                {
                    "record_id": record_id,
                    "record_kind": kind,
                    "rank": 0,
                    "similarity": 0.9,
                    "ranking_score": 1.1,
                }
            ],
        )
        await apply_usage(session, "req-i1", used_memory_ids=[record_id])
        await session.commit()


@pytest.mark.asyncio
async def test_inspector_composes_all_four_questions(maker, tmp_path):
    store = _capture_store(tmp_path)
    audit_store = capture_audit_store(repo_root=tmp_path)
    capture = store.capture("Inspect me.", source="manual").memory
    confirmed, audit_row = review_record_with_audit(
        capture, "confirm", actor="scott", record_kind="capture"
    )
    store.update(confirmed)
    audit_store.append(audit_row)
    await _seed_usage(maker, capture.id, "capture")

    result = await inspect_memory(
        capture.id,
        capture_store=store,
        memory_store=_memory_store(tmp_path),
        audit_store=audit_store,
        session_factory=maker,
    )

    assert result["schema_version"] == "imi.memory.inspector.v1"
    assert result["record_kind"] == "capture"
    # why it exists
    assert result["record"]["provenance_status"] == "user_confirmed"
    # what created/changed it
    assert [row["action"] for row in result["audit_history"]] == ["confirm"]
    assert result["audit_history"][0]["actor"] == "scott"
    # how it was used
    assert result["usage"]["times_returned"] == 1
    assert result["usage"]["times_used"] == 1
    assert result["usage"]["times_ignored"] == 0
    # what it can influence
    assert result["influence"]["can_use_as_instruction"] is True
    assert result["influence"]["position"] == "instruction"


@pytest.mark.asyncio
async def test_inspector_resolves_agent_memory_kind(maker, tmp_path):
    store = _memory_store(tmp_path)
    memory = AgentMemory(memory_type="lesson", content="A lesson.")
    store.save(memory)

    result = await inspect_memory(
        memory.id,
        capture_store=_capture_store(tmp_path),
        memory_store=store,
        audit_store=capture_audit_store(repo_root=tmp_path),
        session_factory=maker,
    )
    assert result["record_kind"] == "agent_memory"
    assert result["record"]["memory_type"] == "lesson"
    assert result["influence"]["position"] == "evidence"
    assert result["usage"]["times_returned"] == 0


@pytest.mark.asyncio
async def test_inspector_supersession_lineage(maker, tmp_path):
    store = _capture_store(tmp_path)
    audit_store = capture_audit_store(repo_root=tmp_path)
    old = store.capture("Old fact.", source="manual").memory
    new = store.capture("New fact.", source="manual").memory
    superseded, audit_row = review_record_with_audit(
        old, "supersede", superseded_by=new.id, record_kind="capture"
    )
    store.update(superseded)
    audit_store.append(audit_row)

    result = await inspect_memory(
        old.id,
        capture_store=store,
        memory_store=_memory_store(tmp_path),
        audit_store=audit_store,
        session_factory=maker,
    )
    assert result["lineage"] == [
        {"record_id": old.id, "relation": "self"},
        {"record_id": new.id, "relation": "successor"},
    ]
    assert result["influence"]["position"] == "blocked"


@pytest.mark.asyncio
async def test_deleted_record_still_answers_from_audit(maker, tmp_path):
    """The audit-survives-hard-delete guarantee, made visible."""
    audit_store = capture_audit_store(repo_root=tmp_path)
    store = _capture_store(tmp_path)
    capture = store.capture("Doomed.", source="manual").memory
    _, audit_row = review_record_with_audit(
        capture, "reject", actor="scott", record_kind="capture"
    )
    audit_store.append(audit_row)
    # hard delete: remove the file entirely
    (tmp_path / "memory" / "captures" / f"{capture.id}.json").unlink()

    result = await inspect_memory(
        capture.id,
        capture_store=store,
        memory_store=_memory_store(tmp_path),
        audit_store=audit_store,
        session_factory=maker,
    )
    assert result["record"] is None
    assert result["record_kind"] == "capture"  # known from the audit rows
    assert [row["action"] for row in result["audit_history"]] == ["reject"]


@pytest.mark.asyncio
async def test_unknown_record_returns_none(maker, tmp_path):
    result = await inspect_memory(
        "ghost",
        capture_store=_capture_store(tmp_path),
        memory_store=_memory_store(tmp_path),
        audit_store=capture_audit_store(repo_root=tmp_path),
        session_factory=maker,
    )
    assert result is None
