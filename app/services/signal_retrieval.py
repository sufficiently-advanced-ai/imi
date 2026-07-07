"""Semantic + hybrid retrieval over signals (G3 of the memory-governance PRD).

Today ``search_signals`` filters signal JSON by exact fields; signals are never
embedded. This module adds:

  - ``index_signal`` — embed a signal and store it in the vector store with
    governance metadata, so the authority/lifecycle state composes with
    similarity at query time.
  - ``search_signals_semantic`` — governance-aware semantic retrieval: an
    authority filter (evidence vs instruction grade) and default exclusion of
    rejected/superseded/disputed records, with an optional recency blend.
  - Pure ranking primitives — Reciprocal Rank Fusion (for true hybrid fusion of
    a vector list and a keyword list, vs today's vector-with-graph-fallback) and
    the exponential recency half-life ported from openbrain.

The vector-store / embedder API mirrors ``semantica_search.index_entity``.
See docs/prd/memory-governance-and-retrieval-prd.md §7 (G3).
"""

import logging
import math
from datetime import UTC, datetime
from typing import Any

import numpy as np

from app.models.signal import Signal

logger = logging.getLogger(__name__)

# Excluded from results by default (the trust axis — mirrors SearchMemory).
_EXCLUDED_REVIEW = {"rejected"}
_EXCLUDED_PROVENANCE = {"superseded", "disputed"}

# Governance metadata carried into the vector store for each signal.
_PROJECTED_FIELDS = (
    "id",
    "signal_type",
    "content",
    "status",
    "source_meeting_id",
    "created_at",
    "provenance_status",
    "review_status",
    "can_use_as_evidence",
    "can_use_as_instruction",
    "tenant_id",
)


def reciprocal_rank_fusion(ranked_lists: list[list[Any]], k: int = 60) -> list[Any]:
    """Fuse several ranked id lists into one via Reciprocal Rank Fusion.

    RRF score for an item is the sum over lists of ``1 / (k + rank)`` (rank
    0-based here). Items appearing high in multiple lists rise to the top. Ties
    break by first appearance across the input lists (stable, deterministic).
    """
    scores: dict[Any, float] = {}
    first_seen: dict[Any, int] = {}
    order = 0
    for lst in ranked_lists:
        for rank, item in enumerate(lst):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
            if item not in first_seen:
                first_seen[item] = order
                order += 1
    return sorted(scores, key=lambda item: (-scores[item], first_seen[item]))


def recency_weight(age_seconds: float, half_life_days: float = 90) -> float:
    """Exponential recency weight in (0, 1]; reaches 0.5 at ``half_life_days``.

    Ported from openbrain SearchMemory: ``exp(-ln2 * age / half_life)``.
    A non-positive half-life means instantaneous decay (avoids ZeroDivisionError).
    """
    if half_life_days <= 0:
        return 1.0 if age_seconds <= 0 else 0.0
    half_life_seconds = half_life_days * 86400.0
    return math.exp(-math.log(2) * age_seconds / half_life_seconds)


def blend_score(similarity: float, recency: float, weight: float) -> float:
    """Linear blend of similarity and recency: ``sim*(1-w) + recency*w``."""
    return similarity * (1.0 - weight) + recency * weight


# Module-level alias so search_signals_semantic can call the recency helper
# without it being shadowed by the ``recency_weight`` parameter.
_recency_weight = recency_weight

# OB1 rankMemory bonuses (agent-memory-api index.ts): provenance tier, use
# policy, review disposition, plus a small confidence multiplier.
_PROVENANCE_BONUS = {
    "user_confirmed": 0.3,
    "imported": 0.22,
    "observed": 0.15,
    "generated": 0.05,
}
_REVIEW_BONUS = {
    "confirmed": 0.15,
    "evidence_only": 0.05,
    "pending": -0.08,
}


