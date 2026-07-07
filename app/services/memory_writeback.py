"""Writeback service — agents persist typed operational memory (Phase 2).

Ports OB1's agent-memory-api writeback (integrations/agent-memory-api) with
the ADR-002 hardening:

  - ``unsafe_reasons`` — faithful port of OB1's gate: private keys, api keys,
    credential-like strings, large code blocks, raw-transcript-likeness. An
    unsafe row rejects the WHOLE batch (atomic — nothing persists).
  - Provenance CLAMP — callers may suggest observed/inferred/generated only.
    OB1 lets a caller mint instruction-grade via provenance "imported"
    (defaultInstruction); imi rejects that at the schema layer: agent
    writeback NEVER produces instruction-grade memory (review is the only
    path, per ADR-002).
  - Fan-out — memory_payload lists become typed AgentMemory rows; next_steps
    become work_log rows with the "Next step:" prefix; artifacts become
    artifact_reference rows.
  - Idempotent replay — per-row key ``{idempotency_key}:{index}``; replaying
    a request returns the same ids with no new rows and no commit.
  - ONE git commit per batch (repo-bloat control) + audit row per memory.

Schema version: ``imi.memory.writeback.v1``.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.git_ops import git_ops
from app.models.agent_memory import AgentMemory, SourceRef
from app.models.signal import SignalAuditRecord
from app.services.agent_memory_store import REPO_ROOT, AgentMemoryStore
from app.services.memory_capture import content_fingerprint
from app.services.memory_governance import capture_audit_store
from app.services.signal_audit import _governance_snapshot

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "imi.memory.writeback.v1"

# Provenance statuses an agent may claim (ADR-002 clamp: never
# user_confirmed/imported — those are review outcomes, not writeback inputs).
_WRITABLE_PROVENANCE = ("observed", "inferred", "generated")

_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")
_API_KEY_RE = re.compile(r"(?:sk-[A-Za-z0-9_-]{20,}|sk-or-v1-[A-Za-z0-9_-]{20,})")
_CREDENTIAL_RE = re.compile(r"(?:password|passwd|secret|token)\s*[:=]\s*\S{12,}", re.I)
_TRANSCRIPT_LINE_RE = re.compile(r"^(user|assistant|system|agent|human):", re.I)

# Guards the idempotency check-then-save loop against thread-executor callers.
_writeback_lock = threading.Lock()


def unsafe_reasons(text: str) -> list[str]:
    """Content-safety gate for write-back (faithful OB1 port)."""
    reasons: list[str] = []
    if _PRIVATE_KEY_RE.search(text):
        reasons.append("private_key")
    if _API_KEY_RE.search(text):
        reasons.append("api_key")
    if _CREDENTIAL_RE.search(text):
        reasons.append("credential_like_string")
    lines = text.split("\n")
    if text.count("```") >= 4 or sum(1 for line in lines if len(line) > 120) > 20:
        reasons.append("large_code_block")
    if (
        len(text) > 15000
        or sum(1 for line in lines if _TRANSCRIPT_LINE_RE.match(line.strip())) > 8
    ):
        reasons.append("raw_transcript_like")
    return reasons


# ---------------------------------------------------------------------------
# Request schema (imi.memory.writeback.v1)
# ---------------------------------------------------------------------------


class ArtifactInput(BaseModel):
    kind: str
    uri: str
    description: str | None = None


class MemoryPayload(BaseModel):
    decisions: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactInput] = Field(default_factory=list)


class RuntimeInfo(BaseModel):
    name: str
    version: str | None = None


class ProvenanceInput(BaseModel):
    default_status: Literal["observed", "inferred", "generated"] = "generated"


class WritebackRequest(BaseModel):
    schema_version: Literal["imi.memory.writeback.v1"] = SCHEMA_VERSION
    memory_payload: MemoryPayload
    task_id: str | None = None
    flow_id: str | None = None
    runtime: RuntimeInfo | None = None
    provider: str | None = None
    model: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    provenance: ProvenanceInput = Field(default_factory=ProvenanceInput)
    stale_after: str | None = None
    idempotency_key: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


def _rows(payload: MemoryPayload) -> list[tuple[str, str]]:
    """(memory_type, content) rows in a stable order (OB1 memoryRows)."""
    rows: list[tuple[str, str]] = []
    rows += [("decision", c) for c in payload.decisions]
    rows += [("output", c) for c in payload.outputs]
    rows += [("lesson", c) for c in payload.lessons]
    rows += [("constraint", c) for c in payload.constraints]
    rows += [("open_question", c) for c in payload.unresolved_questions]
    rows += [("work_log", f"Next step: {c}") for c in payload.next_steps]
    rows += [("failure", c) for c in payload.failures]
    rows += [
        (
            "artifact_reference",
            f"{a.kind}: {a.description or a.uri}\n{a.uri}",
        )
        for a in payload.artifacts
    ]
    return rows


def _index_memory(memory: AgentMemory) -> str | None:
    """Best-effort vector indexing (module-level for test monkeypatching)."""
    from app.services import signal_indexing

    return signal_indexing.index_agent_memory_one(memory)


async def writeback(
    request: WritebackRequest,
    *,
    tenant_id: str | None = None,
    store: AgentMemoryStore | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Persist a writeback batch. Atomic on safety: any unsafe row rejects all."""
    store = store or AgentMemoryStore()
    rows = _rows(request.memory_payload)
    if not rows:
        return {"success": False, "error": "memory_payload is empty"}

    # Safety gate over every row BEFORE anything persists.
    rejected = [
        {"reason": reason, "memory_type": memory_type}
        for memory_type, content in rows
        for reason in unsafe_reasons(content)
    ]
    if rejected:
        logger.warning("[WRITEBACK] Rejected batch: %s", rejected)
        return {"success": False, "rejected": rejected}

    # Idempotent replay: per-row key {base}:{index}. The check-then-save loop
    # is synchronous (atomic on the event loop); the lock additionally guards
    # against thread-executor callers. Cross-process writers are out of scope —
    # the git corpus assumes a single writer per repo (same as SignalStore).
    created: list[dict[str, str]] = []
    replayed = True
    new_memories: list[AgentMemory] = []
    with _writeback_lock:
        for index, (memory_type, content) in enumerate(rows):
            row_key = (
                f"{request.idempotency_key}:{index}"
                if request.idempotency_key
                else None
            )
            existing = (
                store.find_by_idempotency_key(row_key)
                if row_key is not None
                else None
            )
            if existing is not None:
                created.append(
                    {"id": existing.id, "memory_type": existing.memory_type}
                )
                continue
            replayed = False
            memory = AgentMemory(
                memory_type=memory_type,
                content=content,
                source_refs=request.source_refs,
                runtime_name=request.runtime.name if request.runtime else None,
                runtime_version=request.runtime.version if request.runtime else None,
                provider=request.provider,
                model=request.model,
                task_id=request.task_id,
                flow_id=request.flow_id,
                confidence=request.confidence,
                idempotency_key=row_key,
                content_hash=content_fingerprint(content),
                provenance_status=request.provenance.default_status,
                stale_after=request.stale_after,
                tenant_id=tenant_id,
                workspace_id=request.workspace_id,
                project_id=request.project_id,
            )
            store.save(memory)
            new_memories.append(memory)
            created.append({"id": memory.id, "memory_type": memory.memory_type})

    committed = False
    audit_errors: list[dict[str, str]] = []
    if new_memories:
        audit_store = capture_audit_store(repo_root=repo_root)
        commit_paths: list[str] = []
        for memory in new_memories:
            _index_memory(memory)
            try:
                audit_store.append(
                    SignalAuditRecord(
                        signal_id=memory.id,
                        record_kind="agent_memory",
                        action="capture",
                        actor=memory.runtime_name or "agent",
                        tenant_id=memory.tenant_id,
                        reasoning=(
                            f"writeback {memory.memory_type} task={memory.task_id}"
                        ),
                        before={},
                        after=_governance_snapshot(memory),
                    )
                )
                commit_paths.append(audit_store.relative_path(memory.id))
            except Exception as e:
                logger.error(
                    "[WRITEBACK] AUDIT APPEND FAILED for %s: %s",
                    memory.id,
                    e,
                    exc_info=True,
                )
                audit_errors.append({"id": memory.id, "error": str(e)})
            commit_paths.append(store.relative_path(memory))

        try:
            await git_ops.commit_and_push(
                commit_paths,
                f"memory: writeback {len(new_memories)} records"
                + (f" (task {request.task_id})" if request.task_id else ""),
            )
            committed = True
        except Exception as e:
            logger.warning("[WRITEBACK] Git commit failed (non-fatal): %s", e)

    result: dict[str, Any] = {
        "success": True,
        "created": created,
        "replayed": replayed,
        "committed": committed,
        "schema_version": SCHEMA_VERSION,
    }
    if audit_errors:
        # Records persisted but their G2 audit rows are missing — surface it
        # (mirrors review_agent_memory's audit_error contract).
        result["audit_errors"] = audit_errors
    return result


