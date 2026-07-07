"""Signal indexing glue — thin facade for index-on-write and backfill (G3).

This module is the single place that resolves the vector_store/embedder pair
and calls ``signal_retrieval.index_signal``.  Both ``SignalStore`` (index-on-
write) and ``scripts/backfill_signal_index.py`` import from here so the
lazy-import/None-handling logic lives in ONE place.

Public API
----------
index_meeting_signals(meeting_signals) -> (total, indexed, skipped)
    Index every signal in a MeetingSignals container. Returns counts.

index_one(signal) -> str | None
    Index a single Signal. Returns the vector id or None (on error / stack absent).

vector_stack_available() -> bool
    Quick check: True when the live SemanticaKnowledge is available.

resolve_vector_store(default) -> store
    Backend selection (issue #951): ``VECTOR_BACKEND=sqlite`` (community
    default) returns a per-tenant persistent ``SqliteVectorStore``;
    ``pgvector`` (hosted) a per-tenant ``PgVectorStore``; ``faiss`` returns
    ``default`` (the legacy Semantica in-memory store) unchanged.

backfill_signals(signal_store=None) -> (total, indexed, skipped)
    Index every persisted signal for the current tenant (restart recovery /
    migration backfill). Persistent with sqlite/pgvector; idempotent
    (upsert by id).

backfill_captures(capture_store=None) / backfill_agent_memories(memory_store=None)
    Same contract for the other governed record kinds. POST
    /api/admin/backfill-memory-index runs all three.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.signal import MeetingSignals, Signal

logger = logging.getLogger(__name__)


def _get_semantica():
    """Resolve the live SemanticaKnowledge instance. Returns None if unavailable."""
    try:
        from app.services.graph.factory import get_semantica_knowledge

        return get_semantica_knowledge()
    except Exception:
        return None


# Per-tenant PgVectorStore cache (issue #951). Each store owns an async engine,
# so construction is not free — build once per tenant per process. The lock
# makes first-resolution atomic: concurrent callers must not construct
# duplicate engines (the loser would be abandoned without dispose()).
_pg_stores: dict[str, object] = {}
_pg_stores_lock = threading.Lock()

# Per-tenant SqliteVectorStore cache. Construction is cheap (a file open),
# but caching keeps parity with the pgvector path and one instance per
# (tenant, process) keeps the WAL connection churn predictable.
# Failed constructions are cached too (_sqlite_unavailable): resolve fires on
# every record write, so a persistent misconfiguration must degrade once per
# process — not retry mkdir+construct under the global lock and re-log the
# warning on every call.
_sqlite_stores: dict[str, object] = {}
_sqlite_unavailable: set[str] = set()
_sqlite_stores_lock = threading.Lock()


def resolve_vector_store(default):
    """Return the vector store for the current tenant.

    ``VECTOR_BACKEND=pgvector`` (hosted): a per-tenant ``PgVectorStore`` —
    persistent across restarts, upsert-by-id, RLS-scoped by the tenant GUC.
    ``VECTOR_BACKEND=sqlite`` (community): a per-tenant ``SqliteVectorStore``
    over a sidecar file beside ``DATABASE_PATH`` — persistent, upsert-by-id,
    and metadata-carrying (the semantica FAISS facade drops metadata, which
    silently empties governed recall).
    Any other value (community default ``faiss``): ``default`` is returned
    unchanged, preserving the existing Semantica FAISS path exactly.

    Falls back to ``default`` (with a warning) when pgvector is requested but
    ``DATABASE_URL`` is unset, so a misconfigured deployment degrades to the
    old behavior instead of breaking saves.
    """
    from app.config import settings

    backend = getattr(settings, "VECTOR_BACKEND", "faiss")

    if backend == "sqlite":
        from pathlib import Path

        from app.core.middleware.request_context import current_tenant_id

        tenant_id = current_tenant_id.get()
        cache_key = tenant_id or ""
        if cache_key in _sqlite_unavailable:
            return default
        store = _sqlite_stores.get(cache_key)
        if store is None:
            with _sqlite_stores_lock:
                if cache_key in _sqlite_unavailable:
                    return default
                store = _sqlite_stores.get(cache_key)
                if store is None:
                    try:
                        from app.core.tenancy.backends.sqlite_vector_store import (
                            SqliteVectorStore,
                        )

                        db_dir = Path(settings.DATABASE_PATH).parent
                        db_dir.mkdir(parents=True, exist_ok=True)
                        store = SqliteVectorStore(
                            str(db_dir / "vectors.db"), tenant_id=tenant_id
                        )
                    except Exception as e:
                        # Same degradation contract as pgvector-without-
                        # DATABASE_URL: an unusable store falls back to the
                        # default instead of breaking saves/searches. The
                        # failure is cached for the process lifetime —
                        # DATABASE_PATH is env-derived, so a fix needs a
                        # restart anyway, and restarting clears the cache.
                        logger.warning(
                            "VECTOR_BACKEND=sqlite but the store at %s is "
                            "unusable (%s) — falling back to the default "
                            "vector store for this process",
                            settings.DATABASE_PATH,
                            e,
                        )
                        _sqlite_unavailable.add(cache_key)
                        return default
                    _sqlite_stores[cache_key] = store
                    logger.info("SqliteVectorStore created for tenant %r", tenant_id)
        return store

    if backend != "pgvector":
        return default

    database_url = getattr(settings, "DATABASE_URL", None)
    if not database_url:
        logger.warning(
            "VECTOR_BACKEND=pgvector but DATABASE_URL is unset — "
            "falling back to the default vector store"
        )
        return default

    from app.core.middleware.request_context import current_tenant_id

    tenant_id = current_tenant_id.get()
    store = _pg_stores.get(tenant_id)
    if store is None:
        with _pg_stores_lock:
            store = _pg_stores.get(tenant_id)
            if store is None:
                from app.core.tenancy.backends.pgvector_store import PgVectorStore

                store = PgVectorStore(database_url, tenant_id)
                _pg_stores[tenant_id] = store
                logger.info("PgVectorStore created for tenant %s", tenant_id)
    return store


def vector_stack_available() -> bool:
    """Return True when the vector store + embedder are reachable."""
    try:
        sk = _get_semantica()
        return sk is not None
    except Exception:
        return False


def index_one(signal: Signal) -> str | None:
    """Embed and store a single signal in the vector store.

    Returns the vector id on success, None on any failure or when the vector
    stack is not initialised. Never raises.
    """
    try:
        sk = _get_semantica()
        if sk is None:
            return None
        from app.services import signal_retrieval

        store = resolve_vector_store(sk.vector_store)
        return signal_retrieval.index_signal(store, sk.embedder, signal)
    except Exception as e:
        logger.warning(
            "signal_indexing.index_one failed for %s: %s", getattr(signal, "id", "?"), e
        )
        return None


def index_capture_one(capture) -> str | None:
    """Embed and store a single CapturedMemory in the vector store.

    Mirrors ``index_one`` for the capture record kind. Returns the vector id on
    success, None on any failure or when the vector stack is not initialised.
    Never raises.
    """
    try:
        sk = _get_semantica()
        if sk is None:
            return None
        from app.services import signal_retrieval

        store = resolve_vector_store(sk.vector_store)
        return signal_retrieval.index_capture(store, sk.embedder, capture)
    except Exception as e:
        logger.warning(
            "signal_indexing.index_capture_one failed for %s: %s",
            getattr(capture, "id", "?"),
            e,
        )
        return None


def index_agent_memory_one(memory) -> str | None:
    """Embed and store a single AgentMemory. Best-effort, never raises."""
    try:
        sk = _get_semantica()
        if sk is None:
            return None
        from app.services import signal_retrieval

        store = resolve_vector_store(sk.vector_store)
        return signal_retrieval.index_agent_memory(store, sk.embedder, memory)
    except Exception as e:
        logger.warning(
            "signal_indexing.index_agent_memory_one failed for %s: %s",
            getattr(memory, "id", "?"),
            e,
        )
        return None


def index_meeting_signals(meeting_signals: MeetingSignals) -> tuple[int, int, int]:
    """Index every signal in a MeetingSignals container.

    Skips signals that fail individually (best-effort). Never raises.

    Returns:
        (total, indexed, skipped) — counts across all signals in the container.
    """
    signals = getattr(meeting_signals, "signals", []) or []
    total = len(signals)
    indexed = 0
    skipped = 0

    try:
        sk = _get_semantica()
        if sk is None:
            logger.debug(
                "signal_indexing: vector stack unavailable, skipping %d signals", total
            )
            return total, 0, total

        from app.services import signal_retrieval

        store = resolve_vector_store(sk.vector_store)
        for signal in signals:
            try:
                vec_id = signal_retrieval.index_signal(store, sk.embedder, signal)
                if vec_id is not None:
                    indexed += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.warning(
                    "signal_indexing: failed to index signal %s: %s",
                    getattr(signal, "id", "?"),
                    e,
                )
                skipped += 1

    except Exception as e:
        logger.warning("signal_indexing.index_meeting_signals failed: %s", e)
        skipped = total - indexed

    return total, indexed, skipped


def reset_vector_index() -> dict:
    """Wipe the current tenant's vector index ahead of a rebuild.

    pgvector (hosted): tenant-scoped ``DELETE FROM embeddings`` — the change is
    visible to the running API immediately (shared Postgres).

    FAISS (community): swap a fresh in-memory store onto the live
    SemanticaKnowledge facade. Caveat: this only resets the index of the
    process that runs it; a separately running API process keeps its own
    in-memory index until restarted.

    Never raises (module convention): backend failures are reported as an
    "error" key so callers — the rebuild orchestrator fails its WIPE_VECTORS
    phase on it — can decide whether a partial wipe is acceptable.

    Returns a dict: {"backend": "pgvector"|"faiss"|"none", "deleted": int}
    plus "error" when the backend operation failed.
    """
    from app.config import settings

    try:
        if getattr(settings, "VECTOR_BACKEND", "faiss") == "pgvector":
            sk = _get_semantica()
            store = resolve_vector_store(sk.vector_store if sk else None)
            if store is not None and hasattr(store, "delete_all"):
                deleted = store.delete_all()
                return {"backend": "pgvector", "deleted": deleted}
            logger.warning(
                "reset_vector_index: pgvector requested but store unavailable"
            )
            return {"backend": "none", "deleted": 0}

        sk = _get_semantica()
        if sk is None:
            logger.warning(
                "reset_vector_index: vector stack unavailable, nothing to reset"
            )
            return {"backend": "none", "deleted": 0}

        from app.services.semantica_init import create_vector_store

        sk.vector_store = create_vector_store()
        logger.info(
            "reset_vector_index: fresh FAISS store swapped in (in-process only)"
        )
        return {"backend": "faiss", "deleted": -1}
    except Exception as e:
        logger.warning("reset_vector_index failed: %s", e)
        return {
            "backend": getattr(settings, "VECTOR_BACKEND", "faiss"),
            "deleted": 0,
            "error": str(e),
        }


def backfill_signals(signal_store=None) -> tuple[int, int, int]:
    """Index every persisted signal for the current tenant (issue #951).

    Used for restart recovery and post-migration backfill. With the pgvector
    backend this is idempotent (upsert by signal id); with FAISS it appends
    (the pre-existing caveat documented in scripts/backfill_signal_index.py).

    Args:
        signal_store: store to read from; defaults to the current tenant's
            signal store resolved via the tenancy container.

    Returns:
        (total, indexed, skipped) summed across all meetings.
    """
    if signal_store is None:
        from app.core.tenancy.context import current_tenant

        signal_store = current_tenant().signal_store

    total = indexed = skipped = 0
    for meeting_signals in signal_store.load_all():
        t, i, s = index_meeting_signals(meeting_signals)
        total += t
        indexed += i
        skipped += s
    logger.info(
        "backfill_signals: %d total, %d indexed, %d skipped", total, indexed, skipped
    )
    return total, indexed, skipped


def backfill_captures(capture_store=None) -> tuple[int, int, int]:
    """Re-index every capture (restart recovery / backend switch).

    Idempotent on upsert-by-id backends (sqlite, pgvector); with FAISS it
    appends (recall dedups by record id, same caveat as backfill_signals).

    Returns:
        (total, indexed, skipped) — skipped = records the best-effort indexer
        could not embed/store.
    """
    if capture_store is None:
        from app.services.memory_capture import CaptureStore

        capture_store = CaptureStore()

    total = indexed = 0
    for capture in capture_store.iter_all():
        total += 1
        if index_capture_one(capture) is not None:
            indexed += 1
    skipped = total - indexed
    logger.info(
        "backfill_captures: %d total, %d indexed, %d skipped", total, indexed, skipped
    )
    return total, indexed, skipped


def backfill_agent_memories(memory_store=None) -> tuple[int, int, int]:
    """Re-index every agent memory (restart recovery / backend switch).

    Same contract as backfill_captures.
    """
    if memory_store is None:
        from app.services.agent_memory_store import AgentMemoryStore

        memory_store = AgentMemoryStore()

    total = indexed = 0
    for memory in memory_store.iter_all():
        total += 1
        if index_agent_memory_one(memory) is not None:
            indexed += 1
    skipped = total - indexed
    logger.info(
        "backfill_agent_memories: %d total, %d indexed, %d skipped",
        total,
        indexed,
        skipped,
    )
    return total, indexed, skipped
