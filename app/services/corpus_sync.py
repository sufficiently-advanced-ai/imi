"""Corpus sync status — the last reconcile/rebuild outcome, for operators.

``CorpusReconciler.reconcile`` can *adopt* the current disk state as the graph
baseline without ingesting it (manifest missing, build id mismatch, graph
predating the manifest). That is deliberate (see ``corpus_manifest._adopt``),
but its only trace used to be a WARNING log line, and the next reconcile
treats the adopted files as unchanged. This module records the outcome so
``/health/ready``, ``/health/startup`` and the UI can say "the graph may be
stale — run a rebuild" until a full rebuild actually happens.

Statuses:

* ``pending``          — a deferred reconcile is queued but has not started
* ``reconciling``      — a reconcile or full rebuild is running
* ``synced``           — the graph matches the corpus (unchanged, reconciled,
                         or a full rebuild completed)
* ``adopted``          — the baseline was adopted without ingesting; files
                         changed before it may be missing from the graph
* ``rebuild_required`` — a full rebuild was needed and did not complete
* ``unknown``          — no reconcile has run (disabled, unavailable, errored)

``adopted`` survives restarts: the adoption reason is stored as ``source`` on
the ``:_CorpusState`` node, and the reconciler reports it back as
``baseline_source`` until a full rebuild writes a new baseline.
Readiness is unaffected — sync and readiness are separate by design.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# ``record_full_build(source=...)`` values written by ``CorpusReconciler._adopt``.
ADOPTION_SOURCES = frozenset({"graph_without_build_id", "manifest_missing", "manifest_mismatch"})

STATUSES = ("pending", "reconciling", "synced", "adopted", "rebuild_required", "unknown")

REBUILD_HINT = "run POST /api/admin/rebuild-graph to re-ingest the corpus"

_COUNT_KEYS = (
    "files", "entities", "added", "modified", "removed", "graph_ingested",
    "semantica_indexed", "semantica_vectors_removed", "retry_next_boot",
)

_state: dict[str, Any] = {}


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def set_corpus_sync(
    status: str,
    *,
    reason: str | None = None,
    action: str | None = None,
    counts: dict[str, Any] | None = None,
    build_id: str | None = None,
    baseline_at: str | None = None,
    error: str | None = None,
) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown corpus sync status: {status}")
    _state.clear()
    _state.update(
        status=status, reason=reason, action=action, counts=dict(counts or {}),
        build_id=build_id, baseline_at=baseline_at, error=error, updated_at=_now(),
    )


def reset_corpus_sync() -> None:
    """Back to the boot default (also used by tests)."""
    set_corpus_sync("unknown", reason="not_started")
    _state["updated_at"] = None


reset_corpus_sync()


def record_reconcile_result(result: dict[str, Any] | None) -> None:
    """Derive the sync status from a ``CorpusReconciler.reconcile`` summary
    (as extended by ``graph_rebuild.startup_reconcile``)."""
    result = result or {}
    action = result.get("action")
    common: dict[str, Any] = {
        "action": action,
        "counts": {k: result[k] for k in _COUNT_KEYS if isinstance(result.get(k), int)},
        "build_id": result.get("build_id"),
        "baseline_at": result.get("baseline_at"),
    }

    if action == "adopted":
        set_corpus_sync("adopted", reason=result.get("reason"), **common)
    elif action in ("unchanged", "reconciled"):
        baseline_source = result.get("baseline_source")
        if baseline_source in ADOPTION_SOURCES:
            # An adopted baseline stays adopted until a full rebuild replaces
            # it: reconciling the delta since adoption does not ingest what
            # was blessed without being read.
            set_corpus_sync("adopted", reason=baseline_source, **common)
        else:
            set_corpus_sync("synced", reason=action, **common)
    elif action == "full_rebuild_required":
        if result.get("rebuild") == "completed":
            set_corpus_sync("synced", reason="full_rebuild", **common)
        else:
            set_corpus_sync(
                "rebuild_required", reason=result.get("reason") or "graph_empty",
                error=result.get("error"), **common,
            )
    else:
        set_corpus_sync(
            "unknown", reason=result.get("reason") or action or "no_result",
            error=result.get("error"), **common,
        )


def _warnings(state: dict[str, Any]) -> list[str]:
    status = state.get("status")
    warnings: list[str] = []
    if status == "adopted":
        when = f" at {state['baseline_at']}" if state.get("baseline_at") else ""
        warnings.append(
            f"Corpus baseline was adopted without ingesting ({state.get('reason')}){when}: "
            f"files changed before then may be missing from the graph — {REBUILD_HINT}."
        )
    elif status == "rebuild_required":
        detail = f": {state['error']}" if state.get("error") else ""
        warnings.append(
            f"The graph needs a full rebuild ({state.get('reason')}{detail}) — {REBUILD_HINT}."
        )
    retry = (state.get("counts") or {}).get("retry_next_boot")
    if retry:
        warnings.append(
            f"{retry} corpus file(s) failed to reconcile and will be retried on the next boot."
        )
    return warnings


def get_corpus_sync() -> dict[str, Any]:
    """Public snapshot: the recorded state plus ``degraded`` and ``warnings``."""
    snapshot = {**_state, "counts": dict(_state.get("counts") or {})}
    warnings = _warnings(snapshot)
    snapshot["degraded"] = bool(warnings)
    snapshot["warnings"] = warnings
    return snapshot
