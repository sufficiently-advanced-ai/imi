#!/usr/bin/env python3
"""Backfill the vector index for all persisted signals (G3 wiring).

Iterates every meeting-*.json file in the SignalStore, calls
signal_indexing.index_meeting_signals for each, and prints a summary.

Idempotency note
----------------
The underlying vector store (FAISS) uses ``store_vectors``, which appends
rather than upserts by signal id.  Running this script multiple times will
create duplicate embeddings for the same signal.  This is acceptable for the
initial backfill because:
  1. Semantic search de-ranks exact duplicates naturally (they share the same
     embedding and score) and the governance filter is applied post-retrieval.
  2. A full index rebuild (delete + re-add) is an alternative if duplicate
     entries become a concern — rebuild by deleting the FAISS index directory
     (/app/data/faiss) and re-running this script.

Usage
-----
    # Inside the running container:
    python scripts/backfill_signal_index.py

    # With verbose output per meeting:
    python scripts/backfill_signal_index.py --verbose
"""

import argparse
import sys
import os

# Ensure the app root is on PYTHONPATH when run directly.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main():
    parser = argparse.ArgumentParser(description="Backfill signal vector index")
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print per-meeting counts",
    )
    parser.add_argument(
        "--tenant",
        default=None,
        help=(
            "Tenant id to backfill (issue #951). Sets the tenant context so the "
            "signal store and vector store resolve per-tenant. Omit for the "
            "single-tenant default."
        ),
    )
    args = parser.parse_args()

    from app.services.signal_indexing import index_meeting_signals, vector_stack_available

    if args.tenant:
        from app.core.middleware.request_context import current_tenant_id

        current_tenant_id.set(args.tenant)

    if not vector_stack_available():
        print("ERROR: Vector stack (SemanticaKnowledge) is not available.", file=sys.stderr)
        print(
            "Ensure the app is initialised (e.g. run inside the container "
            "after startup completes) before running this script.",
            file=sys.stderr,
        )
        sys.exit(1)

    from app.core.tenancy.context import current_tenant

    store = current_tenant().signal_store
    all_meetings = store.load_all()

    if not all_meetings:
        print("No signal files found — nothing to backfill.")
        return

    grand_total = grand_indexed = grand_skipped = 0

    for ms in all_meetings:
        total, indexed, skipped = index_meeting_signals(ms)
        grand_total += total
        grand_indexed += indexed
        grand_skipped += skipped

        if args.verbose:
            print(
                f"  meeting {ms.bot_id}: "
                f"total={total} indexed={indexed} skipped={skipped}"
            )

    print(
        f"\nBackfill complete: {len(all_meetings)} meetings, "
        f"{grand_total} signals total, "
        f"{grand_indexed} indexed, "
        f"{grand_skipped} skipped."
    )


if __name__ == "__main__":
    main()
