#!/usr/bin/env python3
"""Ingest exported Littlebird meetings (scripts/littlebird_export.py) into imi.

POSTs each meeting to /api/ingest as a call transcript, then polls the job and
prints each pipeline phase as it completes, followed by the delta report
(entities / signals the call added).

Idempotency: every request carries source_id="littlebird:<id>", but imi's
dedup store is in-memory and lost on restart — so this script also keeps its
own ledger (<export dir>/.ingested.json) and skips meetings already recorded
there. Delete a ledger entry to force a re-ingest.

Usage:
    uv run --with httpx scripts/littlebird_ingest.py --base https://holodeck.tail4f2c31.ts.net:9443 [--only ID] [--limit N]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx


def load_meetings(export_dir: Path) -> list[dict]:
    # pathlib's glob matches dotfiles (the .ingested.json ledger); only files
    # written by littlebird_export.py carry a littlebird_id.
    meetings = []
    for path in export_dir.glob("*.json"):
        if path.name.startswith("."):
            continue
        data = json.loads(path.read_text())
        if isinstance(data, dict) and data.get("littlebird_id"):
            meetings.append(data)
    return sorted(meetings, key=lambda m: m.get("start_time") or "")


def summarize_delta(delta: dict) -> str:
    parts = []
    for key, val in delta.items():
        if isinstance(val, list):
            parts.append(f"{key}={len(val)}")
        elif isinstance(val, (int, float)):
            parts.append(f"{key}={val}")
    return ", ".join(parts)


def _request(client: httpx.Client, method: str, url: str, attempts: int = 6, **kw) -> httpx.Response:
    """HTTP with retries for transient transport errors (the tailnet proxy
    occasionally drops a connection mid-run; one blip must not kill a
    multi-hour backfill)."""
    for attempt in range(1, attempts + 1):
        try:
            return client.request(method, url, **kw)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if attempt == attempts:
                raise
            wait = min(60, 2 ** attempt)
            print(f"    ! {type(e).__name__} on {method} {url}; retry {attempt} in {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def ingest_one(client: httpx.Client, m: dict, poll_timeout: float) -> dict:
    body = {
        "content": m["transcript"],
        "source": "local_recording",
        "source_id": f"littlebird:{m['littlebird_id']}",
        "title": m["title"],
        "participants": m["participants"],
        "timestamp": m["start_time"],
        "metadata": {
            "recorder": "littlebird",
            "littlebird_id": m["littlebird_id"],
            "original_title": m["original_title"],
            "time_source": m["time_source"],
            "duration_minutes": m.get("duration_minutes"),
        },
    }
    r = _request(client, "POST", "/api/ingest", json=body)
    r.raise_for_status()
    accepted = r.json()
    job_id = accepted["job_id"]
    print(f"    job {job_id} ({accepted['status']})")
    if accepted["status"] == "duplicate":
        return {"job_id": job_id, "status": "duplicate"}

    seen: list[str] = []
    started = time.monotonic()
    while True:
        s = _request(client, "GET", f"/api/ingest/{job_id}/status").json()
        for phase in s.get("phases_completed", []):
            if phase not in seen:
                seen.append(phase)
                print(f"    ✓ {phase:<20} +{time.monotonic() - started:6.1f}s")
        if s["status"] in ("completed", "failed"):
            break
        if time.monotonic() - started > poll_timeout:
            print(f"    … still {s.get('current_phase')} after {poll_timeout:.0f}s — moving on")
            return {"job_id": job_id, "status": "timeout"}
        time.sleep(2)

    if s["status"] == "failed":
        print(f"    ✗ FAILED: {s.get('error')}")
        return {"job_id": job_id, "status": "failed", "error": s.get("error")}

    delta = _request(client, "GET", f"/api/ingest/{job_id}/delta")
    delta_json = delta.json() if delta.status_code == 200 else {}
    if delta_json:
        print(f"    Δ {summarize_delta(delta_json)}")
    return {
        "job_id": job_id,
        "status": "completed",
        "seconds": round(time.monotonic() - started, 1),
        "result": s.get("result"),
        "delta": delta_json,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="imi base URL, e.g. https://holodeck.tail4f2c31.ts.net:9443")
    ap.add_argument("--export-dir", default="~/Data/littlebird-export")
    ap.add_argument("--only", action="append", default=[], help="littlebird id(s) to ingest")
    ap.add_argument("--limit", type=int, help="ingest at most N meetings this run")
    ap.add_argument("--poll-timeout", type=float, default=900)
    args = ap.parse_args()

    export_dir = Path(args.export_dir).expanduser()
    ledger_path = export_dir / ".ingested.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}

    todo = [
        m for m in load_meetings(export_dir)
        if m["littlebird_id"] not in ledger and (not args.only or m["littlebird_id"] in args.only)
    ]
    missing = [m for m in todo if not m.get("start_time")]
    if missing:
        sys.exit(f"{len(missing)} meeting(s) have no start_time: {[m['title'] for m in missing]}")
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} meeting(s) to ingest ({len(ledger)} already in ledger)")

    with httpx.Client(base_url=args.base, timeout=60) as client:
        for n, m in enumerate(todo, 1):
            print(f"\n[{n}/{len(todo)}] {m['start_time'][:10]}  {m['title']}  ({len(m['participants'])} participants)")
            outcome = ingest_one(client, m, args.poll_timeout)
            if outcome["status"] in ("completed", "duplicate"):
                ledger[m["littlebird_id"]] = outcome
                ledger_path.write_text(json.dumps(ledger, indent=2, default=str))


if __name__ == "__main__":
    main()
