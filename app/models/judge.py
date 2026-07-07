"""Judge extender contracts — imi.judge.*.v1 (Phase 4 of the OB1 absorption).

Runtime-independent envelopes for the judge loop: an agent runtime submits an
ActionProposal before a side-effectual action, asks for policy-aware recall
(JudgeRecallRequest), and writes the outcome back (JudgeDecisionRequest).

Write-back rules (OB1 Judge Extender guide, hardened):
  - Raw tool arguments are REJECTED at the schema layer (extra="forbid") —
    only a sha256 ``arguments_digest`` is ever carried or stored.
  - ``decision`` uses ADR-001's gate vocabulary: allow | block | revise | escalate.
  - Judge-proposed memories flow through the Phase 2 writeback clamps and can
    never be instruction-grade at birth (ADR-002).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.agent_memory import SourceRef
from app.services.memory_writeback import MemoryPayload

RISK_CLASSES = ("read_only", "reversible_write", "external_side_effect", "high_risk")
RiskClass = Literal["read_only", "reversible_write", "external_side_effect", "high_risk"]
Decision = Literal["allow", "block", "revise", "escalate"]
CheckResult = Literal["pass", "fail", "uncertain", "not_applicable"]

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# Check names a judge may report (OB1's six checks).
CHECK_NAMES = frozenset(
    {
        "authorization",
        "evidence",
        "policy",
        "sensitivity",
        "reversibility",
        "quality",
    }
)


class RollbackInfo(BaseModel):
    is_reversible: bool = False
    rollback_plan: str | None = None
    rollback_owner: str | None = None


class AuthorizationRef(BaseModel):
    kind: str = Field(..., description="user_message, task, ticket, memory, policy, manual_approval")
    uri: str | None = None
    quote_or_summary: str = ""
    timestamp: str | None = None


class ActionProposal(BaseModel):
    """Structured proposal for a side-effectual action (pre-execution)."""

    model_config = {"extra": "forbid"}  # rejects raw `arguments` payloads

    schema_version: Literal["imi.judge.action_proposal.v1"] = (
        "imi.judge.action_proposal.v1"
    )
    action_id: str
    risk_class: RiskClass
    description: str
    tool_name: str | None = None
    target_system: str | None = None
    arguments_digest: str | None = Field(
        None, description="sha256 hex of the tool arguments — never the args themselves"
    )
    authorization_refs: list[AuthorizationRef] = Field(default_factory=list)
    evidence_refs: list[SourceRef] = Field(default_factory=list)
    expected_consequence: str | None = None
    rollback: RollbackInfo = Field(default_factory=RollbackInfo)
    sensitivity: dict[str, bool] = Field(default_factory=dict)
    task_id: str | None = None
    flow_id: str | None = None

    @field_validator("arguments_digest")
    @classmethod
    def _digest_is_sha256_hex(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_HEX.match(value):
            raise ValueError("arguments_digest must be 64 lowercase hex chars (sha256)")
        return value


class JudgeRecallRequest(BaseModel):
    """Policy-aware recall before a judge decision (imi.judge.recall.v1)."""

    schema_version: Literal["imi.judge.recall.v1"] = "imi.judge.recall.v1"
    query: str
    action_type: RiskClass
    tool_name: str | None = None
    target_system: str | None = None
    limit: int = Field(10, ge=1, le=50)
    include_stale: bool = False
    task_id: str | None = None
    flow_id: str | None = None
    runtime_name: str | None = None

    @field_validator("query")
    @classmethod
    def _non_empty_query(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("query must be non-empty")
        return value


class MemoryUsedEntry(BaseModel):
    record_id: str
    used_as: Literal["instruction", "evidence", "background"] = "evidence"


class JudgeInfo(BaseModel):
    kind: Literal["llm", "rule", "hybrid", "human"] = "llm"
    provider: str | None = None
    model: str | None = None
    policy_version: str | None = None


class JudgeDecisionRequest(BaseModel):
    """Judge outcome write-back (imi.judge.decision.v1). Idempotent on action_id."""

    schema_version: Literal["imi.judge.decision.v1"] = "imi.judge.decision.v1"
    action_id: str
    risk_class: RiskClass
    decision: Decision
    reasoning_summary: str
    confidence: Literal["high", "medium", "low"] = "medium"
    judge: JudgeInfo = Field(default_factory=JudgeInfo)
    checks: dict[str, CheckResult] = Field(default_factory=dict)
    arguments_digest: str | None = None
    expected_consequence: str | None = None
    rollback: RollbackInfo | None = None
    memory_used: list[MemoryUsedEntry] = Field(default_factory=list)
    memory_to_write: MemoryPayload | None = None
    recall_request_id: str | None = None
    idempotency_key: str | None = None
    task_id: str | None = None
    flow_id: str | None = None
    runtime_name: str | None = None

    @field_validator("checks")
    @classmethod
    def _known_checks(cls, value: dict) -> dict:
        unknown = set(value) - CHECK_NAMES
        if unknown:
            raise ValueError(f"Unknown checks: {sorted(unknown)}")
        return value

    @field_validator("arguments_digest")
    @classmethod
    def _digest_is_sha256_hex(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_HEX.match(value):
            raise ValueError("arguments_digest must be 64 lowercase hex chars (sha256)")
        return value
