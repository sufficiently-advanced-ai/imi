#!/usr/bin/env python3
"""Score the signal_duplicate decision against labeled signal pairs.

Every case in evals/fixtures/signal_dedup/signal_pairs.json is a same-type pair
that clears the embedding threshold, so it reaches the decision model. Reports
relation accuracy and, for the actions the relation maps to, wrong hides
(a signal hidden whose gold is overlap/different: the damaging error), missed
hides and hides of the wrong side, plus cost and latency, and a sweep of the
hide bar over the recorded probabilities.

    python scripts/eval_signal_dedup.py                 # DigitalOcean Jev
    python scripts/eval_signal_dedup.py --runs 3        # variance check
    python scripts/eval_signal_dedup.py --fixture path  # e.g. a private, real-data set

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
from app.services.signal_dedup import (  # noqa: E402
    DEDUP_OPERATION,
    HIDE_MIN_PROBABILITY,
    OMIT_MAX_PROBABILITY,
    DuplicateCandidate,
    build_duplicate_question,
    build_duplicate_state,
    build_omission_questions,
)

FIXTURE = (Path(__file__).resolve().parent.parent / "evals" / "fixtures"
           / "signal_dedup" / "signal_pairs.json")
SWEEP = (0.4, 0.5, 0.6, 0.7, 0.8)
_HIDE = {"same": "hide_new", "earlier_richer": "hide_new", "later_richer": "hide_old"}


def _signal(case: dict, which: str) -> SimpleNamespace:
    side = case[which]
    return SimpleNamespace(id=f"{case['id']}-{which}", type=case["type"], content=side["text"],
                           source_meeting_id=f"{case['id']}-{which}", source_meeting_title=None,
                           source_timestamp=side["date"])


def _action(relation: str, p: float, bar: float, p_omits: float = 0.0) -> str:
    if relation in ("earlier_richer", "later_richer") and p_omits >= OMIT_MAX_PROBABILITY:
        return "keep"  # linked, not hidden (signal_dedup.decide_action)
    return _HIDE[relation] if relation in _HIDE and p >= bar else "keep"


async def _run_once(client: DecisionClient, cases: list[dict]) -> list[dict]:
    questions = {"relation": build_duplicate_question(), **build_omission_questions()}

    async def one(case: dict) -> dict:
        c = DuplicateCandidate(_signal(case, "later"), _signal(case, "earlier"), 0.0)
        r = await client.decide(build_duplicate_state(c), questions, operation=DEDUP_OPERATION)
        a = r.choice("relation")
        kept = "earlier_omits" if a.choice == "earlier_richer" else "later_omits"
        return {"id": case["id"], "gold": case["gold"], "ok": {case["gold"], *case.get("also_ok", [])},
                "pred": a.choice, "p": a.probabilities.get(a.choice, 0.0), "omits": r.noul(kept),
                "cost": r.cost_usd, "ms": r.latency_ms}

    return list(await asyncio.gather(*(one(c) for c in cases)))


def _score(rows: list[dict], bar: float) -> dict:
    wrong, missed, wrong_side = [], [], []
    for r in rows:
        pred, gold = _action(r["pred"], r["p"], bar, r["omits"]), _action(r["gold"], 1.0, 0.0)
        acceptable = {_action(g, 1.0, 0.0) for g in r["ok"]}
        if pred == "keep" and gold != "keep" and "keep" not in acceptable:
            missed.append(r["id"])
        elif pred != "keep" and "keep" in acceptable and gold == "keep":
            wrong.append(r["id"])
        elif pred != "keep" and pred not in acceptable:
            wrong_side.append(r["id"])
    return {"wrong_hides": wrong, "missed_hides": missed, "wrong_side": wrong_side}


def _report(rows: list[dict], run: int) -> dict:
    n = len(rows)
    correct = sum(r["pred"] in r["ok"] for r in rows)
    s = _score(rows, HIDE_MIN_PROBABILITY)
    print(f"\n=== run {run}: {n} pairs ===")
    for r in sorted(rows, key=lambda r: r["id"]):
        mark = "ok " if r["pred"] in r["ok"] else "BAD"
        print(f"  {mark} {r['id']:<36} gold={r['gold']:<15} pred={r['pred']:<15} p={r['p']:.2f} omits={r['omits']:.2f}")
    print(f"relation accuracy: {correct}/{n} ({correct / n:.0%}) (labels only; hides below apply the omission gate)")
    print(f"hide bar {HIDE_MIN_PROBABILITY}: wrong hides {s['wrong_hides']}, missed {s['missed_hides']}, "
          f"wrong side {s['wrong_side']}")
    print("bar sweep (wrong/missed/side):  " + "  ".join(
        f"{b:.1f}: {len(x['wrong_hides'])}/{len(x['missed_hides'])}/{len(x['wrong_side'])}"
        for b in SWEEP for x in [_score(rows, b)]))
    ms = [r["ms"] for r in rows]
    print(f"cost ${sum(r['cost'] for r in rows):.6f}; latency median {statistics.median(ms)} ms, max {max(ms)} ms")
    return {"accuracy": correct / n, "wrong": len(s["wrong_hides"]), "missed": len(s["missed_hides"])}


async def main() -> int:
    ap = argparse.ArgumentParser(description="Score the signal_duplicate decision.")
    ap.add_argument("--fixture", type=Path, default=FIXTURE)
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()

    cases = json.loads(args.fixture.read_text())["cases"]
    if not cases:
        print(f"no cases in {args.fixture}", file=sys.stderr)
        return 1
    client = DecisionClient({
        "endpoints": {"do-jev": {"type": "digitalocean", "api_key_env": "DIGITALOCEAN_MODEL_ACCESS_KEY",
                                 "pricing": {"input": 0.042}}},
        "operations": {DEDUP_OPERATION: "do-jev"},
    })
    try:
        summaries = [_report(await _run_once(client, cases), i + 1) for i in range(max(1, args.runs))]
    finally:
        await client.aclose()
    if len(summaries) > 1:
        acc = [s["accuracy"] for s in summaries]
        print(f"\nover {len(summaries)} runs: accuracy mean {statistics.mean(acc):.0%} (min {min(acc):.0%}); "
              f"wrong hides {[s['wrong'] for s in summaries]}; missed {[s['missed'] for s in summaries]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
