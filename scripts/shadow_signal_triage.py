#!/usr/bin/env python3
"""Replay signal_promotion_triage over a KB's stored signals. Read-only.

For every record-lane meeting in ``<repo>/meetings`` with a signals file in
``<repo>/signals``, the stored signals (what the promoter's heuristics wrote
at ingest) are copied and judged by the decision model in shadow. Nothing is
written back. The report lists, per field, how often the model disagrees with
the stored value and what ``on`` mode would change at the current bars, then
prints every change for a human to check.

    docker exec imi-app python scripts/shadow_signal_triage.py
    docker exec imi-app python scripts/shadow_signal_triage.py --limit 3 --jsonl /tmp/triage.jsonl

Uses the app's decisions config (config/inference.yaml); the operation must
not be ``off``.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.observation import Observation  # noqa: E402
from app.models.signal import MeetingSignals  # noqa: E402
from app.services import signal_triage  # noqa: E402
from app.services.inference.decisions import get_decision_client  # noqa: E402
from app.services.signal_promoter import SignalPromoter  # noqa: E402
from app.services.signal_triage import TRIAGE_OPERATION, apply_verdict, triage_signals  # noqa: E402

FIELDS = ("type", "firmness", "owner", "client")


def _values(sig, dropped: bool = False) -> dict:
    if dropped:
        return {"type": "none", "firmness": None, "owner": None, "client": None}
    return {
        "type": sig.type,
        "firmness": ("proposed" if sig.metadata.get("tier") == "candidate" else "firm")
        if sig.type == "decision" else None,
        "owner": sig.owner.name if sig.owner else None,
        "client": sig.client_id,
    }


async def _meeting(path: Path, signals_dir: Path, client) -> list[dict]:
    obs = Observation.from_markdown(path.read_text())
    sig_file = signals_dir / f"{path.stem}.json"
    if obs.lane == "library" or not sig_file.exists():
        return []
    stored = MeetingSignals.model_validate_json(sig_file.read_text()).signals
    if not stored:
        return []
    promoter = SignalPromoter(claude_client=None, knowledge_graph=None)
    refs = promoter._resolve_entities_from_state(obs)
    resolve = lambda n: promoter._resolve_person_exact(n, refs)  # noqa: E731
    judged = [copy.deepcopy(s) for s in stored]
    for s in judged:
        s.metadata.pop("triage", None)
    await triage_signals(judged, obs, refs, promoter._client_type_ids(), resolve, client=client)

    rows = []
    for before, sig in zip(stored, judged, strict=True):
        triage = sig.metadata.get("triage")
        if triage is None:
            continue
        verdict = {k: v for k, v in triage.items() if k in FIELDS}
        on = copy.deepcopy(before)
        on.metadata.pop("triage", None)
        # Replay the pending-only rule as if at ingest time: reviews came later.
        on.review_status = "pending"
        kept = apply_verdict(on, verdict, "on", resolve)
        rows.append({
            "meeting": obs.title, "signal_id": sig.id, "content": sig.content,
            "review_status": before.review_status,
            "stored": _values(before), "on": _values(on, not kept),
            "model": {
                "type": verdict.get("type", {}).get("choice"),
                "firmness": verdict.get("firmness", {}).get("choice"),
                "owner": verdict.get("owner", {}).get("name"),
                "client": verdict.get("client", {}).get("client_id"),
            },
            "p": {f: verdict.get(f, {}).get("p") for f in FIELDS},
        })
    return rows


async def main() -> int:
    ap = argparse.ArgumentParser(description="Replay signal triage over stored signals (read-only).")
    ap.add_argument("--repo", type=Path, default=Path("/app/repo"))
    ap.add_argument("--limit", type=int, default=0, help="meetings to replay (0 = all)")
    ap.add_argument("--jsonl", type=Path, help="write every row here")
    args = ap.parse_args()

    client = get_decision_client()
    if client.mode(TRIAGE_OPERATION) == "off":
        print(f"{TRIAGE_OPERATION} is off in config/inference.yaml", file=sys.stderr)
        return 2
    # Shadow regardless of the configured mode: this script never writes.
    client_mode = client.mode
    client.mode = lambda operation: "shadow" if operation == TRIAGE_OPERATION else client_mode(operation)
    signal_triage._default_client = lambda: client

    meetings = sorted((args.repo / "meetings").glob("*.md"))
    if args.limit:
        meetings = meetings[: args.limit]
    rows: list[dict] = []
    for path in meetings:
        rows.extend(await _meeting(path, args.repo / "signals", client))
    await client.aclose()

    if args.jsonl:
        args.jsonl.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"{len(rows)} signals from {len(meetings)} meetings")
    for f in FIELDS:
        applicable = [r for r in rows if r["stored"][f] is not None or r["on"][f] is not None
                      or f in ("type", "client")]
        disagree = sum(r["model"][f] != r["stored"][f] for r in applicable
                       if not (f == "firmness" and r["stored"]["type"] != "decision")
                       and not (f == "owner" and r["stored"]["type"] != "action_item"))
        changed = Counter((str(r["stored"][f]), str(r["on"][f])) for r in rows if r["stored"][f] != r["on"][f])
        print(f"\n{f}: model disagrees on {disagree}; on mode would change {sum(changed.values())}")
        for (a, b), n in changed.most_common():
            print(f"  {n:>3}  {a} -> {b}")
    print("\nchanges on mode would make:")
    for r in rows:
        diffs = [f"{f} {r['stored'][f]} -> {r['on'][f]} (p={r['p'][f]})" for f in FIELDS
                 if r["stored"][f] != r["on"][f]]
        if diffs:
            print(f"- [{r['meeting']}] {r['content'][:110]}")
            print(f"    {'; '.join(diffs)}{'  (reviewed: ' + r['review_status'] + ')' if r['review_status'] != 'pending' else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
