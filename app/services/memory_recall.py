"""Unified governed recall over signals + captures + agent memories (Phase 3).

The single recall surface of the OB1 absorption: embed once, search the
shared vector store across record kinds, RE-HYDRATE governance from the
authoritative git-corpus stores (FAISS appends rather than upserts, so vector
metadata can be stale — a stale instruction-grade vector must never leak
through the ADR-002 authority filter), rank with similarity + recency +
OB1's trust-axis bonuses, and leave a SQL trace with per-item snapshots for
the usage-feedback loop.

Schema versions: request ``imi.memory.recall.v1``, response
``imi.memory.recall_response.v1``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator

from app.services.recall_trace_store import record_recall
from app.services.signal_retrieval import (
    _age_seconds,
    _passes_governance,
    authority_bonus,
    blend_score,
    tenant_matches,
)
from app.services.signal_retrieval import (
    recency_weight as _recency_weight_fn,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "imi.memory.recall.v1"
RESPONSE_SCHEMA_VERSION = "imi.memory.recall_response.v1"

RECORD_KINDS = ("signal", "capture", "agent_memory")

# Governance fields re-hydrated from the authoritative record before filtering.
_GOVERNANCE_FIELDS = (
    "provenance_status",
    "review_status",
    "can_use_as_evidence",
    "can_use_as_instruction",
    "superseded_by",
    "valid_to",
)


class RecallRequest(BaseModel):
    schema_version: Literal["imi.memory.recall.v1"] = SCHEMA_VERSION
    query: str
    authority: Literal["evidence", "instruction"] = "evidence"
    record_kinds: list[str] | None = None
    limit: int = Field(10, ge=1, le=100)
    recency_weight: float = Field(0.0, ge=0.0, le=1.0)
    half_life_days: float = 90
    include_rejected: bool = False
    task_id: str | None = None
    flow_id: str | None = None
    runtime_name: str | None = None
    runtime_version: str | None = None
    surface: str = "agent_recall"
    workspace_id: str | None = None  # dormant scope
    project_id: str | None = None  # dormant scope

    @field_validator("query")
    @classmethod
    def _non_empty_query(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("query must be non-empty")
        return value

    @field_validator("record_kinds")
    @classmethod
    def _known_kinds(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            unknown = set(value) - set(RECORD_KINDS)
            if unknown:
                raise ValueError(f"Unknown record_kinds: {sorted(unknown)}")
        return value


# ---------------------------------------------------------------------------
# Default authoritative-store resolvers (kind -> id -> record | None)
# ---------------------------------------------------------------------------


def _resolve_signal(record_id: str):
    from app.services.signal_store import SignalStore

    lookup = SignalStore().find_signal_by_id(record_id)
    return lookup[0] if lookup else None


def _resolve_capture(record_id: str):
    from app.services.memory_capture import CaptureStore

    return CaptureStore().get(record_id)


def _resolve_agent_memory(record_id: str):
    from app.services.agent_memory_store import AgentMemoryStore

    return AgentMemoryStore().get(record_id)


def default_resolvers() -> dict[str, Callable[[str], Any]]:
    return {
        "signal": _resolve_signal,
        "capture": _resolve_capture,
        "agent_memory": _resolve_agent_memory,
    }


def _default_stack():
    """Resolve (vector_store, embedder) from the live Semantica facade."""
    from app.services.signal_indexing import _get_semantica, resolve_vector_store

    sk = _get_semantica()
    if sk is None:
        return None, None
    return resolve_vector_store(sk.vector_store), sk.embedder


def _memory_shape(meta: dict, record: Any, similarity: float, score: float) -> dict:
    """OB1 recall-response memory shape from re-hydrated data."""
    return {
        "record_id": meta["id"],
        "record_kind": meta["content_type"],
        "summary": getattr(record, "summary", None) or meta.get("summary"),
        "content": getattr(record, "content", None) or meta.get("content"),
        "similarity": similarity,
        "score": score,
        "provenance": {
            "status": record.provenance_status,
            "confidence": getattr(record, "confidence", None),
            "created_by": getattr(record, "runtime_name", None),
        },
        "use_policy": {
            "can_use_as_instruction": record.can_use_as_instruction,
            "can_use_as_evidence": record.can_use_as_evidence,
            "requires_confirmation": record.review_status == "pending",
        },
        "freshness": {
            "created_at": getattr(record, "created_at", None),
            "stale_after": getattr(record, "stale_after", None),
            "valid_to": getattr(record, "valid_to", None),
        },
        "scope": {
            "tenant_id": getattr(record, "tenant_id", None),
            "workspace_id": getattr(record, "workspace_id", None),
            "project_id": getattr(record, "project_id", None),
            "visibility": getattr(record, "visibility", None),
        },
        "review_status": record.review_status,
    }


async def recall(
    request: RecallRequest,
    *,
    vector_store=None,
    embedder=None,
    resolvers: dict[str, Callable[[str], Any]] | None = None,
    session_factory=None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Execute a governed recall. Returns the response dict (never raises on
    vector/trace failures — degraded results carry a ``warnings`` list)."""
    warnings: list[str] = []
    kinds = request.record_kinds or list(RECORD_KINDS)

    # Default to the active tenant so no caller can forget scoping; enforced
    # None-tolerantly against the AUTHORITATIVE record below (community
    # records carry tenant_id=None and belong to 'default').
    if tenant_id is None:
        from app.core.middleware.request_context import current_tenant_id

        tenant_id = current_tenant_id.get()

    if vector_store is None or embedder is None:
        vector_store, embedder = _default_stack()
    if vector_store is None or embedder is None:
        return {
            "request_id": None,
            "schema_version": RESPONSE_SCHEMA_VERSION,
            "memories": [],
            "warnings": ["vector stack unavailable"],
        }
    resolvers = resolvers or default_resolvers()

    embedding = embedder.generate_embeddings(request.query, data_type="text")
    if isinstance(embedding, np.ndarray) and embedding.ndim > 1:
        embedding = embedding[0]

    search_kwargs: dict[str, Any] = {}
    try:  # delegate kind/tenant filters to the store when available
        from semantica.vector_store import MetadataFilter

        mf = (
            MetadataFilter().eq("content_type", kinds[0])
            if len(kinds) == 1
            else MetadataFilter().in_list("content_type", kinds)
        )
        # tenant is NOT a store-side filter (community records index with
        # tenant_id=None) — enforced Python-side after re-hydration.
        search_kwargs["filter"] = mf
    except Exception as e:  # pragma: no cover - optional dep absent
        logger.debug("MetadataFilter unavailable, filtering in Python: %s", e)

    results = vector_store.search_vectors(
        embedding, k=request.limit * 3, **search_kwargs
    )

    # Dedup by record id keeping the best-scoring vector (FAISS append caveat).
    best: dict[str, dict] = {}
    for result in results or []:
        meta = result.get("metadata", {}) or {}
        if meta.get("content_type") not in kinds or not meta.get("id"):
            continue
        record_id = meta["id"]
        score = float(result.get("score", 0.0))
        if record_id not in best or score > best[record_id]["similarity"]:
            best[record_id] = {"meta": meta, "similarity": score}

    scored: list[dict] = []
    for record_id, entry in best.items():
        meta, similarity = entry["meta"], entry["similarity"]
        resolver = resolvers.get(meta["content_type"])
        record = resolver(record_id) if resolver else None
        if record is None:
            continue  # deleted or unresolvable — never serve ghosts
        if not tenant_matches(getattr(record, "tenant_id", None), tenant_id):
            continue  # never surface another tenant's memories

        # RE-HYDRATE governance from the authoritative record, then filter.
        governance = {f: getattr(record, f, None) for f in _GOVERNANCE_FIELDS}
        hydrated = {**meta, **governance}
        hydrated["confidence"] = getattr(record, "confidence", None)
        if not _passes_governance(
            hydrated, request.authority, request.include_rejected
        ):
            continue

        score = similarity
        if request.recency_weight > 0:
            recency = _recency_weight_fn(
                _age_seconds(meta.get("created_at")), request.half_life_days
            )
            score = blend_score(similarity, recency, request.recency_weight)
        score += authority_bonus(hydrated)

        scored.append(
            {
                "shape": _memory_shape(hydrated, record, similarity, score),
                "similarity": similarity,
                "score": score,
            }
        )

    scored.sort(key=lambda s: s["score"], reverse=True)
    top = scored[: request.limit]
    memories = [s["shape"] for s in top]

    request_id = str(uuid.uuid4())
    if session_factory is None:
        try:  # lazy: DB may be unconfigured in some deployments
            from app.database import create_database_session, get_database_config

            session_factory = create_database_session(get_database_config())
        except Exception as e:
            warnings.append(f"trace not recorded: {e}")
            session_factory = None

    if session_factory is not None:
        try:
            async with session_factory() as session:
                await record_recall(
                    session,
                    request_id=request_id,
                    query=request.query,
                    authority=request.authority,
                    surface=request.surface,
                    schema_version=request.schema_version,
                    runtime_name=request.runtime_name,
                    runtime_version=request.runtime_version,
                    task_id=request.task_id,
                    flow_id=request.flow_id,
                    workspace_id=request.workspace_id,
                    project_id=request.project_id,
                    scope={
                        "record_kinds": kinds,
                        "include_rejected": request.include_rejected,
                    },
                    response_policy={"authority": request.authority},
                    items=[
                        {
                            "record_id": m["shape"]["record_id"],
                            "record_kind": m["shape"]["record_kind"],
                            "rank": rank,
                            "similarity": m["similarity"],
                            "ranking_score": m["score"],
                            "use_policy_snapshot": m["shape"]["use_policy"],
                        }
                        for rank, m in enumerate(top)
                    ],
                )
                await session.commit()
        except Exception as e:
            logger.warning("[RECALL] Trace write failed (non-fatal): %s", e)
            warnings.append("trace not recorded")

    return {
        "request_id": request_id,
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "memories": memories,
        "warnings": warnings,
    }
