#!/usr/bin/env python3
"""Re-run library intake rules over existing captures.

Two passes over ``memory/captures/`` in the library lane:

  dates  recompute ``source_date`` / ``time_source`` with
         ``capture_dates.published_date`` (the stored date counts as the
         intake's date). Reports real dates versus the ingest fallback, and
         every date that moves (a web URL date replacing a save date).
  noise  run the deterministic drop rules (``lane_admission.drop_rule``:
         automated senders, platform-notification subjects, the configured
         ``drop_senders`` / ``drop_subjects``, minus ``keep_sources``) and
         list what live intake would now drop, with the reason.

  refetch  with ``--refetch-dates``, fetch each web capture still on the ingest
         fallback and read the publisher's date from the page's markup. Paced
         and sent with a generic tool User-Agent. Reported with the date moves.

Dry run by default. ``--apply-dates`` writes the date changes (``valid_from``
follows ``source_date`` when it was its default). ``--reject-noise`` rejects
the noise matches through the audited review state machine, reason in the
audit row — never by deleting files — so recall stops serving them and the
rejection can be reviewed. Graph documents pick up new dates at the next link
projection. Idempotent.

    python scripts/library_intake_backfill.py --corpus <repo dir> [--since 2026-10-29]
        [--refetch-dates] [--apply-dates] [--reject-noise] [--json report.json]
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.captured_memory import CapturedMemory  # noqa: E402
from app.services.capture_dates import html_published_date, published_date  # noqa: E402
from app.services.lane_admission import drop_rule, mail_subject  # noqa: E402
from app.services.memory_governance import capture_audit_store  # noqa: E402
from app.services.signal_audit import review_with_audit  # noqa: E402
from app.utils.event_time import TIME_SOURCE_CONTENT, TIME_SOURCE_FALLBACK, to_utc  # noqa: E402

ACTOR = "library_intake_backfill"
USER_AGENT = "imi-library-backfill/1.0 (+https://github.com/sufficiently-advanced-ai/imi)"
FETCH_PAUSE_SECONDS = 1.0


def _title(cap: CapturedMemory) -> str:
    return (mail_subject(cap.content) or cap.source_id or cap.content.split("\n", 1)[0])[:90]


def refetched_date(client: httpx.Client, url: str) -> datetime | None:
    """The publisher's date for ``url``, or None if the page gives none. A past
    date only: a failed fetch or a future stamp leaves the capture undated."""
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as e:
        print(f"  fetch failed {url}: {e}", file=sys.stderr)
        return None
    found = html_published_date(response.text)
    return found if found is not None and found <= datetime.now(UTC) else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--since", help="noise pass: only captures created on/after this date")
    ap.add_argument("--refetch-dates", action="store_true")
    ap.add_argument("--apply-dates", action="store_true")
    ap.add_argument("--reject-noise", action="store_true")
    ap.add_argument("--json", type=Path, help="write the full report here")
    args = ap.parse_args()

    audit_store = capture_audit_store(repo_root=args.corpus)
    counts: collections.Counter = collections.Counter()
    moved: list[dict] = []
    noise: list[dict] = []
    client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True)
    for path in sorted((args.corpus / "memory" / "captures").glob("*.json")):
        cap = CapturedMemory.model_validate_json(path.read_text(encoding="utf-8"))
        if cap.lane != "library":
            continue
        counts["library"] += 1
        given = cap.source_date if to_utc(cap.source_date) else None
        if cap.source_date and not given:
            counts["unparseable stored date"] += 1
        date, time_source = published_date(cap.content, cap.source, cap.source_id, given)
        if (
            args.refetch_dates
            and time_source == TIME_SOURCE_FALLBACK
            and (cap.source_id or "").startswith(("http://", "https://"))
        ):
            time.sleep(FETCH_PAUSE_SECONDS)
            if (found := refetched_date(client, cap.source_id)) is not None:
                date, time_source = found.isoformat(), TIME_SOURCE_CONTENT
                counts["date refetched"] += 1
        counts[f"date: {time_source}"] += 1
        update: dict = {}
        if (date, time_source) != (cap.source_date, cap.time_source):
            update = {"source_date": date, "time_source": time_source}
            if cap.valid_from == cap.source_date:
                update["valid_from"] = date
            if date != cap.source_date:
                counts["date changed"] += 1
                moved.append({"id": cap.id, "from": cap.source_date, "to": date,
                              "time_source": time_source, "title": _title(cap)})

        reason = None
        if not args.since or cap.created_at[:10] >= args.since:
            reason = drop_rule(cap.content, cap.source, None, cap.source_id)
        if reason and cap.review_status != "rejected":
            counts["noise"] += 1
            noise.append({"id": cap.id, "source": cap.source, "reason": reason, "title": _title(cap)})

        new = cap.model_copy(update=update) if update and args.apply_dates else cap
        if reason and args.reject_noise and cap.review_status != "rejected":
            new, audit = review_with_audit(new, "reject", actor=ACTOR, record_kind="capture")
            audit_store.append(audit.model_copy(update={"reasoning": f"{audit.reasoning} | rule: {reason}"}))
        if new is not cap:
            path.write_text(new.model_dump_json(indent=2), encoding="utf-8")
            counts["written"] += 1

    client.close()
    real = counts["library"] - counts["date: fallback_now"]
    print(f"library captures: {counts['library']}")
    print(f"  real date: {real}  (explicit {counts['date: explicit']}, url {counts['date: inferred']}, "
          f"content {counts['date: content_header']})")
    print(f"  fallback (ingest time): {counts['date: fallback_now']}")
    print(f"  dates that move: {counts['date changed']}  unparseable stored: {counts['unparseable stored date']}")
    print(f"noise matched by rules: {counts['noise']}")
    for row in collections.Counter(n["reason"].split(" (")[0] for n in noise).most_common():
        print(f"  {row[1]:4}  {row[0]}")
    for n in noise[:60]:
        print(f"  - {n['id'][:8]} {n['source']:5} {n['reason'][:60]:60} | {n['title'][:70]}")
    print(f"written: {counts['written']}" if counts["written"] else "dry run: nothing written")
    if args.json:
        args.json.write_text(json.dumps({"counts": counts, "moved": moved, "noise": noise}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
