"""Unified recall against the REAL SqliteVectorStore (regression shape).

The FAISS-backend recall bug escaped because every recall test faked the
vector store: the fakes returned well-formed metadata that the real semantica
facade never did, so ``memory_recall``'s content_type re-hydration dropped
100% of real hits. This test runs the real write path
(``signal_retrieval.index_capture``) into the real sqlite store and the real
recall pipeline over it — only the embedder is faked.
"""

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.tenancy.backends.sqlite_vector_store import SqliteVectorStore
from app.database import Base
from app.services.memory_capture import capture_memory
from app.services.memory_recall import RecallRequest, recall
from app.services.recall_trace_store import get_trace_with_items
from app.services.signal_retrieval import index_capture


class _FakeEmbedder:
    """Deterministic embeddings: identical text -> identical vector."""

    def generate_embeddings(self, text, data_type="text"):
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        return rng.random(8, dtype=np.float32)


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_recall_round_trips_through_real_sqlite_store(tmp_path, maker):
    store = SqliteVectorStore(str(tmp_path / "vectors.db"), tenant_id=None)
    embedder = _FakeEmbedder()

    cap = capture_memory("The deploy pipeline needs MCP_ALLOWED_HOSTS.", source="manual")
    vec_id = index_capture(store, embedder, cap)
    assert vec_id is not None, "real index path must store the vector"

    result = await recall(
        RecallRequest(query=cap.content, task_id="itest-1"),
        vector_store=store,
        embedder=embedder,
        resolvers={"capture": lambda rid: cap if rid == cap.id else None},
        session_factory=maker,
    )

    assert result["warnings"] == []
    assert [m["record_id"] for m in result["memories"]] == [cap.id]
    memory = result["memories"][0]
    assert memory["record_kind"] == "capture"
    assert memory["similarity"] == pytest.approx(1.0, abs=1e-5)

    async with maker() as session:
        trace = await get_trace_with_items(session, result["request_id"])
    assert trace is not None and len(trace["items"]) == 1
