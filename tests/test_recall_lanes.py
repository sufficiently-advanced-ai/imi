"""ADR-003 lanes in recall, against the REAL SqliteVectorStore.

Default recall is the record lane; library hits come back separately under
``background``; a large library can never crowd record hits out of the top-k;
decayed library records drop out; the record's lane (not the vector's) wins.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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
from app.services.memory_recall import (
    RecallRequest,
    _fit_content,
    _published_at,
    _published_since,
    recall,
)
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
    mine = _cap("Ada decided to price the pilot at 10k.")
    article = _cap("Anthropic raised a new round.", lane="library")
    for r in (mine, article):
        index_capture(store, _Embedder(), r)

    result = await _recall(store, [mine, article], maker)

    assert [m["record_id"] for m in result["memories"]] == [mine.id]
    assert result["memories"][0]["lane"] == "record"
    assert result["background"] == []


@pytest.mark.asyncio
async def test_library_is_returned_separately_under_background(store, maker):
    mine = _cap("Ada decided to price the pilot at 10k.")
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
    mine = _cap("Ada decided to price the pilot at 10k.")
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
async def test_pinned_stale_library_stays_in_recall(store, maker):
    """ADR-011 §5: an owner link pins a library capture; its horizon is kept,
    so unpinning restores decay."""
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    pinned = _cap("Pinned newsletter.", lane="library", stale_after=past).model_copy(
        update={"pinned_by": ["note-1:ABOUT:capture-x"]}
    )
    unpinned = _cap("Unpinned newsletter.", lane="library", stale_after=past)
    for r in (pinned, unpinned):
        index_capture(store, _Embedder(), r)

    result = await _recall(store, [pinned, unpinned], maker, lanes=["record", "library"])

    assert [m["record_id"] for m in result["background"]] == [pinned.id]
    assert pinned.stale_after == past


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


@pytest.mark.asyncio
async def test_library_recency_is_publish_time_not_ingest_time(store, maker):
    # Both ingested today; the backfilled August issue must not rank as new.
    now = datetime.now(UTC)
    old = _cap("Risky Bulletin: August issue.", lane="library").model_copy(
        update={"source_date": (now - timedelta(days=70)).isoformat(), "time_source": "explicit"}
    )
    new = _cap("Risky Bulletin: this week's issue.", lane="library").model_copy(
        update={"source_date": (now - timedelta(days=2)).isoformat(), "time_source": "inferred"}
    )
    undated = _cap("An undated page.", lane="library")
    for r in (old, new, undated):
        index_capture(store, _Embedder(), r)

    result = await _recall(
        store, [old, new, undated], maker, lanes=["library"], recency_weight=1.0,
        half_life_days=14,
    )

    ranked = [m["record_id"] for m in result["background"]]
    assert ranked.index(new.id) < ranked.index(old.id)
    fresh = {m["record_id"]: m["freshness"] for m in result["background"]}
    assert fresh[old.id]["published_at"] == old.source_date
    assert fresh[new.id]["time_source"] == "inferred"
    assert fresh[undated.id]["time_source"] == "fallback_now"
    assert fresh[undated.id]["published_at"] == undated.created_at


@pytest.mark.asyncio
async def test_recency_widens_the_candidate_pool(store, maker):
    # Forty old records are all similar; one recent one must still be a
    # candidate for recency to lift it.
    now = datetime.now(UTC)
    old = [
        _cap(f"Old roundup {i}.", lane="library").model_copy(
            update={"source_date": (now - timedelta(days=200)).isoformat()}
        )
        for i in range(40)
    ]
    new = _cap("This week's roundup.", lane="library").model_copy(
        update={"source_date": (now - timedelta(days=1)).isoformat()}
    )
    records = [*old, new]
    for r in records:
        index_capture(store, _Embedder(), r)

    result = await _recall(
        store, records, maker, lanes=["library"], limit=3, recency_weight=0.5
    )

    assert result["background"][0]["record_id"] == new.id


@pytest.mark.asyncio
async def test_content_budget_cuts_both_lanes_and_says_so(store, maker):
    long_record = _cap("r" * 500)
    long_library = _cap("l" * 500, lane="library")
    short = _cap("short")
    records = [long_record, long_library, short]
    for r in records:
        index_capture(store, _Embedder(), r)

    result = await _recall(
        store, records, maker, lanes=["record", "library"], max_content_chars=100
    )

    rows = {m["record_id"]: m for m in result["memories"] + result["background"]}
    for cut in (long_record, long_library):
        assert rows[cut.id]["content"] == cut.content[:100]
        assert rows[cut.id]["content_truncated"] is True
        assert rows[cut.id]["content_chars"] == 500
    assert rows[short.id]["content"] == "short"
    assert "content_truncated" not in rows[short.id]


@pytest.mark.asyncio
async def test_include_content_false_keeps_the_triage_fields(store, maker):
    record = _cap("some content")
    index_capture(store, _Embedder(), record)

    result = await _recall(store, [record], maker, include_content=False)

    row = result["memories"][0]
    assert row["content"] is None
    assert row["content_chars"] == len(record.content)
    assert row["freshness"]["published_at"] == record.created_at


@pytest.mark.asyncio
async def test_published_after_is_event_time_and_excludes_undated_library(store, maker):
    now = datetime.now(UTC)
    old = _cap("An old issue.", lane="library").model_copy(
        update={"source_date": (now - timedelta(days=70)).isoformat(), "time_source": "explicit"}
    )
    new = _cap("A new issue.", lane="library").model_copy(
        update={"source_date": (now - timedelta(days=2)).isoformat(), "time_source": "explicit"}
    )
    undated = _cap("An undated page.", lane="library")
    note = _cap("An undated note of ours.")
    records = [old, new, undated, note]
    for r in records:
        index_capture(store, _Embedder(), r)

    result = await _recall(
        store, records, maker, lanes=["record", "library"],
        published_after=(now - timedelta(days=7)).isoformat(),
    )

    assert [m["record_id"] for m in result["background"]] == [new.id]
    assert [m["record_id"] for m in result["memories"]] == [note.id]


def test_published_after_must_be_a_date():
    with pytest.raises(ValidationError):
        RecallRequest(query="q", published_after="last week")


def _signal(valid_from=None, source_timestamp=None):
    return SimpleNamespace(
        valid_from=valid_from, source_timestamp=source_timestamp, created_at="2026-11-01T00:00:00Z"
    )


def test_signal_event_time_is_when_it_became_true_not_when_it_was_written():
    meta = {"content_type": "signal"}
    backfilled = _signal(valid_from="2026-03-01T00:00:00Z")
    assert _published_at(backfilled, meta)[0].startswith("2026-03-01")
    cutoff = datetime(2026, 10, 1, tzinfo=UTC)
    assert not _published_since(backfilled, meta, "record", cutoff)


def test_library_signal_with_a_known_date_passes_the_cutoff():
    signal = _signal(source_timestamp="2026-10-20T00:00:00Z")
    cutoff = datetime(2026, 10, 1, tzinfo=UTC)
    assert _published_since(signal, {"content_type": "signal"}, "library", cutoff)


def test_signal_without_an_event_time_falls_back_to_creation():
    assert _published_at(_signal(), {"content_type": "signal"}) == (
        "2026-11-01T00:00:00Z", "fallback_now",
    )


def test_length_cap_leaves_signals_whole_but_include_content_false_does_not():
    shape = {"record_kind": "signal", "content": "s" * 500}
    assert _fit_content(shape, RecallRequest(query="q", max_content_chars=100)) == shape
    hidden = _fit_content(shape, RecallRequest(query="q", include_content=False))
    assert hidden["content"] is None and hidden["content_chars"] == 500
