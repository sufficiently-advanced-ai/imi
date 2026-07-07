"""
Signal domain models for the persistence layer.

These models represent signals as first-class persistent objects stored as
JSON files in the repo. They are distinct from the API response models in
signal_feed.py which maintain backward compatibility with the frontend.
"""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.signal_governance import (
    PROVENANCE_STATUSES,
    REVIEW_STATUSES,
    instruction_grade_permitted,
)


class EntityRef(BaseModel):
    """A reference to a knowledge graph entity linked to a signal."""

    id: str = Field(..., description="Deterministic slug ID like 'person-sarah-chen'")
    type: str = Field(..., description="Entity type: person, project, company, etc.")
    name: str = Field(..., description="Human-readable entity name")


class Signal(BaseModel):
    """A first-class signal extracted from a meeting and persisted to disk."""

    id: str = Field(..., description="Deterministic UUID5 signal identifier")
    type: str = Field(
        ..., description="Signal type: decision, action_item, key_point, insight"
    )
    content: str = Field(..., description="Signal content text")
    source_meeting_id: str = Field(..., description="Source meeting bot_id")
    source_meeting_title: str | None = Field(None, description="Source meeting title")
    source_timestamp: str = Field(..., description="Meeting updated_at ISO timestamp")
    participants: list[str] = Field(
        default_factory=list, description="Meeting participants"
    )
    entities: list[EntityRef] = Field(
        default_factory=list, description="Linked entity references with slug IDs"
    )
    confidence: float = Field(0.8, description="Extraction confidence")
    status: str | None = Field(
        None, description="For action items: open, in_progress, done"
    )
    owner: EntityRef | None = Field(
        None, description="For action items: assigned person as entity ref"
    )
    due_date: str | None = Field(None, description="Due date if mentioned")
    position: int = Field(
        0, description="Document order position within source meeting"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="When this signal was extracted",
    )
    metadata: dict = Field(default_factory=dict, description="Additional metadata")
    client_id: str | None = Field(
        None,
        description="Slug ID of the client this signal is scoped to (e.g. 'client-acme-corp')",
    )

    # --- Governance / trust axis (memory-governance PRD §4-6) -----------------
    # Orthogonal to the temporal lifecycle owned by the decision-state PRD.
    provenance_status: str = Field(
        "generated",
        description="How the signal came to exist: observed, inferred, "
        "user_confirmed, imported, generated, superseded, disputed",
    )
    review_status: str = Field(
        "pending",
        description="Human review disposition: pending, confirmed, "
        "evidence_only, rejected, stale, merged",
    )
    can_use_as_evidence: bool = Field(
        True, description="Safe to surface as context/evidence"
    )
    can_use_as_instruction: bool = Field(
        False,
        description="May be used as guidance the system acts on; only true once "
        "a human vouches for provenance (see authority invariant)",
    )
    superseded_by: str | None = Field(
        None, description="Successor signal id when this record has been superseded"
    )
    valid_from: str | None = Field(
        None,
        description=(
            "When this signal became true; defaults to source_timestamp "
            "(meeting observation time) on creation."
        ),
    )
    valid_to: str | None = Field(
        None,
        description=("When this signal stopped being current; set on supersession"),
    )
    tenant_id: str | None = Field(
        None, description="Tenant scope for the governance ladder (multi-tenant)"
    )

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

    @model_validator(mode="after")
    def _default_valid_from(self) -> "Signal":
        """Default valid_from to source_timestamp when not explicitly provided.

        Validators run in declaration order — this one fires before
        _enforce_authority_invariant.  model_copy(update=...) bypasses both
        model_validators, so callers using model_copy must maintain invariants
        themselves (signal_governance.apply_review does this explicitly).
        """
        if self.valid_from is None and self.source_timestamp:
            object.__setattr__(self, "valid_from", self.source_timestamp)
        return self

    @model_validator(mode="after")
    def _enforce_authority_invariant(self) -> "Signal":
        """Instruction-grade requires confirmed/imported provenance.

        Reproduces openbrain's chk_memories_instruction_grade CHECK constraint at
        the model layer: an agent-generated, unconfirmed signal can never be
        instruction-grade, no matter how it is constructed.
        """
        if self.can_use_as_instruction and not instruction_grade_permitted(
            self.provenance_status
        ):
            raise ValueError(
                "can_use_as_instruction requires provenance_status in "
                "{'user_confirmed', 'imported'}, got "
                f"{self.provenance_status!r}"
            )
        return self


class MeetingSignals(BaseModel):
    """Container for all signals extracted from a single meeting."""

    meeting_id: str = Field(..., description="Meeting ID")
    bot_id: str = Field(..., description="Source meeting identifier")
    meeting_title: str | None = Field(None, description="Meeting title")
    extracted_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="When signals were extracted",
    )
    signal_count: int = Field(0, description="Number of signals extracted")
    signals: list[Signal] = Field(default_factory=list)

    def model_post_init(self, __context) -> None:
        """Keep signal_count in sync with signals list."""
        if self.signals and self.signal_count == 0:
            object.__setattr__(self, "signal_count", len(self.signals))


class SignalAuditRecord(BaseModel):
    """An immutable, append-only audit row for a signal governance transition.

    Mirrors openbrain's ``memory_audit``: keyed by signal id with no foreign
    key, so the audit survives hard deletion of the signal. See
    docs/prd/memory-governance-and-retrieval-prd.md §10 (G2).
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Audit row identifier",
    )
    signal_id: str = Field(..., description="Signal this transition applied to")
    record_kind: str = Field(
        "signal",
        description="Governed record kind: signal, capture, agent_memory, decision",
    )
    action: str = Field(
        ..., description="capture, update, review action, delete, supersede"
    )
    gate_response: str | None = Field(
        None, description="ADR-001 gate response: allow, block, revise, escalate"
    )
    actor: str | None = Field(None, description="Who performed the action")
    tenant_id: str | None = Field(None, description="Tenant scope")
    reasoning: str = Field("", description="Human-readable description of the change")
    before: dict = Field(
        default_factory=dict, description="Governance snapshot before the transition"
    )
    after: dict = Field(
        default_factory=dict, description="Governance snapshot after the transition"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="When the transition occurred",
    )
