"""Graph rebuild runner and stateful-startup helpers.

Two startup modes (``NEO4J_REBUILD_ON_STARTUP``):

* **rebuild** (stateless hosts): a full build from the corpus runs before the
  app serves — now with batched UNWIND writes, so seconds rather than minutes.
  The Semantica vector build follows *after* the port is bound, off-loop.
* **stateful** (docker-compose default — persistent Neo4j/data volumes): the
  graph in Neo4j is trusted, in-memory caches are synced from it (~1 s), and
  after the app is serving a reconcile pass re-ingests only the corpus files
  that changed while the container was down (``corpus_manifest.py``). If the
  vector index is empty it is rebuilt from Neo4j — no corpus read.

``run_full_rebuild`` is also what ``POST /api/admin/rebuild-graph`` calls.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Coroutine
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_status: dict[str, Any] = {
    "state": "idle",  # idle | running | completed | failed
    "source": None,
    "clean": False,
    "started_at": None,
    "finished_at": None,
    "stats": {},
    "error": None,
}
_rebuild_lock = asyncio.Lock()


def _accepts_kwarg(fn: Any, name: str) -> bool:
    """True if ``fn`` takes ``name`` (or **kwargs). Unknown signatures → True."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return True
    return name in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def get_rebuild_status() -> dict[str, Any]:
    return {**_status, "stats": dict(_status.get("stats") or {})}


def is_rebuild_running() -> bool:
    return _rebuild_lock.locked()


# ── dependency lookups (late imports: this module is imported by app.main) ──


def _get_kg() -> Any | None:
    try:
        from app.services.graph import get_knowledge_graph

        return get_knowledge_graph()
    except Exception as e:
        logger.debug("knowledge graph unavailable: %s", e)
        return None


def _get_sk() -> Any | None:
    try:
        from app.services.graph.factory import get_semantica_knowledge

        return get_semantica_knowledge()
    except Exception as e:
        logger.debug("semantica unavailable: %s", e)
        return None


def _get_neo4j() -> Any | None:
    try:
        from app.neo4j_client import get_neo4j_client

        client = get_neo4j_client()
        return client if getattr(client, "is_initialized", False) else None
    except Exception as e:
        logger.debug("neo4j client unavailable: %s", e)
        return None


def make_reconciler(kg: Any | None = None, sk: Any | None = None) -> Any | None:
    """Build a ``CorpusReconciler`` for the default tenant, or None."""
    from app.services.corpus_manifest import CorpusReconciler

    kg = kg or _get_kg()
    neo4j = _get_neo4j()
    if kg is None or neo4j is None or not hasattr(kg, "ingest_files"):
        return None
    git_ops = getattr(kg, "git_ops", None)
    repo_path = getattr(git_ops, "repo_path", None)
    if not repo_path:
        return None
    return CorpusReconciler(kg=kg, neo4j=neo4j, repo_path=repo_path, sk=sk, git_ops=git_ops)


# ── full rebuild ────────────────────────────────────────────────────────────


async def run_full_rebuild(
    *,
    clean: bool = False,
    source: str = "manual",
    reingest_signals: bool = True,
    include_semantica: bool = True,
) -> dict[str, Any]:
    """Rebuild the legacy graph (and optionally the Semantica layer) from the corpus.

    Raises ``RuntimeError`` if a rebuild is already running. Records the corpus
    manifest baseline on success so the next stateful boot reconciles from it.
    """
    if _rebuild_lock.locked():
        raise RuntimeError("A graph rebuild is already running")

    async with _rebuild_lock:
        _status.update(
            state="running", source=source, clean=clean,
            started_at=_now(), finished_at=None, stats={}, error=None,
        )
        stats: dict[str, Any] = {}
        try:
            kg = _get_kg()
            if kg is None or not hasattr(kg, "build_graph"):
                raise RuntimeError("Knowledge graph is not available")

            logger.info("[REBUILD] legacy graph build starting (clean=%s, source=%s)", clean, source)
            kwargs: dict[str, Any] = {"force_rebuild": True, "clean": clean}
            if _accepts_kwarg(kg.build_graph, "reingest_signals"):
                kwargs["reingest_signals"] = reingest_signals
            graph_stats = await kg.build_graph(**kwargs)
            stats["graph"] = {k: v for k, v in (graph_stats or {}).items() if isinstance(v, int | float | str)}

            sk = _get_sk()
            if include_semantica and sk is not None:
                stats["semantica"] = await semantica_full_build(sk)

            reconciler = make_reconciler(kg, sk)
            if reconciler is not None:
                await reconciler.record_full_build(source=source)
                stats["manifest"] = "recorded"

            _status.update(state="completed", finished_at=_now(), stats=stats)
            logger.info("[REBUILD] completed: %s", stats)
        except Exception as e:
            _status.update(state="failed", finished_at=_now(), stats=stats, error=str(e))
            logger.exception("[REBUILD] failed")
            raise
    return get_rebuild_status()


