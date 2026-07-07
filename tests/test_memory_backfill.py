"""Backfill for capture/agent-memory vectors (sqlite backend recovery).

``backfill_signals`` existed for signals; captures and agent memories had no
backfill at all, so a store created after records were written (fresh
vectors.db, backend switch, corpus clone) could never recall them. These
tests run the real record stores and the real sqlite vector store — only the
embedder is faked.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from app.config import settings
from app.models.agent_memory import AgentMemory
from app.services import signal_indexing
from app.services.agent_memory_store import AgentMemoryStore
from app.services.memory_capture import CaptureStore, capture_memory


class _FakeEmbedder:
    def generate_embeddings(self, text, data_type="text"):
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        return rng.random(8, dtype=np.float32)


class _BoomEmbedder:
    def generate_embeddings(self, text, data_type="text"):
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _sqlite_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "imi.db"), raising=False)
    signal_indexing._sqlite_stores.clear()
    yield
    signal_indexing._sqlite_stores.clear()


def _fake_sk(embedder=None):
    return SimpleNamespace(vector_store=object(), embedder=embedder or _FakeEmbedder())


def _capture_store(tmp_path):
    store = CaptureStore(
        capture_dir=tmp_path / "memory" / "captures", repo_root=tmp_path
    )
    for text in ("First captured thought.", "Second captured thought."):
        store.update(capture_memory(text, source="manual"))
    return store


def _memory_store(tmp_path):
    store = AgentMemoryStore(agent_dir=tmp_path / "memory" / "agent", repo_root=tmp_path)
    store.save(AgentMemory(memory_type="lesson", content="A backfilled lesson."))
    return store


def test_capture_store_iter_all_yields_every_record(tmp_path):
    store = _capture_store(tmp_path)
    assert len(list(store.iter_all())) == 2


def test_agent_memory_store_iter_all_yields_every_record(tmp_path):
    store = _memory_store(tmp_path)
    assert len(list(store.iter_all())) == 1


def test_backfill_captures_indexes_into_sqlite_store(monkeypatch, tmp_path):
    monkeypatch.setattr(signal_indexing, "_get_semantica", _fake_sk)
    store = _capture_store(tmp_path)

    total, indexed, skipped = signal_indexing.backfill_captures(capture_store=store)

    assert (total, indexed, skipped) == (2, 2, 0)
    vec_store = signal_indexing.resolve_vector_store(None)
    hits = vec_store.search_vectors(
        _FakeEmbedder().generate_embeddings("First captured thought."), k=5
    )
    kinds = {h["metadata"]["content_type"] for h in hits}
    assert kinds == {"capture"}
    assert len(hits) == 2


def test_backfill_agent_memories_indexes_into_sqlite_store(monkeypatch, tmp_path):
    monkeypatch.setattr(signal_indexing, "_get_semantica", _fake_sk)
    store = _memory_store(tmp_path)

    total, indexed, skipped = signal_indexing.backfill_agent_memories(memory_store=store)

    assert (total, indexed, skipped) == (1, 1, 0)
    vec_store = signal_indexing.resolve_vector_store(None)
    hits = vec_store.search_vectors(
        _FakeEmbedder().generate_embeddings("A backfilled lesson."), k=5
    )
    assert [h["metadata"]["content_type"] for h in hits] == ["agent_memory"]


def test_backfill_captures_counts_failures_as_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(
        signal_indexing, "_get_semantica", lambda: _fake_sk(_BoomEmbedder())
    )
    store = _capture_store(tmp_path)

    total, indexed, skipped = signal_indexing.backfill_captures(capture_store=store)

    assert (total, indexed, skipped) == (2, 0, 2)


def test_backfill_is_idempotent_upsert_no_duplicates(monkeypatch, tmp_path):
    monkeypatch.setattr(signal_indexing, "_get_semantica", _fake_sk)
    store = _capture_store(tmp_path)

    signal_indexing.backfill_captures(capture_store=store)
    signal_indexing.backfill_captures(capture_store=store)

    vec_store = signal_indexing.resolve_vector_store(None)
    hits = vec_store.search_vectors(
        _FakeEmbedder().generate_embeddings("First captured thought."), k=10
    )
    assert len(hits) == 2  # two records, not four vectors
