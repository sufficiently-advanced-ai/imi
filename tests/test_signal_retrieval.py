"""Tests for semantic + hybrid retrieval over signals (G3 of the PRD).

Covers:
  - Pure ranking primitives: Reciprocal Rank Fusion, the recency half-life
    weight (ported from openbrain), and the similarity/recency blend.
  - index_signal: building the vector-store document + governance metadata.
  - search_signals_semantic: governance-aware retrieval (authority filter +
    lifecycle exclusion) over a vector store.

See docs/prd/memory-governance-and-retrieval-prd.md §7 (G3).
"""

import pytest

from app.models.signal import Signal
from app.services.signal_retrieval import (
    blend_score,
    index_signal,
    reciprocal_rank_fusion,
    recency_weight,
    search_signals_semantic,
)


def _make_signal(**overrides) -> Signal:
    fields: dict[str, object] = dict(
        id="sig-1",
        type="decision",
        content="We will standardize on the governance ladder.",
        source_meeting_id="bot-123",
        source_timestamp="2026-06-05T10:00:00+00:00",
    )
    fields.update(overrides)
    return Signal(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion (pure)
# ---------------------------------------------------------------------------


def test_rrf_rewards_items_ranked_high_in_multiple_lists():
    # "b" is rank-0 in both lists → its reciprocal-rank contributions dominate.
    vector = ["b", "a", "c"]
    keyword = ["b", "c", "d"]
    fused = reciprocal_rank_fusion([vector, keyword], k=60)
    assert fused[0] == "b"
    # every id from both lists is present, no duplicates
    assert set(fused) == {"a", "b", "c", "d"}
    assert len(fused) == len(set(fused))


def test_rrf_single_top_rank_can_outweigh_two_mid_ranks():
    # "c" is #1 in the keyword list; "b" is #2 in both. The single top rank wins.
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "b", "d"]], k=60)
    assert fused[0] == "c"


def test_rrf_single_list_preserves_order():
    assert reciprocal_rank_fusion([["x", "y", "z"]]) == ["x", "y", "z"]


def test_rrf_empty_input_returns_empty():
    assert reciprocal_rank_fusion([]) == []


# ---------------------------------------------------------------------------
# Recency half-life (pure, ported from openbrain)
# ---------------------------------------------------------------------------


def test_recency_weight_is_one_at_zero_age():
    assert recency_weight(0, half_life_days=90) == pytest.approx(1.0)


def test_recency_weight_is_half_at_one_half_life():
    half_life_days = 30
    age = half_life_days * 86400
    assert recency_weight(age, half_life_days=half_life_days) == pytest.approx(0.5)


def test_recency_weight_decays_monotonically():
    w_young = recency_weight(10 * 86400, half_life_days=90)
    w_old = recency_weight(200 * 86400, half_life_days=90)
    assert w_young > w_old


def test_recency_weight_handles_zero_half_life():
    # half_life_days=0 must not raise ZeroDivisionError: instantaneous decay.
    assert recency_weight(0, half_life_days=0) == pytest.approx(1.0)
    assert recency_weight(86400, half_life_days=0) == 0.0


# ---------------------------------------------------------------------------
# Blend (pure)
# ---------------------------------------------------------------------------


def test_blend_weight_zero_is_pure_similarity():
    assert blend_score(similarity=0.8, recency=0.1, weight=0.0) == pytest.approx(0.8)


def test_blend_weight_one_is_pure_recency():
    assert blend_score(similarity=0.8, recency=0.1, weight=1.0) == pytest.approx(0.1)


def test_blend_is_linear_interpolation():
    out = blend_score(similarity=1.0, recency=0.0, weight=0.25)
    assert out == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# Fakes for index/search
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    def generate_embeddings(self, text, data_type="text"):
        return [0.1, 0.2, 0.3]


class _FakeVectorStore:
    def __init__(self, search_results=None):
        self.stored = []
        self._search_results = search_results or []

    def store_vectors(self, embeddings, metadata=None):
        self.stored.append({"embeddings": embeddings, "metadata": metadata})
        return ["vec-1"]

    def search_vectors(self, embedding, k=10, **kwargs):
        return self._search_results


# ---------------------------------------------------------------------------
# index_signal
# ---------------------------------------------------------------------------


def test_index_signal_stores_governance_metadata():
    store = _FakeVectorStore()
    sig = _make_signal(tenant_id="tenant-x")
    vec_id = index_signal(store, _FakeEmbedder(), sig)

    assert vec_id == "vec-1"
    meta = store.stored[0]["metadata"][0]
    assert meta["content_type"] == "signal"
    assert meta["id"] == "sig-1"
    assert meta["signal_type"] == "decision"
    assert meta["provenance_status"] == "generated"
    assert meta["can_use_as_instruction"] is False
    assert meta["tenant_id"] == "tenant-x"


