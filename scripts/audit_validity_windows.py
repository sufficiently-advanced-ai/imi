#!/usr/bin/env python3
"""Audit validity windows (valid_from / valid_to) on all persisted signals (R1.1).

Scans every signal file in SignalStore and reports gap categories:

  (a) missing_valid_from          — valid_from is None after load
      (fires when both valid_from and source_timestamp are absent/empty so the
      model validator cannot default valid_from — belt-and-braces guard)

  (b) superseded_without_valid_to — provenance_status == "superseded" AND
                                    valid_to is None

  (c) superseded_without_successor— provenance_status == "superseded" AND
                                    superseded_by is None
      (report-only; human judgment required)

  (d) dangling_successor          — superseded_by references an id that does
                                    not exist in the store
      (report-only; human judgment required)

Usage
-----
    # Inside the running container:
    python scripts/audit_validity_windows.py

    # With auto-repair of fixable gaps:
    python scripts/audit_validity_windows.py --fix

    # Point at a non-default signals directory:
    python scripts/audit_validity_windows.py --signals-dir /path/to/signals

    # Also check Neo4j for missing SUPERSEDES edges (Task 15 context):
    python scripts/audit_validity_windows.py --check-graph

Exit codes
----------
    0 — no gaps remain (either none found, or all fixable gaps were repaired)
    1 — one or more gaps remain after any requested fixes
"""

import argparse
import logging
import sys
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

# ---------------------------------------------------------------------------
# sys.path bootstrap — run directly *or* via pytest import
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if TYPE_CHECKING:
    from app.services.signal_store import SignalStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class GapKind(str, Enum):
    missing_valid_from = "missing_valid_from"
    superseded_without_valid_to = "superseded_without_valid_to"
    superseded_without_successor = "superseded_without_successor"
    dangling_successor = "dangling_successor"


# Gaps that can be automatically repaired by --fix
_FIXABLE_KINDS = frozenset(
    {GapKind.missing_valid_from, GapKind.superseded_without_valid_to}
)


@dataclass
class Gap:
    """A single validity-window anomaly found in the signal store."""

    kind: GapKind
    signal_id: str
    meeting_id: str


# ---------------------------------------------------------------------------
# Core functions (importable for testing)
# ---------------------------------------------------------------------------


def audit(store: "SignalStore") -> list[Gap]:
    """Scan all signals in *store* and return a list of Gap records.

    Does not mutate any signal — safe to call repeatedly.
    """
    all_meetings = store.load_all()

    # Build a fast lookup set of all known signal ids (for dangling check)
    all_signal_ids: set[str] = set()
    for ms in all_meetings:
        for sig in ms.signals:
            all_signal_ids.add(sig.id)

    gaps: list[Gap] = []

    for ms in all_meetings:
        meeting_id = ms.meeting_id
        for sig in ms.signals:
            # (a) missing_valid_from
            if sig.valid_from is None:
                gaps.append(
                    Gap(
                        kind=GapKind.missing_valid_from,
                        signal_id=sig.id,
                        meeting_id=meeting_id,
                    )
                )

            # Only signals marked superseded need the following checks
            if sig.provenance_status == "superseded":
                # (b) superseded_without_valid_to
                if sig.valid_to is None:
                    gaps.append(
                        Gap(
                            kind=GapKind.superseded_without_valid_to,
                            signal_id=sig.id,
                            meeting_id=meeting_id,
                        )
                    )

                # (c) superseded_without_successor
                if sig.superseded_by is None:
                    gaps.append(
                        Gap(
                            kind=GapKind.superseded_without_successor,
                            signal_id=sig.id,
                            meeting_id=meeting_id,
                        )
                    )

                # (d) dangling_successor
                elif sig.superseded_by not in all_signal_ids:
                    gaps.append(
                        Gap(
                            kind=GapKind.dangling_successor,
                            signal_id=sig.id,
                            meeting_id=meeting_id,
                        )
                    )

    return gaps


