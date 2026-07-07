#!/usr/bin/env python3
"""Backfill SUPERSEDES edges for signals that already have a superseded_by field.

Scans every signal in SignalStore, finds pairs where signal.superseded_by is set,
and writes (new_signal)-[:SUPERSEDES]->(old_signal) edges in Neo4j via
SignalGraphWriter.write_supersedes_edge.

MERGE semantics in the writer make this idempotent — running multiple times
produces the same result.  Every pair that arrives with both nodes present in
Neo4j is safe to backfill repeatedly.

Usage (inside the running container)
-------------------------------------
    python scripts/backfill_supersedes_edges.py
    python scripts/backfill_supersedes_edges.py --dry-run
    python scripts/backfill_supersedes_edges.py --signals-dir /path/to/signals

Exit codes
----------
    0 — completed (even if some edges were skipped due to missing nodes)
    1 — Neo4j entirely unreachable; no writes attempted
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# ---------------------------------------------------------------------------
# sys.path bootstrap — run directly *or* via pytest importlib
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if TYPE_CHECKING:
    from app.services.graph.signal_graph_writer import SignalGraphWriter
    from app.services.signal_store import SignalStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core functions (importable for testing)
# ---------------------------------------------------------------------------


def collect_pairs(
    store: "SignalStore",
) -> list[tuple[str, str, str]]:
    """Collect all (new_id, old_id, superseded_at) triples from the store.

    A pair is emitted for every signal that has ``superseded_by`` set.
    The signal with ``superseded_by`` set is the *old* signal; the value of
    ``superseded_by`` points to the *new* signal.

    superseded_at fallback order:
      1. old_signal.valid_to  (set on supersession — most accurate)
      2. old_signal.created_at  (creation time — rough proxy)
      3. ISO now  (last-resort only when both above are absent)

    Returns:
        List of (new_signal_id, old_signal_id, superseded_at) tuples.
    """
    from datetime import UTC, datetime

    all_meetings = store.load_all()

    # Build id → Signal lookup for superseded_at fallback
    all_signals_by_id: dict[str, object] = {}
    for ms in all_meetings:
        for sig in ms.signals:
            all_signals_by_id[sig.id] = sig

    pairs: list[tuple[str, str, str, str | None]] = []

    for ms in all_meetings:
        for sig in ms.signals:
            if not sig.superseded_by:
                continue
            new_id = sig.superseded_by
            old_id = sig.id

            # Fallback order for superseded_at (prefer the boundary when the
            # successor became valid, not just when it was created):
            #   1. old.valid_to           — explicit validity close
            #   2. successor.valid_from   — when the successor became effective
            #   3. successor.source_timestamp — origin time of the successor
            #   4. old.created_at         — rough proxy
            #   5. now                    — last resort
            successor = all_signals_by_id.get(new_id)
            superseded_at: str = (
                sig.valid_to
                or (getattr(successor, "valid_from", None) if successor else None)
                or (getattr(successor, "source_timestamp", None) if successor else None)
                or sig.created_at
                or datetime.now(UTC).isoformat()
            )

            # tenant_id: prefer the new (successor) signal's tenant_id, fall back
            # to the old signal's.
            tenant_id: str | None = (
                getattr(successor, "tenant_id", None) if successor else None
            ) or getattr(sig, "tenant_id", None)

            pairs.append((new_id, old_id, superseded_at, tenant_id))

    return pairs


async def backfill(
    pairs: list[tuple],
    writer: "SignalGraphWriter",
    *,
    dry_run: bool = False,
) -> dict:
    """Write SUPERSEDES edges for the given pairs.

    Args:
        pairs: List of (new_id, old_id, superseded_at[, tenant_id]) tuples from
               collect_pairs.  The optional 4th element (tenant_id) is forwarded
               to write_supersedes_edge when present.
        writer: SignalGraphWriter instance (must have Neo4j connection).
        dry_run: If True, log what would be written but make no calls to writer.

    Returns:
        Dict with counts:
            created: edges successfully written (or would be in dry_run)
            skipped_missing: pairs where writer returned False (node absent in Neo4j)
            dry_run: pairs that were logged-only due to dry_run=True

    The MERGE in write_supersedes_edge makes repeated runs idempotent — calling
    backfill twice with the same pairs will still return the same final graph state.
    """
    counts: dict[str, int] = {"created": 0, "skipped_missing": 0, "dry_run": 0}

    for pair in pairs:
        new_id, old_id, superseded_at = pair[0], pair[1], pair[2]
        tenant_id: str | None = pair[3] if len(pair) > 3 else None
        if dry_run:
            logger.info(
                "[BACKFILL] DRY-RUN: would write SUPERSEDES %s → %s at %s",
                new_id,
                old_id,
                superseded_at,
            )
            counts["dry_run"] += 1
            continue

        ok = await writer.write_supersedes_edge(
            new_id,
            old_id,
            superseded_at=superseded_at,
            tenant_id=tenant_id,
        )
        if ok:
            counts["created"] += 1
        else:
            logger.warning(
                "[BACKFILL] Skipped pair — one or both nodes missing in Neo4j: "
                "new=%s old=%s",
                new_id,
                old_id,
            )
            counts["skipped_missing"] += 1

    return counts


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Parse argv, discover pairs, backfill edges, print summary, exit."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Backfill SUPERSEDES edges from superseded_by fields in SignalStore"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print pairs that would be written without calling Neo4j",
    )
    parser.add_argument(
        "--signals-dir",
        metavar="PATH",
        default=None,
        help="Override the default signals directory",
    )
    args = parser.parse_args(argv)

    from app.services.signal_store import SignalStore

    signals_dir_arg = Path(args.signals_dir) if args.signals_dir else None
    if signals_dir_arg is not None:
        store = SignalStore(signals_dir=signals_dir_arg)
    else:
        store = SignalStore()

    pairs = collect_pairs(store)

    if not pairs:
        print("No superseded_by pairs found — nothing to backfill.")
        sys.exit(0)

    print(f"Found {len(pairs)} superseded_by pair(s).")

    if args.dry_run:
        for new_id, old_id, superseded_at in pairs:
            print(f"  DRY-RUN: {new_id} SUPERSEDES {old_id} at {superseded_at}")
        print("Dry run complete — no writes performed.")
        sys.exit(0)

    # --- Neo4j write path ---
    try:
        from app.neo4j_client import get_neo4j_client
        from app.services.graph.signal_graph_writer import SignalGraphWriter
    except ImportError as e:
        print(f"ERROR: Could not import Neo4j dependencies: {e}", file=sys.stderr)
        sys.exit(1)

    async def _run() -> dict:
        client = get_neo4j_client()
        if not client.is_initialized:
            try:
                await client.initialize()
            except Exception as exc:
                print(
                    f"ERROR: Neo4j entirely unreachable — {exc}", file=sys.stderr
                )
                sys.exit(1)

        writer = SignalGraphWriter(client)
        try:
            return await backfill(pairs, writer, dry_run=False)
        finally:
            pass  # client lifecycle managed by app startup; don't close here

    try:
        counts = asyncio.run(_run())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"ERROR: Neo4j entirely unreachable — {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Backfill complete: "
        f"{counts['created']} created, "
        f"{counts['skipped_missing']} skipped (missing nodes), "
        f"{counts['dry_run']} dry-run."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
