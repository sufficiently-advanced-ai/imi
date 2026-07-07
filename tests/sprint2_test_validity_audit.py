"""Tests for the validity-window audit script (R1.1).

Sprint 2 Task 14 — TDD: tests written before implementation.

These tests import the script's public functions directly (audit, fix, main)
and use tmp-dir SignalStore fixtures with deliberate gaps.

Gap categories:
  (a) missing_valid_from          — valid_from is None (requires source_timestamp also
                                    empty/falsy so the model validator doesn't default it)
  (b) superseded_without_valid_to — provenance_status==superseded AND valid_to is None
  (c) superseded_without_successor— provenance_status==superseded AND superseded_by is None
  (d) dangling_successor          — superseded_by set but no signal with that id exists
"""

import importlib.util as _ilu
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers to build minimal Signal / MeetingSignals dicts
# ---------------------------------------------------------------------------

_NOW = "2026-06-11T12:00:00+00:00"
_LATER = "2026-06-11T13:00:00+00:00"

# Ensure project root is on sys.path (mirrors the script's own bootstrap)
_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _sig(
    sig_id: str,
    *,
    # Use empty-string source_timestamp to get valid_from=None after model load.
    # The Signal model validator defaults valid_from from source_timestamp when
    # source_timestamp is truthy; an empty string keeps valid_from=None.
    source_timestamp: str = _NOW,
    valid_from: str | None = _NOW,
    valid_to: str | None = None,
    provenance_status: str = "generated",
    superseded_by: str | None = None,
    created_at: str = _NOW,
) -> dict:
    """Return a minimal serialised Signal dict."""
    return {
        "id": sig_id,
        "type": "key_point",
        "content": f"Content for {sig_id}",
        "source_meeting_id": "meeting-001",
        "source_timestamp": source_timestamp,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "provenance_status": provenance_status,
        "superseded_by": superseded_by,
        "created_at": created_at,
    }


def _ms(bot_id: str, signals: list[dict]) -> dict:
    """Return a minimal MeetingSignals dict."""
    return {
        "meeting_id": f"meet-{bot_id}",
        "bot_id": bot_id,
        "extracted_at": _NOW,
        "signal_count": len(signals),
        "signals": signals,
    }


