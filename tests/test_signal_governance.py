"""Tests for the Signal trust/governance axis (G1 of the memory-governance PRD).

Covers:
  - Default governance state on freshly-extracted signals.
  - The two-tier authority invariant (instruction-grade requires confirmed
    provenance) — the openbrain SQL CHECK reproduced at the model layer.
  - The review state machine (apply_review) mapping openbrain ReviewMemory
    actions onto ADR-001's 4-way approval gate.

See docs/prd/memory-governance-and-retrieval-prd.md sections 5 and 6.
"""

import pytest
from pydantic import ValidationError

from app.models.signal import Signal
from app.services.signal_governance import (
    INSTRUCTION_GRADE_PROVENANCE,
    apply_review,
    gate_response_for_action,
    instruction_grade_permitted,
)


def _make_signal(**overrides) -> Signal:
    """Construct a minimal valid Signal with optional field overrides."""
    fields: dict[str, object] = dict(
        id="sig-1",
        type="decision",
        content="Ship the governance axis in the next release.",
        source_meeting_id="bot-123",
        source_timestamp="2026-06-05T10:00:00+00:00",
    )
    fields.update(overrides)
    return Signal(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Defaults — a freshly extracted signal is evidence-grade, not instruction-grade
# ---------------------------------------------------------------------------


def test_new_signal_defaults_to_evidence_not_instruction():
    sig = _make_signal()
    assert sig.provenance_status == "generated"
    assert sig.review_status == "pending"
    assert sig.can_use_as_evidence is True
    assert sig.can_use_as_instruction is False
    assert sig.superseded_by is None
    assert sig.tenant_id is None


# ---------------------------------------------------------------------------
# Authority invariant — instruction-grade requires confirmed/imported provenance
# ---------------------------------------------------------------------------


def test_instruction_grade_with_generated_provenance_is_rejected():
    with pytest.raises(ValidationError):
        _make_signal(can_use_as_instruction=True, provenance_status="generated")


def test_instruction_grade_with_user_confirmed_provenance_is_allowed():
    sig = _make_signal(can_use_as_instruction=True, provenance_status="user_confirmed")
    assert sig.can_use_as_instruction is True


def test_instruction_grade_with_imported_provenance_is_allowed():
    sig = _make_signal(can_use_as_instruction=True, provenance_status="imported")
    assert sig.can_use_as_instruction is True


def test_instruction_grade_permitted_predicate():
    assert instruction_grade_permitted("user_confirmed") is True
    assert instruction_grade_permitted("imported") is True
    assert instruction_grade_permitted("generated") is False
    assert instruction_grade_permitted("observed") is False
    # the constant the model enforces against
    assert INSTRUCTION_GRADE_PROVENANCE == frozenset({"user_confirmed", "imported"})


def test_unknown_provenance_status_is_rejected():
    with pytest.raises(ValidationError):
        _make_signal(provenance_status="bogus")


def test_unknown_review_status_is_rejected():
    with pytest.raises(ValidationError):
        _make_signal(review_status="bogus")


# ---------------------------------------------------------------------------
# Review state machine — apply_review transitions
# ---------------------------------------------------------------------------


def test_confirm_promotes_to_instruction_grade():
    sig = _make_signal()
    out = apply_review(sig, "confirm")
    assert out.review_status == "confirmed"
    assert out.provenance_status == "user_confirmed"
    assert out.can_use_as_instruction is True
    assert out.can_use_as_evidence is True
    # identity preserved
    assert out.id == sig.id
    assert out.content == sig.content
    # original is not mutated
    assert sig.can_use_as_instruction is False


def test_reject_removes_evidence_and_instruction():
    sig = _make_signal()
    out = apply_review(sig, "reject")
    assert out.review_status == "rejected"
    assert out.can_use_as_evidence is False
    assert out.can_use_as_instruction is False


def test_evidence_only_keeps_evidence_blocks_instruction():
    sig = _make_signal()
    out = apply_review(sig, "evidence_only")
    assert out.review_status == "evidence_only"
    assert out.can_use_as_evidence is True
    assert out.can_use_as_instruction is False


def test_dispute_blocks_instruction_and_marks_provenance():
    sig = _make_signal()
    out = apply_review(sig, "dispute")
    assert out.provenance_status == "disputed"
    assert out.can_use_as_instruction is False


def test_supersede_marks_record_and_links_successor():
    sig = _make_signal()
    out = apply_review(sig, "supersede", superseded_by="sig-2")
    assert out.provenance_status == "superseded"
    assert out.review_status == "merged"
    assert out.superseded_by == "sig-2"


def test_supersede_from_instruction_grade_downgrades_instruction():
    # A superseded record must not remain instruction-grade — and the supersede
    # transition must not be blocked by the authority invariant.
    sig = _make_signal(
        can_use_as_instruction=True,
        provenance_status="user_confirmed",
        review_status="confirmed",
    )
    out = apply_review(sig, "supersede", superseded_by="sig-2")
    assert out.provenance_status == "superseded"
    assert out.review_status == "merged"
    assert out.can_use_as_instruction is False


def test_supersede_requires_successor_id():
    sig = _make_signal()
    with pytest.raises(ValueError):
        apply_review(sig, "supersede")


def test_unknown_action_raises():
    sig = _make_signal()
    with pytest.raises(ValueError):
        apply_review(sig, "frobnicate")


# ---------------------------------------------------------------------------
# Gate mapping — openbrain review actions onto ADR-001's 4-way gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action,expected",
    [
        ("confirm", "allow"),
        ("supersede", "allow"),
        ("reject", "block"),
        ("evidence_only", "revise"),
        ("dispute", "revise"),
        ("escalate", "escalate"),
    ],
)
def test_gate_response_for_action(action, expected):
    assert gate_response_for_action(action) == expected


def test_gate_response_unknown_action_raises():
    with pytest.raises(ValueError):
        gate_response_for_action("frobnicate")
