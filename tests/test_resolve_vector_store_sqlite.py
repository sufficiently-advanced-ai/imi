"""VECTOR_BACKEND=sqlite resolution through signal_indexing.resolve_vector_store.

The sqlite backend must be reachable through the same seam the pgvector
backend uses, so every index/search call site (signals, captures, agent
memories, recall) picks it up without modification.
"""

import pytest

from app.config import settings
from app.core.tenancy.backends.sqlite_vector_store import SqliteVectorStore
from app.services import signal_indexing


_SENTINEL_DEFAULT = object()


@pytest.fixture(autouse=True)
def _clean_store_cache():
    signal_indexing._sqlite_stores.clear()
    signal_indexing._sqlite_unavailable.clear()
    yield
    signal_indexing._sqlite_stores.clear()
    signal_indexing._sqlite_unavailable.clear()


def test_vector_backend_defaults_to_sqlite():
    """sqlite is the community default: the FAISS facade in semantica 0.3-0.5
    drops metadata (recall returns nothing) and loses vectors on restart."""
    assert settings.VECTOR_BACKEND == "sqlite"


def test_faiss_backend_returns_default_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "faiss", raising=False)
    assert signal_indexing.resolve_vector_store(_SENTINEL_DEFAULT) is _SENTINEL_DEFAULT


def test_sqlite_backend_returns_sqlite_store(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "imi.db"), raising=False)
    store = signal_indexing.resolve_vector_store(_SENTINEL_DEFAULT)
    assert isinstance(store, SqliteVectorStore)


def test_sqlite_store_file_lives_beside_database_path(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "imi.db"), raising=False)
    signal_indexing.resolve_vector_store(_SENTINEL_DEFAULT)
    assert (tmp_path / "vectors.db").exists()


def _break_sqlite_store(monkeypatch, tmp_path):
    """Make SqliteVectorStore construction fail deterministically, counting
    attempts (no reliance on OS/procfs permission semantics)."""
    from app.core.tenancy.backends import sqlite_vector_store as svs_module

    attempts = {"n": 0}

    class _Boom:
        def __init__(self, *args, **kwargs):
            attempts["n"] += 1
            raise OSError("store construction failed")

    monkeypatch.setattr(settings, "VECTOR_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "imi.db"), raising=False)
    monkeypatch.setattr(svs_module, "SqliteVectorStore", _Boom)
    return attempts


def test_sqlite_backend_degrades_to_default_when_store_unusable(monkeypatch, tmp_path):
    """Mirror the pgvector contract: a misconfigured deployment degrades to
    the default store with a warning instead of breaking saves/searches."""
    _break_sqlite_store(monkeypatch, tmp_path)
    assert signal_indexing.resolve_vector_store(_SENTINEL_DEFAULT) is _SENTINEL_DEFAULT


def test_sqlite_construction_failure_is_cached_not_retried(monkeypatch, tmp_path):
    """A persistent misconfiguration must degrade ONCE per process, not retry
    (and re-log, and re-serialize on the global lock) on every write."""
    attempts = _break_sqlite_store(monkeypatch, tmp_path)
    for _ in range(3):
        assert signal_indexing.resolve_vector_store(_SENTINEL_DEFAULT) is _SENTINEL_DEFAULT
    assert attempts["n"] == 1


def test_sqlite_store_cached_per_tenant(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "imi.db"), raising=False)
    first = signal_indexing.resolve_vector_store(_SENTINEL_DEFAULT)
    second = signal_indexing.resolve_vector_store(_SENTINEL_DEFAULT)
    assert first is second
