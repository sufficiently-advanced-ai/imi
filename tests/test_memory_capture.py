"""Tests for the general memory capture layer (G4 of the memory-governance PRD).

Covers:
  - content_fingerprint: normalized sha256 dedup key (ported from openbrain).
  - capture_memory: web/mail/manual content enters the governance ladder as
    imported, evidence-grade (not instruction-grade until a human confirms).
  - CaptureStore: dedup by (source, source_id) and by content fingerprint.

See docs/prd/memory-governance-and-retrieval-prd.md §8 (G4).
"""

import pytest
from pydantic import ValidationError

from app.models.captured_memory import CapturedMemory
from app.services.memory_capture import (
    CaptureStore,
    capture_memory,
    content_fingerprint,
)


# ---------------------------------------------------------------------------
# content_fingerprint — normalized dedup key
# ---------------------------------------------------------------------------


def test_fingerprint_is_stable_64_hex():
    fp = content_fingerprint("Hello, world.")
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_normalizes_case_and_whitespace():
    a = content_fingerprint("Hello   World")
    b = content_fingerprint("  hello world  ")
    assert a == b


def test_fingerprint_distinguishes_different_content():
    assert content_fingerprint("alpha") != content_fingerprint("beta")


# ---------------------------------------------------------------------------
# capture_memory — enters the governance ladder as imported/evidence-grade
# ---------------------------------------------------------------------------


def test_captured_memory_is_imported_evidence_not_instruction():
    mem = capture_memory("A useful article about caching.", source="web")
    assert mem.provenance_status == "imported"
    assert mem.can_use_as_evidence is True
    assert mem.can_use_as_instruction is False
    assert mem.review_status == "pending"
    assert mem.content_fingerprint == content_fingerprint(
        "A useful article about caching."
    )


def test_capture_rejects_empty_content():
    # Empty evidence is meaningless: CapturedMemory enforces min_length=1, so
    # capturing empty content is rejected rather than silently stored.
    with pytest.raises(ValidationError):
        capture_memory("", source="manual")


def test_capture_carries_source_and_tenant():
    mem = capture_memory("note body", source="manual", source_id="n-1", tenant_id="t-9")
    assert mem.source == "manual"
    assert mem.source_id == "n-1"
    assert mem.tenant_id == "t-9"


def test_captured_memory_authority_invariant_holds():
    # Reuses the signal authority invariant: instruction-grade requires
    # confirmed/imported provenance.
    with pytest.raises(ValidationError):
        CapturedMemory(
            content="x",
            source="web",
            provenance_status="observed",
            can_use_as_instruction=True,
        )
    # imported provenance may carry instruction grade (after human confirmation)
    ok = CapturedMemory(
        content="x",
        source="web",
        provenance_status="imported",
        can_use_as_instruction=True,
    )
    assert ok.can_use_as_instruction is True


# ---------------------------------------------------------------------------
# CaptureStore — deduplication
# ---------------------------------------------------------------------------


def test_dedup_by_source_and_source_id(tmp_path):
    store = CaptureStore(capture_dir=tmp_path)
    first = store.capture("article body v1", source="web", source_id="https://x/a")
    dup = store.capture("article body v2 edited", source="web", source_id="https://x/a")
    assert first.deduped is False
    assert dup.deduped is True
    assert dup.memory.id == first.memory.id  # same record returned


def test_dedup_by_content_fingerprint_when_no_source_id(tmp_path):
    store = CaptureStore(capture_dir=tmp_path)
    first = store.capture("same exact freeform note", source="manual")
    dup = store.capture("  SAME exact   freeform note ", source="manual")
    assert first.deduped is False
    assert dup.deduped is True
    assert dup.memory.id == first.memory.id


def test_distinct_content_is_not_deduped(tmp_path):
    store = CaptureStore(capture_dir=tmp_path)
    a = store.capture("first note", source="manual")
    b = store.capture("second different note", source="manual")
    assert a.deduped is False
    assert b.deduped is False
    assert a.memory.id != b.memory.id
