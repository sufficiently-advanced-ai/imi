"""Signal trust/governance axis — provenance, review, and the authority gate.

Ports the openbrain "trust ladder" onto imi signals: every signal carries a
provenance status (how it came to exist) and a review status (human disposition),
and two authority booleans — ``can_use_as_evidence`` (safe as context) and
``can_use_as_instruction`` (guidance the system may act on). The load-bearing
invariant, reproduced from openbrain's ``chk_memories_instruction_grade`` SQL
CHECK, is that a signal may only be instruction-grade when a human has vouched
for its provenance.

This module is intentionally **pure** — no I/O and no import of the Signal model
at runtime — so the model can import the invariant from here without a cycle.
``apply_review`` operates on a Signal via its ``model_copy`` method (duck-typed),
not by importing the class.

See docs/prd/memory-governance-and-retrieval-prd.md sections 4-6 and
docs/adr/ADR-001-signals-decision-records-routing-approval-gates.md.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.signal import Signal

# How a signal came to exist (openbrain provenanceStatus).
PROVENANCE_STATUSES = frozenset(
    {
        "observed",
        "inferred",
        "user_confirmed",
        "imported",
        "generated",
        "superseded",
        "disputed",
    }
)

# Human review disposition (openbrain reviewStatus).
REVIEW_STATUSES = frozenset(
    {
        "pending",
        "confirmed",
        "evidence_only",
        "rejected",
        "stale",
        "merged",
    }
)

# Only these provenance values may carry instruction-grade authority.
# Mirrors openbrain's chk_memories_instruction_grade CHECK constraint.
INSTRUCTION_GRADE_PROVENANCE = frozenset({"user_confirmed", "imported"})

# ADR-001's four-way approval gate responses.
GATE_RESPONSES = frozenset({"allow", "block", "revise", "escalate"})

# Maps openbrain review actions onto ADR-001 gate responses.
_GATE_FOR_ACTION = {
    "confirm": "allow",
    "supersede": "allow",
    "reject": "block",
    "evidence_only": "revise",
    "dispute": "revise",
    "escalate": "escalate",
}


# Review actions that leave the record in a human-reviewed state. Each one binds
# the review to the content it was applied to (``reviewed_content_hash``,
# ADR-009); editing that content afterwards sends the record back to pending.
REVIEWED_STATES = frozenset({"confirmed", "evidence_only", "rejected"})
_REVIEW_BINDING_ACTIONS = frozenset({"confirm", "reject", "evidence_only"})

# Audit action recorded when a content edit invalidates a review (ADR-009).
CONTENT_EDITED_ACTION = "content_edited"


def content_hash(content: str | None) -> str:
    """sha256 hex digest of a record's content exactly as stored (ADR-009).

    No normalization: any change to the reviewed text, whitespace included,
    counts as a new revision.
    """
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def instruction_grade_permitted(provenance_status: str) -> bool:
    """Whether a signal with this provenance may be instruction-grade."""
    return provenance_status in INSTRUCTION_GRADE_PROVENANCE


def gate_response_for_action(action: str) -> str:
    """Translate an openbrain review action into its ADR-001 gate response.

    Raises ValueError for actions that are not gate transitions.
    """
    try:
        return _GATE_FOR_ACTION[action]
    except KeyError:
        raise ValueError(f"Unknown review action: {action!r}") from None


def apply_review(
    signal: Signal,
    action: str,
    *,
    superseded_by: str | None = None,
) -> Signal:
    """Apply a review action, returning a NEW Signal (the original is unchanged).

    Implements the openbrain ReviewMemory state machine. Only ``confirm`` (which
    sets provenance to ``user_confirmed``) yields an instruction-grade result;
    the authority invariant is re-checked before returning as defense in depth.

    Args:
        signal: The signal to review (not mutated).
        action: One of confirm, reject, evidence_only, dispute, supersede.
        superseded_by: Successor signal id; required when action == "supersede".

    Returns:
        A new Signal reflecting the review transition.

    Raises:
        ValueError: for an unknown action, a supersede without a successor id, or
            a transition that would violate the instruction-grade invariant.
    """
    if action == "confirm":
        updates: dict = dict(
            review_status="confirmed",
            provenance_status="user_confirmed",
            can_use_as_evidence=True,
            can_use_as_instruction=True,
        )
    elif action == "reject":
        updates = dict(
            review_status="rejected",
            can_use_as_evidence=False,
            can_use_as_instruction=False,
        )
    elif action == "evidence_only":
        updates = dict(
            review_status="evidence_only",
            can_use_as_evidence=True,
            can_use_as_instruction=False,
        )
    elif action == "dispute":
        updates = dict(
            provenance_status="disputed",
            can_use_as_instruction=False,
        )
    elif action == "supersede":
        if not superseded_by:
            raise ValueError("supersede requires a superseded_by successor id")
        updates = dict(
            provenance_status="superseded",
            review_status="merged",
            # A superseded record is no longer authoritative; clearing this also
            # keeps the supersede of an already instruction-grade signal from
            # tripping the authority invariant below.
            can_use_as_instruction=False,
            superseded_by=superseded_by,
            # R1.1 — close the validity window at the moment of supersession.
            valid_to=datetime.now(UTC).isoformat(),
        )
    else:
        raise ValueError(f"Unknown review action: {action!r}")

    # Defense in depth: never emit an instruction-grade record whose provenance
    # is not permitted to carry it (mirrors the model-level invariant).
    new_provenance = updates.get("provenance_status", signal.provenance_status)
    new_instruction = updates.get(
        "can_use_as_instruction", signal.can_use_as_instruction
    )
    if new_instruction and not instruction_grade_permitted(new_provenance):
        raise ValueError(
            f"action {action!r} would produce an instruction-grade signal with "
            f"provenance {new_provenance!r}"
        )

    if action in _REVIEW_BINDING_ACTIONS:
        # ADR-009: bind the review to the revision it was applied to.
        updates["reviewed_content_hash"] = content_hash(signal.content)

    # model_copy bypasses model_validators — authority invariant re-checked above by design.
    return signal.model_copy(update=updates)


def content_edit_demotes(signal: Signal, new_content: str) -> bool:
    """Whether replacing ``signal``'s content with ``new_content`` voids its review.

    True when the signal is in a reviewed state (or instruction-grade) and the
    new content's hash differs from the reviewed revision. Signals reviewed
    before revision binding existed carry no ``reviewed_content_hash``; their
    current content stands in for the reviewed revision.
    """
    if signal.review_status not in REVIEWED_STATES and not signal.can_use_as_instruction:
        return False
    reviewed = getattr(signal, "reviewed_content_hash", None) or content_hash(
        signal.content
    )
    return content_hash(new_content) != reviewed


def demote_for_content_change(signal: Signal, new_content: str) -> Signal:
    """Apply a content edit, voiding the review if the reviewed text changed.

    Returns a NEW Signal (the original is unchanged) with ``content`` set to
    ``new_content``. When ``content_edit_demotes`` holds, the copy is also sent
    back to review: ``review_status="pending"`` and
    ``can_use_as_instruction=False``. ``provenance_status`` and
    ``can_use_as_evidence`` are left as they are — demotion only ever removes
    authority, and the authority invariant (instruction-grade requires
    confirmed provenance) holds trivially once instruction-grade is cleared.
    See ADR-009.
    """
    updates: dict = {"content": new_content}
    if content_edit_demotes(signal, new_content):
        updates["review_status"] = "pending"
        updates["can_use_as_instruction"] = False
    # model_copy bypasses model_validators; the update only clears authority.
    return signal.model_copy(update=updates)
