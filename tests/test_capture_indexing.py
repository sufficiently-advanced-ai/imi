"""Tests for capture vector indexing (Phase 1 of the OB1 absorption).

Covers:
  - index_capture: embeds a capture and stores it with content_type="capture"
    plus the governance metadata the trust-axis filters need at query time.
  - signal_indexing.index_capture_one: the best-effort glue (never raises,
    resolves the store via resolve_vector_store like the signal path).
"""

import pytest

from app.services.memory_capture import capture_memory
from app.services.signal_retrieval import index_capture


class _FakeEmbedder:
    def generate_embeddings(self, text, data_type="text"):
        return [0.1, 0.2, 0.3]


class _FakeVectorStore:
    def __init__(self):
        self.stored = []

    def store_vectors(self, embeddings, metadata=None):
        self.stored.append({"embeddings": embeddings, "metadata": metadata})
        return ["vec-cap-1"]


class _BoomEmbedder:
    def generate_embeddings(self, text, data_type="text"):
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# index_capture
# ---------------------------------------------------------------------------


def test_index_capture_stores_governance_metadata():
    store = _FakeVectorStore()
    cap = capture_memory(
        "We standardized on FastAPI.", source="manual", tenant_id="tenant-x"
    )
    cap = cap.model_copy(update={"enrichment": {"type": "decision"}})

    vec_id = index_capture(store, _FakeEmbedder(), cap)

    assert vec_id == "vec-cap-1"
    meta = store.stored[0]["metadata"][0]
    assert meta["content_type"] == "capture"
    assert meta["id"] == cap.id
    assert meta["content"] == "We standardized on FastAPI."
    assert meta["source"] == "manual"
    assert meta["capture_type"] == "decision"
    assert meta["provenance_status"] == "imported"
    assert meta["review_status"] == "pending"
    assert meta["can_use_as_evidence"] is True
    assert meta["can_use_as_instruction"] is False
    assert meta["tenant_id"] == "tenant-x"
    assert meta["created_at"] == cap.created_at


def test_index_capture_empty_content_returns_none():
    cap = capture_memory("placeholder", source="manual")
    cap = cap.model_copy(update={"content": "   "})
    assert index_capture(_FakeVectorStore(), _FakeEmbedder(), cap) is None


def test_index_capture_never_raises_on_embedder_failure():
    cap = capture_memory("Some thought.", source="manual")
    assert index_capture(_FakeVectorStore(), _BoomEmbedder(), cap) is None


# ---------------------------------------------------------------------------
# index_agent_memory (Phase 2)
# ---------------------------------------------------------------------------


def test_index_agent_memory_stores_governance_metadata():
    from app.models.agent_memory import AgentMemory
    from app.services.signal_retrieval import index_agent_memory

    store = _FakeVectorStore()
    mem = AgentMemory(
        memory_type="lesson",
        content="Batch embedding calls.",
        task_id="task-1",
        runtime_name="openclaw",
        tenant_id="tenant-x",
    )
    vec_id = index_agent_memory(store, _FakeEmbedder(), mem)

    assert vec_id == "vec-cap-1"
    meta = store.stored[0]["metadata"][0]
    assert meta["content_type"] == "agent_memory"
    assert meta["id"] == mem.id
    assert meta["memory_type"] == "lesson"
    assert meta["task_id"] == "task-1"
    assert meta["runtime_name"] == "openclaw"
    assert meta["provenance_status"] == "generated"
    assert meta["can_use_as_instruction"] is False
    assert meta["tenant_id"] == "tenant-x"


def test_index_agent_memory_one_delegates(monkeypatch):
    from types import SimpleNamespace

    from app.config import settings
    from app.models.agent_memory import AgentMemory
    from app.services import signal_indexing

    # Pin the legacy passthrough: this test asserts the semantica facade's
    # store is used, which only happens on the (non-default) faiss backend.
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "faiss", raising=False)
    store = _FakeVectorStore()
    fake_sk = SimpleNamespace(vector_store=store, embedder=_FakeEmbedder())
    monkeypatch.setattr(signal_indexing, "_get_semantica", lambda: fake_sk)

    mem = AgentMemory(memory_type="decision", content="Pick A.")
    assert signal_indexing.index_agent_memory_one(mem) == "vec-cap-1"
    assert store.stored[0]["metadata"][0]["content_type"] == "agent_memory"


# ---------------------------------------------------------------------------
# signal_indexing.index_capture_one — best-effort glue
# ---------------------------------------------------------------------------


def test_index_capture_one_returns_none_when_stack_unavailable(monkeypatch):
    from app.services import signal_indexing

    monkeypatch.setattr(signal_indexing, "_get_semantica", lambda: None)
    cap = capture_memory("Some thought.", source="manual")
    assert signal_indexing.index_capture_one(cap) is None


def test_index_capture_one_delegates_to_index_capture(monkeypatch):
    from types import SimpleNamespace

    from app.config import settings
    from app.services import signal_indexing

    # Pin the legacy passthrough (see test_index_agent_memory_one_delegates).
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "faiss", raising=False)
    store = _FakeVectorStore()
    fake_sk = SimpleNamespace(vector_store=store, embedder=_FakeEmbedder())
    monkeypatch.setattr(signal_indexing, "_get_semantica", lambda: fake_sk)

    cap = capture_memory("Some thought.", source="manual")
    assert signal_indexing.index_capture_one(cap) == "vec-cap-1"
    assert store.stored[0]["metadata"][0]["content_type"] == "capture"
