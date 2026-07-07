"""CapturedMemory — general (non-meeting) evidence in the governance ladder.

The general capture layer (web / mail / manual notes) feeds the *same* trust
axis as meeting signals, but is a distinct entity: a CapturedMemory is imported
evidence, not a meeting Signal and not (yet) a DecisionRecord. It enters as
``imported`` provenance, evidence-grade, and only becomes instruction-grade once
a human confirms it — enforced by the shared authority invariant.

See docs/prd/memory-governance-and-retrieval-prd.md §8 (G4).
"""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.signal_governance import (
    PROVENANCE_STATUSES,
    REVIEW_STATUSES,
    instruction_grade_permitted,
)

# Dormant scope vocabulary (OB1 forward-compat columns; only tenant_id is
# enforced in retrieval until multi-user lands).
VISIBILITIES = frozenset({"personal", "project", "workspace"})


class CapturedMemory(BaseModel):
    """A piece of generally-captured evidence subject to the governance ladder."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str = Field(..., min_length=1, description="Captured text")
    source: str = Field(..., description="Capture source: web, mail, manual, rss")
    source_id: str | None = Field(
        None, description="External id (URL, message id) for idempotent re-capture"
    )
    content_fingerprint: str = Field(
        "", description="Normalized sha256 dedup key (see memory_capture)"
    )
    summary: str | None = Field(None, description="Optional enrichment summary")
    tags: list[str] = Field(default_factory=list)
    source_date: str | None = Field(None, description="Original publish/sent date")
    enrichment: dict = Field(
        default_factory=dict,
        description="LLM-extracted metadata (type/topics/people/action_items/dates)",
    )
    related_record_ids: list[str] = Field(
        default_factory=list,
        description="Advisory cross-source links (fingerprint matches — never merged)",
    )

    # --- Supersession / validity (shared review machinery, R1.1) -------------
    superseded_by: str | None = Field(
        None, description="Successor record id when superseded (immutable-correct)"
    )
    valid_from: str | None = Field(None, description="Validity window start (ISO)")
    valid_to: str | None = Field(
        None, description="Validity window end (ISO); set at supersession"
    )

    # --- Dormant scope columns (OB1 forward-compat; tenant_id enforced only) -
    workspace_id: str | None = Field(None)
    project_id: str | None = Field(None)
    visibility: str = Field("personal")

    # --- Governance / trust axis (shared with Signal) ------------------------
    provenance_status: str = Field("imported")
    review_status: str = Field("pending")
    can_use_as_evidence: bool = Field(True)
    can_use_as_instruction: bool = Field(False)
    tenant_id: str | None = Field(None)
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
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

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, value: str) -> str:
        if value not in VISIBILITIES:
            raise ValueError(f"Unknown visibility: {value!r}")
        return value

    @model_validator(mode="after")
    def _enforce_authority_invariant(self) -> "CapturedMemory":
        """Instruction-grade requires confirmed/imported provenance (shared rule)."""
        if self.can_use_as_instruction and not instruction_grade_permitted(
            self.provenance_status
        ):
            raise ValueError(
                "can_use_as_instruction requires provenance_status in "
                "{'user_confirmed', 'imported'}, got "
                f"{self.provenance_status!r}"
            )
        return self
