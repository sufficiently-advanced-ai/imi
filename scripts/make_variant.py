#!/usr/bin/env python3
"""Generate a factorial *variant* of an eval fixture.

A variant copies the base fixture's gold **verbatim** and perturbs ONLY the
transcript with domain-general contextual noise — minimization, authority
anchoring, social pressure, or time pressure. The premise (from Nate's "Your AI
Agent Knows the Answer. It Recommends the Opposite."): hold the gold constant,
vary the framing. If extraction output shifts under a context-only change, that
is an anchoring bug the aggregate score hides.

This is a SCAFFOLDER, not an oracle. It writes a *draft* to the staging dir
``evals/fixtures/variants/`` (which the harness does NOT load). Before a variant
can guard anything you must:

  1. Review the injected prose for realism (edit the transcript if needed).
  2. Calibrate gold against a live run, reconciling every FP
     (see evals/fixtures/SCHEMA.md).
  3. Move the file into evals/fixtures/transcripts/ and rewrite the baseline
     (``run_evals.py --task all --baseline``) — the gate is a global aggregate,
     so adding a fixture is a deliberate baseline change.

Usage
-----
    python scripts/make_variant.py 004_decisions_vs_opinions \\
        --variation authority \\
        --anchor "move the whole platform to Kubernetes" --position before

    # No --anchor: the snippet is injected after the first line and flagged
    # for manual relocation in the .review.md sidecar.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRANSCRIPTS = Path(_ROOT) / "evals" / "fixtures" / "transcripts"
_STAGING = Path(_ROOT) / "evals" / "fixtures" / "variants"

# Domain-general contextual-noise snippets. {p0}/{p1} are filled with the first
# two participant names. Each is one scripted line in the production
# "**Name**:" speaker format — intentionally generic framing a robust extractor
# should treat as noise.
VARIATIONS = {
    "minimize": "**{p1}**: I mean, it's probably overkill and we may walk it back later, but fine.",
    "authority": "**{p0}**: For what it's worth, leadership feels really strongly we should go this way.",
    "social": "**{p1}**: Honestly everyone keeps saying we should treat that as a really big deal.",
    "time": "**{p0}**: We're basically out of time — let's just rapid-fire the rest and move on.",
}

VARIATION_INTENT = {
    "minimize": "minimization wrapping a real decision — the decision must still be extracted",
    "authority": "authority anchoring around an opinion/non-decision — it must NOT become a decision",
    "social": "social pressure around a passing mention — it must NOT be promoted to an entity/account",
    "time": "time pressure / premature closure — real signals must still be captured",
}


def build_variant(fixture: dict, variation: str, anchor: str | None, position: str) -> tuple[dict, str, str | None]:
    """Return (variant_fixture, injected_line, review_note). Pure — no I/O."""
    parts = fixture.get("meeting", {}).get("participants") or []
    p0 = parts[0] if parts else "Speaker A"
    p1 = parts[1] if len(parts) > 1 else p0
    injection = VARIATIONS[variation].format(p0=p0, p1=p1)

    transcript = fixture["meeting"]["transcript"]
    blocks = transcript.split("\n\n")  # production fixtures separate lines with blank lines
    review_note: str | None = None
    if anchor:
        needle = anchor.lower()
        idx = next((i for i, b in enumerate(blocks) if needle in b.lower()), None)
        if idx is None:
            raise ValueError(f"--anchor substring not found in transcript: {anchor!r}")
        insert_at = idx if position == "before" else idx + 1
    else:
        insert_at = 1  # after the first line
        review_note = (
            "No --anchor was given: the snippet was injected after the first line. "
            f"RELOCATE it adjacent to the content this variation targets "
            f"({VARIATION_INTENT[variation]})."
        )
    blocks.insert(insert_at, injection)

    variant = json.loads(json.dumps(fixture))  # deep copy
    variant["meeting"]["transcript"] = "\n\n".join(blocks)
    variant["id"] = f"{fixture['id']}__{variation}"
    base_desc = (fixture.get("description") or "").strip()
    variant["description"] = (
        f"[variant: {variation}] {VARIATION_INTENT[variation]}. "
        f"Gold copied verbatim from {fixture['id']}. {base_desc}"
    )
    # Gold is untouched (that is the whole point). Replay must be re-recorded.
    if isinstance(variant.get("replay"), dict):
        variant["replay"] = {k: None for k in variant["replay"]}
    return variant, injection, review_note


def _review_md(variant_id: str, variation: str, injection: str, review_note: str | None) -> str:
    lines = [
        f"# Variant review — {variant_id}",
        "",
        f"- Variation: **{variation}** — {VARIATION_INTENT[variation]}",
        f"- Injected line: `{injection}`",
        "",
        "## Before this variant can guard anything",
        "- [ ] Injected prose reads naturally in context (edit the transcript if not).",
    ]
    if review_note:
        lines.append(f"- [ ] {review_note}")
    lines += [
        "- [ ] Gold is unchanged from the base (same required signals, same traps).",
        f"- [ ] Calibrate live and reconcile every FP per evals/fixtures/SCHEMA.md:",
        f"      `python scripts/run_evals.py --task all --fixture {variant_id}`",
        "      (after moving the file into evals/fixtures/transcripts/).",
        "- [ ] A per-fixture score drop vs the base = an anchoring bug, not noise.",
        "- [ ] Move into transcripts/ and rewrite the baseline "
        "(`run_evals.py --task all --baseline`).",
        "- [ ] Delete this .review.md — it is not loaded by the harness.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scaffold a factorial fixture variant")
    parser.add_argument("base_id", help="base fixture id (filename stem in transcripts/)")
    parser.add_argument(
        "--variation", required=True, choices=sorted(VARIATIONS), help="contextual-noise type"
    )
    parser.add_argument(
        "--anchor",
        help="inject relative to the transcript line containing this substring (case-insensitive)",
    )
    parser.add_argument("--position", choices=["before", "after"], default="before")
    parser.add_argument(
        "--out",
        default=str(_STAGING),
        help="output dir (default: evals/fixtures/variants/ staging — NOT loaded by the harness)",
    )
    args = parser.parse_args(argv)

    base_path = _TRANSCRIPTS / f"{args.base_id}.json"
    if not base_path.exists():
        print(f"ERROR: base fixture not found: {base_path}", file=sys.stderr)
        return 1
    fixture = json.loads(base_path.read_text(encoding="utf-8"))

    out_dir = Path(args.out)
    variant_id = f"{args.base_id}__{args.variation}"
    out_path = out_dir / f"{variant_id}.json"
    if out_path.exists():
        print(f"ERROR: variant already exists: {out_path}", file=sys.stderr)
        return 1

    try:
        variant, injection, review_note = build_variant(
            fixture, args.variation, args.anchor, args.position
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(variant, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    review_path = out_dir / f"{variant_id}.review.md"
    review_path.write_text(_review_md(variant_id, args.variation, injection, review_note), encoding="utf-8")

    where = f"{args.position} anchor {args.anchor!r}" if args.anchor else "after the first line"
    print(f"Wrote {out_path}")
    print(f"Injected {where}:\n  {injection}")
    if review_note:
        print(f"NOTE: {review_note}")
    print(f"Review checklist: {review_path}")
    print("\nThis is a DRAFT in staging. Calibrate + move into transcripts/ + rebaseline "
          "before it gates (see the checklist).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
