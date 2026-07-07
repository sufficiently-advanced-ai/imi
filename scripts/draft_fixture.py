#!/usr/bin/env python3
"""Scaffold an eval fixture skeleton from an observed failure.

The failure-to-regression flywheel (Nate, "Your AI Agent Knows the Answer. It
Recommends the Opposite."): every production extraction failure a human catches
becomes a permanent fixture, so the suite compounds into a real failure library.
The raw material already exists in this product — decisions rejected in the
/decisions Review tab, signals corrected via update_signal/delete_signal.

This script does the mechanical part: emit a schema-correct skeleton with empty
gold and a null replay, into the staging dir ``evals/fixtures/variants/``. It
does NOT label gold — that is the human calibration step (see
evals/fixtures/SCHEMA.md). Fill the gold (especially the ``forbidden_*`` trap
that encodes the failure you observed), calibrate live, then move the file into
``transcripts/`` and rewrite the baseline.

Usage
-----
    # transcript from a file
    python scripts/draft_fixture.py --transcript meeting.txt --id 008_late_decision

    # transcript piped on stdin, with provenance and participants
    pbpaste | python scripts/draft_fixture.py --transcript - --id 008_late_decision \\
        --participants "Chris Fernandes,Dana Okafor" --decision-id dec-1234
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STAGING = Path(_ROOT) / "evals" / "fixtures" / "variants"

_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


def build_skeleton(
    fixture_id: str,
    transcript: str,
    participants: list[str],
    decision_id: str | None,
) -> dict:
    """Return a schema-correct fixture skeleton (gold empty, replay null)."""
    provenance = (
        f" Seeded from observed failure (decision {decision_id})."
        if decision_id
        else ""
    )
    return {
        "id": fixture_id,
        "version": 1,
        "description": (
            "TODO: what this fixture stresses and the trap that encodes the "
            f"observed failure.{provenance}"
        ),
        "meeting": {
            "title_context": "TODO: input context only — NOT the gold title",
            "date": "2026-01-01",
            "participants": participants,
            "transcript": transcript,
        },
        "gold": {
            # Fill the labeled tasks; set a task to null to opt out. Encode the
            # observed failure as a forbidden_* trap.
            "entities": [],
            "forbidden_entities": [],
            "relationships": None,
            "forbidden_relationships": [],
            "signals": [],
            "forbidden_signals": [],
            "summary": None,
        },
        "replay": {
            "entities": None,
            "signals": None,
            "relationships": None,
            "summary": None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scaffold an eval fixture skeleton from an observed failure"
    )
    parser.add_argument(
        "--id", required=True, help="new fixture id (filename stem; [A-Za-z0-9_])"
    )
    parser.add_argument(
        "--transcript",
        required=True,
        help="path to a transcript file, or '-' to read stdin",
    )
    parser.add_argument(
        "--participants", default="", help="comma-separated participant names"
    )
    parser.add_argument(
        "--decision-id", help="provenance: the rejected decision / corrected signal id"
    )
    parser.add_argument(
        "--out",
        default=str(_STAGING),
        help="output dir (default: evals/fixtures/variants/ staging — NOT loaded)",
    )
    args = parser.parse_args(argv)

    if not _ID_RE.match(args.id):
        print(f"ERROR: --id must match [A-Za-z0-9_]: {args.id!r}", file=sys.stderr)
        return 1

    if args.transcript == "-":
        transcript = sys.stdin.read()
    else:
        tpath = Path(args.transcript)
        if not tpath.exists():
            print(f"ERROR: transcript not found: {tpath}", file=sys.stderr)
            return 1
        transcript = tpath.read_text(encoding="utf-8")
    transcript = transcript.strip()
    if not transcript:
        print("ERROR: transcript is empty", file=sys.stderr)
        return 1
    if "**" not in transcript:
        print(
            "WARNING: transcript has no '**Name**:' speaker headers — the entity "
            "prompt depends on that format (see evals/fixtures/SCHEMA.md).",
            file=sys.stderr,
        )

    participants = [p.strip() for p in args.participants.split(",") if p.strip()]

    out_path = Path(args.out) / f"{args.id}.json"
    if out_path.exists():
        print(f"ERROR: fixture already exists: {out_path}", file=sys.stderr)
        return 1

    skeleton = build_skeleton(args.id, transcript, participants, args.decision_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(skeleton, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote skeleton {out_path}")
    print(
        "Next: label gold (encode the failure as a forbidden_* trap), calibrate "
        "live, move into transcripts/, then rewrite the baseline. See "
        "evals/fixtures/SCHEMA.md."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
