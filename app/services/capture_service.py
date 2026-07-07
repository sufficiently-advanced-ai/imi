"""Capture orchestrator — the first production caller of the G4 capture layer.

Wires the dormant CaptureStore into a live flow (Phase 1 of the OB1
absorption): persist → enrich (best-effort) → index (best-effort) → audit row
→ ONE git commit (capture file + audit JSONL together). Persist-first
ordering is the G4 guarantee: enrichment, indexing, and git are all
best-effort and can never lose the capture.

Governance fields are server-injected (ADR-002): captures enter as
``imported`` evidence-grade; ``review_capture`` is the only governance entry
point, routing through the shared audited state machine.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.git_ops import git_ops
from app.models.signal import SignalAuditRecord
from app.services import signal_indexing
from app.services.capture_enrichment import enrich_capture
from app.services.memory_capture import REPO_ROOT, CaptureStore
from app.services.memory_governance import (
    capture_audit_store,
    review_record_with_audit,
)
from app.services.signal_audit import _governance_snapshot

logger = logging.getLogger(__name__)


def _resolve_claude_client():
    """Best-effort default ClaudeClient; None degrades enrichment to fallback."""
    try:
        from app.services.claude_client import get_claude_client

        return get_claude_client()
    except Exception as e:
        logger.warning("[CAPTURE] No Claude client available: %s", e)
        return None


def _capture_dict(memory) -> dict[str, Any]:
    return memory.model_dump()


async def _commit_capture_files(
    store: CaptureStore, audit_store, memory_id: str, message: str
) -> bool:
    """One commit for the capture JSON + its audit JSONL. Best-effort."""
    try:
        paths = [
            store.relative_path(memory_id),
            audit_store.relative_path(memory_id),
        ]
        await git_ops.commit_and_push(paths, message)
        return True
    except Exception as e:
        logger.warning("[CAPTURE] Git commit failed (non-fatal): %s", e)
        return False


async def capture_and_persist(
    content: str,
    source: str = "manual",
    source_id: str | None = None,
    *,
    tenant_id: str | None = None,
    tags: list[str] | None = None,
    source_date: str | None = None,
    actor: str | None = None,
    claude_client=None,
    store: CaptureStore | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Capture a thought end-to-end. Returns a result dict, never raises."""
    try:
        store = store or CaptureStore()
        result = store.capture(
            content,
            source,
            source_id,
            tenant_id=tenant_id,
            tags=tags,
            source_date=source_date,
        )
        memory = result.memory
        if result.deduped:
            return {
                "success": True,
                "id": memory.id,
                "deduped": True,
                "enrichment": memory.enrichment,
                "vector_indexed": False,
                "committed": False,
                "capture": _capture_dict(memory),
            }

        # Enrichment (best-effort; persist-first — the capture already exists)
        try:
            enrichment = await enrich_capture(
                content, claude_client=claude_client or _resolve_claude_client()
            )
        except Exception as e:
            from app.services.capture_enrichment import FALLBACK_METADATA

            logger.warning("[CAPTURE] Enrichment failed (non-fatal): %s", e)
            enrichment = dict(FALLBACK_METADATA)
        memory = memory.model_copy(update={"enrichment": enrichment})
        store.update(memory)

        # Vector index (best-effort)
        vec_id = signal_indexing.index_capture_one(memory)

        # Audit row: the capture event itself (openbrain thought_audit semantics)
        audit_store = capture_audit_store(repo_root=repo_root)
        audit_record = SignalAuditRecord(
            signal_id=memory.id,
            record_kind="capture",
            action="capture",
            actor=actor,
            tenant_id=memory.tenant_id,
            reasoning=f"captured from source={source}",
            before={},
            after=_governance_snapshot(memory),
        )
        audit_error: str | None = None
        try:
            audit_store.append(audit_record)
        except Exception as e:
            logger.error(
                "[CAPTURE] AUDIT APPEND FAILED for %s: %s", memory.id, e, exc_info=True
            )
            audit_error = str(e)

        committed = await _commit_capture_files(
            store, audit_store, memory.id, f"capture: add {memory.id} ({source})"
        )

        result_dict: dict[str, Any] = {
            "success": True,
            "id": memory.id,
            "deduped": False,
            "enrichment": enrichment,
            "vector_indexed": vec_id is not None,
            "committed": committed,
            "capture": _capture_dict(memory),
        }
        if audit_error is not None:
            # Capture persisted but its G2 audit row is missing — surface it
            # (mirrors review_capture's audit_error contract).
            result_dict["audit_error"] = audit_error
        return result_dict
    except Exception as e:
        logger.error("[CAPTURE] capture_and_persist failed: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


async def review_capture(
    capture_id: str,
    action: str,
    *,
    actor: str | None = None,
    superseded_by: str | None = None,
    store: CaptureStore | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Apply an audited governance transition to a capture.

    Mirrors chat_tools.update_signal's governance path: transition (pure) →
    persist → audit append (the G2 guarantee) → git commit (best-effort) →
    re-index (best-effort; stale FAISS vectors are handled at recall time).
    """
    try:
        store = store or CaptureStore()
        memory = store.get(capture_id)
        if memory is None:
            return {"success": False, "error": f"Capture '{capture_id}' not found"}

        try:
            new_memory, audit_record = review_record_with_audit(
                memory,
                action,
                actor=actor,
                superseded_by=superseded_by,
                record_kind="capture",
            )
        except ValueError as e:
            return {"success": False, "error": str(e)}

        store.update(new_memory)

        audit_store = capture_audit_store(repo_root=repo_root)
        try:
            audit_store.append(audit_record)
        except Exception as e:
            logger.error(
                "[CAPTURE] AUDIT APPEND FAILED for %s action=%s: %s",
                capture_id,
                action,
                e,
                exc_info=True,
            )
            return {
                "success": True,
                "review_applied": True,
                "audit_error": str(e),
                "capture": _capture_dict(new_memory),
            }

        committed = await _commit_capture_files(
            store, audit_store, capture_id, f"audit: {action} capture {capture_id}"
        )

        signal_indexing.index_capture_one(new_memory)

        return {
            "success": True,
            "review_applied": True,
            "audit_row_id": audit_record.id,
            "gate_response": audit_record.gate_response,
            "committed": committed,
            "capture": _capture_dict(new_memory),
        }
    except Exception as e:
        logger.error("[CAPTURE] review_capture failed: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}