def fix(store: "SignalStore", gaps: list[Gap]) -> int:
    """Repair fixable gaps in-place via SignalStore.replace_signal.

    Auto-repairs:
      (a) missing_valid_from  — sets valid_from = created_at (fallback: now)
      (b) superseded_without_valid_to — sets valid_to = successor's created_at
                                        if successor exists, else now-ISO

    Does NOT modify (c) or (d) gaps — those need human judgment.

    Returns the count of signals actually written.
    """
    fixable = [g for g in gaps if g.kind in _FIXABLE_KINDS]
    if not fixable:
        return 0

    # Collect all gap signal_ids so we can batch by meeting
    gap_by_signal: dict[str, list[Gap]] = {}
    for g in fixable:
        gap_by_signal.setdefault(g.signal_id, []).append(g)

    # Build a signal-id → (signal, container) lookup from store
    all_meetings = store.load_all()

    # Also build id → signal for successor lookups
    all_signals_by_id = {}
    for ms in all_meetings:
        for sig in ms.signals:
            all_signals_by_id[sig.id] = sig

    # Group gapped signals by container (bot_id) to batch saves
    container_updates: dict[str, object] = {}  # bot_id → MeetingSignals
    updated_signals: dict[str, object] = {}  # signal_id → updated Signal

    for ms in all_meetings:
        for sig in ms.signals:
            if sig.id not in gap_by_signal:
                continue

            kinds_for_sig = {g.kind for g in gap_by_signal[sig.id]}
            updates: dict = {}
            now_iso = datetime.now(UTC).isoformat()

            # (a) set valid_from
            if GapKind.missing_valid_from in kinds_for_sig:
                # Prefer created_at; fall back to now
                fallback = sig.created_at or now_iso
                updates["valid_from"] = fallback
                logger.info(
                    "[AUDIT] Fixing (a) missing_valid_from for %s → %s",
                    sig.id,
                    fallback,
                )

            # (b) set valid_to
            # Prefer the boundary when the successor *became* valid, falling back
            # to when it was created, then to now.  Using valid_from or
            # source_timestamp gives a tighter boundary than created_at.
            if GapKind.superseded_without_valid_to in kinds_for_sig:
                successor_id = sig.superseded_by
                if successor_id and successor_id in all_signals_by_id:
                    succ = all_signals_by_id[successor_id]
                    valid_to = (
                        succ.valid_from
                        or succ.source_timestamp
                        or succ.created_at
                        or now_iso
                    )
                else:
                    valid_to = now_iso
                updates["valid_to"] = valid_to
                logger.info(
                    "[AUDIT] Fixing (b) superseded_without_valid_to for %s → %s",
                    sig.id,
                    valid_to,
                )

            if updates:
                new_sig = sig.model_copy(update=updates)
                updated_signals[sig.id] = new_sig
                container_updates[ms.bot_id] = ms  # track container for save

    # Apply updates to containers and persist.
    # Strategy: mutate all changed signals in the container object first,
    # then call replace_signal once per container (replace_signal calls
    # store.save which persists the whole file).
    written = 0
    for ms in all_meetings:
        if ms.bot_id not in container_updates:
            continue

        # Count how many signals in this container need updating
        sigs_to_fix = [sig for sig in ms.signals if sig.id in updated_signals]
        if not sigs_to_fix:
            continue

        # Patch all changed signals in the container's list
        ms.signals = [
            updated_signals.get(sig.id, sig)
            for sig in ms.signals  # type: ignore[arg-type]
        ]

        # One save per container (replace_signal calls store.save internally)
        # Use the first updated signal as the anchor; the container already holds
        # all mutations so the full file is correct after this single call.
        store.replace_signal(ms.signals[0], ms)
        written += len(sigs_to_fix)

    return written