# ---------------------------------------------------------------------------
# search_signals_semantic — governance-aware retrieval
# ---------------------------------------------------------------------------


def _result(sig_id, score, **gov):
    base = {
        "content_type": "signal",
        "id": sig_id,
        "signal_type": "decision",
        "content": f"content {sig_id}",
        "provenance_status": "generated",
        "review_status": "pending",
        "can_use_as_evidence": True,
        "can_use_as_instruction": False,
        "created_at": "2026-06-05T10:00:00+00:00",
    }
    base.update(gov)
    return {"score": score, "metadata": base}


def test_search_returns_signals_ranked_by_similarity():
    results = [_result("a", 0.9), _result("b", 0.5)]
    store = _FakeVectorStore(results)
    out = search_signals_semantic(store, _FakeEmbedder(), "ladder", limit=10)
    assert [r["id"] for r in out] == ["a", "b"]


def test_search_instruction_filter_excludes_evidence_only():
    results = [
        _result("evi", 0.9, can_use_as_instruction=False),
        _result(
            "ins",
            0.4,
            can_use_as_instruction=True,
            provenance_status="user_confirmed",
            review_status="confirmed",
        ),
    ]
    store = _FakeVectorStore(results)
    out = search_signals_semantic(
        store, _FakeEmbedder(), "ladder", authority="instruction"
    )
    assert [r["id"] for r in out] == ["ins"]


def test_search_excludes_rejected_and_superseded_by_default():
    results = [
        _result("ok", 0.9),
        _result("rej", 0.8, review_status="rejected", can_use_as_evidence=False),
        _result("sup", 0.7, provenance_status="superseded"),
    ]
    store = _FakeVectorStore(results)
    out = search_signals_semantic(store, _FakeEmbedder(), "ladder")
    assert [r["id"] for r in out] == ["ok"]


def test_search_empty_query_returns_empty():
    store = _FakeVectorStore([_result("a", 0.9)])
    assert search_signals_semantic(store, _FakeEmbedder(), "   ") == []


def test_tenant_filter_is_none_tolerant():
    """Community-indexed signals carry tenant_id=None (Signal default; nothing
    stamps it) — filtering tenant='default' must keep them, and must exclude
    records indexed for OTHER tenants. Store-side eq() filters would drop the
    None-tenant records, so the tenant check lives Python-side."""
    results = [
        _result("legacy", 0.9),  # tenant_id absent → None
        _result("mine", 0.8, tenant_id="default"),
        _result("theirs", 0.7, tenant_id="tenant-other"),
    ]
    store = _FakeVectorStore(results)
    out = search_signals_semantic(
        store, _FakeEmbedder(), "ladder", tenant_id="default"
    )
    assert [r["id"] for r in out] == ["legacy", "mine"]


# ---------------------------------------------------------------------------
# SemanticaSearch facade delegation
# ---------------------------------------------------------------------------


def test_facade_index_and_search_delegate_to_signal_retrieval():
    import asyncio

    from app.services.semantica_search import SemanticaSearch

    store = _FakeVectorStore([_result("a", 0.9)])
    search = SemanticaSearch(vector_store=store, embedding_generator=_FakeEmbedder())

    assert asyncio.run(search.index_signal(_make_signal())) == "vec-1"
    out = asyncio.run(search.search_signals_semantic("ladder"))
    assert [r["id"] for r in out] == ["a"]


class _BoomVectorStore:
    def store_vectors(self, *a, **k):
        raise RuntimeError("boom")

    def search_vectors(self, *a, **k):
        raise RuntimeError("boom")


def test_facade_index_returns_none_on_error():
    import asyncio

    from app.services.semantica_search import SemanticaSearch

    search = SemanticaSearch(
        vector_store=_BoomVectorStore(), embedding_generator=_FakeEmbedder()
    )
    assert asyncio.run(search.index_signal(_make_signal())) is None


def test_facade_search_returns_empty_on_error_and_blank_query():
    import asyncio

    from app.services.semantica_search import SemanticaSearch

    search = SemanticaSearch(
        vector_store=_BoomVectorStore(), embedding_generator=_FakeEmbedder()
    )
    assert asyncio.run(search.search_signals_semantic("ladder")) == []
    assert asyncio.run(search.search_signals_semantic("   ")) == []
