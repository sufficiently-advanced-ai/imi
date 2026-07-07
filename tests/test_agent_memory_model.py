"""Tests for the AgentMemory model (Phase 2 of the OB1 absorption).

AgentMemory is agent-written operational memory (OB1 agent_memories) as a
governed record in the git corpus: same trust axis as signals/captures, but
typed (decision/output/lesson/...) and carrying runtime/task provenance.
Default posture is the ADR-002-hardened one: provenance=generated,
review=pending, never instruction-grade at birth.
"""

import pytest
from pydantic import ValidationError

from app.models.agent_memory import MEMORY_TYPES, AgentMemory


def _make(**overrides) -> AgentMemory:
    fields: dict[str, object] = dict(
        memory_type="lesson",
        content="Batch embedding calls — one-by-one calls hit rate limits.",
    )
    fields.update(overrides)
    return AgentMemory(**fields)  # type: ignore[arg-type]


def test_memory_types_vocabulary():
    assert MEMORY_TYPES == frozenset(
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


def test_defaults_are_generated_pending_evidence_grade():
    mem = _make()
    assert mem.provenance_status == "generated"
    assert mem.review_status == "pending"
    assert mem.can_use_as_evidence is True
    assert mem.can_use_as_instruction is False
    assert mem.superseded_by is None
    assert mem.valid_to is None
    assert mem.visibility == "personal"
    assert mem.confidence == 0.5
    assert mem.source_refs == []
    assert mem.artifacts == []


def test_summary_derived_from_content_when_absent():
    long_content = "x" * 300
    mem = _make(content=long_content)
    assert len(mem.summary) <= 140
    assert mem.summary.startswith("xxx")


def test_memory_type_validated():
    with pytest.raises(ValidationError):
        _make(memory_type="haiku")


def test_visibility_validated():
    with pytest.raises(ValidationError):
        _make(visibility="everyone")


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        _make(confidence=1.5)


def test_authority_invariant_holds_for_agent_memory():
    """ADR-002: generated agent memory can never be instruction-grade."""
    with pytest.raises(ValidationError):
        _make(can_use_as_instruction=True)


def test_shared_review_machinery_confirms_agent_memory():
    from app.services.memory_governance import review_record_with_audit

    mem = _make()
    confirmed, record = review_record_with_audit(
        mem, "confirm", actor="scott", record_kind="agent_memory"
    )
    assert confirmed.can_use_as_instruction is True
    assert confirmed.provenance_status == "user_confirmed"
    assert record.record_kind == "agent_memory"
    assert record.signal_id == mem.id
