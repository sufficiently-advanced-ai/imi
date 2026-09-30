#!/usr/bin/env python3
"""Write a synthesized summary into meetings that were ingested without one.

For each record-lane meeting file that has a transcript (``## Full
Transcript``) but no ``summary_prompt`` in its frontmatter, runs the same
synthesis the ingest SYNTHESIZE phase runs (app/services/meeting_synthesis.py)
and rewrites the file: the summary becomes the body, the transcript is kept
once, and ``purpose`` / ``key_points`` / ``summary_prompt`` go into the
frontmatter. Everything else in the file (ids, times, entities, participants)
is written back unchanged. Signals are not touched — they come from the
transcript, which is unchanged.

Dry run by default: summarizes the first meeting it would change and prints
the result, writing nothing. ``--apply`` rewrites every eligible file and
commits them in the KB repo. Run inside the app container:

    docker exec -w /app imi-app python scripts/backfill_meeting_synthesis.py
    docker exec -w /app imi-app python scripts/backfill_meeting_synthesis.py --apply

The graph picks the rewritten files up on the next start (the corpus
reconcile re-ingests changed files): ``docker compose restart app``.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from app.models.observation import Observation  # noqa: E402
from app.services.meeting_synthesis import synthesize_meeting  # noqa: E402

ADDED_KEYS = {"purpose", "key_points", "summary_prompt"}


def frontmatter(text: str) -> dict:
    return yaml.safe_load(text.split("---", 2)[1]) or {}


def frontmatter_loss(old_text: str, new_text: str) -> list[str]:
    """Frontmatter keys the rewrite would drop or change. Observation only
    models the keys ingest writes; an older file (a live-recorder meeting with
    duration/speakers, say) must not lose the rest."""
    old, new = frontmatter(old_text), frontmatter(new_text)
    return sorted(k for k in old if k not in ADDED_KEYS and old[k] != new.get(k))


def eligible(obs: Observation, force: bool) -> str | None:
    """Why a meeting is skipped, or None when it should be summarized."""
    if obs.lane != "record":
        return "library lane"
    if not obs.raw_content:
        return "no transcript"
    if obs.summary_prompt and not force:
        return "already summarized"
    return None


async def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill meeting summaries.")
    ap.add_argument("--repo", default="/app/repo", help="KB repo root")
    ap.add_argument("--apply", action="store_true", help="rewrite files (default: dry run)")
    ap.add_argument("--only", action="append", default=[], help="bot_id(s) to process")
    ap.add_argument("--force", action="store_true", help="re-summarize already summarized meetings")
    ap.add_argument("--no-commit", action="store_true", help="leave rewritten files uncommitted")
    args = ap.parse_args()

    from app.services.claude_client import get_claude_client

    repo = Path(args.repo)
    files = sorted((repo / "meetings").glob("meeting-*.md"))
    todo: list[tuple[Path, Observation, str]] = []
    for path in files:
        try:
            original = path.read_text(encoding="utf-8")
            obs = Observation.from_markdown(original)
        except Exception as e:
            print(f"  skip {path.name}: unparseable ({e})")
            continue
        if args.only and obs.external_id not in args.only:
            continue
        reason = eligible(obs, args.force)
        if reason:
            print(f"  skip {path.name}: {reason}")
            continue
        todo.append((path, obs, original))
    print(f"{len(todo)} meeting(s) to summarize (of {len(files)})")
    if not todo:
        return 0

    client = get_claude_client()
    if not args.apply:
        path, obs, _ = todo[0]
        synthesis = await _synthesize(client, obs)
        print(f"\n--- dry run: {path.name} ({obs.title}) ---")
        print(synthesis.summary if synthesis else "(no usable summary)")
        if synthesis:
            print(f"\nkey_points: {synthesis.key_points}\nprompt: {synthesis.prompt}")
        print("\nNothing written. Re-run with --apply.")
        return 0

    written: list[Path] = []
    failed: list[str] = []
    for n, (path, obs, original) in enumerate(todo, 1):
        print(f"[{n}/{len(todo)}] {path.name}  {obs.title}")
        try:
            synthesis = await _synthesize(client, obs)
        except Exception as e:
            print(f"    ✗ {e}")
            failed.append(path.name)
            continue
        if synthesis is None:
            print("    ✗ no usable summary")
            failed.append(path.name)
            continue
        before = obs.content
        obs.summary = synthesis.summary
        obs.purpose = synthesis.purpose or None
        obs.summary_prompt = synthesis.prompt
        obs.key_points = synthesis.key_points
        text = obs.to_markdown()
        # The extraction body must survive the rewrite, or a rebuild replay
        # would promote signals from something other than the transcript.
        reparsed = Observation.from_markdown(text)
        if reparsed.content.strip() != before.strip():
            print("    ✗ extraction body would change on re-parse; not written")
            failed.append(path.name)
            continue
        lost = frontmatter_loss(original, text)
        if lost:
            print(f"    ✗ rewrite would drop or change frontmatter {lost}; not written")
            failed.append(path.name)
            continue
        path.write_text(text, encoding="utf-8")
        written.append(path)
        print(f"    ✓ {len(synthesis.key_points)} key points")

    if written and not args.no_commit:
        rel = [str(p.relative_to(repo)) for p in written]
        subprocess.run(["git", "add", "--", *rel], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", f"[synthesis] Summarize {len(written)} meeting(s)"],
            cwd=repo,
            check=True,
        )
        print(f"\nCommitted {len(written)} file(s) in {repo}.")
    print(f"\nwritten={len(written)} failed={len(failed)}")
    for name in failed:
        print(f"  failed: {name}")
    if written:
        print("Restart the app so the corpus reconcile re-ingests them: docker compose restart app")
    return 1 if failed else 0


async def _synthesize(client, obs: Observation):
    occurred = obs.occurred_at.date().isoformat() if obs.occurred_at else None
    return await synthesize_meeting(
        client,
        obs.raw_content or "",
        title=obs.title,
        participants=obs.participants,
        occurred=occurred,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
