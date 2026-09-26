#!/usr/bin/env python3
"""Migrate an old corpus's memory into a new KB as captures (ADR-003 lanes).

Two steps, so selection and cleanup can be reviewed before anything is
written, and so ``apply`` can run later (inside the target imi container):

  build   old corpus + classify_memories verdicts -> bundle.jsonl + summary
  apply   bundle.jsonl -> the target repo's memory/captures (+ audit rows)

What ``build`` selects:
  - every record-lane capture (own notes, decisions, circle notes, retros)
  - library captures that are durable (>= --durable-min), or recent
    (within --recent-days of --as-of) and at least --recent-durable-min
  - library *documents* (third-party "meetings") under the same rule,
    converted to captures: their text only — old signals, participants and
    entity links are left behind (ADR-003: library never creates entities)
  - never junk, never the shorter copy of a URL captured twice

Library text goes through ``content_cleaner`` (the intake safety net). When a
``--refreshed`` JSON is given (``{capture_id: markdown}``, produced by
re-extracting truncated web pages upstream), its text replaces the stored
4,000-char cut.

``apply`` is idempotent: a capture whose id, source_id or fingerprint is
already in the target store is skipped. It writes files and audit rows only;
afterwards run ``POST /api/admin/backfill-memory-index`` so the new captures
are embedded, and optionally ``--enrich`` to summarize those without a
summary.

    python scripts/migrate_captures.py build --corpus OLD --verdicts V.jsonl \\
        --doc-durable D.jsonl --out bundle/ [--refreshed R.json] [--as-of 2026-09-26]
    python scripts/migrate_captures.py apply --bundle bundle/bundle.jsonl --repo /app/repo [--apply] [--enrich]
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import glob
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.captured_memory import CapturedMemory  # noqa: E402
from app.services.content_cleaner import clean_content  # noqa: E402
from app.services.lane_admission import library_stale_after  # noqa: E402
from app.services.memory_capture import content_fingerprint, parse_instant  # noqa: E402

MIGRATION_SOURCE_NOTE = "migrated from the previous KB (ADR-003 lanes)"
# openbrain type tags and Gmail mailbox state are not topics.
_NOISE_TAG = re.compile(
    r"^(openbrain-import|ob:.*|UNREAD|INBOX|IMPORTANT|STARRED|SENT|DRAFT|SPAM|TRASH|CHAT|CATEGORY_[A-Z_]+|Label_\d+)$"
)
_FM = re.compile(r"\A---\n(.*?)\n---\n?(.*)", re.S)


def _date(value: str | None) -> datetime | None:
    try:
        return parse_instant(value) if value else None
    except (TypeError, ValueError):
        return None


def _tags(tags: list[str]) -> list[str]:
    return [t for t in tags if isinstance(t, str) and not _NOISE_TAG.match(t)]


def _selected(durable: float, when: datetime | None, args) -> str | None:
    """Reason a library item is migrated, or None."""
    if durable >= args.durable_min:
        return "durable"
    if when and when >= args.cutoff and durable >= args.recent_durable_min:
        return "recent"
    return None


def load_verdicts(path: Path) -> dict[str, dict]:
    v: dict[str, dict] = {}
    for line in path.open():
        r = json.loads(line)
        if r.get("status") == "ok":
            v[r["key"]] = r
    return v


def _library_source(capture_source: str, content: str) -> str:
    """The cleaning source for a library item whose own source is generic."""
    if capture_source in ("mail", "email", "web", "youtube", "rss"):
        return capture_source
    return "mail" if re.search(r"^From: .+$", content, re.M) else "web"


def build(args) -> None:
    verdicts = load_verdicts(args.verdicts)
    refreshed = json.loads(args.refreshed.read_text()) if args.refreshed else {}
    doc_durable = {}
    if args.doc_durable:
        for line in args.doc_durable.open():
            r = json.loads(line)
            doc_durable[r["id"]] = r.get("durable", 0.0)
    args.cutoff = args.as_of - timedelta(days=args.recent_days)

    out: list[dict] = []
    counts: collections.Counter = collections.Counter()
    cleaned_chars = 0

    for path in sorted(glob.glob(f"{args.corpus}/memory/captures/*.json")):
        cap = CapturedMemory.model_validate_json(Path(path).read_text(encoding="utf-8"))
        r = verdicts.get(f"capture:{cap.id}")
        if r is None:
            counts["skip: no verdict"] += 1
            continue
        lane = r.get("lane") or r["lane_verdict"]["choice"]
        if lane == "junk" or cap.review_status == "rejected":
            counts["skip: junk"] += 1
            continue
        if r.get("duplicate_of"):
            counts["skip: duplicate URL"] += 1
            continue
        update: dict = {"tags": _tags(cap.tags)}
        if lane == "memory":
            update["lane"] = "record"
            counts["record capture"] += 1
        else:
            durable = float(r.get("durable") or 0.0)
            reason = _selected(durable, _date(cap.source_date) or _date(cap.created_at), args)
            if reason is None:
                counts["skip: library, not durable/recent"] += 1
                continue
            text = refreshed.get(cap.id) or cap.content
            if cap.id in refreshed:
                counts["library: refreshed full text"] += 1
            result = clean_content(text, _library_source(cap.source, text))
            cleaned_chars += result.removed_chars
            update.update(
                lane="library",
                content=result.text,
                content_fingerprint=content_fingerprint(result.text),
                stale_after=library_stale_after(durable, _date(cap.created_at)),
            )
            counts[f"library capture ({reason})"] += 1
        out.append(cap.model_copy(update=update).model_dump(mode="json"))

    for path in sorted(glob.glob(f"{args.corpus}/meetings/**/*.md", recursive=True)):
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        m = _FM.match(text)
        if not m:
            continue
        fm, body = m.groups()
        mid = re.search(r"^meeting_id:\s*\"?([^\"\n]+)", fm, re.M)
        if not mid or mid.group(1).strip() not in doc_durable:
            continue
        meeting_id = mid.group(1).strip()
        durable = doc_durable[meeting_id]
        start = re.search(r"^(?:start_time|updated_at):\s*(\S+)", fm, re.M)
        when = _date(start.group(1)) if start else None
        reason = _selected(durable, when, args)
        if reason is None:
            counts["skip: library doc, not durable/recent"] += 1
            continue
        # The ingested text lives under "## Full Transcript" when present;
        # the rest of the body is the observation scaffold.
        full = body.split("## Full Transcript", 1)
        content = (full[1] if len(full) == 2 else body).strip()
        title = re.search(r"^title:\s*\"?(.*?)\"?$", fm, re.M)
        if title and title.group(1) and not content.startswith("#"):
            content = f"# {title.group(1)}\n\n{content}"
        source = _library_source("document", content)
        result = clean_content(content, source)
        cleaned_chars += result.removed_chars
        cap = CapturedMemory(
            content=result.text,
            source=source,
            source_id=f"prev-kb-doc:{meeting_id}",
            content_fingerprint=content_fingerprint(result.text),
            source_date=start.group(1) if start else None,
            provenance_status="imported",
            lane="library",
            stale_after=library_stale_after(durable, when),
            created_at=(when.isoformat() if when else datetime.now().astimezone().isoformat()),
        )
        out.append(cap.model_dump(mode="json"))
        counts[f"library doc -> capture ({reason})"] += 1

    args.out.mkdir(parents=True, exist_ok=True)
    bundle = args.out / "bundle.jsonl"
    bundle.write_text("".join(json.dumps(c) + "\n" for c in out))
    summary = {
        "as_of": args.as_of.isoformat(),
        "rule": f"durable >= {args.durable_min} OR (within {args.recent_days}d AND durable >= {args.recent_durable_min})",
        "records": len(out),
        "counts": dict(sorted(counts.items())),
        "chars_removed_by_cleaner": cleaned_chars,
        "truncated_4000_remaining": sum(
            1 for c in out if c["lane"] == "library" and len(c["content"]) in range(3990, 4001)
        ),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


def truncated_urls(args) -> None:
    """URLs of selected web captures still cut at 4,000 chars — input for an
    upstream re-extraction (openbrain's extractor), whose output is --refreshed."""
    rows = [json.loads(line) for line in args.bundle.open()]
    urls = [
        {"id": c["id"], "url": c["source_id"]}
        for c in rows
        if c["lane"] == "library" and (c.get("source_id") or "").startswith("http")
        and "youtube.com" not in c["source_id"] and 3900 <= len(c["content"]) <= 4000
    ]
    args.out.write_text(json.dumps(urls))
    print(f"{len(urls)} truncated web captures -> {args.out}")


async def apply(args) -> None:
    from app.git_ops import git_ops
    from app.models.signal import SignalAuditRecord
    from app.services.memory_capture import CaptureStore
    from app.services.memory_governance import capture_audit_store
    from app.services.signal_audit import _governance_snapshot

    repo = Path(args.repo)
    store = CaptureStore(capture_dir=repo / "memory" / "captures", repo_root=repo)
    audit_store = capture_audit_store(repo_root=repo)
    existing_ids = {m.id for m in store.iter_all()}
    counts: collections.Counter = collections.Counter()
    written: list[CapturedMemory] = []

    for line in args.bundle.open():
        cap = CapturedMemory.model_validate(json.loads(line))
        if cap.id in existing_ids or store.find_existing(cap.content, cap.source, cap.source_id):
            counts["skip: already present"] += 1
            continue
        counts[f"write: {cap.lane}"] += 1
        if not args.apply:
            continue
        store.update(cap)  # _save: plain JSON write in the store's format
        audit_store.append(SignalAuditRecord(
            signal_id=cap.id, record_kind="capture", action="capture",
            actor="migrate_captures", tenant_id=cap.tenant_id,
            reasoning=f"{MIGRATION_SOURCE_NOTE}; lane={cap.lane}",
            before={}, after=_governance_snapshot(cap),
        ))
        existing_ids.add(cap.id)
        written.append(cap)

    if args.apply and args.enrich:
        from app.services.capture_enrichment import enrich_capture
        from app.services.claude_client import get_claude_client

        client = get_claude_client()
        sem = asyncio.Semaphore(args.enrich_concurrency)

        async def one(cap: CapturedMemory) -> None:
            if cap.summary:
                return
            async with sem:
                try:
                    enrichment = await enrich_capture(cap.content, claude_client=client)
                except Exception as e:  # best-effort, like the live capture path
                    print(f"enrich failed {cap.id}: {e}", file=sys.stderr)
                    return
            store.update(cap.model_copy(update={
                "enrichment": enrichment, "summary": enrichment.get("summary") or cap.summary,
            }))
            counts["enriched"] += 1

        await asyncio.gather(*(one(c) for c in written))

    if args.apply and written:
        try:
            await git_ops.commit_and_push(
                ["memory/captures", "memory/audit"],
                f"migrate: {len(written)} captures from the previous KB (ADR-003)",
            )
            counts["committed"] = 1
        except Exception as e:
            print(f"git commit failed (files are written): {e}", file=sys.stderr)

    print(("APPLIED" if args.apply else "DRY RUN — nothing written") + f" · {args.repo}")
    for k, n in sorted(counts.items()):
        print(f"  {n:>6}  {k}")
    if args.apply:
        print("\nNext: POST /api/admin/backfill-memory-index to embed the migrated captures.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build")
    b.add_argument("--corpus", required=True, type=Path)
    b.add_argument("--verdicts", required=True, type=Path)
    b.add_argument("--doc-durable", type=Path, help="JSONL {id: meeting_id, durable} for library documents")
    b.add_argument("--refreshed", type=Path, help="JSON {capture_id: markdown} re-extracted upstream")
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--as-of", type=lambda s: parse_instant(s), default=parse_instant("2026-09-26T00:00:00+00:00"))
    b.add_argument("--durable-min", type=float, default=0.5)
    b.add_argument("--recent-days", type=int, default=30)
    b.add_argument("--recent-durable-min", type=float, default=0.3)

    t = sub.add_parser("truncated-urls")
    t.add_argument("--bundle", required=True, type=Path)
    t.add_argument("--out", required=True, type=Path)

    a = sub.add_parser("apply")
    a.add_argument("--bundle", required=True, type=Path)
    a.add_argument("--repo", required=True)
    a.add_argument("--apply", action="store_true", help="write (default: dry run)")
    a.add_argument("--enrich", action="store_true", help="summarize migrated captures without a summary")
    a.add_argument("--enrich-concurrency", type=int, default=3)

    args = ap.parse_args()
    if args.command == "build":
        build(args)
    elif args.command == "truncated-urls":
        truncated_urls(args)
    else:
        asyncio.run(apply(args))


if __name__ == "__main__":
    main()
