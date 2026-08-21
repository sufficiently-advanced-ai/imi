"""SemanticaSearch: resolved store, off-loop calls, upsert ids, entity_type post-filter."""

import threading

import numpy as np
import pytest

from app.services import semantica_search as ss
from app.services.blocking import run_blocking


class FakeEmbedder:
    def __init__(self):
        self.threads = set()

    def generate_embeddings(self, text, data_type="text"):
        self.threads.add(threading.current_thread().name)
        return np.ones((1, 4), dtype=np.float32)


class FakeStore:
    def __init__(self):
        self.stored = []
        self.results = []

    def store_vectors(self, embeddings, metadata=None, ids=None, **_):
        self.stored.append((ids, metadata))
        return ids or ["generated"]

    def search_vectors(self, query_embedding, k=10, filter=None, **_):
        return self.results

    def count(self, content_type=None):
        return len([s for s in self.stored if (s[1] or [{}])[0].get("content_type") == content_type])

    def delete(self, vector_id):
        self.stored = [s for s in self.stored if s[0] != [vector_id]]


@pytest.fixture
def search(monkeypatch):
    store = FakeStore()
    embedder = FakeEmbedder()
    s = ss.SemanticaSearch(vector_store=object(), embedding_generator=embedder, graph_store=None)
    monkeypatch.setattr(ss.SemanticaSearch, "store", property(lambda self: store))
    return s, store, embedder


@pytest.mark.asyncio
async def test_run_blocking_uses_worker_thread():
    name = await run_blocking(lambda: threading.current_thread().name)
    assert name.startswith("imi-blocking")


@pytest.mark.asyncio
async def test_index_entity_upserts_by_entity_id_off_loop(search):
    s, store, embedder = search
    vid = await s.index_entity(entity_id="person-a", name="A", entity_type="person", attributes={"role": "dev"})
    assert vid == "person-a"
    ids, metadata = store.stored[0]
    assert ids == ["person-a"]
    assert metadata[0]["content_type"] == "entity" and metadata[0]["entity_type"] == "person"
    assert all(t.startswith("imi-blocking") for t in embedder.threads)
    assert await s.entity_vector_count() == 1
    await s.delete_entity_vector("person-a")
    assert await s.entity_vector_count() == 0


@pytest.mark.asyncio
async def test_hybrid_search_post_filters_entity_type(search, monkeypatch):
    s, store, _ = search
    monkeypatch.setitem(__import__("sys").modules, "semantica.vector_store", _FakeMetadataFilterModule())
    store.results = [
        {"id": "person-a", "score": 0.9, "metadata": {"id": "person-a", "name": "A", "entity_type": "person"}},
        {"id": "project-x", "score": 0.8, "metadata": {"id": "project-x", "name": "X", "entity_type": "project"}},
    ]
    hits = await s.hybrid_search("a", entity_types=["project"], limit=5)
    assert [h["id"] for h in hits] == ["project-x"]
    hits = await s.hybrid_search("a", limit=5)
    assert [h["id"] for h in hits] == ["person-a", "project-x"]


class _FakeMetadataFilterModule:
    class MetadataFilter:
        def eq(self, *_):
            return self

        def in_list(self, *_):
            return self
