"""Tests for the unified recall service (Phase 3 of the OB1 absorption).

One governed recall surface over signals + captures + agent memories:
  - authority_bonus: OB1's rankMemory provenance/policy/review/confidence
    bonuses as a pure function layered on the existing primitives.
  - Governance RE-HYDRATION: vector-store metadata can be stale (FAISS appends
    rather than upserts) — the authoritative store is consulted by id BEFORE
    the authority filter, so a stale instruction-grade vector can never leak.
  - Dedup by record id keeping the best-scoring vector.
  - Every recall leaves a SQL trace with per-item ranking snapshots.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.agent_memory import AgentMemory
from app.services.memory_capture import capture_memory
from app.services.memory_recall import RecallRequest, recall
from app.services.recall_trace_store import get_trace_with_items
from app.services.signal_retrieval import authority_bonus


# ---------------------------------------------------------------------------
# authority_bonus (pure — OB1 rankMemory port)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "meta,expected",
    [
        (
            {
                "provenance_status": "user_confirmed",
                "can_use_as_instruction": True,
                "review_status": "confirmed",
                "confidence": 1.0,
            },
            0.3 + 0.2 + 0.15 + 0.15,
        ),
        (
            {
                "provenance_status": "generated",
                "can_use_as_instruction": False,
                "can_use_as_evidence": True,
                "review_status": "pending",
                "confidence": 0.5,
            },
            0.05 + 0.08 - 0.08 + 0.075,
        ),
        (
            {
                "provenance_status": "generated",
                "can_use_as_instruction": False,
                "can_use_as_evidence": False,
                "review_status": "rejected",
            },
            0.05 - 0.2 - 0.25 + 0.0,
        ),
        ({"provenance_status": "imported", "can_use_as_evidence": True,
          "review_status": "evidence_only", "confidence": 0.0},
         0.22 + 0.08 + 0.05),
    ],
)
def test_authority_bonus_values(meta, expected):
    assert authority_bonus(meta) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# recall — fakes
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    def generate_embeddings(self, text, data_type="text"):
        return [0.1, 0.2, 0.3]


class _FakeVectorStore:
    def __init__(self, results):
        self._results = results

    def search_vectors(self, embedding, k=10, **kwargs):
        return self._results


def _vec(record_id, kind, score, **meta):
    base = {
        "content_type": kind,
        "id": record_id,
        "content": f"content of {record_id}",
        "created_at": "2026-07-01T00:00:00+00:00",
        "provenance_status": "generated",
        "review_status": "pending",
        "can_use_as_evidence": True,
        "can_use_as_instruction": False,
    }
    base.update(meta)
    return {"score": score, "metadata": base}


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _resolvers(records):
    """kind -> (id -> record) resolvers over an in-memory dict."""
    return {
        kind: (lambda k: (lambda rid: records.get((k, rid))))(kind)
        for kind in ("signal", "capture", "agent_memory")
    }


# ---------------------------------------------------------------------------
# recall behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_spans_kinds_and_writes_trace(maker):
    cap = capture_memory("A capture.", source="manual")
    mem = AgentMemory(memory_type="lesson", content="A lesson.")
    records = {("capture", cap.id): cap, ("agent_memory", mem.id): mem}

    store = _FakeVectorStore(
        [
            _vec(cap.id, "capture", 0.9, provenance_status="imported"),
            _vec(mem.id, "agent_memory", 0.8),
        ]
    )
    result = await recall(
        RecallRequest(query="anything", task_id="task-1"),
        vector_store=store,
        embedder=_FakeEmbedder(),
        resolvers=_resolvers(records),
        session_factory=maker,
    )

    assert result["schema_version"] == "imi.memory.recall_response.v1"
    kinds = [m["record_kind"] for m in result["memories"]]
    assert set(kinds) == {"capture", "agent_memory"}
    # capture outranks: higher similarity AND better provenance bonus
    assert result["memories"][0]["record_id"] == cap.id
    assert result["memories"][0]["use_policy"]["can_use_as_instruction"] is False
    assert result["memories"][0]["provenance"]["status"] == "imported"

    async with maker() as session:
        trace = await get_trace_with_items(session, result["request_id"])
    assert trace is not None
    assert trace["task_id"] == "task-1"
    assert len(trace["items"]) == 2
    assert trace["items"][0]["ranking_score"] >= trace["items"][1]["ranking_score"]


@pytest.mark.asyncio
async def test_stale_vector_metadata_cannot_leak_instruction_grade(maker):
    """THE ADR-002 recall guarantee: authoritative governance wins."""
    cap = capture_memory("Once confirmed, later disputed.", source="manual")
    # authoritative store: NOT instruction-grade
    records = {("capture", cap.id): cap}
    # stale vector claims instruction-grade
    store = _FakeVectorStore(
        [
            _vec(
                cap.id,
                "capture",
                0.95,
                provenance_status="user_confirmed",
                review_status="confirmed",
                can_use_as_instruction=True,
            )
        ]
    )
    result = await recall(
        RecallRequest(query="anything", authority="instruction"),
        vector_store=store,
        embedder=_FakeEmbedder(),
        resolvers=_resolvers(records),
        session_factory=maker,
    )
    assert result["memories"] == []


@pytest.mark.asyncio
async def test_deleted_records_are_dropped(maker):
    store = _FakeVectorStore([_vec("ghost-1", "capture", 0.9)])
    result = await recall(
        RecallRequest(query="anything"),
        vector_store=store,
        embedder=_FakeEmbedder(),
        resolvers=_resolvers({}),  # nothing resolves
        session_factory=maker,
    )
    assert result["memories"] == []


@pytest.mark.asyncio
async def test_duplicate_vectors_deduped_keeping_best_score(maker):
    cap = capture_memory("Re-indexed capture.", source="manual")
    records = {("capture", cap.id): cap}
    store = _FakeVectorStore(
        [
            _vec(cap.id, "capture", 0.5),
            _vec(cap.id, "capture", 0.9),  # newer append, better score
        ]
    )
    result = await recall(
        RecallRequest(query="anything"),
        vector_store=store,
        embedder=_FakeEmbedder(),
        resolvers=_resolvers(records),
        session_factory=maker,
    )
    assert len(result["memories"]) == 1
    assert result["memories"][0]["similarity"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_record_kinds_filter(maker):
    cap = capture_memory("A capture.", source="manual")
    mem = AgentMemory(memory_type="lesson", content="A lesson.")
    records = {("capture", cap.id): cap, ("agent_memory", mem.id): mem}
    store = _FakeVectorStore(
        [
            _vec(cap.id, "capture", 0.9, provenance_status="imported"),
            _vec(mem.id, "agent_memory", 0.8),
        ]
    )
    result = await recall(
        RecallRequest(query="anything", record_kinds=["agent_memory"]),
        vector_store=store,
        embedder=_FakeEmbedder(),
        resolvers=_resolvers(records),
        session_factory=maker,
    )
    assert [m["record_kind"] for m in result["memories"]] == ["agent_memory"]


@pytest.mark.asyncio
async def test_confirmed_outranks_similar_pending(maker):
    pending = capture_memory("Pending capture.", source="manual")
    confirmed_raw = capture_memory("Confirmed capture.", source="manual")
    from app.services.signal_governance import apply_review

    confirmed = apply_review(confirmed_raw, "confirm")
    records = {
        ("capture", pending.id): pending,
        ("capture", confirmed.id): confirmed,
    }
    # pending is slightly MORE similar, but confirmed's bonuses win
    store = _FakeVectorStore(
        [
            _vec(pending.id, "capture", 0.80, provenance_status="imported"),
            _vec(confirmed.id, "capture", 0.75),
        ]
    )
    result = await recall(
        RecallRequest(query="anything"),
        vector_store=store,
        embedder=_FakeEmbedder(),
        resolvers=_resolvers(records),
        session_factory=maker,
    )
    assert result["memories"][0]["record_id"] == confirmed.id


@pytest.mark.asyncio
async def test_empty_query_errors(maker):
    with pytest.raises(ValueError):
        RecallRequest(query="   ")


@pytest.mark.asyncio
async def test_recall_enforces_tenant_none_tolerantly(maker):
    """Recall must default to the active tenant and enforce it against the
    AUTHORITATIVE record (None-tenant community records pass under 'default';
    other tenants' records never surface)."""
    mine = capture_memory("Mine.", source="manual")  # tenant None (community)
    theirs = capture_memory("Theirs.", source="manual", tenant_id="tenant-other")
    records = {("capture", mine.id): mine, ("capture", theirs.id): theirs}
    store = _FakeVectorStore(
        [
            _vec(mine.id, "capture", 0.9, provenance_status="imported"),
            _vec(theirs.id, "capture", 0.8, provenance_status="imported"),
        ]
    )

    from app.core.middleware.request_context import current_tenant_id

    token = current_tenant_id.set("default")
    try:
        result = await recall(
            RecallRequest(query="anything"),
            vector_store=store,
            embedder=_FakeEmbedder(),
            resolvers=_resolvers(records),
            session_factory=maker,
        )
    finally:
        current_tenant_id.reset(token)

    assert [m["record_id"] for m in result["memories"]] == [mine.id]
