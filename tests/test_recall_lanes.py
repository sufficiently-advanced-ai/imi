"""ADR-003 lanes in recall, against the REAL SqliteVectorStore.

Default recall is the record lane; library hits come back separately under
``background``; a large library can never crowd record hits out of the top-k;
decayed library records drop out; the record's lane (not the vector's) wins.
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.tenancy.backends.sqlite_vector_store import SqliteVectorStore
from app.database import Base
from app.models.captured_memory import CapturedMemory
from app.services.memory_capture import capture_memory
from app.services.memory_recall import RecallRequest, recall
from app.services.signal_retrieval import index_capture


class _Embedder:
    """Every text embeds near one axis, so similarity ties are broken only by
    a small per-text perturbation — every stored vector is a strong match."""

    def generate_embeddings(self, text, data_type="text"):
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        v = np.zeros(8, dtype=np.float32)
        v[0] = 1.0
        return v + rng.random(8, dtype=np.float32) * 0.01


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _cap(text: str, lane: str = "record", stale_after: str | None = None) -> CapturedMemory:
    return capture_memory(text, source="manual").model_copy(
        update={"lane": lane, "stale_after": stale_after}
    )


async def _recall(store, records, maker, **kw):
    by_id = {r.id: r for r in records}
    return await recall(
        RecallRequest(query="query", **kw),
        vector_store=store,
        embedder=_Embedder(),
        resolvers={"capture": by_id.get},
        session_factory=maker,
    )


@pytest.fixture
def store(tmp_path):
    return SqliteVectorStore(str(tmp_path / "vectors.db"), tenant_id=None)


@pytest.mark.asyncio
async def test_default_recall_is_record_lane_only(store, maker):
    mine = _cap("Scott decided to price the pilot at 10k.")
    article = _cap("Anthropic raised a new round.", lane="library")
    for r in (mine, article):
        index_capture(store, _Embedder(), r)

    result = await _recall(store, [mine, article], maker)

    assert [m["record_id"] for m in result["memories"]] == [mine.id]
    assert result["memories"][0]["lane"] == "record"
    assert result["background"] == []


@pytest.mark.asyncio
async def test_library_is_returned_separately_under_background(store, maker):
    mine = _cap("Scott decided to price the pilot at 10k.")
    article = _cap("Anthropic raised a new round.", lane="library")
    for r in (mine, article):
        index_capture(store, _Embedder(), r)

    result = await _recall(store, [mine, article], maker, lanes=["record", "library"])

    assert [m["record_id"] for m in result["memories"]] == [mine.id]
    assert [m["record_id"] for m in result["background"]] == [article.id]
    assert result["background"][0]["lane"] == "library"


@pytest.mark.asyncio
async def test_library_volume_cannot_starve_record_hits(store, maker):
    """limit=1 searches k=3 per lane: with 30 library vectors, a post-hoc
    lane filter would see no record vector at all."""
    mine = _cap("Scott decided to price the pilot at 10k.")
    articles = [_cap(f"Newsletter issue {i} about AI.", lane="library") for i in range(30)]
    for r in [mine, *articles]:
        index_capture(store, _Embedder(), r)

    result = await _recall(store, [mine, *articles], maker, limit=1)

    assert [m["record_id"] for m in result["memories"]] == [mine.id]


@pytest.mark.asyncio
async def test_stale_library_is_excluded_but_record_stale_after_is_not(store, maker):
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    stale = _cap("Old newsletter.", lane="library", stale_after=past)
    fresh = _cap("New newsletter.", lane="library", stale_after=future)
    mine = _cap("My note with a horizon.", stale_after=past)
    for r in (stale, fresh, mine):
        index_capture(store, _Embedder(), r)

    result = await _recall(store, [stale, fresh, mine], maker, lanes=["record", "library"])

    assert [m["record_id"] for m in result["background"]] == [fresh.id]
    assert [m["record_id"] for m in result["memories"]] == [mine.id]


@pytest.mark.asyncio
async def test_record_lane_wins_over_stale_vector_metadata(store, maker):
    """Indexed as record, later re-laned to library: recall must follow the
    authoritative record, not the vector."""
    cap = _cap("Was filed as mine, is really an article.")
    index_capture(store, _Embedder(), cap)
    relaned = cap.model_copy(update={"lane": "library"})

    result = await _recall(store, [relaned], maker)

    assert result["memories"] == []


@pytest.mark.asyncio
async def test_vectors_without_lane_count_as_record(store, maker):
    """Vectors indexed before lanes existed have no lane in their metadata."""
    cap = _cap("Pre-lane note.")
    store.store_vectors(
        [_Embedder().generate_embeddings(cap.content)],
        metadata=[{"content_type": "capture", "id": cap.id, "content": cap.content,
                   "provenance_status": "imported", "review_status": "pending",
                   "can_use_as_evidence": True, "can_use_as_instruction": False}],
    )

    result = await _recall(store, [cap], maker)

    assert [m["record_id"] for m in result["memories"]] == [cap.id]


def test_lanes_are_validated():
    with pytest.raises(ValidationError):
        RecallRequest(query="q", lanes=["world"])
    with pytest.raises(ValidationError):
        RecallRequest(query="q", lanes=[])
    assert RecallRequest(query="q").lanes == ["record"]
