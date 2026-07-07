"""Tests for staleness/zombie evaluation job (Sprint 3, Task S3-3 — R2.3).

TDD: tests written first, then implementation.

Coverage:
- evaluate_states: pure read, returns {signal_id: {state, state_reason}} for all decisions
- run_staleness_evaluation:
    - first run: seeds snapshot, no transitions, committed=True, first_run=True
    - second run, no changes: no transitions, NO commit, snapshot unchanged
    - second run with transition: transitions logged, JSONL updated, commit called
    - new decision between runs: not a transition, appears in new snapshot
    - git failure: committed=False, files still written, transitions returned
- HTTP endpoint: POST /api/decisions/staleness/evaluate returns 200 with expected shape
- Route-order guard: staleness/evaluate declared before /{decision_id}
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

NOW_BASE = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)  # base "now" for run-1
NOW_STALE = NOW_BASE + timedelta(days=91)  # run-2 "now" that ages a decision to stale


def _make_signal(
    *,
    signal_id: str,
    source_timestamp: str,
    review_status: str = "pending",
    provenance_status: str = "generated",
    superseded_by: str | None = None,
    metadata: dict | None = None,
    signal_type: str = "decision",
) -> "Signal":
    from app.models.signal import Signal

    return Signal(
        id=signal_id,
        type=signal_type,
        content=f"Decision content for {signal_id}",
        source_meeting_id="bot-test-001",
        source_timestamp=source_timestamp,
        review_status=review_status,
        provenance_status=provenance_status,
        superseded_by=superseded_by,
        metadata=metadata or {},
    )


def _build_store(tmp_path: Path, signals: list) -> "SignalStore":
    from app.models.signal import MeetingSignals
    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path / "signals")
    ms = MeetingSignals(
        meeting_id="meet-test-001",
        bot_id="bot-test-001",
        signals=signals,
    )
    store.save(ms)
    return store


def _make_git_ops(tmp_path: Path) -> MagicMock:
    """Return a mock git_ops with repo_path set and commit_and_push as AsyncMock."""
    mock = MagicMock()
    mock.repo_path = str(tmp_path)
    mock.commit_and_push = AsyncMock()
    return mock


# ---------------------------------------------------------------------------
# 1. evaluate_states — pure read
# ---------------------------------------------------------------------------


class TestEvaluateStates:
    """Pure-read evaluate_states: {signal_id: {state, state_reason}} for all decisions."""

    def test_returns_entry_per_decision_signal(self, tmp_path):
        from app.services.staleness_evaluator import evaluate_states

        signals = [
            _make_signal(
                signal_id="dec-001",
                source_timestamp="2026-03-01T12:00:00Z",
                review_status="confirmed",
            ),
            _make_signal(
                signal_id="dec-002",
                source_timestamp="2026-03-01T12:00:00Z",
                review_status="pending",
            ),
        ]
        store = _build_store(tmp_path, signals)
        result = evaluate_states(store=store, now=NOW_BASE)

        assert "dec-001" in result
        assert "dec-002" in result
        assert result["dec-001"]["state"] == "active"
        assert "state_reason" in result["dec-001"]
        assert result["dec-002"]["state"] == "candidate"

    def test_excludes_non_decision_signals(self, tmp_path):
        from app.models.signal import MeetingSignals
        from app.services.signal_store import SignalStore
        from app.services.staleness_evaluator import evaluate_states

        # Mix decision and action_item
        dec = _make_signal(
            signal_id="dec-only",
            source_timestamp="2026-03-01T12:00:00Z",
            signal_type="decision",
        )
        action = _make_signal(
            signal_id="action-only",
            source_timestamp="2026-03-01T12:00:00Z",
            signal_type="action_item",
        )
        store = SignalStore(signals_dir=tmp_path / "signals")
        store.save(
            MeetingSignals(
                meeting_id="m1",
                bot_id="bot-1",
                signals=[dec, action],
            )
        )
        result = evaluate_states(store=store, now=NOW_BASE)

        assert "dec-only" in result
        assert "action-only" not in result

    def test_state_and_reason_keys_present(self, tmp_path):
        from app.services.staleness_evaluator import evaluate_states

        signals = [
            _make_signal(
                signal_id="dec-check",
                source_timestamp="2026-03-01T12:00:00Z",
            )
        ]
        store = _build_store(tmp_path, signals)
        result = evaluate_states(store=store, now=NOW_BASE)

        entry = result["dec-check"]
        assert "state" in entry
        assert "state_reason" in entry
        assert isinstance(entry["state"], str)
        assert isinstance(entry["state_reason"], str)

    def test_empty_store_returns_empty_dict(self, tmp_path):
        from app.services.signal_store import SignalStore
        from app.services.staleness_evaluator import evaluate_states

        store = SignalStore(signals_dir=tmp_path / "signals")
        result = evaluate_states(store=store, now=NOW_BASE)
        assert result == {}

    def test_superseded_state_detected(self, tmp_path):
        from app.services.staleness_evaluator import evaluate_states

        signals = [
            _make_signal(
                signal_id="dec-sup",
                source_timestamp="2026-03-01T12:00:00Z",
                superseded_by="dec-newer",
            )
        ]
        store = _build_store(tmp_path, signals)
        result = evaluate_states(store=store, now=NOW_BASE)
        assert result["dec-sup"]["state"] == "superseded"

    def test_stale_detected_by_age(self, tmp_path):
        from app.services.staleness_evaluator import evaluate_states

        # Decision is 100 days old at NOW_STALE
        old_ts = (NOW_STALE - timedelta(days=100)).isoformat()
        signals = [
            _make_signal(
                signal_id="dec-stale",
                source_timestamp=old_ts,
            )
        ]
        store = _build_store(tmp_path, signals)
        result = evaluate_states(store=store, now=NOW_STALE)
        assert result["dec-stale"]["state"] == "stale"


# ---------------------------------------------------------------------------
# 2. run_staleness_evaluation — snapshot + JSONL writer
# ---------------------------------------------------------------------------


class TestRunStalenessEvaluationFirstRun:
    """First run: seed snapshot, no transitions, first_run=True."""

    @pytest.mark.asyncio
    async def test_first_run_seeds_snapshot(self, tmp_path):
        from app.services.staleness_evaluator import (
            SNAPSHOT_RELATIVE_PATH,
            run_staleness_evaluation,
        )

        signals = [
            _make_signal(
                signal_id="dec-001",
                source_timestamp="2026-03-01T12:00:00Z",
                review_status="confirmed",
            )
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        result = await run_staleness_evaluation(
            store=store,
            git_ops_override=mock_git,
            now=NOW_BASE,
            commit=True,
        )

        snapshot_path = tmp_path / SNAPSHOT_RELATIVE_PATH
        assert snapshot_path.exists(), "Snapshot file must be written on first run"
        snapshot = json.loads(snapshot_path.read_text())
        assert "dec-001" in snapshot
        assert "state" in snapshot["dec-001"]

    @pytest.mark.asyncio
    async def test_first_run_returns_no_transitions(self, tmp_path):
        from app.services.staleness_evaluator import run_staleness_evaluation

        signals = [
            _make_signal(
                signal_id="dec-001",
                source_timestamp="2026-03-01T12:00:00Z",
            )
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        result = await run_staleness_evaluation(
            store=store,
            git_ops_override=mock_git,
            now=NOW_BASE,
            commit=True,
        )

        assert result["first_run"] is True
        assert result["transitions"] == []

    @pytest.mark.asyncio
    async def test_first_run_commits_once_as_seed(self, tmp_path):
        from app.services.staleness_evaluator import run_staleness_evaluation

        signals = [
            _make_signal(
                signal_id="dec-001",
                source_timestamp="2026-03-01T12:00:00Z",
            )
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        result = await run_staleness_evaluation(
            store=store,
            git_ops_override=mock_git,
            now=NOW_BASE,
            commit=True,
        )

        mock_git.commit_and_push.assert_awaited_once()
        assert result["committed"] is True

    @pytest.mark.asyncio
    async def test_first_run_evaluated_count(self, tmp_path):
        from app.services.staleness_evaluator import run_staleness_evaluation

        signals = [
            _make_signal(
                signal_id=f"dec-{i:03d}",
                source_timestamp="2026-03-01T12:00:00Z",
            )
            for i in range(3)
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        result = await run_staleness_evaluation(
            store=store,
            git_ops_override=mock_git,
            now=NOW_BASE,
            commit=True,
        )

        assert result["evaluated"] == 3


class TestRunStalenessEvaluationSecondRunNoChange:
    """Second run with no state change: no transitions, NO commit."""

    @pytest.mark.asyncio
    async def test_second_run_no_change_no_commit(self, tmp_path):
        from app.services.staleness_evaluator import run_staleness_evaluation

        signals = [
            _make_signal(
                signal_id="dec-stable",
                source_timestamp="2026-03-01T12:00:00Z",
                review_status="confirmed",
            )
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        # First run seeds the snapshot
        await run_staleness_evaluation(
            store=store,
            git_ops_override=mock_git,
            now=NOW_BASE,
            commit=True,
        )
        mock_git.commit_and_push.reset_mock()

        # Second run: same state
        result = await run_staleness_evaluation(
            store=store,
            git_ops_override=mock_git,
            now=NOW_BASE,
            commit=True,
        )

        mock_git.commit_and_push.assert_not_awaited()
        assert result["transitions"] == []
        assert result["committed"] is False
        assert result["first_run"] is False

    @pytest.mark.asyncio
    async def test_second_run_no_change_snapshot_still_exists(self, tmp_path):
        from app.services.staleness_evaluator import (
            SNAPSHOT_RELATIVE_PATH,
            run_staleness_evaluation,
        )

        signals = [
            _make_signal(
                signal_id="dec-stable",
                source_timestamp="2026-03-01T12:00:00Z",
            )
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )
        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )

        snapshot_path = tmp_path / SNAPSHOT_RELATIVE_PATH
        assert snapshot_path.exists()


class TestRunStalenessEvaluationTransitions:
    """Second run with a state change: transition recorded, JSONL updated, commit called."""

    @pytest.mark.asyncio
    async def test_transition_detected_and_returned(self, tmp_path):
        from app.services.staleness_evaluator import run_staleness_evaluation

        # Decision that becomes stale between run1 and run2
        # At NOW_BASE it's only 1 day old (candidate), at NOW_STALE it's 91 days old (stale)
        ts_fresh = (NOW_BASE - timedelta(days=1)).isoformat()
        signals = [
            _make_signal(
                signal_id="dec-aging",
                source_timestamp=ts_fresh,
            )
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        # Run 1: candidate state
        result1 = await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )
        assert result1["first_run"] is True
        mock_git.commit_and_push.reset_mock()

        # Run 2: now 91+ days old → stale
        result2 = await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_STALE, commit=True
        )

        assert len(result2["transitions"]) == 1
        t = result2["transitions"][0]
        assert t["signal_id"] == "dec-aging"
        assert t["from"] == "candidate"
        assert t["to"] == "stale"
        assert "reason" in t
        assert "at" in t

    @pytest.mark.asyncio
    async def test_transition_commit_called_with_both_files(self, tmp_path):
        from app.services.staleness_evaluator import (
            SNAPSHOT_RELATIVE_PATH,
            TRANSITIONS_RELATIVE_PATH,
            run_staleness_evaluation,
        )

        ts_fresh = (NOW_BASE - timedelta(days=1)).isoformat()
        signals = [
            _make_signal(signal_id="dec-aging", source_timestamp=ts_fresh)
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )
        mock_git.commit_and_push.reset_mock()

        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_STALE, commit=True
        )

        mock_git.commit_and_push.assert_awaited_once()
        call_args = mock_git.commit_and_push.call_args
        files_arg = call_args[0][0]  # first positional arg: list of paths
        assert SNAPSHOT_RELATIVE_PATH in files_arg
        assert TRANSITIONS_RELATIVE_PATH in files_arg

    @pytest.mark.asyncio
    async def test_transition_written_to_jsonl(self, tmp_path):
        from app.services.staleness_evaluator import (
            TRANSITIONS_RELATIVE_PATH,
            run_staleness_evaluation,
        )

        ts_fresh = (NOW_BASE - timedelta(days=1)).isoformat()
        signals = [
            _make_signal(signal_id="dec-aging", source_timestamp=ts_fresh)
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )
        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_STALE, commit=True
        )

        jsonl_path = tmp_path / TRANSITIONS_RELATIVE_PATH
        assert jsonl_path.exists(), "JSONL file must exist after a transition"
        lines = [line for line in jsonl_path.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["signal_id"] == "dec-aging"
        assert entry["from"] == "candidate"
        assert entry["to"] == "stale"

    @pytest.mark.asyncio
    async def test_jsonl_appends_across_multiple_runs(self, tmp_path):
        """Three runs with two different transitions → JSONL has exactly two lines."""
        from app.services.staleness_evaluator import (
            TRANSITIONS_RELATIVE_PATH,
            run_staleness_evaluation,
        )

        ts1 = (NOW_BASE - timedelta(days=1)).isoformat()
        ts2 = (NOW_BASE - timedelta(days=2)).isoformat()
        signals = [
            _make_signal(signal_id="dec-a", source_timestamp=ts1),
            _make_signal(signal_id="dec-b", source_timestamp=ts2),
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        # Run 1: seed (both candidate)
        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )
        # Run 2: both go stale (91+ days)
        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_STALE, commit=True
        )

        jsonl_path = tmp_path / TRANSITIONS_RELATIVE_PATH
        lines = [line for line in jsonl_path.read_text().splitlines() if line.strip()]
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_snapshot_updated_after_transition(self, tmp_path):
        """After a transition, snapshot reflects the new state."""
        from app.services.staleness_evaluator import (
            SNAPSHOT_RELATIVE_PATH,
            run_staleness_evaluation,
        )

        ts_fresh = (NOW_BASE - timedelta(days=1)).isoformat()
        signals = [
            _make_signal(signal_id="dec-aging", source_timestamp=ts_fresh)
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )
        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_STALE, commit=True
        )

        snapshot = json.loads(
            (tmp_path / SNAPSHOT_RELATIVE_PATH).read_text()
        )
        assert snapshot["dec-aging"]["state"] == "stale"

    @pytest.mark.asyncio
    async def test_transition_at_is_iso_timestamp(self, tmp_path):
        from app.services.staleness_evaluator import run_staleness_evaluation

        ts_fresh = (NOW_BASE - timedelta(days=1)).isoformat()
        signals = [
            _make_signal(signal_id="dec-aging", source_timestamp=ts_fresh)
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )
        result = await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_STALE, commit=True
        )

        at_str = result["transitions"][0]["at"]
        # Must parse as ISO
        parsed = datetime.fromisoformat(at_str)
        assert parsed.year == NOW_STALE.year


class TestRunStalenessEvaluationNewDecision:
    """New decision appearing between runs: NOT a transition, but in new snapshot."""

    @pytest.mark.asyncio
    async def test_new_decision_not_a_transition(self, tmp_path):
        from app.models.signal import MeetingSignals
        from app.services.signal_store import SignalStore
        from app.services.staleness_evaluator import run_staleness_evaluation

        # Run 1: one decision
        store = SignalStore(signals_dir=tmp_path / "signals")
        sig1 = _make_signal(
            signal_id="dec-old",
            source_timestamp="2026-03-01T12:00:00Z",
        )
        store.save(
            MeetingSignals(meeting_id="m1", bot_id="bot-1", signals=[sig1])
        )
        mock_git = _make_git_ops(tmp_path)

        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )
        mock_git.commit_and_push.reset_mock()

        # Add a second decision between runs
        sig2 = _make_signal(
            signal_id="dec-new",
            source_timestamp="2026-03-01T12:00:00Z",
        )
        store.save(
            MeetingSignals(meeting_id="m2", bot_id="bot-2", signals=[sig2])
        )

        result = await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )

        # dec-new is NEW — must not appear in transitions
        transition_ids = [t["signal_id"] for t in result["transitions"]]
        assert "dec-new" not in transition_ids

    @pytest.mark.asyncio
    async def test_new_decision_appears_in_snapshot(self, tmp_path):
        from app.models.signal import MeetingSignals
        from app.services.signal_store import SignalStore
        from app.services.staleness_evaluator import (
            SNAPSHOT_RELATIVE_PATH,
            run_staleness_evaluation,
        )

        store = SignalStore(signals_dir=tmp_path / "signals")
        sig1 = _make_signal(
            signal_id="dec-old",
            source_timestamp="2026-03-01T12:00:00Z",
        )
        store.save(MeetingSignals(meeting_id="m1", bot_id="bot-1", signals=[sig1]))
        mock_git = _make_git_ops(tmp_path)

        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )

        sig2 = _make_signal(
            signal_id="dec-new",
            source_timestamp="2026-03-01T12:00:00Z",
        )
        store.save(MeetingSignals(meeting_id="m2", bot_id="bot-2", signals=[sig2]))

        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )

        snapshot = json.loads(
            (tmp_path / SNAPSHOT_RELATIVE_PATH).read_text()
        )
        assert "dec-new" in snapshot


class TestRunStalenessEvaluationGitFailure:
    """Git failure: committed=False, files still written, transitions still returned."""

    @pytest.mark.asyncio
    async def test_git_failure_committed_false(self, tmp_path):
        from app.services.staleness_evaluator import (
            SNAPSHOT_RELATIVE_PATH,
            run_staleness_evaluation,
        )

        signals = [
            _make_signal(
                signal_id="dec-001",
                source_timestamp="2026-03-01T12:00:00Z",
            )
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)
        mock_git.commit_and_push = AsyncMock(side_effect=RuntimeError("git boom"))

        result = await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )

        assert result["committed"] is False
        # File must still be written
        assert (tmp_path / SNAPSHOT_RELATIVE_PATH).exists()

    @pytest.mark.asyncio
    async def test_git_failure_transitions_still_returned(self, tmp_path):
        from app.services.staleness_evaluator import (
            TRANSITIONS_RELATIVE_PATH,
            run_staleness_evaluation,
        )

        ts_fresh = (NOW_BASE - timedelta(days=1)).isoformat()
        signals = [
            _make_signal(signal_id="dec-aging", source_timestamp=ts_fresh)
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        # Seed run succeeds, transition run has git failure
        await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=True
        )

        mock_git.commit_and_push = AsyncMock(side_effect=RuntimeError("git boom"))
        result = await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_STALE, commit=True
        )

        # Transitions still returned even though git failed
        assert len(result["transitions"]) == 1
        assert result["committed"] is False
        # JSONL still written
        jsonl_path = tmp_path / TRANSITIONS_RELATIVE_PATH
        assert jsonl_path.exists()


class TestRunStalenessEvaluationCommitFalse:
    """commit=False: no git call, files still written."""

    @pytest.mark.asyncio
    async def test_commit_false_skips_git(self, tmp_path):
        from app.services.staleness_evaluator import run_staleness_evaluation

        signals = [
            _make_signal(
                signal_id="dec-001",
                source_timestamp="2026-03-01T12:00:00Z",
            )
        ]
        store = _build_store(tmp_path, signals)
        mock_git = _make_git_ops(tmp_path)

        result = await run_staleness_evaluation(
            store=store, git_ops_override=mock_git, now=NOW_BASE, commit=False
        )

        mock_git.commit_and_push.assert_not_awaited()
        assert result["committed"] is False


# ---------------------------------------------------------------------------
# 3. HTTP endpoint tests
# ---------------------------------------------------------------------------


class TestStalenessEvaluateEndpoint:
    """POST /api/decisions/staleness/evaluate returns 200 with expected shape."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        self.tmp_path = tmp_path
        from app.routes.decisions import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_post_returns_200(self, tmp_path):
        import app.routes.decisions as decisions_mod

        mock_result = {
            "evaluated": 2,
            "transitions": [],
            "committed": True,
            "first_run": True,
        }

        async def _fake_eval(**kwargs):
            return mock_result

        with patch.object(decisions_mod, "run_staleness_evaluation", _fake_eval):
            resp = self.client.post("/api/decisions/staleness/evaluate")

        assert resp.status_code == 200

    def test_response_has_required_shape(self, tmp_path):
        import app.routes.decisions as decisions_mod

        mock_result = {
            "evaluated": 3,
            "transitions": [
                {
                    "signal_id": "dec-001",
                    "from": "candidate",
                    "to": "stale",
                    "reason": "age 91d > 90d threshold",
                    "at": "2026-06-01T12:00:00+00:00",
                }
            ],
            "committed": True,
            "first_run": False,
        }

        async def _fake_eval(**kwargs):
            return mock_result

        with patch.object(decisions_mod, "run_staleness_evaluation", _fake_eval):
            resp = self.client.post("/api/decisions/staleness/evaluate")

        body = resp.json()
        assert "evaluated" in body
        assert "transitions" in body
        assert "committed" in body
        assert "first_run" in body
        assert isinstance(body["transitions"], list)
        assert body["evaluated"] == 3
        assert len(body["transitions"]) == 1

    def test_transition_entry_shape(self, tmp_path):
        import app.routes.decisions as decisions_mod

        mock_result = {
            "evaluated": 1,
            "transitions": [
                {
                    "signal_id": "dec-xyz",
                    "from": "active",
                    "to": "zombie",
                    "reason": "revisit_date 2026-01-01 passed without action",
                    "at": "2026-06-01T12:00:00+00:00",
                }
            ],
            "committed": False,
            "first_run": False,
        }

        async def _fake_eval(**kwargs):
            return mock_result

        with patch.object(decisions_mod, "run_staleness_evaluation", _fake_eval):
            resp = self.client.post("/api/decisions/staleness/evaluate")

        t = resp.json()["transitions"][0]
        assert t["signal_id"] == "dec-xyz"
        assert t["from"] == "active"
        assert t["to"] == "zombie"
        assert "reason" in t
        assert "at" in t


class TestRouteOrderGuard:
    """staleness/evaluate must be declared before /{decision_id}."""

    def test_staleness_evaluate_before_decision_id(self):
        from app.routes.decisions import router

        route_paths = [r.path for r in router.routes]
        staleness_path = "/api/decisions/staleness/evaluate"
        detail_path = "/api/decisions/{decision_id}"

        assert staleness_path in route_paths, (
            f"Missing {staleness_path}. Available: {route_paths}"
        )
        assert detail_path in route_paths
        assert route_paths.index(staleness_path) < route_paths.index(detail_path), (
            f"staleness/evaluate must be declared before {{decision_id}}. "
            f"Paths in order: {route_paths}"
        )
