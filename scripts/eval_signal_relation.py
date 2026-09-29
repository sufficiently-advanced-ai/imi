#!/usr/bin/env python3
"""Score the signal_relation supersession gate against labeled decision pairs.

Every case in evals/fixtures/signal_relation/decision_pairs.json shares an
entity, so today's entity-overlap matcher proposes all of them as supersession
candidates (the baseline). Each pair is then judged by the decision model and
gated at SUPERSEDE_MIN_PROBABILITY. Reports relation accuracy, false
supersessions (gate keeps a pair whose gold is not ``supersedes``: the
damaging error), missed supersessions, cost and latency, and a sweep of the
gate bar over the recorded probabilities.

    python scripts/eval_signal_relation.py                 # DigitalOcean Jev
    python scripts/eval_signal_relation.py --runs 3        # variance check
    python scripts/eval_signal_relation.py --fixture path  # e.g. a private, real-data set

Needs DIGITALOCEAN_MODEL_ACCESS_KEY (env or .env).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.inference.decisions import DecisionClient  # noqa: E402
from app.services.signal_relation import (  # noqa: E402
    RELATION_OPERATION,
    SUPERSEDE_MIN_PROBABILITY,
    build_relation_question,
    build_relation_state,
)

FIXTURE = (Path(__file__).resolve().parent.parent / "evals" / "fixtures"
           / "signal_relation" / "decision_pairs.json")
SWEEP = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def _signal(side: dict, case_id: str, which: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"{case_id}-{which}", content=side["text"], source_meeting_id=f"{case_id}-{which}",
        source_meeting_title=side.get("meeting"), source_timestamp=side["date"], entities=[],
    )


async def _run_once(client: DecisionClient, cases: list[dict]) -> list[dict]:
    question = build_relation_question()

    async def one(case: dict) -> dict:
        state = build_relation_state(_signal(case["new"], case["id"], "new"),
                                     _signal(case["old"], case["id"], "old"), case.get("shared", []))
        r = await client.decide(state, {"relation": question}, operation=RELATION_OPERATION)
        a = r.choice("relation")
        return {"id": case["id"], "gold": case["gold"], "ok": {case["gold"], *case.get("also_ok", [])},
                "pred": a.choice, "p_supersedes": a.probabilities.get("supersedes", 0.0),
                "probs": a.probabilities, "cost": r.cost_usd, "ms": r.latency_ms}

    return list(await asyncio.gather(*(one(c) for c in cases)))


def _gate(rows: list[dict], bar: float) -> dict:
    kept = [r for r in rows if r["pred"] == "supersedes" and r["p_supersedes"] >= bar]
    false_sup = [r["id"] for r in kept if r["gold"] != "supersedes"]
    missed = [r["id"] for r in rows if r["gold"] == "supersedes" and r not in kept]
    return {"kept": len(kept), "false_supersedes": false_sup, "missed_supersedes": missed}


def _report(rows: list[dict], run: int) -> dict:
    n = len(rows)
    correct = sum(r["pred"] in r["ok"] for r in rows)
    gate = _gate(rows, SUPERSEDE_MIN_PROBABILITY)
    baseline_false = sum(r["gold"] != "supersedes" for r in rows)
    print(f"\n=== run {run}: {n} pairs ===")
    for r in sorted(rows, key=lambda r: r["id"]):
        mark = "ok " if r["pred"] in r["ok"] else "BAD"
        print(f"  {mark} {r['id']:<36} gold={r['gold']:<10} pred={r['pred']:<10} "
              f"p={r['probs'].get(r['pred'], 0):.2f} p_sup={r['p_supersedes']:.2f}")
    print(f"relation accuracy: {correct}/{n} ({correct / n:.0%})")
    print(f"baseline (entity overlap): all {n} proposed, {baseline_false} false supersessions")
    print(f"gate @ {SUPERSEDE_MIN_PROBABILITY}: kept {gate['kept']}, "
          f"false supersessions {len(gate['false_supersedes'])} {gate['false_supersedes']}, "
          f"missed {len(gate['missed_supersedes'])} {gate['missed_supersedes']}")
    print("bar sweep (false / missed):  " + "  ".join(
        f"{b:.1f}: {len(g['false_supersedes'])}/{len(g['missed_supersedes'])}"
        for b in SWEEP for g in [_gate(rows, b)]))
    ms = [r["ms"] for r in rows]
    print(f"cost ${sum(r['cost'] for r in rows):.6f}; latency median {statistics.median(ms)} ms, max {max(ms)} ms")
    return {"accuracy": correct / n, "false": len(gate["false_supersedes"]), "missed": len(gate["missed_supersedes"])}


async def main() -> int:
    ap = argparse.ArgumentParser(description="Score the signal_relation supersession gate.")
    ap.add_argument("--fixture", type=Path, default=FIXTURE)
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()

    cases = json.loads(args.fixture.read_text()).get("cases") or []
    if not cases:
        print(f"fixture error: {args.fixture} has no cases", file=sys.stderr)
        return 2
    client = DecisionClient({
        "endpoints": {"do-jev": {"type": "digitalocean", "api_key_env": "DIGITALOCEAN_MODEL_ACCESS_KEY",
                                 "pricing": {"input": 0.042}}},
        "operations": {RELATION_OPERATION: "do-jev"},
    })
    try:
        summaries = [_report(await _run_once(client, cases), i + 1) for i in range(args.runs)]
    finally:
        await client.aclose()
    if args.runs > 1:
        acc = [s["accuracy"] for s in summaries]
        print(f"\nover {args.runs} runs: accuracy mean {statistics.mean(acc):.0%} "
              f"(min {min(acc):.0%}); false supersessions {[s['false'] for s in summaries]}; "
              f"missed {[s['missed'] for s in summaries]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
