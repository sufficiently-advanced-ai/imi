#!/usr/bin/env python3
"""Score the meeting_linking prompt (ADR-013) against labeled call transcripts.

Each case in evals/fixtures/meeting_linking/cases.json is one synthetic call
with gold excerpts (storyline, a line range to overlap, stance). The calls go
through the production path: ``parse_meeting`` numbers the transcript,
``judge`` calls the model (operation ``meeting_linking``, routed by
config/inference.yaml), ``select_excerpts`` applies the server's rules (§3).
Reports, per confidence floor: recall of required gold excerpts, false
excerpts (no gold on that storyline overlaps them), and stance accuracy on
matched excerpts.

    python scripts/eval_meeting_linking.py                 # live model calls
    python scripts/eval_meeting_linking.py --runs 3        # variance check
    python scripts/eval_meeting_linking.py --fixture path  # e.g. a private, real-data set
    python scripts/eval_meeting_linking.py --replay out.json   # re-score a saved run, no calls

Run inside the app container so the inference routing and credentials apply.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE = (Path(__file__).resolve().parent.parent / "evals" / "fixtures"
           / "meeting_linking" / "cases.json")
SWEEP = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9)


def meeting_markdown(case: dict) -> str:
    import yaml

    front = yaml.safe_dump({"title": case["title"], "start_time": case["date"],
                            "updated_at": case["date"], "participants": case["participants"]},
                           sort_keys=False)
    return (f"---\n{front}---\n\n# {case['title']}\n\n## Full Transcript\n\n"
            + "\n".join(case["lines"]) + "\n")


def _overlaps(a: list[int], b: list[int]) -> bool:
    return a[0] <= b[-1] and b[0] <= a[-1]


def score(cases: list[dict], predictions: dict[str, list[dict]], floor: float) -> dict:
    """``predictions``: case id -> [{storyline_id, lines, stance, confidence}]."""
    required = matched_required = false = kept = stance_ok = stance_total = 0
    misses, falses, wrong = [], [], []
    for case in cases:
        preds = [p for p in predictions.get(case["id"], []) if p["confidence"] >= floor]
        kept += len(preds)
        gold = case["gold"]
        for g in gold:
            hits = [p for p in preds if p["storyline_id"] == g["storyline_id"]
                    and _overlaps(p["lines"], g["lines"])]
            if not g.get("optional"):
                required += 1
                if hits:
                    matched_required += 1
                else:
                    misses.append(f"{case['id']}:{g['lines']}")
            if hits:
                stance_total += 1
                best = max(hits, key=lambda p: p["confidence"])
                if best["stance"] in {g["stance"], *g.get("also_ok", [])}:
                    stance_ok += 1
                else:
                    wrong.append(f"{case['id']}:{g['lines']} {best['stance']}!={g['stance']}")
        for p in preds:
            if not any(g["storyline_id"] == p["storyline_id"] and _overlaps(p["lines"], g["lines"])
                       for g in gold):
                false += 1
                falses.append(f"{case['id']}:{p['storyline_id'].removeprefix('storyline-')}"
                              f"{p['lines']}@{p['confidence']:.2f}")
    return {"floor": floor, "excerpts": kept,
            "recall": matched_required / required if required else 1.0,
            "false": false, "stance_accuracy": stance_ok / stance_total if stance_total else 1.0,
            "misses": misses, "false_excerpts": falses, "wrong_stance": wrong}


async def predict(cases: list[dict], owner: str, storylines: list[dict]) -> dict[str, list[dict]]:
    from app.services.claude_client import get_claude_client
    from app.services.meeting_linker import judge, select_excerpts
    from app.services.meeting_transcript import parse_meeting

    client = get_claude_client()
    semaphore = asyncio.Semaphore(4)
    ids = {s["id"] for s in storylines}

    async def one(case: dict) -> tuple[str, list[dict]]:
        meeting = parse_meeting(meeting_markdown(case), f"meetings/{case['id']}.md")
        raw = await judge(client, meeting, storylines, owner, 400, 40, semaphore)
        if raw is None:
            print(f"  {case['id']}: model call failed", file=sys.stderr)
            return case["id"], []
        kept, _ = select_excerpts(meeting, raw, ids, min_confidence=0.0, max_per_storyline=3,
                                  max_storylines=5, speaker_key=lambda turn: str(turn))
        return case["id"], [{"storyline_id": c.storyline_id, "lines": [c.start, c.end],
                             "stance": c.stance, "confidence": c.confidence, "claim": c.claim}
                            for c in kept]

    return dict(await asyncio.gather(*(one(c) for c in cases)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--replay", type=Path, help="score saved predictions instead of calling the model")
    parser.add_argument("--save", type=Path, help="write the predictions of the last run here")
    args = parser.parse_args()
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    cases = data["cases"]
    runs = ([json.loads(args.replay.read_text(encoding="utf-8"))] if args.replay
            else [asyncio.run(predict(cases, data["owner"], data["storylines"])) for _ in range(args.runs)])
    if args.save:
        args.save.write_text(json.dumps(runs[-1], indent=2), encoding="utf-8")
    print(f"{len(cases)} cases, {len(runs)} run(s)")
    print(f"{'floor':>6} {'excerpts':>9} {'recall':>7} {'false':>6} {'stance':>7}")
    for floor in SWEEP:
        rows = [score(cases, run, floor) for run in runs]
        mean = {k: sum(r[k] for r in rows) / len(rows) for k in ("excerpts", "recall", "false", "stance_accuracy")}
        print(f"{floor:>6.2f} {mean['excerpts']:>9.1f} {mean['recall']:>7.2f} {mean['false']:>6.1f} "
              f"{mean['stance_accuracy']:>7.2f}")
    detail = score(cases, runs[-1], 0.75)
    for key in ("misses", "false_excerpts", "wrong_stance"):
        if detail[key]:
            print(f"at 0.75, {key}: {', '.join(detail[key])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
