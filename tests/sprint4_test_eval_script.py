"""Tests for the conflict-precision evaluation gate script (S4-5).

TDD: all tests written to fail first, then the script was implemented.

Imports the script via importlib (same pattern as sprint2_test_validity_audit.py)
so the tests work without the scripts/ directory being a Python package.

All Claude API calls are mocked — no real LLM calls.
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---------------------------------------------------------------------------
# Import the script via importlib
# ---------------------------------------------------------------------------
_SCRIPT_PATH = Path(_ROOT) / "scripts" / "eval_conflict_precision.py"
_spec = _ilu.spec_from_file_location("eval_conflict_precision", str(_SCRIPT_PATH))
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

load_pairs = _mod.load_pairs
pair_to_signals = _mod.pair_to_signals
evaluate = _mod.evaluate
main = _mod.main

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_FIXTURE_PATH = Path(_ROOT) / "tests" / "fixtures" / "conflict_eval_pairs.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_verdict(contradicts: bool, confidence: float, rationale: str = "test"):
    """Build a ConflictVerdict-like object (no import needed — duck-typed)."""
    from app.services.conflict_detector import ConflictVerdict

    return ConflictVerdict(
        contradicts=contradicts,
        confidence=confidence,
        rationale=rationale,
        speakers=[],
    )


def _mock_client_from_verdicts(verdicts):
    """Return a mock async client whose judge_conflict side_effect is *verdicts*.

    We patch judge_conflict directly (it is the async function under test),
    so the client object only needs to exist — it is passed through but the
    actual LLM call never fires.
    """
    client = MagicMock()
    return client


# ---------------------------------------------------------------------------
# 1. Fixture integrity
# ---------------------------------------------------------------------------


class TestFixtureIntegrity:
    def test_fixture_file_exists(self):
        assert _FIXTURE_PATH.exists(), f"Fixture not found at {_FIXTURE_PATH}"

    def test_fixture_loads(self):
        pairs = load_pairs(_FIXTURE_PATH)
        assert isinstance(pairs, list)

    def test_exactly_16_pairs(self):
        pairs = load_pairs(_FIXTURE_PATH)
        assert len(pairs) == 16, f"Expected 16 pairs, got {len(pairs)}"

    def test_8_true_contradictions(self):
        pairs = load_pairs(_FIXTURE_PATH)
        trues = [p for p in pairs if p["expected_contradiction"] is True]
        assert len(trues) == 8, f"Expected 8 TRUE pairs, got {len(trues)}"

    def test_8_false_non_contradictions(self):
        pairs = load_pairs(_FIXTURE_PATH)
        falses = [p for p in pairs if p["expected_contradiction"] is False]
        assert len(falses) == 8, f"Expected 8 FALSE pairs, got {len(falses)}"

    def test_each_pair_has_required_top_keys(self):
        pairs = load_pairs(_FIXTURE_PATH)
        required = {"id", "expected_contradiction", "a", "b", "note"}
        for pair in pairs:
            missing = required - set(pair)
            assert not missing, f"Pair {pair.get('id')} missing keys: {missing}"

    def test_each_side_has_required_keys(self):
        pairs = load_pairs(_FIXTURE_PATH)
        required = {"content", "meeting_title", "speaker", "timestamp"}
        for pair in pairs:
            for side in ("a", "b"):
                missing = required - set(pair[side])
                assert (
                    not missing
                ), f"Pair {pair['id']} side '{side}' missing keys: {missing}"

    def test_all_pair_ids_unique(self):
        pairs = load_pairs(_FIXTURE_PATH)
        ids = [p["id"] for p in pairs]
        assert len(ids) == len(set(ids)), "Duplicate pair IDs found"

    def test_load_pairs_invalid_path_raises(self, tmp_path):
        with pytest.raises(ValueError):
            load_pairs(tmp_path / "does_not_exist.json")

    def test_load_pairs_non_array_raises(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text('{"not": "an array"}')
        with pytest.raises(ValueError, match="JSON array"):
            load_pairs(bad)

    def test_load_pairs_missing_key_raises(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                [
                    {
                        "id": "x",
                        "expected_contradiction": True,
                        "a": {
                            "content": "c",
                            "meeting_title": "m",
                            "speaker": "s",
                            "timestamp": "t",
                        },
                        # "b" key is missing
                    }
                ]
            )
        )
        with pytest.raises(ValueError):
            load_pairs(bad)


# ---------------------------------------------------------------------------
# 2. pair_to_signals
# ---------------------------------------------------------------------------


class TestPairToSignals:
    def test_returns_two_signals(self):
        pairs = load_pairs(_FIXTURE_PATH)
        sig_a, sig_b = pair_to_signals(pairs[0])
        from app.models.signal import Signal

        assert isinstance(sig_a, Signal)
        assert isinstance(sig_b, Signal)

    def test_signals_are_decision_type(self):
        pairs = load_pairs(_FIXTURE_PATH)
        sig_a, sig_b = pair_to_signals(pairs[0])
        assert sig_a.type == "decision"
        assert sig_b.type == "decision"

    def test_distinct_meeting_ids(self):
        pairs = load_pairs(_FIXTURE_PATH)
        sig_a, sig_b = pair_to_signals(pairs[0])
        assert sig_a.source_meeting_id != sig_b.source_meeting_id

    def test_all_pairs_produce_distinct_meeting_ids(self):
        pairs = load_pairs(_FIXTURE_PATH)
        for pair in pairs:
            sig_a, sig_b = pair_to_signals(pair)
            assert (
                sig_a.source_meeting_id != sig_b.source_meeting_id
            ), f"Pair {pair['id']} has identical meeting ids"

    def test_owner_built_from_speaker(self):
        pairs = load_pairs(_FIXTURE_PATH)
        pair = pairs[0]
        sig_a, sig_b = pair_to_signals(pair)
        assert sig_a.owner is not None
        assert sig_a.owner.name == pair["a"]["speaker"]
        assert sig_b.owner is not None
        assert sig_b.owner.name == pair["b"]["speaker"]

    def test_entities_empty(self):
        pairs = load_pairs(_FIXTURE_PATH)
        sig_a, sig_b = pair_to_signals(pairs[0])
        assert sig_a.entities == []
        assert sig_b.entities == []

    def test_content_matches_fixture(self):
        pairs = load_pairs(_FIXTURE_PATH)
        pair = pairs[0]
        sig_a, sig_b = pair_to_signals(pair)
        assert sig_a.content == pair["a"]["content"]
        assert sig_b.content == pair["b"]["content"]

    def test_signal_ids_distinct_across_all_pairs(self):
        pairs = load_pairs(_FIXTURE_PATH)
        all_ids = []
        for pair in pairs:
            sig_a, sig_b = pair_to_signals(pair)
            all_ids.extend([sig_a.id, sig_b.id])
        assert len(all_ids) == len(set(all_ids)), "Signal IDs not unique across pairs"


# ---------------------------------------------------------------------------
# 3. evaluate — math correctness with canned verdicts
# ---------------------------------------------------------------------------


class TestEvaluateMath:
    """
    Fixture: 16 pairs, 8 TRUE + 8 FALSE (pairs 01-08 = TRUE, 09-16 = FALSE).

    We feed canned verdicts via patching judge_conflict.
    CONFLICT_CONFIDENCE_THRESHOLD defaults to 0.7.

    Scenario A — perfect detector:
        - TRUE pairs: contradicts=True, confidence=0.9
        - FALSE pairs: contradicts=False, confidence=0.1
        → TP=8, FP=0, TN=8, FN=0
        → precision=1.0, recall=1.0, fp_rate=0.0

    Scenario B — 2 FP, 1 FN (imperfect):
        - TRUE pairs 01-07: contradicts=True, confidence=0.9  → TP=7
        - TRUE pair 08:    contradicts=False, confidence=0.5  → FN=1
        - FALSE pairs 09-10: contradicts=True, confidence=0.9 → FP=2
        - FALSE pairs 11-16: contradicts=False, confidence=0.1 → TN=6
        → TP=7, FP=2, TN=6, FN=1
        → precision = 7/(7+2) = 7/9 ≈ 0.778
        → recall    = 7/(7+1) = 7/8 = 0.875
        → fp_rate   = 2/(6+2) = 2/8 = 0.25

    Scenario C — all predicted negative (all contradicts=False):
        → TP=0, FP=0, TN=8, FN=8
        → precision = 1.0 (zero-division guard: no positive predictions)
        → recall = 0.0

    Scenario D — parse failures count as predicted False:
        - All 16 pairs return None from judge_conflict
        → parse_failures=16, TP=0, FP=0, TN=8, FN=8
        → precision=1.0 (zero-division guard)
    """

    @pytest.fixture()
    def pairs(self):
        return load_pairs(_FIXTURE_PATH)

    def _build_side_effects(self, verdicts_sequence):
        """verdicts_sequence: list of 16 ConflictVerdict|None in pair order."""
        return verdicts_sequence











# ---------------------------------------------------------------------------
# 4. main() exit codes
# ---------------------------------------------------------------------------


class TestMainExitCodes:
    @pytest.fixture()
    def fixture_path(self):
        return str(_FIXTURE_PATH)

    def _perfect_side_effects(self):
        """16 verdicts: TRUE pairs → contradicts=True/conf=0.9, FALSE → False/conf=0.1."""
        pairs = load_pairs(_FIXTURE_PATH)
        side_effects = []
        for pair in pairs:
            if pair["expected_contradiction"]:
                side_effects.append(_make_verdict(True, 0.9))
            else:
                side_effects.append(_make_verdict(False, 0.1))
        return side_effects

    def _below_gate_side_effects(self):
        """Make precision drop below 0.8: many FP (all pairs predicted contradicting)."""
        pairs = load_pairs(_FIXTURE_PATH)
        # All predicted True: 8 TRUE → TP=8, 8 FALSE → FP=8
        # precision = 8/(8+8) = 0.5 < 0.8
        return [_make_verdict(True, 0.9) for _ in pairs]

    def _common_patches(self, judge_side_effects):
        """Return a context-manager stack patching judge_conflict + get_claude_client."""
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(
            patch(
                "app.services.conflict_detector.judge_conflict",
                new=AsyncMock(side_effect=judge_side_effects),
            )
        )
        stack.enter_context(
            patch(
                "app.services.claude_client.get_claude_client",
                return_value=MagicMock(),
            )
        )
        return stack



    def test_exit_1_on_missing_fixture(self, tmp_path):
        result = main(["--fixture", str(tmp_path / "nonexistent.json")])
        assert result == 1


