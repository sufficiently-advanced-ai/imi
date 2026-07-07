"""AgentMemory — typed, governed agent-written operational memory (Phase 2).

Ports OB1's ``agent_memories`` concept as a git-corpus record: agents persist
compact, typed memories (decisions, lessons, constraints, ...) that enter the
SAME trust axis as signals and captures. The ADR-002-hardened default posture:
``generated`` provenance, ``pending`` review, evidence-grade only — never
instruction-grade until a human confirms (deliberate divergence from OB1,
whose writeback can mint instruction-grade).

One governed record model — no sidecar table split (plan "Do NOT copy" #1):
source refs and artifacts embed in the record rather than joining to it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.captured_memory import VISIBILITIES
from app.services.signal_governance import (
    PROVENANCE_STATUSES,
    REVIEW_STATUSES,
    instruction_grade_permitted,
)

# OB1 memory_type taxonomy, verbatim.
MEMORY_TYPES = frozenset(
    {
        "decision",
        "output",
        "lesson",
        "constraint",
        "open_question",
        "failure",
        "artifact_reference",
        "work_log",
    }
)

_SUMMARY_MAX = 140


class SourceRef(BaseModel):
    """Link from a memory to the artifact it derives from (PR, message, doc...)."""

    kind: str = Field(..., description="file, message, doc, ticket, log, web, api")
    uri: str | None = None
    title: str | None = None
    source_timestamp: str | None = None
    summary: str | None = None


class ArtifactRef(BaseModel):
    """Artifact created by or related to a memory (link, file, snippet ref)."""

    kind: str = Field(..., description="pr, file, doc, link, dataset, ...")
    uri: str | None = None
    description: str | None = None


class AgentMemory(BaseModel):
    """A typed agent-written memory in the governance ladder."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    memory_type: str = Field(..., description="OB1 memory type taxonomy")
    summary: str = Field("", description="Compact display summary (derived)")
    content: str = Field(..., min_length=1)
    source_refs: list[SourceRef] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)

    # --- Runtime / task provenance (who wrote this, during what) -------------
    runtime_name: str | None = None
    runtime_version: str | None = None
    provider: str | None = None
    model: str | None = None
    task_id: str | None = None
    flow_id: str | None = None
    confidence: float = Field(0.5, ge=0.0, le=1.0)

    # --- Idempotency / dedup --------------------------------------------------
    idempotency_key: str | None = Field(
        None, description="Writeback replay key ({base}:{row-index})"
    )
    content_hash: str = Field("", description="Normalized sha256 (memory_capture)")

    # --- Governance / trust axis (shared with Signal/CapturedMemory) ---------
    provenance_status: str = Field("generated")
    review_status: str = Field("pending")
    can_use_as_evidence: bool = Field(True)
    can_use_as_instruction: bool = Field(False)
    superseded_by: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    stale_after: str | None = Field(
        None, description="Freshness horizon (ISO); staleness computed on read"
    )
    related_record_ids: list[str] = Field(default_factory=list)

    # --- Scope (tenant enforced; workspace/project/visibility dormant) -------
    tenant_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    visibility: str = Field("personal")

    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )
    metadata: dict = Field(default_factory=dict)

    @field_validator("memory_type")
    @classmethod
    def _validate_memory_type(cls, value: str) -> str:
        if value not in MEMORY_TYPES:
            raise ValueError(f"Unknown memory_type: {value!r}")
        return value

    @field_validator("provenance_status")
    @classmethod
    def _validate_provenance_status(cls, value: str) -> str:
        if value not in PROVENANCE_STATUSES:
            raise ValueError(f"Unknown provenance_status: {value!r}")
        return value

    @field_validator("review_status")
    @classmethod
    def _validate_review_status(cls, value: str) -> str:
        if value not in REVIEW_STATUSES:
            raise ValueError(f"Unknown review_status: {value!r}")
        return value

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, value: str) -> str:
        if value not in VISIBILITIES:
            raise ValueError(f"Unknown visibility: {value!r}")
        return value

    @model_validator(mode="after")
    def _derive_summary_and_enforce_invariant(self) -> AgentMemory:
        if not self.summary:
            text = " ".join(self.content.split())
            object.__setattr__(
                self,
                "summary",
                text if len(text) <= _SUMMARY_MAX else text[: _SUMMARY_MAX - 1] + "…",
            )
        if self.can_use_as_instruction and not instruction_grade_permitted(
            self.provenance_status
        ):
            raise ValueError(
                "can_use_as_instruction requires provenance_status in "
                f"{{'user_confirmed', 'imported'}}, got {self.provenance_status!r}"
            )
        return self
