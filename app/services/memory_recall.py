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
from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.lane import Lane
from app.services.recall_trace_store import record_recall
from app.services.signal_dedup import shown_under
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
from app.utils.event_time import (
    TIME_SOURCE_FALLBACK,
    TIME_SOURCE_UNRECORDED,
    signal_event_time,
    to_utc,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "imi.memory.recall.v1"
RESPONSE_SCHEMA_VERSION = "imi.memory.recall_response.v1"

RECORD_KINDS = ("signal", "capture", "agent_memory")
CANDIDATE_FACTOR = 3  # vector candidates per requested result
RECENCY_CANDIDATE_FACTOR = 20  # with recency blended in (see _candidate_k)
CANDIDATE_GROWTH = 4  # pool growth per pass under a date cutoff
MAX_CANDIDATES = 8000  # each candidate re-hydrates its record from the store

# Governance fields re-hydrated from the authoritative record before filtering.
_GOVERNANCE_FIELDS = (
    "provenance_status",
    "review_status",
    "can_use_as_evidence",
    "can_use_as_instruction",
    "superseded_by",
    "valid_to",
    # ADR-003: the lane (and library decay) come from the record too — a
    # stale vector must not move a record between lanes.
    "lane",
    "stale_after",
    "pinned_by",  # ADR-011 §5: an owner link keeps a library capture in recall
)


class RecallRequest(BaseModel):
    schema_version: Literal["imi.memory.recall.v1"] = SCHEMA_VERSION
    query: str
    authority: Literal["evidence", "instruction"] = "evidence"
    record_kinds: list[str] | None = None
    # ADR-003: default recall is the record lane. Library results, when
    # requested, are ranked separately and returned under "background".
    # ADR-006 §7: a deployment changes the default with recall.default_lanes
    # in config/lanes.yaml; an explicit ``lanes`` always wins.
    lanes: list[Lane] = Field(default_factory=lambda: _default_lanes())
    limit: int = Field(10, ge=1, le=100)
    recency_weight: float = Field(0.0, ge=0.0, le=1.0)
    half_life_days: float = 90
    # Bound the response, not the ranking: recall triages on summary and date.
    include_content: bool = True
    max_content_chars: int | None = Field(None, ge=1)
    # Event time, ADR-004. A library record with no known publish date cannot
    # be shown to be recent, so it is excluded rather than dated by ingest.
    published_after: datetime | None = None
    include_rejected: bool = False
    # Only captures linked FROM_SOURCE to this source (an id or a name, see
    # source_items.resolve_source). Signals and agent memories have no source.
    source: str | None = None
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

    @field_validator("source", mode="before")
    @classmethod
    def _blank_source(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("source must be a string")
        return value.strip() or None

    @field_validator("published_after", mode="before")
    @classmethod
    def _utc_cutoff(cls, value: Any) -> datetime | None:
        if value is None:
            return None
        parsed = to_utc(value)
        if parsed is None:
            raise ValueError(f"published_after is not a date: {value!r}")
        return parsed

    @field_validator("lanes")
    @classmethod
    def _non_empty_lanes(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("lanes must name at least one lane")
        return list(dict.fromkeys(value))  # dedup, keep order

    @model_validator(mode="after")
    def _source_spans_lanes(self) -> RecallRequest:
        # Sourced content is mostly library; a source filter that kept the
        # record-only default would answer "nothing" for a busy channel.
        if self.source and "lanes" not in self.model_fields_set:
            self.lanes = ["record", "library"]
        return self

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


def _pointer_lookup(resolver: Callable[[str], Any], record: Any):
    """``signal_dedup.PointerLookup`` over a record resolver (``record`` is known)."""

    def lookup(sid: str) -> tuple[bool, str | None]:
        found = record if sid == getattr(record, "id", None) else resolver(sid)
        if found is None:
            return False, None
        return True, (getattr(found, "metadata", None) or {}).get("duplicate_of")

    return lookup


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


class _Filter:
    """Minimal MetadataFilter stand-in (same ``conditions`` shape) for stacks
    without semantica; the sqlite store reads ``conditions`` duck-typed."""

    def __init__(self) -> None:
        self.conditions: list[dict[str, Any]] = []

    def eq(self, field: str, value: Any) -> _Filter:
        self.conditions.append({"field": field, "operator": "eq", "value": value})
        return self

    def in_list(self, field: str, values: list[Any]) -> _Filter:
        self.conditions.append({"field": field, "operator": "in", "value": list(values)})
        return self


def _metadata_filter(kinds: list[str], lane: str, ids: set[str] | None = None):
    try:
        from semantica.vector_store import MetadataFilter

        mf = MetadataFilter()
    except Exception:  # optional dep absent — same shape, local shim
        mf = _Filter()
    mf = mf.eq("content_type", kinds[0]) if len(kinds) == 1 else mf.in_list("content_type", kinds)
    mf = mf.eq("lane", lane)
    if ids:
        # Stores that understand it filter before the top-k cut; recall
        # re-checks every result, so a store that ignores it is still correct.
        mf = mf.in_list("id", sorted(ids))
    return mf


def _empty_response(warnings: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "request_id": None,
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "memories": [],
        "background": [],
        "warnings": warnings,
        **extra,
    }


async def _source_scope(source: str, kg: Any) -> tuple[dict, set[str]]:
    """(resolved source, its capture ids). Raises source_items.SourceError."""
    from app.services import source_items

    kg = kg or source_items._graph()
    resolved = await source_items.resolve_source(source, kg=kg)
    return resolved, set(await source_items.source_capture_ids(resolved["id"], kg=kg))


async def _annotate_origins(shapes: list[dict], kg: Any) -> None:
    """Add ``title`` and ``source`` to capture results, from the graph.
    Best effort: recall never fails because the graph is unavailable."""
    ids = [s["record_id"] for s in shapes if s.get("record_kind") == "capture"]
    if not ids:
        return
    try:
        from app.services import source_items

        origins = await source_items.capture_origins(ids, kg=kg)
    except Exception as e:  # noqa: BLE001
        logger.debug("[RECALL] capture origins unavailable: %s", e)
        return
    for shape in shapes:
        origin = origins.get(shape["record_id"])
        if origin and shape.get("record_kind") == "capture":
            shape["title"] = origin.get("title")
            shape["source"] = origin.get("source")


def _default_lanes() -> list[str]:
    from app.services.lane_admission import recall_default_lanes

    return recall_default_lanes()


def _decay_enabled() -> bool:
    """lanes.yaml ``library.decay.enabled``. Off, a stamped ``stale_after`` is
    ignored rather than rewritten — decay is never deletion (ADR-003 §5), so
    turning it back on restores the same horizons."""
    try:
        from app.services.lane_admission import library_decay_enabled

        return library_decay_enabled()
    except Exception:
        return True


def _is_stale(stale_after: str | None) -> bool:
    if not stale_after:
        return False
    from app.services.memory_capture import parse_instant

    try:
        return parse_instant(stale_after) <= datetime.now(UTC)
    except (TypeError, ValueError):
        return False  # unparseable horizon never hides a record


def _candidate_k(request: RecallRequest) -> int:
    """Vector candidates per lane. Recency reorders only what similarity
    already fetched; on a broad query hundreds of records are about equally
    similar, so a 3x pool would hold none of last week's. With recency on,
    fetch a wider pool for it to choose from."""
    wide = request.recency_weight > 0 or request.published_after is not None
    factor = RECENCY_CANDIDATE_FACTOR if wide else CANDIDATE_FACTOR
    return request.limit * factor


def _published_at(record: Any, meta: dict) -> tuple[str | None, str]:
    """(event time, time_source) for recency (ADR-004). A capture's
    ``source_date`` is when its content was published or sent; ingest time
    (``created_at``) is only the fallback, flagged ``fallback_now`` — a
    backfill must not rank old content as new."""
    source_date = getattr(record, "source_date", None)
    if source_date:
        return source_date, getattr(record, "time_source", None) or TIME_SOURCE_UNRECORDED
    if meta.get("content_type") == "signal":
        occurred = signal_event_time(record)
        if occurred is not None:
            return occurred.isoformat(), TIME_SOURCE_UNRECORDED
    return getattr(record, "created_at", None) or meta.get("created_at"), TIME_SOURCE_FALLBACK


def _published_since(record: Any, meta: dict, lane: str, cutoff: datetime) -> bool:
    """Event time on or after ``cutoff``. Record-lane items without a source
    date keep their creation time, which is when they happened to us; a
    library item without one has an unknown publish date."""
    published_at, time_source = _published_at(record, meta)
    if lane == "library" and time_source == TIME_SOURCE_FALLBACK:
        return False
    published = to_utc(published_at)
    return published is not None and published >= cutoff


def _fit_content(shape: dict, request: RecallRequest) -> dict:
    """Apply the response's content budget. A cut says so, with the full
    length, so a caller knows to fetch the record with ``inspect_memory``.
    Signals are short and ``inspect_memory`` cannot return them, so the length
    cap leaves them whole; ``include_content=False`` is the caller's explicit ask."""
    content = shape.get("content")
    if not content:
        return shape
    if request.include_content and shape.get("record_kind") == "signal":
        return shape
    if request.include_content and (
        request.max_content_chars is None or len(content) <= request.max_content_chars
    ):
        return shape
    kept = content[: request.max_content_chars] if request.include_content else None
    return {**shape, "content": kept, "content_truncated": True, "content_chars": len(content)}


def _memory_shape(meta: dict, record: Any, similarity: float, score: float) -> dict:
    """OB1 recall-response memory shape from re-hydrated data."""
    published_at, time_source = _published_at(record, meta)
    return {
        "record_id": meta["id"],
        "record_kind": meta["content_type"],
        "lane": meta.get("lane") or "record",
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
            "published_at": published_at,
            "time_source": time_source,
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
    kg: Any = None,
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

    allowed: set[str] | None = None
    source_info: dict | None = None
    if request.source:
        from app.services.source_items import SourceError

        try:
            source_info, allowed = await _source_scope(request.source, kg)
        except SourceError as e:
            return _empty_response([str(e)])
        kinds = [k for k in kinds if k == "capture"]
        if not kinds:
            return _empty_response(
                ["source filters captures only; record_kinds excludes capture"],
                source=source_info,
            )
        if not allowed:
            return _empty_response(
                [f"source {source_info['name']!r} has no linked captures"], source=source_info
            )

    if vector_store is None or embedder is None:
        vector_store, embedder = _default_stack()
    if vector_store is None or embedder is None:
        return _empty_response(["vector stack unavailable"])
    resolvers = resolvers or default_resolvers()
    decay = _decay_enabled()

    embedding = embedder.generate_embeddings(request.query, data_type="text")
    if isinstance(embedding, np.ndarray) and embedding.ndim > 1:
        embedding = embedding[0]

    def rank_candidates(lane: str, k: int) -> tuple[list[dict], int]:
        """Rank one lane's top-k vectors. Returns (eligible, vectors fetched).
        Each lane gets its own store-side filtered top-k so a large library
        can never crowd record results out (or vice versa)."""
        results = vector_store.search_vectors(
            embedding, k=k, filter=_metadata_filter(kinds, lane, allowed)
        )

        # Dedup by record id keeping the best-scoring vector (FAISS append caveat).
        best: dict[str, dict] = {}
        for result in results or []:
            meta = result.get("metadata", {}) or {}
            if meta.get("content_type") not in kinds or not meta.get("id"):
                continue
            record_id = meta["id"]
            if allowed is not None and record_id not in allowed:
                continue  # the store ignored the id filter, or the vector lags
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
            governance["lane"] = governance["lane"] or "record"
            hydrated = {**meta, **governance}
            # Dedup state is authoritative on the record too (vector metadata lags);
            # a pointer at a signal that no longer exists hides nothing.
            dup = (getattr(record, "metadata", None) or {}).get("duplicate_of")
            signal_resolver = resolvers.get("signal")
            if dup and signal_resolver is not None:
                if shown_under(record_id, _pointer_lookup(signal_resolver, record)) is None:
                    dup = None  # dangling or looping pointer: hides nothing
            hydrated["duplicate_of"] = dup
            hydrated["confidence"] = getattr(record, "confidence", None)
            if hydrated["lane"] != lane:
                continue  # vector metadata is stale; the record is authoritative
            if (
                lane == "library"
                and decay
                and not hydrated.get("pinned_by")
                and _is_stale(hydrated.get("stale_after"))
            ):
                continue  # ADR-003 §5: decayed library is out of recall unless pinned
            if not _passes_governance(
                hydrated, request.authority, request.include_rejected
            ):
                continue

            if request.published_after is not None and not _published_since(
                record, meta, lane, request.published_after
            ):
                continue

            score = similarity
            if request.recency_weight > 0:
                recency = _recency_weight_fn(
                    _age_seconds(_published_at(record, meta)[0]), request.half_life_days
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
        return scored, len(results or [])

    def search_lane(lane: str) -> list[dict]:
        """The lane's best ``limit``. Similarity picks the candidates, so a date
        cutoff can reject a whole pool of old near-matches: widen until enough
        records pass or the lane runs out."""
        k = _candidate_k(request)
        while True:
            scored, fetched = rank_candidates(lane, k)
            if (
                (request.published_after is None and allowed is None)
                or len(scored) >= request.limit
                or fetched < k
            ):
                break
            if k >= MAX_CANDIDATES:
                warnings.append(
                    f"searched {k} {lane} candidates under a date or source filter; "
                    "more matches may be hidden"
                )
                break
            k = min(k * CANDIDATE_GROWTH, MAX_CANDIDATES)
        return scored[: request.limit]

    top = search_lane("record") if "record" in request.lanes else []
    background = search_lane("library") if "library" in request.lanes else []
    await _annotate_origins([s["shape"] for s in top + background], kg)
    memories = [_fit_content(s["shape"], request) for s in top]

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
                        "lanes": request.lanes,
                        "include_rejected": request.include_rejected,
                        **({"source": source_info["id"]} if source_info else {}),
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
                        for rank, m in enumerate(top + background)
                    ],
                )
                await session.commit()
        except Exception as e:
            logger.warning("[RECALL] Trace write failed (non-fatal): %s", e)
            warnings.append("trace not recorded")

    response = {
        "request_id": request_id,
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "memories": memories,
        "background": [_fit_content(s["shape"], request) for s in background],
        "warnings": warnings,
    }
    if source_info:
        response["source"] = source_info
    return response
