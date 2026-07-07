"""SqliteVectorStore — persistent, metadata-carrying vector store for community.

The semantica 0.3.x FAISS facade drops metadata at store time and ignores
search filters, which silently breaks governed recall (every hit is missing
``content_type`` so the Python-side kind/governance re-hydration discards it).
This backend owns the metadata round-trip end to end, mirroring the hosted
PgVectorStore contract:

    store_vectors(embeddings, metadata=..., ids=...) -> list[str]   # upsert by id
    search_vectors(query_embedding, k=..., filter=...) -> [{id, score, metadata}]
    delete(vector_id)

Tests run against the real store on a tmp SQLite file — no fakes, because a
faked store is exactly how the FAISS bug escaped to production.
"""

import numpy as np
import pytest

from app.core.tenancy.backends.sqlite_vector_store import SqliteVectorStore


class _StubFilter:
    """Duck-typed stand-in for semantica.vector_store.MetadataFilter."""

    def __init__(self, conditions):
        self.conditions = conditions


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "vectors.db")


@pytest.fixture()
def store(db_path):
    return SqliteVectorStore(db_path, tenant_id=None)


def _vec(*values):
    return np.asarray(values, dtype=np.float32)


def test_search_round_trips_metadata(store):
    store.store_vectors(
        [_vec(1.0, 0.0, 0.0)],
        metadata=[{"id": "cap-1", "content_type": "capture", "summary": "hello"}],
    )
    results = store.search_vectors(_vec(1.0, 0.0, 0.0), k=5)
    assert len(results) == 1
    assert results[0]["id"] == "cap-1"
    assert results[0]["metadata"]["content_type"] == "capture"
    assert results[0]["metadata"]["summary"] == "hello"


def test_search_ranks_by_cosine_similarity(store):
    store.store_vectors(
        [_vec(1.0, 0.0, 0.0), _vec(0.0, 1.0, 0.0), _vec(0.9, 0.1, 0.0)],
        metadata=[
            {"id": "exact", "content_type": "signal"},
            {"id": "orthogonal", "content_type": "signal"},
            {"id": "close", "content_type": "signal"},
        ],
    )
    results = store.search_vectors(_vec(1.0, 0.0, 0.0), k=3)
    assert [r["id"] for r in results] == ["exact", "close", "orthogonal"]
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(1.0, abs=1e-5)


def test_k_limits_result_count(store):
    store.store_vectors(
        [_vec(1.0, float(i), 0.0) for i in range(5)],
        metadata=[{"id": f"m-{i}", "content_type": "signal"} for i in range(5)],
    )
    assert len(store.search_vectors(_vec(1.0, 0.0, 0.0), k=2)) == 2


def test_store_upserts_by_id(store):
    store.store_vectors(
        [_vec(1.0, 0.0, 0.0)], metadata=[{"id": "cap-1", "content_type": "capture"}]
    )
    store.store_vectors(
        [_vec(0.0, 1.0, 0.0)],
        metadata=[{"id": "cap-1", "content_type": "capture", "summary": "v2"}],
    )
    results = store.search_vectors(_vec(0.0, 1.0, 0.0), k=10)
    assert len(results) == 1
    assert results[0]["metadata"]["summary"] == "v2"
    assert results[0]["score"] == pytest.approx(1.0, abs=1e-5)


def test_ids_generated_when_metadata_has_none(store):
    ids = store.store_vectors([_vec(1.0, 0.0, 0.0)], metadata=[{"content_type": "signal"}])
    assert len(ids) == 1 and ids[0]
    results = store.search_vectors(_vec(1.0, 0.0, 0.0), k=1)
    assert results[0]["id"] == ids[0]


def test_eq_filter_restricts_content_type(store):
    store.store_vectors(
        [_vec(1.0, 0.0, 0.0), _vec(0.99, 0.01, 0.0)],
        metadata=[
            {"id": "sig-1", "content_type": "signal"},
            {"id": "cap-1", "content_type": "capture"},
        ],
    )
    results = store.search_vectors(
        _vec(1.0, 0.0, 0.0),
        k=10,
        filter=_StubFilter([{"field": "content_type", "operator": "eq", "value": "capture"}]),
    )
    assert [r["id"] for r in results] == ["cap-1"]