def _check_graph_gaps(gaps: list[Gap]) -> list[str]:
    """Optional: report superseded Signal nodes lacking an incoming SUPERSEDES edge.

    Returns warning strings. Swallows all Neo4j errors — unavailability must not
    affect the script's exit code.
    """
    warnings: list[str] = []
    try:
        from neo4j import GraphDatabase  # type: ignore[import-untyped]
        from app.config import settings

        uri = getattr(settings, "neo4j_uri", None) or os.environ.get("NEO4J_URI", "")
        user = getattr(settings, "neo4j_username", None) or os.environ.get(
            "NEO4J_USERNAME", "neo4j"
        )
        password = getattr(settings, "neo4j_password", None) or os.environ.get(
            "NEO4J_PASSWORD", ""
        )

        if not uri:
            warnings.append(
                "WARN --check-graph: NEO4J_URI not configured — skipping graph check"
            )
            return warnings

        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            result = session.run(
                """
                MATCH (s:Signal {provenance_status: 'superseded'})
                WHERE NOT ()-[:SUPERSEDES]->(s)
                RETURN s.id AS signal_id
                LIMIT 100
                """
            )
            missing = [record["signal_id"] for record in result]

        driver.close()

        if missing:
            for sid in missing:
                warnings.append(
                    f"GRAPH superseded Signal {sid!r} has no incoming SUPERSEDES edge"
                )
        else:
            warnings.append("GRAPH: all superseded Signal nodes have SUPERSEDES edges")

    except ImportError:
        warnings.append(
            "WARN --check-graph: neo4j driver not available — skipping graph check"
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"WARN --check-graph: Neo4j unreachable ({exc}) — skipping")

    return warnings


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def _report(gaps: list[Gap]) -> None:
    """Print a human-readable report of gaps to stdout."""
    if not gaps:
        return

    from collections import Counter

    counts = Counter(g.kind for g in gaps)
    print("\nGap report:")
    for kind in GapKind:
        count = counts.get(kind, 0)
        if count:
            print(f"  {kind.value}: {count}")

    print("\nPer-signal detail:")
    for g in gaps:
        fixable_tag = " [fixable]" if g.kind in _FIXABLE_KINDS else " [manual]"
        print(
            f"  {g.kind.value}{fixable_tag}  "
            f"signal_id={g.signal_id}  meeting_id={g.meeting_id}"
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Parse argv, run audit (and optional fix), print report, exit with code."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Audit (and optionally fix) validity windows on all signals"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-repair fixable gaps (a, b) via SignalStore.replace_signal",
    )
    parser.add_argument(
        "--signals-dir",
        metavar="PATH",
        default=None,
        help="Override the default signals directory",
    )
    parser.add_argument(
        "--check-graph",
        action="store_true",
        default=False,
        help=(
            "If Neo4j is reachable, also report superseded Signal nodes "
            "lacking an incoming SUPERSEDES edge (Task 15+). "
            "Unavailability is a warning, not an error."
        ),
    )
    args = parser.parse_args(argv)

    from app.services.signal_store import SignalStore

    signals_dir_arg = Path(args.signals_dir) if args.signals_dir else None
    if signals_dir_arg is not None:
        store = SignalStore(signals_dir=signals_dir_arg)
    else:
        store = SignalStore()

    # --- Audit ---
    gaps = audit(store)

    # --- Optional fix ---
    fixed_count = 0
    if args.fix and gaps:
        fixed_count = fix(store, gaps)
        if fixed_count:
            print(f"Fixed {fixed_count} signal(s).")
        # Re-audit after fix so exit code reflects final state
        gaps = audit(store)

    # --- Report ---
    _report(gaps)

    # --- Optional graph check ---
    if args.check_graph:
        graph_warnings = _check_graph_gaps(gaps)
        for w in graph_warnings:
            print(w)

    # --- Summary line ---
    fixable_remaining = [g for g in gaps if g.kind in _FIXABLE_KINDS]
    unfixable_remaining = [g for g in gaps if g.kind not in _FIXABLE_KINDS]

    if not gaps:
        print("OK: no gaps found")
        sys.exit(0)
    else:
        total = len(gaps)
        fixable_n = len(fixable_remaining)
        unfixable_n = len(unfixable_remaining)
        if args.fix:
            print(
                f"FOUND {total} gap(s) remaining "
                f"({fixable_n} fixable, {unfixable_n} manual)"
            )
        else:
            all_fixable_n = sum(1 for g in gaps if g.kind in _FIXABLE_KINDS)
            print(
                f"FOUND {total} gap(s) "
                f"({all_fixable_n} fixable with --fix, "
                f"{total - all_fixable_n} manual)"
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