async def review_agent_memory(
    memory_id: str,
    action: str,
    *,
    actor: str | None = None,
    superseded_by: str | None = None,
    store: AgentMemoryStore | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Audited governance transition over an agent memory (mirrors captures)."""
    from app.services.memory_governance import review_record_with_audit

    try:
        store = store or AgentMemoryStore()
        memory = store.get(memory_id)
        if memory is None:
            return {"success": False, "error": f"Memory '{memory_id}' not found"}

        try:
            new_memory, audit_record = review_record_with_audit(
                memory,
                action,
                actor=actor,
                superseded_by=superseded_by,
                record_kind="agent_memory",
            )
        except ValueError as e:
            return {"success": False, "error": str(e)}

        store.update(new_memory)

        audit_store = capture_audit_store(repo_root=repo_root)
        try:
            audit_store.append(audit_record)
        except Exception as e:
            logger.error(
                "[WRITEBACK] AUDIT APPEND FAILED for %s action=%s: %s",
                memory_id,
                action,
                e,
                exc_info=True,
            )
            return {
                "success": True,
                "review_applied": True,
                "audit_error": str(e),
                "memory": new_memory.model_dump(),
            }

        committed = False
        try:
            await git_ops.commit_and_push(
                [
                    store.relative_path(new_memory),
                    audit_store.relative_path(memory_id),
                ],
                f"audit: {action} agent_memory {memory_id}",
            )
            committed = True
        except Exception as e:
            logger.warning("[WRITEBACK] Git commit failed (non-fatal): %s", e)

        _index_memory(new_memory)

        return {
            "success": True,
            "review_applied": True,
            "audit_row_id": audit_record.id,
            "gate_response": audit_record.gate_response,
            "committed": committed,
            "memory": new_memory.model_dump(),
        }
    except Exception as e:
        logger.error("[WRITEBACK] review_agent_memory failed: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}
