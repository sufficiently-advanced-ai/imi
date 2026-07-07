"""Audited review over non-signal governed records (Phase 1 of OB1 absorption).

``signal_governance.apply_review`` and ``signal_audit.review_with_audit`` are
duck-typed over the shared governance field surface, so captures (and later
agent memories) reuse the SAME state machine and audit record — this module
only names the record kind and the audit location. Capture audit rows live at
``memory/audit/{record_id}.jsonl`` (generalizing ``signals/audit/``), keyed by
record id with no back-link so history survives hard deletion.

See the OB1 absorption plan (Phase 1) and
docs/prd/memory-governance-and-retrieval-prd.md §10 (G2/G4).
"""

from __future__ import annotations

from pathlib import Path

from app.models.signal import SignalAuditRecord
from app.services.signal_audit import REPO_ROOT, SignalAuditStore, review_with_audit


def review_record_with_audit(
    record,
    action: str,
    *,
    actor: str | None = None,
    superseded_by: str | None = None,
    record_kind: str = "capture",
) -> tuple[object, SignalAuditRecord]:
    """Apply a review action to any governed record, emitting a typed audit row.

    Thin delegation to ``review_with_audit`` — one state machine for all
    record kinds (plan rule: never duplicate the governance transitions).
    """
    return review_with_audit(
        record,
        action,
        actor=actor,
        superseded_by=superseded_by,
        record_kind=record_kind,
    )


def capture_audit_store(repo_root: Path = REPO_ROOT) -> SignalAuditStore:
    """Audit store for capture (and agent-memory) rows at memory/audit/."""
    return SignalAuditStore(
        audit_dir=Path(repo_root) / "memory" / "audit",
        repo_root=Path(repo_root),
    )