def test_in_filter_restricts_content_type(store):
    store.store_vectors(
        [_vec(1.0, 0.0, 0.0), _vec(0.99, 0.01, 0.0), _vec(0.98, 0.02, 0.0)],
        metadata=[
            {"id": "sig-1", "content_type": "signal"},
            {"id": "cap-1", "content_type": "capture"},
            {"id": "ent-1", "content_type": "entity"},
        ],
    )
    results = store.search_vectors(
        _vec(1.0, 0.0, 0.0),
        k=10,
        filter=_StubFilter(
            [{"field": "content_type", "operator": "in", "value": ["capture", "signal"]}]
        ),
    )
    assert {r["id"] for r in results} == {"sig-1", "cap-1"}


def test_unsupported_filter_fields_fall_through(store):
    """Filters on fields other than content_type are left to the caller
    (recall re-hydrates governance Python-side), never over-restrict."""
    store.store_vectors(
        [_vec(1.0, 0.0, 0.0)], metadata=[{"id": "sig-1", "content_type": "signal"}]
    )
    results = store.search_vectors(
        _vec(1.0, 0.0, 0.0),
        k=10,
        filter=_StubFilter([{"field": "review_status", "operator": "eq", "value": "confirmed"}]),
    )
    assert [r["id"] for r in results] == ["sig-1"]


def test_tenant_scoping_isolates_search(db_path):
    a = SqliteVectorStore(db_path, tenant_id="tenant-a")
    b = SqliteVectorStore(db_path, tenant_id="tenant-b")
    a.store_vectors([_vec(1.0, 0.0, 0.0)], metadata=[{"id": "a-1", "content_type": "signal"}])
    b.store_vectors([_vec(1.0, 0.0, 0.0)], metadata=[{"id": "b-1", "content_type": "signal"}])
    assert [r["id"] for r in a.search_vectors(_vec(1.0, 0.0, 0.0), k=10)] == ["a-1"]
    assert [r["id"] for r in b.search_vectors(_vec(1.0, 0.0, 0.0), k=10)] == ["b-1"]


def test_none_tenant_store_sees_only_none_tenant_rows(db_path):
    none_store = SqliteVectorStore(db_path, tenant_id=None)
    tenant_store = SqliteVectorStore(db_path, tenant_id="tenant-a")
    none_store.store_vectors(
        [_vec(1.0, 0.0, 0.0)], metadata=[{"id": "legacy-1", "content_type": "signal"}]
    )
    tenant_store.store_vectors(
        [_vec(1.0, 0.0, 0.0)], metadata=[{"id": "a-1", "content_type": "signal"}]
    )
    assert [r["id"] for r in none_store.search_vectors(_vec(1.0, 0.0, 0.0), k=10)] == ["legacy-1"]


def test_vectors_persist_across_store_instances(db_path):
    SqliteVectorStore(db_path, tenant_id=None).store_vectors(
        [_vec(1.0, 0.0, 0.0)], metadata=[{"id": "cap-1", "content_type": "capture"}]
    )
    reopened = SqliteVectorStore(db_path, tenant_id=None)
    results = reopened.search_vectors(_vec(1.0, 0.0, 0.0), k=5)
    assert [r["id"] for r in results] == ["cap-1"]
    assert results[0]["metadata"]["content_type"] == "capture"


def test_delete_removes_vector(store):
    store.store_vectors(
        [_vec(1.0, 0.0, 0.0)], metadata=[{"id": "cap-1", "content_type": "capture"}]
    )
    store.delete("cap-1")
    assert store.search_vectors(_vec(1.0, 0.0, 0.0), k=5) == []


def test_search_on_empty_store_returns_empty_list(store):
    assert store.search_vectors(_vec(1.0, 0.0, 0.0), k=5) == []


def test_zero_norm_query_returns_empty_not_nan(store):
    store.store_vectors(
        [_vec(1.0, 0.0, 0.0)], metadata=[{"id": "cap-1", "content_type": "capture"}]
    )
    results = store.search_vectors(_vec(0.0, 0.0, 0.0), k=5)
    assert results == []
