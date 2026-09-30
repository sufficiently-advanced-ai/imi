#!/usr/bin/env python3
"""Score the signal_promotion_triage operation against labeled meetings.

Each meeting in evals/fixtures/signal_triage/meetings.json carries the signals
the promoter's extractor returned for it. They are fed through the real
``SignalPromoter.promote()`` (a scripted extractor stands in for the LLM), so
the baseline is the shipping heuristics: tier from self-reported confidence,
first-name owner match, lone-client scope. Triage runs in shadow; the script
then applies each verdict in ``on`` mode to a copy, and scores four fields for
the heuristics, the model's raw answer and the ``on`` outcome:

  type      every signal (a dropped signal counts as ``none``)
  firmness  signals extracted as decisions whose gold is a decision
  owner     signals whose gold type is action_item
  client    every signal

For ``on`` it also counts fixes (heuristic wrong -> right) and breaks
(heuristic right -> wrong); breaks are the number to hold at zero.

    python scripts/eval_signal_triage.py                 # DigitalOcean Jev
    python scripts/eval_signal_triage.py --runs 3        # variance check
    python scripts/eval_signal_triage.py --fixture path  # e.g. a private, real-data set

Needs DIGITALOCEAN_MODEL_ACCESS_KEY (env or .env).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.observation import Observation  # noqa: E402
from app.services import signal_promoter, signal_triage  # noqa: E402
from app.services.inference.decisions import DecisionClient  # noqa: E402
from app.services.signal_promoter import SignalPromoter  # noqa: E402
from app.services.signal_triage import TRIAGE_OPERATION, apply_verdict  # noqa: E402

FIXTURE = (Path(__file__).resolve().parent.parent / "evals" / "fixtures"
           / "signal_triage" / "meetings.json")
FIELDS = ("type", "firmness", "owner", "client")


class _ScriptedExtractor:
    """Stands in for the LLM: returns the fixture's extracted signals."""

    def __init__(self, signals: list[dict]):
        self._text = json.dumps(signals)

    async def generate_message(self, **_kw):
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


class _Promoter(SignalPromoter):
    @staticmethod
    def _client_type_ids() -> set[str]:
        return {"account", "client"}


def _observation(m: dict) -> Observation:
    return Observation(
        observation_id=f"ingest-{m['id']}", external_id=m["id"], title=m["title"],
        observed_at=datetime.fromisoformat(m["date"]).replace(tzinfo=UTC),
        participants=m["participants"], entities_mentioned=m["entities"], content=m["text"],
    )


def _values(sig, dropped: bool, names: dict[str, str]) -> dict:
    """The four scored fields as a signal currently holds them."""
    if dropped:
        return {"type": "none", "firmness": None, "owner": None, "client": None}
    return {
        "type": sig.type,
        "firmness": ("proposed" if sig.metadata.get("tier") == "candidate" else "firm")
        if sig.type == "decision" else None,
        "owner": sig.owner.name if sig.owner else None,
        "client": names.get(sig.client_id) if sig.client_id else None,
    }


def _model_values(verdict: dict, names: dict[str, str]) -> dict:
    t = verdict.get("type", {}).get("choice")
    return {
        "type": t,
        "firmness": verdict.get("firmness", {}).get("choice"),
        "owner": verdict.get("owner", {}).get("name"),
        "client": names.get(verdict.get("client", {}).get("client_id")),
    }


def _scored(field: str, gold: dict, heuristic_type: str) -> bool:
    if field == "firmness":
        return heuristic_type == "decision" and "firmness" in gold
    if field == "owner":
        return gold["type"] == "action_item"
    return True


def _ok(field: str, value, gold: dict) -> bool:
    if field == "type":
        return value in {gold["type"], *gold.get("also_ok_type", [])}
    if field == "firmness" and value is None:
        # retyped away from decision: right exactly when the new type is
        return gold["type"] != "decision"
    return value in {gold.get(field), *gold.get(f"also_ok_{field}", [])}