def _write_ms(signals_dir: Path, bot_id: str, signals: list[dict]) -> None:
    """Write a meeting signals JSON file to signals_dir."""
    signals_dir.mkdir(parents=True, exist_ok=True)
    path = signals_dir / f"meeting-{bot_id}.json"
    path.write_text(json.dumps(_ms(bot_id, signals), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Import the script functions (deferred until after sys.path setup)
# ---------------------------------------------------------------------------
# The scripts/ directory is not a Python package and is not bind-mounted into
# the dev container (it is baked into the image), so we use importlib to load
# the module from its absolute path rather than the dotted-package form.

_SCRIPT_PATH = Path(_ROOT) / "scripts" / "audit_validity_windows.py"
_spec = _ilu.spec_from_file_location("audit_validity_windows", str(_SCRIPT_PATH))
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

Gap = _mod.Gap
GapKind = _mod.GapKind
audit = _mod.audit
fix = _mod.fix
main = _mod.main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_store(tmp_path):
    """SignalStore with one perfectly-formed signal — no gaps."""
    signals_dir = tmp_path / "signals"
    _write_ms(
        signals_dir,
        "bot-clean",
        [
            _sig(
                "sig-clean-1",
                source_timestamp=_NOW,
                valid_from=_NOW,
                provenance_status="generated",
            )
        ],
    )
    from app.services.signal_store import SignalStore

    return SignalStore(signals_dir=signals_dir)


@pytest.fixture()
def gapped_store(tmp_path):
    """
    SignalStore with one signal per gap category:

    (a) missing_valid_from   — sig-a: source_timestamp="" so valid_from stays None
    (b) superseded_no_to     — sig-b: superseded, valid_to=None, successor sig-c exists
    (b2) superseded_no_to_ns — sig-b2: superseded, valid_to=None, no successor
    (c) superseded_no_by     — sig-c: provenance_status=superseded, superseded_by=None
                                (also triggers (b) since valid_to=None)
    (d) dangling_successor   — sig-d: superseded_by="sig-ghost" (doesn't exist)
    healthy                  — sig-h: clean signal (should never appear in report)
    """
    signals_dir = tmp_path / "signals"

    # (a) source_timestamp="" → model validator skips defaulting → valid_from=None
    sig_a = _sig(
        "sig-a",
        source_timestamp="",  # falsy — model validator won't default valid_from
        valid_from=None,
        created_at=_NOW,
    )

    # successor used by sig-b
    sig_c = _sig(
        "sig-c",
        provenance_status="superseded",
        superseded_by=None,
        valid_from=_NOW,
        valid_to=None,  # also triggers (b) on sig-c itself
        created_at=_LATER,
    )
    # (b) superseded with a known successor but missing valid_to
    sig_b = _sig(
        "sig-b",
        provenance_status="superseded",
        valid_to=None,
        superseded_by="sig-c",
        valid_from=_NOW,
        created_at=_NOW,
    )
    # (b2) + (c) superseded with no successor AND no valid_to
    sig_b2 = _sig(
        "sig-b2",
        provenance_status="superseded",
        valid_to=None,
        superseded_by=None,
        valid_from=_NOW,
        created_at=_NOW,
    )
    # (d) dangling successor reference
    sig_d = _sig(
        "sig-d",
        provenance_status="superseded",
        superseded_by="sig-ghost",
        valid_from=_NOW,
        valid_to=_NOW,  # valid_to is set so no (b) gap
        created_at=_NOW,
    )
    # healthy — should never appear in any gap report
    sig_h = _sig("sig-h", valid_from=_NOW, created_at=_NOW)

    _write_ms(
        signals_dir,
        "bot-gapped",
        [sig_a, sig_b, sig_b2, sig_c, sig_d, sig_h],
    )

    from app.services.signal_store import SignalStore

    return SignalStore(signals_dir=signals_dir)


# ---------------------------------------------------------------------------
# Tests: detection (audit function)
# ---------------------------------------------------------------------------


class TestAuditDetection:
    def test_clean_store_no_gaps(self, clean_store):
        gaps = audit(clean_store)
        assert gaps == [], f"Expected no gaps, got {gaps}"

    def test_detects_missing_valid_from(self, gapped_store):
        gaps = audit(gapped_store)
        kinds = {g.kind for g in gaps}
        assert GapKind.missing_valid_from in kinds, f"Kinds found: {kinds}"

    def test_detects_superseded_without_valid_to(self, gapped_store):
        gaps = audit(gapped_store)
        kinds = {g.kind for g in gaps}
        assert GapKind.superseded_without_valid_to in kinds, f"Kinds found: {kinds}"

    def test_detects_superseded_without_successor(self, gapped_store):
        gaps = audit(gapped_store)
        kinds = {g.kind for g in gaps}
        assert GapKind.superseded_without_successor in kinds, f"Kinds found: {kinds}"

    def test_detects_dangling_successor(self, gapped_store):
        gaps = audit(gapped_store)
        kinds = {g.kind for g in gaps}
        assert GapKind.dangling_successor in kinds, f"Kinds found: {kinds}"

    def test_healthy_signal_not_reported(self, gapped_store):
        gaps = audit(gapped_store)
        gap_ids = {g.signal_id for g in gaps}
        assert (
            "sig-h" not in gap_ids
        ), f"Healthy signal should not be in gaps: {gap_ids}"

    def test_gap_contains_meeting_id(self, gapped_store):
        gaps = audit(gapped_store)
        for g in gaps:
            assert g.meeting_id is not None


# ---------------------------------------------------------------------------
# Tests: fix function
# ---------------------------------------------------------------------------


class TestFix:
    def test_fix_repairs_missing_valid_from_using_created_at(self, tmp_path):
        """(a) fix sets valid_from = created_at when source_timestamp is empty/falsy."""
        signals_dir = tmp_path / "signals"
        sig_a = _sig(
            "sig-a",
            source_timestamp="",
            valid_from=None,
            created_at=_NOW,
        )
        _write_ms(signals_dir, "bot-fix-a", [sig_a])

        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=signals_dir)
        gaps = audit(store)
        assert any(g.kind == GapKind.missing_valid_from for g in gaps)

        fixed = fix(store, gaps)
        assert fixed >= 1

        # Re-audit: should have no (a) gaps
        store2 = SignalStore(signals_dir=signals_dir)
        remaining = audit(store2)
        assert not any(g.kind == GapKind.missing_valid_from for g in remaining)

        # Value should equal created_at
        ms = store2.load("bot-fix-a")
        assert ms is not None
        sig = next(s for s in ms.signals if s.id == "sig-a")
        assert sig.valid_from == _NOW

    def test_fix_repairs_superseded_without_valid_to_uses_successor_valid_from(
        self, tmp_path
    ):
        """(b) fix sets valid_to = successor's valid_from when available (Fix K)."""
        signals_dir = tmp_path / "signals"
        # Successor has valid_from=_NOW and created_at=_LATER; fix should prefer valid_from.
        sig_successor = _sig(
            "sig-succ",
            provenance_status="superseded",
            superseded_by=None,
            valid_from=_NOW,
            valid_to=_NOW,  # has valid_to so no (b) gap on this one
            created_at=_LATER,
        )
        sig_pred = _sig(
            "sig-pred",
            provenance_status="superseded",
            valid_to=None,
            superseded_by="sig-succ",
            valid_from=_NOW,
            created_at=_NOW,
        )
        _write_ms(signals_dir, "bot-fix-b", [sig_pred, sig_successor])

        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=signals_dir)
        gaps = audit(store)
        assert any(g.kind == GapKind.superseded_without_valid_to for g in gaps)

        fix(store, gaps)

        store2 = SignalStore(signals_dir=signals_dir)
        ms = store2.load("bot-fix-b")
        assert ms is not None
        pred = next(s for s in ms.signals if s.id == "sig-pred")
        # Fix K: prefer successor.valid_from over successor.created_at
        assert (
            pred.valid_to == _NOW
        ), f"Expected valid_to=={_NOW!r} (successor.valid_from), got {pred.valid_to!r}"

    def test_fix_repairs_superseded_without_valid_to_falls_back_to_now(self, tmp_path):
        """(b) fix sets valid_to = now-ISO when no successor exists (sig-b2 case)."""
        signals_dir = tmp_path / "signals"
        sig = _sig(
            "sig-b2",
            provenance_status="superseded",
            valid_to=None,
            superseded_by=None,
            valid_from=_NOW,
            created_at=_NOW,
        )
        _write_ms(signals_dir, "bot-fix-b2", [sig])

        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=signals_dir)
        gaps_before = audit(store)
        # sig-b2 has both superseded_without_valid_to AND superseded_without_successor
        assert any(g.kind == GapKind.superseded_without_valid_to for g in gaps_before)

        before_ts = datetime.now(UTC)
        fix(store, gaps_before)
        after_ts = datetime.now(UTC)

        store2 = SignalStore(signals_dir=signals_dir)
        ms = store2.load("bot-fix-b2")
        assert ms is not None
        fixed_sig = next(s for s in ms.signals if s.id == "sig-b2")
        assert fixed_sig.valid_to is not None
        # It should be close to now
        ts = datetime.fromisoformat(fixed_sig.valid_to)
        assert before_ts <= ts <= after_ts

    def test_fix_does_not_auto_fix_superseded_without_successor(self, tmp_path):
        """(c) superseded_without_successor is NOT auto-fixed."""
        signals_dir = tmp_path / "signals"
        # A superseded signal with no superseded_by AND valid_to already set
        # (so only (c) applies, not (b)).
        sig = _sig(
            "sig-c",
            provenance_status="superseded",
            superseded_by=None,
            valid_to=_NOW,  # already has valid_to so no (b) gap
            valid_from=_NOW,
            created_at=_NOW,
        )
        _write_ms(signals_dir, "bot-fix-c", [sig])

        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=signals_dir)
        gaps = audit(store)
        assert any(g.kind == GapKind.superseded_without_successor for g in gaps)
        fix(store, gaps)

        store2 = SignalStore(signals_dir=signals_dir)
        remaining = audit(store2)
        assert any(
            g.kind == GapKind.superseded_without_successor for g in remaining
        ), "Category (c) should NOT be auto-fixed"

    def test_fix_does_not_auto_fix_dangling_successor(self, tmp_path):
        """(d) dangling_successor is NOT auto-fixed."""
        signals_dir = tmp_path / "signals"
        sig = _sig(
            "sig-d",
            provenance_status="superseded",
            superseded_by="sig-ghost",
            valid_from=_NOW,
            valid_to=_NOW,
            created_at=_NOW,
        )
        _write_ms(signals_dir, "bot-fix-d", [sig])

        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=signals_dir)
        gaps = audit(store)
        fix(store, gaps)

        store2 = SignalStore(signals_dir=signals_dir)
        remaining = audit(store2)
        assert any(
            g.kind == GapKind.dangling_successor for g in remaining
        ), "Category (d) should NOT be auto-fixed"

    def test_valid_signals_untouched_by_fix(self, gapped_store, tmp_path):
        """Healthy signals must not be modified by fix."""
        # Load the healthy signal before fix
        ms_before = gapped_store.load("bot-gapped")
        assert ms_before is not None
        sig_h_before = next(s for s in ms_before.signals if s.id == "sig-h")

        gaps = audit(gapped_store)
        fix(gapped_store, gaps)

        ms_after = gapped_store.load("bot-gapped")
        assert ms_after is not None
        sig_h_after = next(s for s in ms_after.signals if s.id == "sig-h")

        assert sig_h_before.model_dump() == sig_h_after.model_dump()

    def test_idempotent_fix(self, tmp_path):
        """Running fix twice should leave no (a)/(b) gaps on second run."""
        signals_dir = tmp_path / "signals"
        sig_a = _sig(
            "sig-a",
            source_timestamp="",
            valid_from=None,
            created_at=_NOW,
        )
        sig_b = _sig(
            "sig-b",
            provenance_status="superseded",
            valid_to=None,
            superseded_by=None,
            valid_from=_NOW,
            created_at=_NOW,
        )
        _write_ms(signals_dir, "bot-idem", [sig_a, sig_b])

        from app.services.signal_store import SignalStore

        # First fix
        store1 = SignalStore(signals_dir=signals_dir)
        gaps1 = audit(store1)
        fix(store1, gaps1)

        # Second fix — reload from disk
        store2 = SignalStore(signals_dir=signals_dir)
        gaps2 = audit(store2)
        fixable_kinds = {
            GapKind.missing_valid_from,
            GapKind.superseded_without_valid_to,
        }
        fixable = [g for g in gaps2 if g.kind in fixable_kinds]
        assert fixable == [], f"Second run should have no fixable gaps, got: {fixable}"


