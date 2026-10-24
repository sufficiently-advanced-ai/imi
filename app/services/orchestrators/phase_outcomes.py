"""Per-phase outcomes for the ingest pipeline.

A phase either raises (the job fails, as before) or returns. A phase that
returns can still have degraded: an optional step crashed and the phase
carried on. Phases report that through ``phase_report()`` instead of only
logging it, so the job record says what actually happened:

    ok       the phase did its work
    empty    the phase ran and found nothing (no signals, no entities)
    skipped  the phase was intentionally not run (lane rules, no LLM client)
    failed   part or all of the phase crashed; the pipeline continued

``_run_phase`` opens a report per phase and appends the resulting record to
``job["phase_outcomes"]``. Records are plain JSON-serializable dicts:

    {"phase": str, "outcome": "ok"|"empty"|"skipped"|"failed",
     "detail": str | None, "retryable": bool}

Job status is derived from them (``derive_job_status``): any failed outcome
turns ``completed`` into ``completed_with_warnings``. A failure reported as
``critical`` (the job's own output was not persisted, e.g. the corpus write in
PERSIST) turns it into ``failed``; those phases are listed in
``job["critical_failures"]``.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

OUTCOME_OK = "ok"
OUTCOME_EMPTY = "empty"
OUTCOME_SKIPPED = "skipped"
OUTCOME_FAILED = "failed"

STATUS_COMPLETED = "completed"
STATUS_COMPLETED_WITH_WARNINGS = "completed_with_warnings"
STATUS_FAILED = "failed"

# Severity order: a phase that both found nothing and crashed reports failed.
_RANK = {OUTCOME_OK: 0, OUTCOME_SKIPPED: 1, OUTCOME_EMPTY: 2, OUTCOME_FAILED: 3}

_DETAIL_MAX = 500


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _DETAIL_MAX else text[: _DETAIL_MAX - 1] + "…"


def describe_error(context: str, exc: BaseException) -> str:
    """One-line detail for a failed step: ``"<context>: <Type>: <message>"``."""
    message = str(exc).strip()
    kind = type(exc).__name__
    return _short(f"{context}: {kind}: {message}" if message else f"{context}: {kind}")


class PhaseReport:
    """Collects what one phase run reports. Worst outcome wins."""

    __slots__ = ("phase", "outcome", "details", "retryable", "critical")

    def __init__(self, phase: str):
        self.phase = phase
        self.outcome = OUTCOME_OK
        self.details: list[str] = []
        self.retryable = False
        self.critical = False

    def _set(self, outcome: str, detail: str | None) -> None:
        if _RANK[outcome] > _RANK[self.outcome]:
            self.outcome = outcome
        if detail and detail not in self.details:
            self.details.append(_short(detail))

    def failed(self, detail: str, *, retryable: bool = True, critical: bool = False) -> None:
        """A step of this phase crashed and the phase continued without it.

        ``retryable``: whether re-running the ingest could plausibly succeed
        (transient service/LLM/network errors), as opposed to a deterministic
        failure such as bad configuration. ``critical``: the job's own output
        was not persisted, so the job is failed rather than warned.
        """
        self._set(OUTCOME_FAILED, detail)
        self.retryable = self.retryable or retryable
        self.critical = self.critical or critical

    def empty(self, detail: str | None = None) -> None:
        """The phase ran and found nothing."""
        self._set(OUTCOME_EMPTY, detail)

    def skipped(self, reason: str) -> None:
        """The phase was intentionally not run."""
        self._set(OUTCOME_SKIPPED, reason)

    def as_record(self) -> dict[str, Any]:
        failed = self.outcome == OUTCOME_FAILED
        return {
            "phase": self.phase,
            "outcome": self.outcome,
            "detail": "; ".join(self.details) or None,
            "retryable": bool(failed and self.retryable),
        }


_current: ContextVar[PhaseReport | None] = ContextVar("ingest_phase_report", default=None)


def phase_report() -> PhaseReport:
    """The report of the phase currently running, or a detached one.

    Detached when a phase function is called directly (unit tests, the
    rebuild helpers) — reports then go nowhere, which is the old behavior.
    """
    report = _current.get()
    return report if report is not None else PhaseReport("detached")


def open_report(phase: str):
    """Start a report for ``phase``; returns ``(report, token)``."""
    report = PhaseReport(phase)
    return report, _current.set(report)


def close_report(token) -> None:
    _current.reset(token)


def warnings_from(outcomes: list[dict[str, Any]] | None) -> list[str]:
    """Human-readable warnings for the failed phases, in phase order."""
    out = []
    for rec in outcomes or []:
        if rec.get("outcome") == OUTCOME_FAILED:
            detail = rec.get("detail") or "failed"
            out.append(f"{rec.get('phase')}: {detail}")
    return out


def derive_job_status(
    outcomes: list[dict[str, Any]] | None, critical_failures: list[str] | None = None
) -> str:
    """Terminal status for a job whose phases all returned."""
    if critical_failures:
        return STATUS_FAILED
    failed = any(r.get("outcome") == OUTCOME_FAILED for r in outcomes or [])
    return STATUS_COMPLETED_WITH_WARNINGS if failed else STATUS_COMPLETED
