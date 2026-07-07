"""Tests for the signal decision-audit trail (G2 of the memory-governance PRD).

Covers:
  - review_with_audit: composes the pure apply_review transition with an
    immutable audit record (action, gate response, derived reasoning, before/
    after governance snapshots).
  - SignalAuditStore: append-only JSONL persistence that survives signal
    deletion (audit keyed by signal id, no foreign key).

See docs/prd/memory-governance-and-retrieval-prd.md §10 (G2) and issue #914.
"""

import pytest

from app.models.signal import Signal, SignalAuditRecord
from app.services.signal_audit import (
    SignalAuditStore,
    review_with_audit,
)


def _make_signal(**overrides) -> Signal:
    fields: dict[str, object] = dict(
        id="sig-1",
        type="decision",
        content="Adopt the governance ladder.",
        source_meeting_id="bot-123",
        source_timestamp="2026-06-05T10:00:00+00:00",
    )
    fields.update(overrides)
    return Signal(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# review_with_audit — transition + audit record
# ---------------------------------------------------------------------------


def test_review_with_audit_returns_updated_signal_and_record():
    sig = _make_signal()
    new_sig, record = review_with_audit(sig, "confirm", actor="alice")

    # transition applied (delegates to apply_review)
    assert new_sig.can_use_as_instruction is True
    assert new_sig.provenance_status == "user_confirmed"

    # audit record captures the transition
    assert isinstance(record, SignalAuditRecord)
    assert record.signal_id == "sig-1"
    assert record.action == "confirm"
    assert record.gate_response == "allow"
    assert record.actor == "alice"
    # before/after snapshot the governance state, not the whole signal
    assert record.before["provenance_status"] == "generated"
    assert record.after["provenance_status"] == "user_confirmed"
    assert record.after["can_use_as_instruction"] is True


def test_audit_reasoning_describes_provenance_and_authority_change():
    sig = _make_signal()
    _, record = review_with_audit(sig, "confirm")
    assert "generated" in record.reasoning
    assert "user_confirmed" in record.reasoning


def test_audit_records_gate_for_reject():
    sig = _make_signal()
    new_sig, record = review_with_audit(sig, "reject")
    assert record.gate_response == "block"
    assert new_sig.can_use_as_evidence is False


def test_review_with_audit_carries_tenant():
    sig = _make_signal(tenant_id="tenant-x")
    _, record = review_with_audit(sig, "evidence_only")
    assert record.tenant_id == "tenant-x"


def test_review_with_audit_propagates_invalid_action():
    sig = _make_signal()
    with pytest.raises(ValueError):
        review_with_audit(sig, "frobnicate")


# ---------------------------------------------------------------------------
# SignalAuditStore — append-only persistence
# ---------------------------------------------------------------------------


def test_store_appends_and_reads_back_in_order(tmp_path):
    store = SignalAuditStore(audit_dir=tmp_path)
    sig = _make_signal()
    _, r1 = review_with_audit(sig, "evidence_only")
    _, r2 = review_with_audit(sig, "confirm")

    store.append(r1)
    store.append(r2)

    history = store.read_for_signal("sig-1")
    assert [r.action for r in history] == ["evidence_only", "confirm"]


def test_store_is_append_only(tmp_path):
    store = SignalAuditStore(audit_dir=tmp_path)
    sig = _make_signal()
    _, r1 = review_with_audit(sig, "evidence_only")
    store.append(r1)
    # a second append must not overwrite the first
    _, r2 = review_with_audit(sig, "reject")
    store.append(r2)
    assert len(store.read_for_signal("sig-1")) == 2


def test_audit_survives_signal_deletion(tmp_path):
    # The audit log is keyed by signal id and persists independently of the
    # signal record itself (no foreign key), mirroring openbrain's memory_audit.
    store = SignalAuditStore(audit_dir=tmp_path)
    sig = _make_signal()
    _, record = review_with_audit(sig, "confirm", actor="bob")
    store.append(record)

    del sig  # the signal object is gone; the audit row must remain

    history = store.read_for_signal("sig-1")
    assert len(history) == 1
    assert history[0].actor == "bob"


def test_relative_path_is_derived_from_audit_dir(tmp_path):
    # relative_path must track the configured audit_dir (relative to repo_root),
    # not a hardcoded prefix — so git references the file actually written.
    store = SignalAuditStore(
        audit_dir=tmp_path / "signals" / "audit", repo_root=tmp_path
    )
    assert store.relative_path("sig-1") == "signals/audit/sig-1.jsonl"


def test_read_skips_corrupt_rows(tmp_path, caplog):
    store = SignalAuditStore(audit_dir=tmp_path)
    sig = _make_signal()
    _, record = review_with_audit(sig, "confirm")
    store.append(record)

    # Append a corrupt (non-JSON) row directly to the log.
    with store._file_path("sig-1").open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")

    with caplog.at_level("WARNING"):
        history = store.read_for_signal("sig-1")

    assert len(history) == 1  # the valid row survives, the corrupt one is skipped
    assert "Skipping corrupt audit row" in caplog.text
