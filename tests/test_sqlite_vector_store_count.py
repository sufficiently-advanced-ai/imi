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
    # a fresh instance on the same file learns the dim lazily
    again = SqliteVectorStore(str(tmp_path / "v.db"))
    with pytest.raises(ValueError):
        again.store_vectors([np.ones(10)], ids=["x"])
    again.store_vectors([np.ones(384)], metadata=[{"content_type": "entity"}], ids=["e1"])
    assert again.count("entity") == 1
