"""Memory inspector — the trust surface (Phase 5 of the OB1 absorption).

For any governed record, answers OB1's four inspector questions:
  1. Why does this memory exist?    → the record + its provenance/sources
  2. What created/changed it?       → the append-only audit history
  3. How has it been used?          → recall/judge usage aggregates (SQL)
  4. What can it influence?         → authority flags + supersession lineage

A hard-deleted record still answers questions 2-4 from its audit trail — the
audit-survives-deletion guarantee, made visible. Response schema:
``imi.memory.inspector.v1``.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.services.recall_trace_store import usage_stats

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "imi.memory.inspector.v1"

_MAX_LINEAGE_HOPS = 20  # cycle guard for supersession chains


def _session_factory():
    from app.database import create_database_session, get_database_config

    return create_database_session(get_database_config())


def _position(record) -> str:
    """Governance-ladder position (mirrors decisions' GovernanceLadder)."""
    if record.can_use_as_instruction:
        return "instruction"
    if record.can_use_as_evidence and record.review_status not in (
        "rejected",
        "merged",
    ):
        return "evidence"
    return "blocked"


def _record_dict(record) -> dict[str, Any]:
    data = record.model_dump()
    return data


async def _judge_usage(session, record_id: str) -> list[dict]:
    """Judgment events that reported using this record (recent, Python-filtered

    — the JSON column scan is bounded and SQLite/Postgres-portable)."""
    from app.user_models.memory_ops_models import JudgeDecisionEvent

    rows = (
        (
            await session.execute(
                select(JudgeDecisionEvent)
                .order_by(JudgeDecisionEvent.created_at.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    hits = []
    for row in rows:
        for entry in row.memory_used or []:
            if entry.get("record_id") == record_id:
                hits.append(
                    {
                        "decision_id": row.id,
                        "decision": row.decision,
                        "used_as": entry.get("used_as"),
                        "task_id": row.task_id,
                    }
                )
    return hits


async def inspect_memory(
    record_id: str,
    *,
    capture_store=None,
    memory_store=None,
    audit_store=None,
    session_factory=None,
) -> dict[str, Any] | None:
    """Compose the inspector view for a capture or agent memory.

    Returns None only when the record is unknown to BOTH the stores and the
    audit trail.
    """
    from app.services.agent_memory_store import AgentMemoryStore
    from app.services.memory_capture import CaptureStore
    from app.services.memory_governance import capture_audit_store

    capture_store = capture_store or CaptureStore()
    memory_store = memory_store or AgentMemoryStore()
    audit_store = audit_store or capture_audit_store()

    # Resolve the living record (kind by store) …
    record = capture_store.get(record_id)
    record_kind: str | None = "capture" if record is not None else None
    if record is None:
        record = memory_store.get(record_id)
        record_kind = "agent_memory" if record is not None else None

    # … and the audit history (which survives deletion and knows the kind).
    audit_rows = audit_store.read_for_signal(record_id)
    if record is None and not audit_rows:
        return None
    if record_kind is None and audit_rows:
        record_kind = audit_rows[0].record_kind

    # Usage aggregates (best-effort — SQL may be unconfigured).
    usage: dict[str, Any] = {
        "times_returned": 0,
        "times_used": 0,
        "times_ignored": 0,
        "last_returned_at": None,
    }
    judge_usage: list[dict] = []
    factory = session_factory
    if factory is None:
        try:
            factory = _session_factory()
        except Exception as e:
            logger.warning("[INSPECTOR] usage stats unavailable: %s", e)
            factory = None
    if factory is not None:
        try:
            async with factory() as session:
                usage = await usage_stats(session, record_id)
                judge_usage = await _judge_usage(session, record_id)
        except Exception as e:
            logger.warning("[INSPECTOR] usage stats failed (non-fatal): %s", e)

    # Supersession lineage: walk successors (cycle-guarded).
    lineage: list[dict] = [{"record_id": record_id, "relation": "self"}]
    cursor = record
    hops = 0
    while (
        cursor is not None
        and getattr(cursor, "superseded_by", None)
        and hops < _MAX_LINEAGE_HOPS
    ):
        successor_id = cursor.superseded_by
        lineage.append({"record_id": successor_id, "relation": "successor"})
        cursor = capture_store.get(successor_id) or memory_store.get(successor_id)
        hops += 1

    influence: dict[str, Any] = {
        "can_use_as_instruction": bool(
            record is not None and record.can_use_as_instruction
        ),
        "can_use_as_evidence": bool(
            record is not None and record.can_use_as_evidence
        ),
        "position": _position(record) if record is not None else "blocked",
        "superseded_by": getattr(record, "superseded_by", None),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": record_id,
        "record_kind": record_kind,
        "record": _record_dict(record) if record is not None else None,
        "audit_history": [
            {
                "action": row.action,
                "gate_response": row.gate_response,
                "actor": row.actor,
                "reasoning": row.reasoning,
                "created_at": row.created_at,
            }
            for row in audit_rows
        ],
        "usage": usage,
        "judge_usage": judge_usage,
        "lineage": lineage,
        "influence": influence,
    }