def authority_bonus(meta: dict) -> float:
    """Trust-axis ranking bonus for a record's governance metadata.

    Pure port of OB1's ``rankMemory`` (minus similarity, which callers add):
    confirmed instruction-grade memory rises, unreviewed generated memory
    sinks, rejected memory sinks hard. Layered onto ``blend_score`` by the
    unified recall service; ``search_signals_semantic`` keeps its original
    behavior unless a caller opts in.
    """
    provenance = _PROVENANCE_BONUS.get(meta.get("provenance_status"), 0.0)
    if meta.get("can_use_as_instruction"):
        policy = 0.2
    elif meta.get("can_use_as_evidence"):
        policy = 0.08
    else:
        policy = -0.2
    review = _REVIEW_BONUS.get(meta.get("review_status"), -0.25)
    confidence = float(meta.get("confidence") or 0.0) * 0.15
    return provenance + policy + review + confidence


def index_signal(vector_store, embedder, signal: Signal) -> str | None:
    """Embed a signal and store it with governance metadata.

    Returns the vector id, or None on failure. Mirrors
    ``semantica_search.index_entity``.
    """
    if not signal.content or not signal.content.strip():
        return None
    try:
        embedding = embedder.generate_embeddings(signal.content, data_type="text")
        if isinstance(embedding, np.ndarray) and embedding.ndim > 1:
            embedding = embedding[0]

        metadata = {
            "content_type": "signal",
            "id": signal.id,
            "signal_type": signal.type,
            "content": signal.content,
            "status": signal.status,
            "source_meeting_id": signal.source_meeting_id,
            "created_at": signal.created_at,
            "provenance_status": signal.provenance_status,
            "review_status": signal.review_status,
            "can_use_as_evidence": signal.can_use_as_evidence,
            "can_use_as_instruction": signal.can_use_as_instruction,
            "tenant_id": signal.tenant_id,
        }
        ids = vector_store.store_vectors([embedding], metadata=[metadata])
        return ids[0] if ids else None
    except Exception as e:
        logger.error("Failed to index signal %s: %s", signal.id, e)
        return None


def index_capture(vector_store, embedder, capture) -> str | None:
    """Embed a CapturedMemory and store it with governance metadata.

    Mirrors ``index_signal`` with ``content_type="capture"`` so captures share
    the trust-axis filters (and, in Phase 3, the unified recall surface).
    Returns the vector id, or None on failure.
    """
    if not capture.content or not capture.content.strip():
        return None
    try:
        embedding = embedder.generate_embeddings(capture.content, data_type="text")
        if isinstance(embedding, np.ndarray) and embedding.ndim > 1:
            embedding = embedding[0]

        metadata = {
            "content_type": "capture",
            "id": capture.id,
            "content": capture.content,
            "source": capture.source,
            "capture_type": (capture.enrichment or {}).get("type"),
            "created_at": capture.created_at,
            "provenance_status": capture.provenance_status,
            "review_status": capture.review_status,
            "can_use_as_evidence": capture.can_use_as_evidence,
            "can_use_as_instruction": capture.can_use_as_instruction,
            "tenant_id": capture.tenant_id,
        }
        ids = vector_store.store_vectors([embedding], metadata=[metadata])
        return ids[0] if ids else None
    except Exception as e:
        logger.error("Failed to index capture %s: %s", capture.id, e)
        return None


def index_agent_memory(vector_store, embedder, memory) -> str | None:
    """Embed an AgentMemory and store it with governance metadata.

    Mirrors ``index_signal``/``index_capture`` with ``content_type="agent_memory"``
    so agent memories join the trust-axis filters and the unified recall surface.
    Returns the vector id, or None on failure.
    """
    if not memory.content or not memory.content.strip():
        return None
    try:
        embedding = embedder.generate_embeddings(memory.content, data_type="text")
        if isinstance(embedding, np.ndarray) and embedding.ndim > 1:
            embedding = embedding[0]

        metadata = {
            "content_type": "agent_memory",
            "id": memory.id,
            "memory_type": memory.memory_type,
            "content": memory.content,
            "summary": memory.summary,
            "task_id": memory.task_id,
            "runtime_name": memory.runtime_name,
            "created_at": memory.created_at,
            "provenance_status": memory.provenance_status,
            "review_status": memory.review_status,
            "can_use_as_evidence": memory.can_use_as_evidence,
            "can_use_as_instruction": memory.can_use_as_instruction,
            "tenant_id": memory.tenant_id,
        }
        ids = vector_store.store_vectors([embedding], metadata=[metadata])
        return ids[0] if ids else None
    except Exception as e:
        logger.error("Failed to index agent memory %s: %s", memory.id, e)
        return None


