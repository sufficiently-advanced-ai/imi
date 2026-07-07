"""SQLAlchemy models for memory operations telemetry (Phase 3 of OB1 absorption).

High-churn operational data — recall traces, per-item ranking snapshots,
usage feedback, and the append-only event log — lives in SQL (SQLite default,
Postgres hosted), NEVER in the git corpus (plan storage decision: traces at
agent-call frequency would bloat the repo).

``record_id`` deliberately has NO foreign key: the governed records live in
the git corpus, not SQL, and traces must survive record deletion (the same
audit-survives-deletion posture as the JSONL audit log).
"""

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.user_models.db_models import TenantScopedMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class MemoryRecallTrace(TenantScopedMixin, Base):
    """One row per recall request (OB1 agent_memory_recall_traces)."""

    __tablename__ = "memory_recall_traces"

    id = Column(String(36), primary_key=True, default=_uuid)
    request_id = Column(String(64), unique=True, nullable=False, index=True)
    workspace_id = Column(String(255), nullable=True)  # dormant scope
    project_id = Column(String(255), nullable=True)  # dormant scope
    surface = Column(String(32), nullable=False, default="agent_recall")
    runtime_name = Column(String(255), nullable=True)
    runtime_version = Column(String(64), nullable=True)
    task_id = Column(String(255), nullable=True, index=True)
    flow_id = Column(String(255), nullable=True)
    query = Column(Text, nullable=False)
    authority = Column(String(16), nullable=False, default="evidence")
    schema_version = Column(String(64), nullable=False)
    request_scope = Column(JSON, nullable=False, default=dict)
    response_policy = Column(JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    items = relationship(
        "MemoryRecallItem",
        back_populates="trace",
        cascade="all, delete-orphan",
        order_by="MemoryRecallItem.rank",
    )


class MemoryRecallItem(TenantScopedMixin, Base):
    """Per-returned-record detail for a trace (OB1 agent_memory_recall_items).

    ``used`` stays NULL until usage feedback arrives; the use_policy_snapshot
    preserves what the caller was told at recall time even if governance
    changes later. Tenant-scoped directly (not only via the trace FK) so
    by-record reads like ``usage_stats`` are RLS-confined on hosted Postgres.
    """

    __tablename__ = "memory_recall_items"
    __table_args__ = (UniqueConstraint("trace_id", "record_id"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    trace_id = Column(
        String(36),
        ForeignKey("memory_recall_traces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    record_id = Column(String(64), nullable=False)  # git-corpus id; no FK
    record_kind = Column(String(32), nullable=False)
    rank = Column(Integer, nullable=False)
    similarity = Column(Float, nullable=True)
    ranking_score = Column(Float, nullable=True)
    returned = Column(Boolean, nullable=False, default=True)
    used = Column(Boolean, nullable=True)
    used_as = Column(String(16), nullable=True)  # instruction|evidence|background
    ignored_reason = Column(Text, nullable=True)
    use_policy_snapshot = Column(JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    trace = relationship("MemoryRecallTrace", back_populates="items")


class JudgeDecisionEvent(TenantScopedMixin, Base):
    """Compact judgment event (Phase 4). Named to avoid any collision with the
    DecisionRecord domain concept — this is runtime judgment telemetry, not
    knowledge. Raw tool arguments are NEVER stored (arguments_digest only).
    Idempotent on (tenant_id, action_id)."""

    __tablename__ = "judge_decision_events"
    __table_args__ = (UniqueConstraint("tenant_id", "action_id"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    action_id = Column(String(64), nullable=False)
    # No unique constraint: dedup rides on (tenant_id, action_id); a global
    # unique here would let one tenant's key collide with another's.
    idempotency_key = Column(String(128), nullable=True, index=True)
    risk_class = Column(String(32), nullable=False)
    decision = Column(String(16), nullable=False)  # ADR-001 gate vocabulary
    reasoning_summary = Column(Text, nullable=False, default="")
    confidence = Column(String(8), nullable=False, default="medium")
    judge = Column(JSON, nullable=False, default=dict)
    checks = Column(JSON, nullable=False, default=dict)
    memory_used = Column(JSON, nullable=False, default=list)
    memory_written = Column(JSON, nullable=False, default=list)
    arguments_digest = Column(String(64), nullable=True)
    expected_consequence = Column(Text, nullable=True)
    rollback = Column(JSON, nullable=True)
    recall_request_id = Column(String(64), nullable=True)
    schema_version = Column(String(64), nullable=False)
    runtime_name = Column(String(255), nullable=True)
    task_id = Column(String(255), nullable=True, index=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MemoryEvent(TenantScopedMixin, Base):
    """Append-only operational event log (OB1 agent_memory_audit_events)."""

    __tablename__ = "memory_events"

    id = Column(String(36), primary_key=True, default=_uuid)
    event_type = Column(String(48), nullable=False, index=True)
    record_id = Column(String(64), nullable=True)
    record_kind = Column(String(32), nullable=True)
    trace_id = Column(String(64), nullable=True)
    actor_kind = Column(String(16), nullable=False, default="system")
    actor_label = Column(String(255), nullable=True)
    runtime_name = Column(String(255), nullable=True)
    task_id = Column(String(255), nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