async def semantica_full_build(sk: Any | None = None) -> dict[str, Any]:
    """Rebuild the Semantica graph/vector layer. Safe to run while serving."""
    sk = sk or _get_sk()
    if sk is None or not hasattr(sk, "build_graph"):
        return {"status": "skipped", "reason": "semantica_unavailable"}
    try:
        result = await sk.build_graph(force_rebuild=True, clean=False)
        return {k: v for k, v in (result or {}).items() if isinstance(v, int | float | str)}
    except Exception as e:
        logger.warning("[REBUILD] semantica build failed: %s", e)
        return {"status": "error", "error": str(e)}


# ── stateful boot helpers ───────────────────────────────────────────────────


async def startup_reconcile() -> dict[str, Any]:
    """Stateful boot: ingest only what changed on disk since the last build.

    Falls back to a full rebuild when Neo4j holds no entities at all (first
    boot on an empty volume).
    """
    reconciler = make_reconciler(sk=_get_sk())
    if reconciler is None:
        return {"action": "skipped", "reason": "graph_or_neo4j_unavailable"}

    result = await reconciler.reconcile()
    logger.info("[RECONCILE] %s", result)

    if result.get("action") == "full_rebuild_required":
        logger.info("[RECONCILE] graph is empty — running a full build in the background")
        status = await run_full_rebuild(source="startup_empty_graph")
        result["rebuild"] = status.get("state")
        result["rebuild_stats"] = status.get("stats")
    return result


async def bootstrap_vector_index() -> dict[str, Any]:
    """Ensure the entity vector index is populated, rebuilding from Neo4j if not.

    A counting store (sqlite/pgvector) is left alone when it already has entity
    vectors; a non-counting, in-memory store (FAISS) is always empty after a
    restart and is reindexed. Either way: no corpus read, all off-loop.
    """
    sk = _get_sk()
    if sk is None or not hasattr(sk, "reindex_entities_from_graph"):
        return {"action": "skipped", "reason": "semantica_unavailable"}

    count: int | None = None
    search = getattr(sk, "search", None)
    if search is not None and hasattr(search, "entity_vector_count"):
        count = await search.entity_vector_count()
    if count:
        return {"action": "unchanged", "entity_vectors": count}

    indexed = await sk.reindex_entities_from_graph()
    return {"action": "reindexed", "entity_vectors": indexed, "store_counts": count is not None}


# ── background task plumbing ────────────────────────────────────────────────


def spawn_background(coro: Coroutine[Any, Any, Any] | Awaitable[Any], name: str) -> asyncio.Task:
    """Schedule ``coro`` on the loop, register it with the lifecycle manager,
    and log its outcome. Exceptions are logged, never lost."""

    async def _runner() -> Any:
        try:
            result = await coro
            logger.info("[BACKGROUND] %s finished: %s", name, _brief(result))
            return result
        except asyncio.CancelledError:
            logger.info("[BACKGROUND] %s cancelled", name)
            raise
        except Exception:
            logger.exception("[BACKGROUND] %s failed", name)
            return None

    task = asyncio.create_task(_runner(), name=name)
    try:
        from app.core.lifecycle import get_lifecycle_manager

        get_lifecycle_manager().add_background_task(task, name)
    except Exception:
        pass
    return task


def _brief(value: Any, limit: int = 300) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."
