"""Judge extender service — policy-aware recall + idempotent decision write-back.

Phase 4 of the OB1 absorption. The judge loop:

  before: ``judge_recall`` — Phase 3 recall (surface="judge_recall") for
      evidence, PLUS ``policy_hits``: the instruction-grade slice of memory
      and active confirmed decisions. policy_hits can ONLY ever contain
      instruction-grade records (ADR-002 read path); ``required_behavior``
      comes from a human-stamped ``metadata.required_behavior`` and defaults
      to "revise" (conservative — the least-specified part of the OB1 spec).

  after: ``judge_decide`` — idempotent compact event on (tenant, action_id);
      ``memory_used`` feeds the Phase 3 usage loop; ``memory_to_write`` flows
      through the Phase 2 writeback clamps (pending, never instruction-grade).
      An unsafe memory payload is rejected WITHOUT losing the decision event.

DecisionRecord (decision-state P2) is deliberately not a dependency —
policy_hits serve confirmed decision signals via decision_view today and will
pick up record_kind="decision" transparently when it lands.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.judge import JudgeDecisionRequest, JudgeRecallRequest
from app.services.memory_recall import RecallRequest
from app.services.memory_recall import recall as _recall
from app.services.recall_trace_store import apply_usage, log_event
from app.user_models.memory_ops_models import JudgeDecisionEvent

logger = logging.getLogger(__name__)

RECALL_RESPONSE_SCHEMA = "imi.judge.recall_response.v1"

# Conservative default when no human has stamped a required behavior.
_DEFAULT_REQUIRED_BEHAVIOR = "revise"
_BEHAVIORS = {"allow", "block", "revise", "escalate"}


def _required_behavior(metadata: dict | None) -> str:
    value = (metadata or {}).get("required_behavior")
    return value if value in _BEHAVIORS else _DEFAULT_REQUIRED_BEHAVIOR


def _session_factory():
    from app.database import create_database_session, get_database_config

    return create_database_session(get_database_config())


async def judge_recall(
    request: JudgeRecallRequest,
    *,
    recall_fn: Callable | None = None,
    decisions_fn: Callable | None = None,
) -> dict[str, Any]:
    """Policy-aware recall for a judge decision."""
    recall_fn = recall_fn or _recall
    if decisions_fn is None:
        from app.services.decision_view import list_decisions as decisions_fn

    warnings: list[str] = []

    # Evidence: the general recall surface, traced under the judge surface.
    evidence = await recall_fn(
        RecallRequest(
            query=request.query,
            authority="evidence",
            limit=request.limit,
            task_id=request.task_id,
            flow_id=request.flow_id,
            runtime_name=request.runtime_name,
            surface="judge_recall",
        )
    )
    warnings.extend(evidence.get("warnings", []))

    # Policy hits, source (a): instruction-grade memory (ADR-002-filtered recall).
    instruction = await recall_fn(
        RecallRequest(
            query=request.query,
            authority="instruction",
            limit=request.limit,
            surface="judge_recall_policy",
        )
    )
    policy_hits: list[dict[str, Any]] = [
        {
            "record_id": memory["record_id"],
            "record_kind": memory["record_kind"],
            "content": memory["content"],
            "summary": memory.get("summary"),
            "required_behavior": _DEFAULT_REQUIRED_BEHAVIOR,
            "provenance_status": memory["provenance"]["status"],
            "review_status": memory.get("review_status"),
        }
        for memory in instruction.get("memories", [])
    ]

    # Policy hits, source (b): active decisions — instruction-grade ONLY.
    try:
        decisions = decisions_fn(state="active", max_results=request.limit)
        for decision in decisions.get("decisions", []):
            if not decision.get("can_use_as_instruction"):
                continue  # ADR-002: unconfirmed decisions never gate actions
            content = decision.get("content") or ""  # key may be present-but-null
            policy_hits.append(
                {
                    "record_id": decision["id"],
                    "record_kind": "decision",
                    "content": content,
                    "summary": content[:140],
                    "required_behavior": _required_behavior(
                        decision.get("metadata")
                    ),
                    "provenance_status": decision.get("provenance_status"),
                    "review_status": decision.get("review_status"),
                }
            )
    except Exception as e:
        logger.warning("[JUDGE] decision policy source failed (non-fatal): %s", e)
        warnings.append("decision policy source unavailable")

    # Dedup by record id (a confirmed constraint may appear in both sources).
    seen: set[str] = set()
    unique_hits = []
    for hit in policy_hits:
        if hit["record_id"] not in seen:
            seen.add(hit["record_id"])
            unique_hits.append(hit)

    return {
        "schema_version": RECALL_RESPONSE_SCHEMA,
        "recall_request_id": evidence.get("request_id"),
        "memories": evidence.get("memories", []),
        "policy_hits": unique_hits,
        "warnings": warnings,
    }


async def judge_decide(
    request: JudgeDecisionRequest,
    *,
    session_factory=None,
    memory_store=None,
    repo_root=None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Record a judge decision (idempotent) and process its side channels."""
    from app.core.middleware.request_context import current_tenant_id

    tenant = tenant_id or current_tenant_id.get()
    factory = session_factory or _session_factory()

    async with factory() as session:
        existing = (
            await session.execute(
                select(JudgeDecisionEvent).where(
                    JudgeDecisionEvent.tenant_id == tenant,
                    JudgeDecisionEvent.action_id == request.action_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {
                "success": True,
                "decision_id": existing.id,
                "replayed": True,
                "memory_written": existing.memory_written,
                "memory_write_rejected": [],
            }

    # Side channel 1: judge-proposed memories → Phase 2 writeback (clamped).
    memory_written: list[dict] = []
    memory_write_rejected: list[dict] = []
    if request.memory_to_write is not None:
        from app.services import memory_writeback as writeback_service

        writeback_kwargs: dict[str, Any] = {}
        if memory_store is not None:
            writeback_kwargs["store"] = memory_store
        if repo_root is not None:
            writeback_kwargs["repo_root"] = repo_root
        wb = await writeback_service.writeback(
            writeback_service.WritebackRequest(
                memory_payload=request.memory_to_write,
                task_id=request.task_id,
                flow_id=request.flow_id,
                runtime=(
                    {"name": request.runtime_name} if request.runtime_name else None
                ),
                provenance={"default_status": "generated"},
                idempotency_key=(
                    f"judge:{request.idempotency_key}"
                    if request.idempotency_key
                    else f"judge:{request.action_id}"
                ),
            ),
            tenant_id=tenant,
            **writeback_kwargs,
        )
        if wb.get("success"):
            memory_written = wb.get("created", [])
        else:
            memory_write_rejected = wb.get("rejected", [])

    decision_id = str(uuid.uuid4())
    try:
        await _insert_decision_event(
            factory, decision_id, request, tenant, memory_written
        )
    except IntegrityError:
        # Check-then-insert race: a concurrent duplicate won the
        # UNIQUE(tenant_id, action_id) — re-read and report replayed.
        async with factory() as session:
            existing = (
                await session.execute(
                    select(JudgeDecisionEvent).where(
                        JudgeDecisionEvent.tenant_id == tenant,
                        JudgeDecisionEvent.action_id == request.action_id,
                    )
                )
            ).scalar_one()
        return {
            "success": True,
            "decision_id": existing.id,
            "replayed": True,
            "memory_written": existing.memory_written,
            "memory_write_rejected": memory_write_rejected,
        }

    return {
        "success": True,
        "decision_id": decision_id,
        "replayed": False,
        "memory_written": memory_written,
        "memory_write_rejected": memory_write_rejected,
    }


async def _insert_decision_event(
    factory, decision_id: str, request: JudgeDecisionRequest, tenant: str,
    memory_written: list[dict],
) -> None:
    async with factory() as session:
        session.add(
            JudgeDecisionEvent(
                id=decision_id,
                tenant_id=tenant,
                action_id=request.action_id,
                idempotency_key=request.idempotency_key,
                risk_class=request.risk_class,
                decision=request.decision,
                reasoning_summary=request.reasoning_summary,
                confidence=request.confidence,
                judge=request.judge.model_dump(),
                checks=dict(request.checks),
                memory_used=[m.model_dump() for m in request.memory_used],
                memory_written=memory_written,
                arguments_digest=request.arguments_digest,
                expected_consequence=request.expected_consequence,
                rollback=request.rollback.model_dump() if request.rollback else None,
                recall_request_id=request.recall_request_id,
                schema_version=request.schema_version,
                runtime_name=request.runtime_name,
                task_id=request.task_id,
            )
        )
        # Flush NOW so a duplicate-insert IntegrityError surfaces here (and
        # propagates to the replay handler) instead of poisoning the session
        # inside a swallowed side-channel flush below.
        await session.flush()
        # Side channel 2: memory_used → the Phase 3 usage loop.
        if request.recall_request_id and request.memory_used:
            try:
                await apply_usage(
                    session,
                    request.recall_request_id,
                    used_memory_ids=[m.record_id for m in request.memory_used],
                )
            except Exception as e:
                logger.warning("[JUDGE] usage feedback failed (non-fatal): %s", e)
        try:
            await log_event(
                session,
                "judge_decided",
                trace_id=request.recall_request_id,
                actor_kind="agent",
                runtime_name=request.runtime_name,
                task_id=request.task_id,
                payload={
                    "action_id": request.action_id,
                    "decision": request.decision,
                    "risk_class": request.risk_class,
                },
            )
        except Exception as e:
            logger.warning("[JUDGE] event log failed (non-fatal): %s", e)
        await session.commit()


async def get_judge_decision(
    decision_id: str, *, session_factory=None
) -> dict[str, Any] | None:
    """Load one judgment event by id."""
    factory = session_factory or _session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(JudgeDecisionEvent).where(JudgeDecisionEvent.id == decision_id)
            )
        ).scalar_one_or_none()
    return _event_dict(row) if row is not None else None


async def list_judge_decisions(
    *,
    task_id: str | None = None,
    decision: str | None = None,
    limit: int = 50,
    session_factory=None,
) -> list[dict[str, Any]]:
    """List judgment events, newest first, with optional filters."""
    factory = session_factory or _session_factory()
    query = select(JudgeDecisionEvent).order_by(JudgeDecisionEvent.created_at.desc())
    if task_id:
        query = query.where(JudgeDecisionEvent.task_id == task_id)
    if decision:
        query = query.where(JudgeDecisionEvent.decision == decision)
    query = query.limit(limit)
    async with factory() as session:
        rows = (await session.execute(query)).scalars().all()
    return [_event_dict(row) for row in rows]


def _event_dict(row: JudgeDecisionEvent) -> dict[str, Any]:
    return {
        "decision_id": row.id,
        "action_id": row.action_id,
        "risk_class": row.risk_class,
        "decision": row.decision,
        "reasoning_summary": row.reasoning_summary,
        "confidence": row.confidence,
        "judge": row.judge,
        "checks": row.checks,
        "memory_used": row.memory_used,
        "memory_written": row.memory_written,
        "arguments_digest": row.arguments_digest,
        "expected_consequence": row.expected_consequence,
        "rollback": row.rollback,
        "recall_request_id": row.recall_request_id,
        "schema_version": row.schema_version,
        "runtime_name": row.runtime_name,
        "task_id": row.task_id,
        "tenant_id": row.tenant_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
