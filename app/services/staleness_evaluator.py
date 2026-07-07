"""Staleness/zombie evaluation job with transition log (Sprint 3, R2.3).

Computes decision lifecycle states for all signals, diffs against a persisted
snapshot, records state transitions to a JSONL log, and commits both artifacts
via git_ops in one atomic commit.

Public API:
    SNAPSHOT_RELATIVE_PATH    — repo-relative path of the state snapshot JSON
    TRANSITIONS_RELATIVE_PATH — repo-relative path of the transitions JSONL
    evaluate_states(...)      — pure-ish read; returns {signal_id: {state, state_reason}}
    run_staleness_evaluation(...)  — async snapshot-diff runner; writes files + commits
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.git_ops import git_ops
from app.services.decision_states import compute_decision_state
from app.services.decision_view import load_decision_signals

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.signal_store import SignalStore

logger = logging.getLogger(__name__)

# Repo-relative output paths (under the constitution/ directory so they travel
# with the constitution artifact in the same commit namespace).
SNAPSHOT_RELATIVE_PATH = "constitution/state-snapshot.json"
TRANSITIONS_RELATIVE_PATH = "constitution/state-transitions.jsonl"


# ---------------------------------------------------------------------------
# Public: evaluate_states (pure-ish read)
# ---------------------------------------------------------------------------


def evaluate_states(
    store: SignalStore | None = None,
    now: datetime | None = None,
) -> dict[str, dict]:
    """Return the current lifecycle state for every decision-type signal.

    Args:
        store: SignalStore instance. Defaults to the module-level tenant-scoped
               proxy when None.
        now: Reference time for state computation. Defaults to UTC now.

    Returns:
        Mapping from signal_id → {"state": str, "state_reason": str} for every
        signal whose type == "decision". Non-decision signals are excluded.
    """
    if now is None:
        now = datetime.now(UTC)

    signals = load_decision_signals(store=store)

    result: dict[str, dict] = {}
    for sig in signals:
        state, state_reason = compute_decision_state(sig, now=now)
        result[sig.id] = {"state": state, "state_reason": state_reason}

    return result


# ---------------------------------------------------------------------------
# Private I/O helpers (mirror constitution.py's atomic-write pattern)
# ---------------------------------------------------------------------------


def _write_atomic(final_path: str, content: str) -> None:
    """Write *content* to *final_path* atomically via tempfile + os.replace."""
    dir_path = os.path.dirname(final_path)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, final_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _append_jsonl_lines(path: str, lines: list[dict]) -> None:
    """Append *lines* (one JSON object per line) to *path*, creating if absent."""
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for obj in lines:
            fh.write(json.dumps(obj) + "\n")


def _load_snapshot(path: str) -> dict | None:
    """Load the existing state snapshot JSON, returning None if absent or unreadable."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        logger.warning(
            "[STALENESS] Failed to load snapshot at %s; treating as first run",
            path,
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Public: run_staleness_evaluation
# ---------------------------------------------------------------------------


async def run_staleness_evaluation(
    *,
    store: SignalStore | None = None,
    git_ops_override=None,
    now: datetime | None = None,
    commit: bool = True,
) -> dict:
    """Snapshot-diff runner: evaluate states, detect transitions, commit artifacts.

    Algorithm:
      1. current = evaluate_states(store, now)
      2. previous = load snapshot from {repo}/constitution/state-snapshot.json
         (None → first run)
      3. Build transitions: for each signal_id present in BOTH previous and current,
         if state changed → record {signal_id, from, to, reason, at: now.isoformat()}.
         - IDs new since previous snapshot (absent from previous) → NOT transitions
           (they are new decisions being seeded for the first time)
         - IDs no longer in store (absent from current) → skipped
      4. Write snapshot atomically; append transition lines to JSONL
      5. Commit BOTH files in one commit_and_push([...], "[bot] Decision state
         evaluation: N transitions") when transitions occurred OR on first seed.
         Git failure → logged, committed=False (files are always written first).
      6. Return {"evaluated", "transitions", "committed", "first_run"}.

    Args:
        store: SignalStore instance. Defaults to module-level tenant proxy.
        git_ops_override: Override git_ops (useful for tests; uses module-level
                          ``git_ops`` proxy when None).
        now: Reference time. Defaults to UTC now.
        commit: If False, skip the git commit entirely (committed=False in result).

    Returns:
        dict with keys:
            evaluated: int — number of decision signals evaluated
            transitions: list of {signal_id, from, to, reason, at}
            committed: bool — True if git commit succeeded
            first_run: bool — True if no prior snapshot existed
    """
    if now is None:
        now = datetime.now(UTC)

    _git = git_ops_override if git_ops_override is not None else git_ops

    # --- 1. Evaluate current states ---
    current = evaluate_states(store=store, now=now)

    # --- 2. Load previous snapshot ---
    snapshot_full_path = os.path.join(_git.repo_path, SNAPSHOT_RELATIVE_PATH)
    previous = await asyncio.to_thread(_load_snapshot, snapshot_full_path)
    first_run = previous is None

    # --- 3. Compute transitions ---
    transitions: list[dict] = []
    at_str = now.isoformat()

    if previous is not None:
        for signal_id, cur_entry in current.items():
            if signal_id not in previous:
                # New decision since last snapshot → not a transition
                continue
            prev_state = previous[signal_id].get("state")
            cur_state = cur_entry["state"]
            if prev_state != cur_state:
                transitions.append(
                    {
                        "signal_id": signal_id,
                        "from": prev_state,
                        "to": cur_state,
                        "reason": cur_entry.get("state_reason", ""),
                        "at": at_str,
                    }
                )

    # --- 4. Write files ---
    # Only rewrite the snapshot when its content actually changed (state
    # transitions, but also new/removed decision ids): an unconditional
    # rewrite leaves the file perpetually dirty in the repo working tree on
    # no-change runs, while skipping new-id-only changes would mean a new
    # decision never gets seeded and its future transitions are missed.
    snapshot_changed = previous != current
    snapshot_content = json.dumps(current, indent=2, ensure_ascii=False)
    transitions_full_path = os.path.join(_git.repo_path, TRANSITIONS_RELATIVE_PATH)

    if snapshot_changed:
        await asyncio.to_thread(_write_atomic, snapshot_full_path, snapshot_content)
        logger.info(
            "[STALENESS] Snapshot written to %s (%d decisions)",
            snapshot_full_path,
            len(current),
        )

    if transitions:
        await asyncio.to_thread(_append_jsonl_lines, transitions_full_path, transitions)
        logger.info(
            "[STALENESS] %d transition(s) appended to %s",
            len(transitions),
            transitions_full_path,
        )

    # --- 5. Commit ---
    # Commit whenever the snapshot changed (incl. new/removed ids) so the
    # working tree never accumulates uncommitted artifact state.
    committed = False
    should_commit = commit and snapshot_changed

    if should_commit:
        files_to_commit = [SNAPSHOT_RELATIVE_PATH]
        if transitions:
            files_to_commit.append(TRANSITIONS_RELATIVE_PATH)

        n = len(transitions)
        msg = f"[bot] Decision state evaluation: {n} transition{'s' if n != 1 else ''}"
        if first_run:
            msg = "[bot] Decision state evaluation: initial seed"

        try:
            await _git.commit_and_push(files_to_commit, msg)
            committed = True
        except Exception:
            logger.warning(
                "[STALENESS] git commit failed; snapshot written but not committed",
                exc_info=True,
            )

    # --- 6. Return ---
    return {
        "evaluated": len(current),
        "transitions": transitions,
        "committed": committed,
        "first_run": first_run,
    }
