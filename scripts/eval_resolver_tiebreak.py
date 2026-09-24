#!/usr/bin/env python3
"""Score the entity-resolution tiebreak against the fuzzy-zone fixture.

Each case in evals/fixtures/resolver/fuzzy_zone.json is resolved twice: by the
string heuristic alone (``resolve_against``) and by the heuristic plus the
decision-model tiebreak (``EntityResolver.prefetch`` in ``on`` mode). Reports
accuracy, wrong merges (merged into a candidate the gold says is different,
the damaging error) and missed merges (minted a duplicate), plus cost and
latency, and a sweep of the acceptance bar over the recorded probabilities.

    python scripts/eval_resolver_tiebreak.py                  # DigitalOcean Jev
    python scripts/eval_resolver_tiebreak.py --runs 3         # variance check
    python scripts/eval_resolver_tiebreak.py --heuristic-only # no API calls

Needs DIGITALOCEAN_MODEL_ACCESS_KEY (env or .env) unless --heuristic-only.
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

from app.services.entity_resolver import (  # noqa: E402
    TIEBREAK_MIN_PROBABILITY,
    TIEBREAK_OPERATION,
    TIEBREAK_SPLIT_MIN_PROBABILITY,
    EntityResolver,
    ResolvedEntity,
    apply_tiebreak,
    resolve_against,
)
from app.services.inference.decisions import DecisionClient  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "resolver" / "fuzzy_zone.json"
SWEEP = (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)


class _Recorder:
    """Wraps a DecisionClient: forces ``on`` mode and keeps every answer."""

    def __init__(self, client: DecisionClient):
        self._client = client
        self.results: list = []

    def mode(self, _operation: str) -> str:
        return "on"

    async def decide(self, state, questions, *, operation):
        r = await self._client.decide(state, questions, operation=operation)
        self.results.append((state, r))
        return r


def _graph(case: dict) -> SimpleNamespace:
    nodes = {}
    for c in case["candidates"]:
        meta = {"aliases": c.get("aliases", []), **c.get("context", {})}
        nodes[c["id"]] = SimpleNamespace(id=c["id"], name=c["name"], type=case["type"], metadata=meta)
    return SimpleNamespace(nodes=nodes)


def _outcome(pred_id: str, case: dict) -> str:
    candidate_ids = {c["id"] for c in case["candidates"]}
    gold = case["gold"]
    if (pred_id in candidate_ids) == (gold in candidate_ids) and (gold == "new" or pred_id == gold):
        return "correct"
    if pred_id in candidate_ids:
        return "wrong_merge"  # merged into the wrong entity, or into one when it's new
    return "missed_merge"


def _tally(outcomes: list[str]) -> dict:
    return {k: outcomes.count(k) for k in ("correct", "wrong_merge", "missed_merge")}


async def run_once(cases: list[dict], client: DecisionClient | None) -> dict:
    rows = []
    for case in cases:
        mention = {**case["mention"], "type": case["type"]}
        resolver = EntityResolver(_graph(case), decisions=None)
        heuristic = resolve_against(case["type"], mention["name"], resolver._candidates(case["type"]))
        row = {"id": case["id"], "gold": case["gold"], "heuristic": heuristic.id, "asked": False}
        if client is not None:
            rec = _Recorder(client)
            tb = EntityResolver(_graph(case), decisions=rec)
            await tb.prefetch([mention])
            row["tiebreak"] = tb.resolve(case["type"], mention["name"]).id
            if rec.results:
                state, r = rec.results[0]
                a = r.choice("match")
                options = {k: {"id": c_id} for k, c_id in _option_ids(state, case).items()}
                row.update(
                    asked=True,
                    choice=options.get(a.choice, {}).get("id", a.choice),
                    p=a.probabilities.get(a.choice, 0.0),
                    raw_choice=a.choice,
                    cost=r.cost_usd,
                    latency_ms=r.latency_ms,
                    _options=options,
                    _heuristic=heuristic,
                )
        rows.append(row)
    return {"rows": rows}


def _option_ids(state: dict, case: dict) -> dict[str, str]:
    by_name = {c["name"]: c["id"] for c in case["candidates"]}
    return {key: by_name[c["name"]] for key, c in state["candidates"].items()}


def _sweep(rows: list[dict], cases: dict[str, dict]) -> list[tuple[float, dict]]:
    out = []
    for bar in SWEEP:
        outcomes = []
        for row in rows:
            case = cases[row["id"]]
            if row.get("asked"):
                h: ResolvedEntity = row["_heuristic"]
                opts = {k: {"id": v["id"], "name": ""} for k, v in row["_options"].items()}
                pred = apply_tiebreak(h, opts, row["raw_choice"], row["p"], case["type"], case["mention"]["name"], bar).id
            else:
                pred = row["heuristic"]
            outcomes.append(_outcome(pred, case))
        out.append((bar, _tally(outcomes)))
    return out


def report(runs: list[dict], cases: list[dict]) -> None:
    by_id = {c["id"]: c for c in cases}
    first = runs[0]["rows"]
    n = len(cases)
    h = _tally([_outcome(r["heuristic"], by_id[r["id"]]) for r in first])
    print(f"\n{n} cases   heuristic only: {h['correct']}/{n} correct, "
          f"{h['wrong_merge']} wrong merges, {h['missed_merge']} missed merges")
    if "tiebreak" not in first[0]:
        return
    for i, run in enumerate(runs, 1):
        t = _tally([_outcome(r["tiebreak"], by_id[r["id"]]) for r in run["rows"]])
        asked = [r for r in run["rows"] if r["asked"]]
        lat = [r["latency_ms"] for r in asked]
        cost = sum(r["cost"] for r in asked)
        print(f"run {i}  + tiebreak (merge p>={TIEBREAK_MIN_PROBABILITY}, split p>={TIEBREAK_SPLIT_MIN_PROBABILITY}): {t['correct']}/{n} correct, "
              f"{t['wrong_merge']} wrong merges, {t['missed_merge']} missed merges   "
              f"asked {len(asked)}/{n}, ${cost:.6f}, latency p50 {statistics.median(lat) if lat else 0:.0f} ms")

    print("\nper case (run 1):  gold | heuristic | decision (p) | tiebreak outcome")
    for r in first:
        case = by_id[r["id"]]
        h_ok = _outcome(r["heuristic"], case) == "correct"
        t_ok = _outcome(r["tiebreak"], case) == "correct"
        dec = f"{r['choice']} ({r['p']:.2f})" if r["asked"] else "- not asked -"
        flag = "  " if h_ok == t_ok else ("+ " if t_ok else "- ")
        print(f"{flag}{r['id']:30s} gold={r['gold']:34s} heur={'ok ' if h_ok else 'BAD'} "
              f"dec={dec:44s} final={'ok' if t_ok else 'BAD'}")

    print(f"\nmerge-bar sweep, split bar fixed at {TIEBREAK_SPLIT_MIN_PROBABILITY} (run 1):")
    for bar, t in _sweep(first, by_id):
        print(f"  p>={bar:.2f}: {t['correct']}/{n} correct, {t['wrong_merge']} wrong merges, "
              f"{t['missed_merge']} missed merges")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--heuristic-only", action="store_true")
    ap.add_argument("--json", type=Path, help="also write raw rows here")
    args = ap.parse_args()

    cases = json.loads(FIXTURE.read_text())["cases"]
    client = None
    if not args.heuristic_only:
        client = DecisionClient({
            "endpoints": {"do-jev": {"type": "digitalocean", "api_key_env": "DIGITALOCEAN_MODEL_ACCESS_KEY",
                                     "pricing": {"input": 0.042}}},
            "operations": {TIEBREAK_OPERATION: "do-jev"},
        })
    runs = []
    try:
        for _ in range(max(1, args.runs)):
            runs.append(await run_once(cases, client))
    finally:
        if client:
            await client.aclose()
    report(runs, cases)
    if args.json:
        clean = [[{k: v for k, v in r.items() if not k.startswith("_")} for r in run["rows"]] for run in runs]
        args.json.write_text(json.dumps(clean, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
