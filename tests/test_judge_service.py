"""Tests for the judge extender v1 (Phase 4 of the OB1 absorption).

Contracts (imi.judge.*.v1), policy-aware recall, and idempotent decision
write-back. The load-bearing guarantees:
  - policy_hits are ONLY ever instruction-grade records (the ADR-002
    read-path proof for the judge surface).
  - Raw tool arguments are rejected at the schema layer — only an
    arguments_digest is ever stored (OB1 write-back rule).
  - judge_decide is idempotent on (tenant, action_id); memory_to_write flows
    through the Phase 2 writeback clamps (never instruction-grade at birth).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.judge import (
    ActionProposal,
    JudgeDecisionRequest,
    JudgeRecallRequest,
)
from app.services.agent_memory_store import AgentMemoryStore
from app.services.judge_service import judge_decide, judge_recall
from app.user_models.memory_ops_models import JudgeDecisionEvent


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


def test_action_proposal_validates_risk_class():
    with pytest.raises(ValidationError):
        ActionProposal(
            action_id="a-1",
            risk_class="yolo",
            description="Send email",
            arguments_digest="0" * 64,
        )


def test_action_proposal_rejects_raw_arguments():
    """Only digests are accepted — raw tool args must never reach storage."""
    with pytest.raises(ValidationError):
        ActionProposal(
            action_id="a-1",
            risk_class="external_side_effect",
            description="Send email",
            arguments_digest="0" * 64,
            arguments={"to": "everyone@example.com"},
        )


def test_action_proposal_digest_must_be_sha256_hex():
    with pytest.raises(ValidationError):
        ActionProposal(
            action_id="a-1",
            risk_class="read_only",
            description="Search",
            arguments_digest="not-a-digest",
        )


def test_judge_decision_request_validates_decision_and_checks():
    with pytest.raises(ValidationError):
        JudgeDecisionRequest(
            action_id="a-1",
            risk_class="read_only",
            decision="maybe",
            reasoning_summary="?",
        )
    req = JudgeDecisionRequest(
        action_id="a-1",
        risk_class="read_only",
        decision="allow",
        reasoning_summary="fine",
        checks={"authorization": "pass", "policy": "not_applicable"},
    )
    assert req.schema_version == "imi.judge.decision.v1"


def test_judge_recall_request_schema():
    req = JudgeRecallRequest(
        query="can I email the client list",
        action_type="external_side_effect",
    )
    assert req.schema_version == "imi.judge.recall.v1"
    with pytest.raises(ValidationError):
        JudgeRecallRequest(query="  ", action_type="read_only")


# ---------------------------------------------------------------------------
# judge_recall — policy_hits are instruction-grade only
# ---------------------------------------------------------------------------


def _recall_memory(record_id, kind, instruction):
    return {
        "record_id": record_id,
        "record_kind": kind,
        "summary": f"summary {record_id}",
        "content": f"content {record_id}",
        "similarity": 0.8,
        "score": 1.0,
        "provenance": {"status": "user_confirmed" if instruction else "generated"},
        "use_policy": {
            "can_use_as_instruction": instruction,
            "can_use_as_evidence": True,
            "requires_confirmation": not instruction,
        },
        "freshness": {},
        "scope": {},
        "review_status": "confirmed" if instruction else "pending",
    }


@pytest.mark.asyncio
async def test_judge_recall_composes_evidence_and_policy_hits():
    async def fake_recall(request, **kw):
        if request.authority == "instruction":
            return {
                "request_id": "req-ins",
                "memories": [_recall_memory("con-1", "agent_memory", True)],
                "warnings": [],
            }
        return {
            "request_id": "req-evi",
            "memories": [
                _recall_memory("cap-1", "capture", False),
                _recall_memory("con-1", "agent_memory", True),
            ],
            "warnings": [],
        }

    def fake_decisions(**kw):
        return {
            "decisions": [
                {
                    "id": "dec-1",
                    "content": "Never email the full client list.",
                    "state": "active",
                    "can_use_as_instruction": True,
                    "review_status": "confirmed",
                    "provenance_status": "user_confirmed",
                    "metadata": {"required_behavior": "block"},
                },
                {
                    "id": "dec-2",
                    "content": "Unconfirmed candidate decision.",
                    "state": "active",
                    "can_use_as_instruction": False,
                    "review_status": "pending",
                    "provenance_status": "inferred",
                    "metadata": {},
                },
            ]
        }

    result = await judge_recall(
        JudgeRecallRequest(
            query="can I email the client list",
            action_type="external_side_effect",
        ),
        recall_fn=fake_recall,
        decisions_fn=fake_decisions,
    )

    assert result["schema_version"] == "imi.judge.recall_response.v1"
    assert result["recall_request_id"] == "req-evi"
    # evidence memories pass through
    assert {m["record_id"] for m in result["memories"]} == {"cap-1", "con-1"}

    hits = {h["record_id"]: h for h in result["policy_hits"]}
    # instruction-grade recall + confirmed decision — NEVER dec-2 (ADR-002)
    assert set(hits) == {"con-1", "dec-1"}
    assert hits["dec-1"]["required_behavior"] == "block"
    assert hits["con-1"]["required_behavior"] == "revise"  # conservative default
    assert hits["dec-1"]["record_kind"] == "decision"


# ---------------------------------------------------------------------------
# judge_decide — idempotent event write-back
# ---------------------------------------------------------------------------


def _decision_request(**overrides) -> JudgeDecisionRequest:
    fields: dict = dict(
        action_id="act-1",
        risk_class="external_side_effect",
        decision="block",
        reasoning_summary="Confirmed constraint forbids this.",
        checks={"policy": "fail"},
        idempotency_key="act-1-key",
        task_id="task-7",
    )
    fields.update(overrides)
    return JudgeDecisionRequest(**fields)


def _mem_store(tmp_path) -> AgentMemoryStore:
    return AgentMemoryStore(agent_dir=tmp_path / "memory" / "agent", repo_root=tmp_path)


@pytest.mark.asyncio
async def test_judge_decide_persists_event(maker, tmp_path):
    result = await judge_decide(
        _decision_request(),
        session_factory=maker,
        memory_store=_mem_store(tmp_path),
        repo_root=tmp_path,
    )
    assert result["success"] is True
    assert result["decision_id"]
    assert result["replayed"] is False

    async with maker() as session:
        rows = (await session.execute(select(JudgeDecisionEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].decision == "block"
    assert rows[0].action_id == "act-1"
    assert rows[0].task_id == "task-7"


@pytest.mark.asyncio
async def test_judge_decide_is_idempotent_on_action_id(maker, tmp_path):
    store = _mem_store(tmp_path)
    first = await judge_decide(
        _decision_request(), session_factory=maker, memory_store=store,
        repo_root=tmp_path,
    )
    second = await judge_decide(
        _decision_request(), session_factory=maker, memory_store=store,
        repo_root=tmp_path,
    )
    assert second["replayed"] is True
    assert second["decision_id"] == first["decision_id"]

    async with maker() as session:
        rows = (await session.execute(select(JudgeDecisionEvent))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_judge_decide_concurrent_duplicate_returns_replayed(tmp_path):
    """Check-then-insert race: the loser of a concurrent duplicate insert must
    return replayed=True (re-read the winner), not surface an IntegrityError.

    Uses a file-backed engine so concurrent sessions get SEPARATE connections
    (an in-memory StaticPool shares one connection/transaction, which models
    nothing real)."""
    import asyncio

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/race.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    file_maker = async_sessionmaker(engine, expire_on_commit=False)

    try:
        store = _mem_store(tmp_path)
        first, second = await asyncio.gather(
            judge_decide(
                _decision_request(idempotency_key=None),
                session_factory=file_maker,
                memory_store=store,
                repo_root=tmp_path,
            ),
            judge_decide(
                _decision_request(idempotency_key=None),
                session_factory=file_maker,
                memory_store=store,
                repo_root=tmp_path,
            ),
        )
        assert first["success"] and second["success"]
        assert first["decision_id"] == second["decision_id"]
        assert sorted([first["replayed"], second["replayed"]]) == [False, True]

        async with file_maker() as session:
            rows = (
                (await session.execute(select(JudgeDecisionEvent))).scalars().all()
            )
        assert len(rows) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_judge_decide_routes_memory_to_write_through_clamps(maker, tmp_path):
    store = _mem_store(tmp_path)
    request = _decision_request(
        memory_to_write={
            "lessons": ["Blocked mass-email attempts must escalate to a human."]
        },
    )
    mock_git = MagicMock()
    mock_git.commit_and_push = AsyncMock()
    with (
        patch("app.services.memory_writeback.git_ops", mock_git),
        patch("app.services.memory_writeback._index_memory", lambda m: "vec"),
    ):
        result = await judge_decide(
            request,
            session_factory=maker,
            memory_store=store,
            repo_root=tmp_path,
        )

    assert result["memory_written"], "lesson should have been written"
    written = store.get(result["memory_written"][0]["id"])
    # ADR-002: judge write-back is never instruction-grade at birth
    assert written.review_status == "pending"
    assert written.can_use_as_instruction is False
    assert written.provenance_status == "generated"

    async with maker() as session:
        row = (await session.execute(select(JudgeDecisionEvent))).scalars().one()
    assert row.memory_written == result["memory_written"]


@pytest.mark.asyncio
async def test_judge_decide_reports_rejected_memory_but_keeps_decision(
    maker, tmp_path
):
    request = _decision_request(
        memory_to_write={"lessons": ["password: supersecretvalue123"]},
    )
    result = await judge_decide(
        request,
        session_factory=maker,
        memory_store=_mem_store(tmp_path),
        repo_root=tmp_path,
    )
    # the decision event is still recorded; the unsafe memory is not
    assert result["success"] is True
    assert result["memory_written"] == []
    assert result["memory_write_rejected"]
    async with maker() as session:
        rows = (await session.execute(select(JudgeDecisionEvent))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_judge_decide_applies_memory_used_feedback(maker, tmp_path):
    from app.services.recall_trace_store import get_trace_with_items, record_recall

    async with maker() as session:
        await record_recall(
            session,
            request_id="req-j1",
            query="q",
            authority="evidence",
            surface="judge_recall",
            schema_version="imi.judge.recall.v1",
            items=[
                {
                    "record_id": "con-1",
                    "record_kind": "agent_memory",
                    "rank": 0,
                    "similarity": 0.9,
                    "ranking_score": 1.0,
                }
            ],
        )
        await session.commit()

    request = _decision_request(
        recall_request_id="req-j1",
        memory_used=[{"record_id": "con-1", "used_as": "instruction"}],
    )
    result = await judge_decide(
        request,
        session_factory=maker,
        memory_store=_mem_store(tmp_path),
        repo_root=tmp_path,
    )
    assert result["success"] is True

    async with maker() as session:
        trace = await get_trace_with_items(session, "req-j1")
    assert trace["items"][0]["used"] is True
