import numpy as np

from app.core.tenancy.backends.sqlite_vector_store import SqliteVectorStore


def test_count_by_content_type_and_tenant(tmp_path):
    store = SqliteVectorStore(str(tmp_path / "v.db"))
    assert store.count() == 0
    store.store_vectors([np.ones(3), np.ones(3)], metadata=[{"content_type": "entity"}, {"content_type": "signal"}], ids=["a", "b"])
    store.store_vectors([np.zeros(3)], metadata=[{"content_type": "entity"}], ids=["a"])  # upsert, not a new row
    assert store.count() == 2
    assert store.count("entity") == 1
    other = SqliteVectorStore(str(tmp_path / "v.db"), tenant_id="t2")
    assert other.count() == 0


def test_dimension_mismatch_is_rejected(tmp_path):
    import pytest

    store = SqliteVectorStore(str(tmp_path / "v.db"))
    store.store_vectors([np.ones(384)], metadata=[{"content_type": "signal"}], ids=["s1"])
    with pytest.raises(ValueError, match="384-dim"):
        store.store_vectors([np.ones(768)], metadata=[{"content_type": "entity"}], ids=["e1"])
    with pytest.raises(ValueError, match="mixed"):
        store.store_vectors([np.ones(384), np.ones(3)], ids=["a", "b"])
    assert store.count() == 1
    # a second instance on the same file sees the authoritative dim (no per-process cache)
    again = SqliteVectorStore(str(tmp_path / "v.db"))
    with pytest.raises(ValueError):
        again.store_vectors([np.ones(10)], ids=["x"])
    again.store_vectors([np.ones(384)], metadata=[{"content_type": "entity"}], ids=["e1"])
    assert again.count("entity") == 1


def test_dimension_is_validated_against_the_table_not_a_stale_cache(tmp_path):
    """Two instances over one file: the first write wins and the conflicting
    second one is rejected; once the tenant is emptied a new dim is accepted."""
    import pytest

    a = SqliteVectorStore(str(tmp_path / "v.db"))
    b = SqliteVectorStore(str(tmp_path / "v.db"))
    a.store_vectors([np.ones(384)], metadata=[{"content_type": "entity"}], ids=["a1"])
    with pytest.raises(ValueError, match="384-dim"):
        b.store_vectors([np.ones(3)], metadata=[{"content_type": "entity"}], ids=["b1"])
    assert a.delete_by_content_type("entity") == 1
    assert a.count() == 0
    # instance b never saw the delete, yet must not reject on stale state
    b.store_vectors([np.ones(3)], metadata=[{"content_type": "entity"}], ids=["b1"])
    assert b.count("entity") == 1


class _Filter:
    def __init__(self, *conditions):
        self.conditions = list(conditions)


def test_search_filters_entity_type_in_the_query(tmp_path):
    store = SqliteVectorStore(str(tmp_path / "v.db"))
    q = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    # 60 projects that match the query perfectly, one person that matches worse
    embeddings = [q.copy() for _ in range(60)] + [np.array([0.6, 0.8, 0.0], dtype=np.float32)]
    metadata = [{"content_type": "entity", "entity_type": "project", "id": f"project-{i}"} for i in range(60)]
    metadata.append({"content_type": "entity", "entity_type": "person", "id": "person-x"})
    store.store_vectors(embeddings, metadata=metadata, ids=[m["id"] for m in metadata])

    f = _Filter(
        {"field": "content_type", "operator": "eq", "value": "entity"},
        {"field": "entity_type", "operator": "in_list", "value": ["person"]},
    )
    hits = store.search_vectors(q, k=10, filter=f)
    assert [h["id"] for h in hits] == ["person-x"]
    # without the entity_type constraint the person is outside the top-10 window
    only_kind = _Filter({"field": "content_type", "operator": "eq", "value": "entity"})
    assert "person-x" not in [h["id"] for h in store.search_vectors(q, k=10, filter=only_kind)]
