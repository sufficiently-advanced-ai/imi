"""Tests for the capture governance surface (Phase 1 of the OB1 absorption).

Covers:
  - CapturedMemory model extensions: supersession/validity fields so the shared
    ``apply_review`` state machine operates on captures unchanged, dormant
    scope columns (workspace/project/visibility), enrichment payload, and
    advisory cross-source links.
  - review_record_with_audit: the thin generalization of the audited review
    path (signal_audit.review_with_audit) over non-signal record kinds.
  - The ADR-002 invariant holds on captures through every transition.

See docs/prd/memory-governance-and-retrieval-prd.md §8 (G4) and the OB1
absorption plan (Phase 1).
"""

import pytest
from pydantic import ValidationError

from app.models.captured_memory import CapturedMemory
from app.services.memory_capture import capture_memory


def _make_capture(**overrides) -> CapturedMemory:
    fields: dict[str, object] = dict(
        content="We standardized on FastAPI for all new services.",
        source="manual",
    )
    fields.update(overrides)
    return CapturedMemory(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Model extensions — fields the shared review machinery requires
# ---------------------------------------------------------------------------


def test_capture_has_supersession_and_validity_fields():
    cap = _make_capture()
    assert cap.superseded_by is None
    assert cap.valid_from is None
    assert cap.valid_to is None


def test_capture_has_dormant_scope_fields():
    cap = _make_capture()
    assert cap.workspace_id is None
    assert cap.project_id is None
    assert cap.visibility == "personal"


def test_capture_visibility_is_validated():
    with pytest.raises(ValidationError):
        _make_capture(visibility="everyone")


def test_capture_has_enrichment_and_related_records():
    cap = _make_capture()
    assert cap.enrichment == {}
    assert cap.related_record_ids == []


# ---------------------------------------------------------------------------
# Shared state machine operates on captures
# ---------------------------------------------------------------------------


def test_apply_review_confirm_makes_capture_instruction_grade():
    from app.services.signal_governance import apply_review

    cap = capture_memory("Use bun over npm.", source="manual")
    confirmed = apply_review(cap, "confirm")
    assert confirmed.review_status == "confirmed"
    assert confirmed.provenance_status == "user_confirmed"
    assert confirmed.can_use_as_instruction is True
    # original unchanged (immutable transition)
    assert cap.can_use_as_instruction is False


def test_apply_review_supersede_closes_validity_window():
    from app.services.signal_governance import apply_review

    cap = capture_memory("Old fact.", source="manual")
    superseded = apply_review(cap, "supersede", superseded_by="cap-successor")
    assert superseded.superseded_by == "cap-successor"
    assert superseded.provenance_status == "superseded"
    assert superseded.review_status == "merged"
    assert superseded.valid_to is not None
    assert superseded.can_use_as_instruction is False


# ---------------------------------------------------------------------------
# review_record_with_audit — audited review over any governed record kind
# ---------------------------------------------------------------------------


def test_review_record_with_audit_emits_capture_kind_row():
    from app.services.memory_governance import review_record_with_audit

    cap = capture_memory("Prefer TypeScript for new projects.", source="manual")
    new_cap, record = review_record_with_audit(
        cap, "confirm", actor="reviewer", record_kind="capture"
    )
    assert new_cap.can_use_as_instruction is True
    assert record.signal_id == cap.id
    assert record.record_kind == "capture"
    assert record.gate_response == "allow"
    assert record.actor == "reviewer"
    # before/after snapshots capture the governance change
    assert record.before["provenance_status"] == "imported"
    assert record.after["provenance_status"] == "user_confirmed"


def test_review_record_with_audit_defaults_to_signal_kind_for_signals():
    from app.models.signal import Signal
    from app.services.signal_audit import review_with_audit

    signal = Signal(
        id="sig-rk-1",
        type="decision",
        content="Adopt the new platform.",
        source_meeting_id="bot-456",
        source_timestamp="2026-06-05T12:00:00+00:00",
    )
    _, record = review_with_audit(signal, "confirm", actor="reviewer")
    assert record.record_kind == "signal"


def test_capture_audit_store_writes_to_memory_audit_dir(tmp_path):
    from app.services.memory_governance import (
        capture_audit_store,
        review_record_with_audit,
    )

    cap = capture_memory("Always run the eval gate before merging.", source="manual")
    _, record = review_record_with_audit(
        cap, "evidence_only", actor="reviewer", record_kind="capture"
    )
    store = capture_audit_store(repo_root=tmp_path)
    path = store.append(record)
    assert path == tmp_path / "memory" / "audit" / f"{cap.id}.jsonl"
    assert store.relative_path(cap.id) == f"memory/audit/{cap.id}.jsonl"
    history = store.read_for_signal(cap.id)
    assert len(history) == 1
    assert history[0].action == "evidence_only"


# ---------------------------------------------------------------------------
# ADR-002 negative: no path builds an instruction-grade unconfirmed capture
# ---------------------------------------------------------------------------


def test_generated_capture_can_never_be_instruction_grade():
    with pytest.raises(ValidationError):
        CapturedMemory(
            content="Agent-inferred rule.",
            source="manual",
            provenance_status="generated",
            can_use_as_instruction=True,
        )


def test_dispute_strips_instruction_grade_from_confirmed_capture():
    from app.services.signal_governance import apply_review

    cap = capture_memory("Disputed fact.", source="manual")
    confirmed = apply_review(cap, "confirm")
    disputed = apply_review(confirmed, "dispute")
    assert disputed.provenance_status == "disputed"
    assert disputed.can_use_as_instruction is False