async def _run_once(client: DecisionClient, meetings: list[dict]) -> list[dict]:
    rows: list[dict] = []

    async def one(m: dict) -> None:
        obs = _observation(m)
        promoter = _Promoter(claude_client=_ScriptedExtractor([s["extracted"] for s in m["signals"]]))
        result = await promoter.promote(obs)
        refs = promoter._resolve_entities_from_state(obs)
        names = {r.id: r.name for r in refs}
        by_content = {s["extracted"]["content"]: s["gold"] for s in m["signals"]}
        for sig in result.signals:
            triage = sig.metadata.get("triage")
            if triage is None:
                raise RuntimeError(f"{m['id']}: no verdict for {sig.content!r} (call failed?)")
            verdict = {k: v for k, v in triage.items() if k in ("type", "firmness", "owner", "client")}
            heuristic = copy.deepcopy(sig)
            heuristic.metadata.pop("triage", None)
            on = copy.deepcopy(heuristic)
            kept = apply_verdict(on, verdict, "on", lambda n: promoter._resolve_person_exact(n, refs))
            gold = by_content[sig.content]
            rows.append({
                "id": f"{m['id']}:{sig.position}", "content": sig.content, "gold": gold,
                "heuristic": _values(heuristic, False, names),
                "model": _model_values(verdict, names),
                "on": _values(on, not kept, names),
                "verdict": verdict,
            })

    await asyncio.gather(*(one(m) for m in meetings))
    return rows


def _report(rows: list[dict], run: int, cost: float, verbose: bool = False) -> dict:
    print(f"\n=== run {run}: {len(rows)} signals ===")
    summary = {}
    for field in FIELDS:
        scored = [r for r in rows if _scored(field, r["gold"], r["heuristic"]["type"])]
        n = len(scored)
        h = sum(_ok(field, r["heuristic"][field], r["gold"]) for r in scored)
        mo = sum(_ok(field, r["model"][field], r["gold"]) for r in scored)
        on = sum(_ok(field, r["on"][field], r["gold"]) for r in scored)
        fixes = [r["id"] for r in scored
                 if not _ok(field, r["heuristic"][field], r["gold"]) and _ok(field, r["on"][field], r["gold"])]
        breaks = [r["id"] for r in scored
                  if _ok(field, r["heuristic"][field], r["gold"]) and not _ok(field, r["on"][field], r["gold"])]
        print(f"{field:<9} n={n:<3} heuristic {h}/{n}  model {mo}/{n}  on {on}/{n}  "
              f"fixes {len(fixes)}  breaks {len(breaks)} {breaks}")
        for r in scored:
            if verbose or not (_ok(field, r["model"][field], r["gold"]) and _ok(field, r["on"][field], r["gold"])):
                v = r["verdict"].get(field, {})
                print(f"    {r['id']:<40} gold={r['gold'].get(field)!s:<12} heur={r['heuristic'][field]!s:<14} "
                      f"model={r['model'][field]!s:<14} p={v.get('p', 0):.2f} on={r['on'][field]!s}")
        summary[field] = {"heuristic": h, "model": mo, "on": on, "n": n, "breaks": len(breaks)}
    print(f"cost ${cost:.6f}")
    return summary


async def main() -> int:
    ap = argparse.ArgumentParser(description="Score the signal_promotion_triage operation.")
    ap.add_argument("--fixture", type=Path, default=FIXTURE)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--verbose", action="store_true", help="print every scored row, not only misses")
    args = ap.parse_args()

    meetings = json.loads(args.fixture.read_text()).get("meetings") or []
    if not meetings:
        print(f"fixture error: {args.fixture} has no meetings", file=sys.stderr)
        return 2
    types = sorted({t for m in meetings for t in m["entities"]} | {"person", "account"})
    signal_promoter.get_active_entity_types = lambda: set(types)

    client = DecisionClient({
        "endpoints": {"do-jev": {"type": "digitalocean", "api_key_env": "DIGITALOCEAN_MODEL_ACCESS_KEY",
                                 "pricing": {"input": 0.042}}},
        "operations": {TRIAGE_OPERATION: "do-jev"},
        "modes": {TRIAGE_OPERATION: "shadow"},
    })
    signal_triage._default_client = lambda: client
    costs: list[float] = []
    original = client.decide

    async def metered(*a, **kw):
        r = await original(*a, **kw)
        costs.append(r.cost_usd)
        return r

    client.decide = metered
    try:
        summaries = []
        for i in range(args.runs):
            costs.clear()
            rows = await _run_once(client, meetings)
            summaries.append(_report(sorted(rows, key=lambda r: r["id"]), i + 1, sum(costs), args.verbose))
    finally:
        await client.aclose()
    if args.runs > 1:
        print(f"\nover {args.runs} runs (on correct / breaks):")
        for field in FIELDS:
            print(f"  {field:<9} " + "  ".join(f"{s[field]['on']}/{s[field]['n']} b{s[field]['breaks']}"
                                                for s in summaries))
        print("  heuristic baseline: " + "  ".join(
            f"{f} {summaries[0][f]['heuristic']}/{summaries[0][f]['n']}" for f in FIELDS))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