# ---------------------------------------------------------------------------
# Tests: exit codes via main()
# ---------------------------------------------------------------------------


class TestExitCodes:
    def test_exit_0_on_clean_store(self, tmp_path):
        signals_dir = tmp_path / "signals"
        _write_ms(
            signals_dir,
            "bot-clean",
            [_sig("sig-ok", valid_from=_NOW)],
        )
        with pytest.raises(SystemExit) as exc_info:
            main(["--signals-dir", str(signals_dir)])
        assert exc_info.value.code == 0

    def test_exit_1_on_gaps(self, tmp_path):
        signals_dir = tmp_path / "signals"
        sig_a = _sig(
            "sig-a",
            source_timestamp="",
            valid_from=None,
            created_at=_NOW,
        )
        _write_ms(signals_dir, "bot-gaps", [sig_a])
        with pytest.raises(SystemExit) as exc_info:
            main(["--signals-dir", str(signals_dir)])
        assert exc_info.value.code == 1

    def test_exit_0_after_fix_when_only_fixable_gaps(self, tmp_path):
        """--fix on a store with only (a)/(b) gaps → exits 0."""
        signals_dir = tmp_path / "signals"
        sig_a = _sig(
            "sig-a",
            source_timestamp="",
            valid_from=None,
            created_at=_NOW,
        )
        _write_ms(signals_dir, "bot-fixable", [sig_a])
        with pytest.raises(SystemExit) as exc_info:
            main(["--signals-dir", str(signals_dir), "--fix"])
        assert exc_info.value.code == 0

    def test_exit_1_after_fix_when_unfixable_gaps_remain(self, tmp_path):
        """--fix on a store with (d) dangling gaps → exits 1 (unfixed remain)."""
        signals_dir = tmp_path / "signals"
        sig_d = _sig(
            "sig-d",
            provenance_status="superseded",
            superseded_by="sig-ghost",
            valid_from=_NOW,
            valid_to=_NOW,
            created_at=_NOW,
        )
        _write_ms(signals_dir, "bot-unfixable", [sig_d])
        with pytest.raises(SystemExit) as exc_info:
            main(["--signals-dir", str(signals_dir), "--fix"])
        assert exc_info.value.code == 1

    def test_main_smoke_with_signals_dir(self, tmp_path):
        """main() runs end-to-end with --signals-dir pointing at a clean dir."""
        signals_dir = tmp_path / "signals"
        _write_ms(
            signals_dir,
            "bot-smoke",
            [_sig("sig-smoke", valid_from=_NOW)],
        )
        with pytest.raises(SystemExit) as exc_info:
            main(["--signals-dir", str(signals_dir)])
        assert exc_info.value.code == 0
