"""Decision-audit trail for signal governance transitions (G2).

``apply_review`` (in signal_governance) is a pure state-transition function. The
audit concern is deliberately layered *over* it here, at the service boundary:
``review_with_audit`` runs the transition and emits an immutable
``SignalAuditRecord`` with a derived reasoning string and before/after
governance snapshots, and ``SignalAuditStore`` appends those records to an
append-only JSONL log keyed by signal id.

Keeping the transition pure and the audit emission separate means the state
machine stays unit-testable without I/O, and the audit log can be wired to git
at the persistence boundary without touching the transition logic.

See docs/prd/memory-governance-and-retrieval-prd.md §10 (G2) and issue #914.
"""

import logging
import os
from pathlib import Path

from app.models.signal import Signal, SignalAuditRecord
from app.services.signal_governance import apply_review, gate_response_for_action
from app.utils.atomic_write import fsync_dir

logger = logging.getLogger(__name__)

REPO_ROOT = Path("/app/repo")
AUDIT_DIR = REPO_ROOT / "signals" / "audit"

# The governance fields snapshotted in before/after for each audit row.
# valid_to is included so the audit trail captures when a validity window
# is closed (R1.1 / Sprint 2).
_GOVERNANCE_FIELDS = (
    "provenance_status",
    "review_status",
    "can_use_as_evidence",
    "can_use_as_instruction",
    "superseded_by",
    "valid_to",
)


def _governance_snapshot(signal: Signal) -> dict:
    """Extract just the governance fields from a signal for the audit row."""
    return {field: getattr(signal, field) for field in _GOVERNANCE_FIELDS}


def _derive_reasoning(before: dict, after: dict, action: str, gate: str) -> str:
    """Build a deterministic human-readable description of the transition."""
    parts = [f"action={action}", f"gate={gate}"]
    for field in _GOVERNANCE_FIELDS:
        if before.get(field) != after.get(field):
            parts.append(f"{field} {before.get(field)!r}→{after.get(field)!r}")
    return "; ".join(parts)


def review_with_audit(
    signal: Signal,
    action: str,
    *,
    actor: str | None = None,
    superseded_by: str | None = None,
    record_kind: str = "signal",
) -> tuple[Signal, SignalAuditRecord]:
    """Apply a review action and emit an immutable audit record.

    Composes the pure ``apply_review`` transition with audit generation. The
    returned record is not persisted — call ``SignalAuditStore.append`` to do
    that — so the audit boundary stays explicit and testable.

    ``apply_review`` is duck-typed (model_copy-based), so this works for any
    governed record kind (Signal, CapturedMemory, ...) that carries the
    governance field surface; ``record_kind`` labels the audit row accordingly.

    Raises:
        ValueError: propagated from apply_review for invalid actions.
    """
    before = _governance_snapshot(signal)
    new_signal = apply_review(signal, action, superseded_by=superseded_by)
    after = _governance_snapshot(new_signal)
    gate = gate_response_for_action(action)

    record = SignalAuditRecord(
        signal_id=signal.id,
        record_kind=record_kind,
        action=action,
        gate_response=gate,
        actor=actor,
        tenant_id=signal.tenant_id,
        reasoning=_derive_reasoning(before, after, action, gate),
        before=before,
        after=after,
    )
    return new_signal, record


PERSIST_FAILED_SUFFIX = "_failed"


def persist_failed_record(original: SignalAuditRecord, error: str) -> SignalAuditRecord:
    """Compensating audit row for a transition whose state write failed.

    The governance path appends the audit row *before* persisting the signal.
    If that persist then fails, this row is appended after the original so the
    log stays truthful: the attempt is recorded, and so is the fact that it was
    not applied. Schema-compatible with every reader of the log: ``action`` is
    ``<action>_failed`` (free-form string, rendered as e.g. "confirm failed"),
    ``gate_response`` is None, and ``after`` equals ``before`` because the
    signal state did not change.
    """
    return SignalAuditRecord(
        signal_id=original.signal_id,
        record_kind=original.record_kind,
        action=f"{original.action}{PERSIST_FAILED_SUFFIX}",
        gate_response=None,
        actor=original.actor,
        tenant_id=original.tenant_id,
        reasoning=(
            f"action={original.action} not applied: signal state write failed "
            f"after audit row {original.id}; state unchanged ({error})"
        ),
        before=dict(original.before),
        after=dict(original.before),
    )


class SignalAuditStore:
    """Append-only JSONL audit log, one file per signal id.

    Keyed by signal id with no link back to the signal record, so the audit
    history survives hard deletion of the signal (openbrain memory_audit
    semantics).
    """

    def __init__(self, audit_dir: Path = AUDIT_DIR, repo_root: Path = REPO_ROOT):
        self.audit_dir = Path(audit_dir)
        self.repo_root = Path(repo_root)

    def _file_path(self, signal_id: str) -> Path:
        return self.audit_dir / f"{signal_id}.jsonl"

    def relative_path(self, signal_id: str) -> str:
        """Repo-relative path for git operations, derived from ``audit_dir``.

        Kept consistent with ``_file_path`` so git references the file that is
        actually written, even when a custom ``audit_dir`` is configured.
        """
        return str(self._file_path(signal_id).relative_to(self.repo_root))

    def append(self, record: SignalAuditRecord) -> Path:
        """Append one audit record. Never rewrites existing rows.

        Flushes and fsyncs before returning, so once this returns the row is
        durable; callers that gate a state change on the audit row (see
        ``chat_tools.update_signal``) can rely on it surviving a crash.
        """
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        path = self._file_path(record.signal_id)
        is_new = not path.exists()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if is_new:
            # First row for this signal: make the new directory entry durable too.
            fsync_dir(self.audit_dir)
        return path

    def read_for_signal(self, signal_id: str) -> list[SignalAuditRecord]:
        """Return the audit history for a signal, in append order."""
        path = self._file_path(signal_id)
        if not path.is_file():
            return []
        records: list[SignalAuditRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(SignalAuditRecord.model_validate_json(line))
            except Exception as e:
                logger.warning(
                    "[AUDIT] Skipping corrupt audit row in %s: %s", path.name, e
                )
        return records
