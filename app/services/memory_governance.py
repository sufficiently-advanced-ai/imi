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

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.models.signal import SignalAuditRecord
from app.services.signal_audit import (
    REPO_ROOT,
    SignalAuditStore,
    persist_failed_record,
    review_with_audit,
)

logger = logging.getLogger(__name__)


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


def persist_audited_transition(
    audit_store: SignalAuditStore,
    audit_record: SignalAuditRecord,
    persist: Callable[[], Any],
    *,
    log_prefix: str = "[GOVERNANCE]",
) -> dict[str, Any] | None:
    """Audit-first persistence of a governance transition (same contract as signals).

    Appends ``audit_record`` (fsynced) and only then calls ``persist``. Returns
    None on success, or the failure response to return to the caller:

    - audit append fails: nothing is persisted; ``success: False``.
    - ``persist`` fails after the row was written: a compensating
      ``<action>_failed`` row is appended so the log stays truthful;
      ``success: False``.
    """
    record_id = audit_record.signal_id
    action = audit_record.action
    try:
        audit_store.append(audit_record)
    except Exception as e:
        logger.error(
            "%s AUDIT APPEND FAILED for %s action=%s; review NOT applied: %s",
            log_prefix, record_id, action, e, exc_info=True,
        )
        return {
            "success": False,
            "review_applied": False,
            "error": f"Audit append failed; review not applied: {e}",
            "audit_error": str(e),
        }
    try:
        persist()
    except Exception as e:
        logger.error(
            "%s PERSIST FAILED for %s action=%s after audit row %s; review NOT "
            "applied: %s",
            log_prefix, record_id, action, audit_record.id, e, exc_info=True,
        )
        compensating = persist_failed_record(audit_record, str(e))
        compensating_id: str | None = None
        try:
            audit_store.append(compensating)
            compensating_id = compensating.id
        except Exception as ce:
            logger.error(
                "%s Compensating audit row FAILED for %s (audit row %s records an "
                "attempt that was not applied): %s",
                log_prefix, record_id, audit_record.id, ce, exc_info=True,
            )
        return {
            "success": False,
            "review_applied": False,
            "error": f"Record persist failed; review not applied: {e}",
            "audit_row_id": audit_record.id,
            "compensating_audit_row_id": compensating_id,
        }
    return None


def capture_audit_store(repo_root: Path = REPO_ROOT) -> SignalAuditStore:
    """Audit store for capture (and agent-memory) rows at memory/audit/."""
    return SignalAuditStore(
        audit_dir=Path(repo_root) / "memory" / "audit",
        repo_root=Path(repo_root),
    )