def _age_seconds(created_at: str | None) -> float:
    """Seconds between created_at (ISO) and now; 0 if unparseable."""
    if not created_at:
        return 0.0
    try:
        ts = datetime.fromisoformat(created_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - ts).total_seconds())
    except ValueError:
        return 0.0


def tenant_matches(record_tenant: str | None, requested: str | None) -> bool:
    """None-tolerant tenant comparison.

    Community records carry tenant_id=None and belong to the single 'default'
    tenant; treating None as 'default' on BOTH sides keeps them retrievable
    while still excluding records scoped to other tenants.
    """
    if requested is None:
        return True
    return (record_tenant or "default") == (requested or "default")


def _passes_governance(meta: dict, authority: str, include_rejected: bool) -> bool:
    """Apply the trust-axis filter to a single result's metadata."""
    if not include_rejected:
        if meta.get("review_status") in _EXCLUDED_REVIEW:
            return False
        if meta.get("provenance_status") in _EXCLUDED_PROVENANCE:
            return False
    if authority == "instruction":
        return bool(meta.get("can_use_as_instruction"))
    # default: evidence-grade or better
    return meta.get("can_use_as_evidence", True)


def search_signals_semantic(
    vector_store,
    embedder,
    query: str,
    *,
    signal_types: list[str] | None = None,
    status: str | None = None,
    tenant_id: str | None = None,
    authority: str = "evidence",
    limit: int = 10,
    recency_weight: float = 0.0,
    half_life_days: float = 90,
    include_rejected: bool = False,
) -> list[dict[str, Any]]:
    """Governance-aware semantic search over indexed signals.

    Args:
        authority: "evidence" (default) keeps evidence-grade-or-better;
            "instruction" keeps only instruction-grade (human-confirmed) signals.
        recency_weight: 0 = pure similarity (default); >0 blends an exponential
            recency half-life into the score.
    """
    if not query or not query.strip():
        return []

    embedding = embedder.generate_embeddings(query, data_type="text")
    if isinstance(embedding, np.ndarray) and embedding.ndim > 1:
        embedding = embedding[0]

    search_kwargs: dict[str, Any] = {}
    try:  # delegate field filters to the store when available
        from semantica.vector_store import MetadataFilter

        mf = MetadataFilter().eq("content_type", "signal")
        if signal_types:
            mf = (
                mf.eq("signal_type", signal_types[0])
                if len(signal_types) == 1
                else mf.in_list("signal_type", signal_types)
            )
        if status:
            mf = mf.eq("status", status)
        # tenant_id is deliberately NOT a store-side filter: community-indexed
        # records carry tenant_id=None (Signal default), which eq("default")
        # would exclude. Tenant is enforced None-tolerantly in Python below.
        search_kwargs["filter"] = mf
    except Exception as e:  # pragma: no cover - store/optional dep absent
        logger.debug("MetadataFilter unavailable, filtering in Python: %s", e)

    results = vector_store.search_vectors(embedding, k=limit * 2, **search_kwargs)

    scored: list[dict[str, Any]] = []
    for result in results or []:
        meta = result.get("metadata", {}) or {}
        if meta.get("content_type") not in (None, "signal"):
            continue
        if not tenant_matches(meta.get("tenant_id"), tenant_id):
            continue
        if not _passes_governance(meta, authority, include_rejected):
            continue

        similarity = float(result.get("score", 0.0))
        score = similarity
        if recency_weight > 0:
            recency = _recency_weight(
                _age_seconds(meta.get("created_at")), half_life_days
            )
            score = blend_score(similarity, recency, recency_weight)

        projected = {field: meta.get(field) for field in _PROJECTED_FIELDS}
        projected["similarity"] = similarity
        projected["score"] = score
        scored.append(projected)

    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:limit]
