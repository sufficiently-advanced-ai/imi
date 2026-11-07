"""Library captures by source: list_source_items, and recall's source filter.

The graph (faked here) supplies FROM_SOURCE ids, titles and dates; governance
comes from the capture records, and recall runs against the REAL
SqliteVectorStore so the id pre-filter is exercised.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.tenancy.backends.sqlite_vector_store import SqliteVectorStore
from app.database import Base
from app.services import memory_recall, source_items
from app.services.memory_capture import capture_memory
from app.services.memory_recall import RecallRequest, recall
from app.services.signal_retrieval import index_capture

SOURCES = [
    {"id": "source-ai-engineer", "name": "AI Engineer", "aliases": None,
     "medium": "youtube", "url": None},
    {"id": "source-ai-engineer-worlds-fair", "name": "AI Engineer World's Fair",
     "aliases": '["AIEWF"]', "medium": "youtube", "url": None},
    {"id": "source-latent-space", "name": "Latent Space", "aliases": None,
     "medium": "newsletter", "url": None},
]


class _Neo4j:
    """Answers the four source_items queries from in-memory documents:
    ``docs`` is [{capture_id, title, url, summary, occurred_at, source_id}]."""

    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    async def execute_read(self, query, params):
        self.calls.append((query, params))
        if "RETURN s.id AS id" in query:
            return [dict(s) for s in SOURCES]
        if "WHERE d.capture_id IN $ids" in query:
            by_id = {s["id"]: s for s in SOURCES}
            return [
                {"capture_id": d["capture_id"], "title": d["title"], "url": d["url"],
                 "source_id": d["source_id"], "source_name": by_id[d["source_id"]]["name"],
                 "medium": by_id[d["source_id"]]["medium"]}
                for d in self.docs if d["capture_id"] in params["ids"]
            ]
        rows = [d for d in self.docs if d["source_id"] == params["source_id"]]
        if "cutoff" in params:
            assert isinstance(params["cutoff"], datetime)  # typed, never a string (ADR-004)
            rows = [d for d in rows if d["occurred_at"] and d["occurred_at"] >= params["cutoff"]]
        rows.sort(key=lambda d: (d["occurred_at"] is None,
                                 -(d["occurred_at"].timestamp() if d["occurred_at"] else 0)))
        return [
            {"capture_id": d["capture_id"], "title": d["title"], "url": d["url"],
             "summary": d["summary"], "time_source": "explicit",
             "occurred_at": d["occurred_at"].isoformat() if d["occurred_at"] else None}
            for d in rows
        ]


def _kg(docs):
    return SimpleNamespace(neo4j=_Neo4j(docs), domain=SimpleNamespace(entities={"source": {}}))


@pytest.fixture(autouse=True)
def _source_role(monkeypatch):
    monkeypatch.setattr(source_items, "source_type", lambda kg: "source")
    monkeypatch.setattr(memory_recall, "_decay_enabled", lambda: True)


def _cap(text, lane="library", stale_after=None, **update):
    return capture_memory(text, source="web").model_copy(
        update={"lane": lane, "stale_after": stale_after, **update}
    )


def _doc(cap, source_id, days_ago, title=None):
    when = None if days_ago is None else datetime.now(UTC) - timedelta(days=days_ago)
    return {"capture_id": cap.id, "title": title or cap.content[:30], "url": f"https://x/{cap.id}",
            "summary": None, "occurred_at": when, "source_id": source_id}


# ---------------------------------------------------------------------------
# resolve_source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["AI Engineer", "ai engineer", "ai-engineer", "source-ai-engineer",
                                  "  AI Engineer  "])
async def test_resolve_source_by_name_spelling_or_id(text):
    resolved = await source_items.resolve_source(text, kg=_kg([]))
    assert resolved["id"] == "source-ai-engineer"
    assert resolved["name"] == "AI Engineer" and resolved["medium"] == "youtube"


@pytest.mark.asyncio
async def test_resolve_source_by_alias():
    resolved = await source_items.resolve_source("AIEWF", kg=_kg([]))
    assert resolved["id"] == "source-ai-engineer-worlds-fair"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Totally Unknown Channel", "", "   "])
async def test_resolve_source_unknown_raises(text):
    with pytest.raises(source_items.SourceError):
        await source_items.resolve_source(text, kg=_kg([]))


@pytest.mark.asyncio
async def test_resolve_source_without_role_raises(monkeypatch):
    monkeypatch.setattr(source_items, "source_type", lambda kg: None)
    with pytest.raises(source_items.SourceError, match="no source role"):
        await source_items.resolve_source("AI Engineer", kg=_kg([]))


# ---------------------------------------------------------------------------
# list_source_items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lists_one_sources_items_newest_first():
    old, new, undated = _cap("Old talk"), _cap("New talk"), _cap("Undated talk")
    other = _cap("Someone else's video")
    docs = [_doc(old, "source-ai-engineer", 30), _doc(new, "source-ai-engineer", 1),
            _doc(undated, "source-ai-engineer", None), _doc(other, "source-latent-space", 0)]
    records = {r.id: r for r in (old, new, undated, other)}

    out = await source_items.list_source_items("AI Engineer", kg=_kg(docs),
                                               resolve_capture=records.get)

    assert [i["record_id"] for i in out["items"]] == [new.id, old.id, undated.id]
    assert out["source"]["id"] == "source-ai-engineer"
    assert out["matching"] == 3 and out["returned"] == 3
    first = out["items"][0]
    assert first["title"] == "New talk" and first["lane"] == "library"
    assert first["use_policy"]["can_use_as_instruction"] is False


@pytest.mark.asyncio
async def test_published_after_and_max_results():
    caps = [_cap(f"Talk {i}") for i in range(5)]
    docs = [_doc(c, "source-ai-engineer", days) for c, days in zip(caps, [1, 2, 3, 40, 50], strict=True)]
    records = {c.id: c for c in caps}
    cutoff = (datetime.now(UTC) - timedelta(days=10)).date().isoformat()

    out = await source_items.list_source_items("AI Engineer", published_after=cutoff, max_results=2,
                                               kg=_kg(docs), resolve_capture=records.get)

    assert [i["record_id"] for i in out["items"]] == [caps[0].id, caps[1].id]
    assert out["matching"] == 3  # three inside the cutoff, two returned
    assert out["published_after"].startswith(cutoff)


@pytest.mark.asyncio
async def test_bad_published_after_raises():
    with pytest.raises(source_items.SourceError, match="published_after"):
        await source_items.list_source_items("AI Engineer", published_after="last tuesday",
                                             kg=_kg([]), resolve_capture=lambda _: None)


@pytest.mark.asyncio
async def test_governance_comes_from_the_capture_record():
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    live = _cap("Live talk")
    stale = _cap("Decayed talk", stale_after=past)
    pinned = _cap("Pinned decayed talk", stale_after=past, pinned_by=["storyline-x"])
    rejected = _cap("Rejected talk", review_status="rejected")
    foreign = _cap("Other tenant talk", tenant_id="someone-else")
    deleted = _cap("Deleted talk")
    caps = [live, stale, pinned, rejected, foreign, deleted]
    docs = [_doc(c, "source-ai-engineer", i) for i, c in enumerate(caps)]
    records = {c.id: c for c in caps if c is not deleted}

    out = await source_items.list_source_items("AI Engineer", kg=_kg(docs),
                                               resolve_capture=records.get)
    assert [i["record_id"] for i in out["items"]] == [live.id, pinned.id]
    assert out["stale_excluded"] == 1

    out = await source_items.list_source_items("AI Engineer", include_stale=True, kg=_kg(docs),
                                               resolve_capture=records.get)
    ids = [i["record_id"] for i in out["items"]]
    assert ids == [live.id, stale.id, pinned.id]
    assert [i["stale"] for i in out["items"]] == [False, True, False]


# ---------------------------------------------------------------------------
# recall with source
# ---------------------------------------------------------------------------


class _Embedder:
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


@pytest.fixture
def store(tmp_path):
    return SqliteVectorStore(str(tmp_path / "vectors.db"), tenant_id=None)


async def _recall(store, records, maker, kg, **kw):
    by_id = {r.id: r for r in records}
    return await recall(
        RecallRequest(query="agents", **kw),
        vector_store=store,
        embedder=_Embedder(),
        resolvers={"capture": by_id.get},
        session_factory=maker,
        kg=kg,
    )


@pytest.mark.asyncio
async def test_recall_source_filter_beats_a_crowded_library(store, maker):
    """limit=1 searches k=3 per lane: 30 off-source library vectors would bury
    the one sourced capture under a post-hoc filter."""
    mine = _cap("AI Engineer talk on agents")
    crowd = [_cap(f"Other channel video {i} on agents") for i in range(30)]
    record_lane = _cap("Our call about agents", lane="record")
    every = [mine, record_lane, *crowd]
    for r in every:
        index_capture(store, _Embedder(), r)
    kg = _kg([_doc(mine, "source-ai-engineer", 2, title="Agents at scale"),
              *[_doc(c, "source-latent-space", 2) for c in crowd]])

    result = await _recall(store, every, maker, kg, source="AI Engineer", limit=1)

    assert result["memories"] == []
    assert [m["record_id"] for m in result["background"]] == [mine.id]
    hit = result["background"][0]
    assert hit["title"] == "Agents at scale"
    assert hit["source"] == {"id": "source-ai-engineer", "name": "AI Engineer", "medium": "youtube"}
    assert result["source"]["id"] == "source-ai-engineer"


def test_source_searches_both_lanes_unless_lanes_given():
    assert RecallRequest(query="q", source="AI Engineer").lanes == ["record", "library"]
    assert RecallRequest(query="q", source="AI Engineer", lanes=["library"]).lanes == ["library"]
    assert RecallRequest(query="q", source="  ").source is None


@pytest.mark.asyncio
async def test_recall_unknown_source_warns_and_returns_nothing(store, maker):
    cap = _cap("Something")
    index_capture(store, _Embedder(), cap)
    result = await _recall(store, [cap], maker, _kg([]), source="Nobody Channel")
    assert result["memories"] == [] and result["background"] == []
    assert "no source matches" in result["warnings"][0]


@pytest.mark.asyncio
async def test_recall_source_with_no_captures_warns(store, maker):
    result = await _recall(store, [], maker, _kg([]), source="Latent Space")
    assert result["background"] == []
    assert "no linked captures" in result["warnings"][0]


@pytest.mark.asyncio
async def test_recall_source_excludes_non_capture_kinds(store, maker):
    cap = _cap("A talk")
    result = await _recall(store, [cap], maker, _kg([_doc(cap, "source-ai-engineer", 1)]),
                           source="AI Engineer", record_kinds=["signal"])
    assert "captures only" in result["warnings"][0]


@pytest.mark.asyncio
async def test_recall_without_source_annotates_captures(store, maker):
    cap = _cap("A talk on agents")
    index_capture(store, _Embedder(), cap)
    kg = _kg([_doc(cap, "source-ai-engineer", 1, title="Agent talk")])
    result = await _recall(store, [cap], maker, kg, lanes=["library"])
    assert result["background"][0]["title"] == "Agent talk"
    assert result["background"][0]["source"]["name"] == "AI Engineer"
    assert "source" not in result


@pytest.mark.asyncio
async def test_recall_annotation_survives_a_broken_graph(store, maker):
    class _Broken:
        async def execute_read(self, *a, **k):
            raise RuntimeError("neo4j down")

    cap = _cap("A talk on agents")
    index_capture(store, _Embedder(), cap)
    result = await _recall(store, [cap], maker, SimpleNamespace(neo4j=_Broken()), lanes=["library"])
    assert [m["record_id"] for m in result["background"]] == [cap.id]
    assert "source" not in result["background"][0]


# ---------------------------------------------------------------------------
# surfaces
# ---------------------------------------------------------------------------


def test_sqlite_store_filters_ids_before_top_k(store):
    caps = [_cap(f"Video {i}") for i in range(20)]
    for c in caps:
        index_capture(store, _Embedder(), c)
    filt = memory_recall._metadata_filter(["capture"], "library", {caps[7].id})
    hits = store.search_vectors(_Embedder().generate_embeddings("q"), k=1, filter=filt)
    assert [h["metadata"]["id"] for h in hits] == [caps[7].id]


def test_tool_is_registered_once():
    from app.routes.mcp_server import TOOLS
    from app.services.mcp_tool_definitions import TOOL_DEFS

    assert [t.name for t in TOOLS].count("list_source_items") == 1
    assert TOOL_DEFS["list_source_items"]["inputSchema"]["required"] == ["source"]
    assert "source" in TOOL_DEFS["memory_recall"]["inputSchema"]["properties"]
