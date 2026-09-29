#!/usr/bin/env python3
"""Apply lane verdicts to a corpus — ADR-003 migration steps 1-2.

Reads ``verdicts.jsonl`` from ``scripts/classify_memories.py`` and, per record:

  capture       lane from its verdict (memory -> record, library -> library);
                library gets stale_after from the durability judgment, counted
                from the capture's created_at
  signal        lane from its source document's kind (conversation / own_note
                -> record, third_party -> library); library signals become
                attributed claims (type claim, original type in
                metadata.extracted_type, no owner/status/due date), as live
                ingest writes them
  agent memory  record
  meeting file  documents judged third_party / junk get ``lane: library`` in
                their frontmatter and ``participants`` renamed to ``authors``,
                so a rebuild from files links them only to their recorded
                entity_ids and never mints person stubs from their names

With ``--reject-junk`` it also rejects, through the audited review state
machine (never by deleting files):

  - junk captures and signals from junk documents
  - signals the model scored as not standalone (P < 0.2)
  - e2e test-fixture agent memories
  - cross-source duplicate captures (the shorter copy of a URL captured twice;
    rejected with a reason, the longer copy is kept)

Dry run by default: prints what would change. ``--apply`` writes the files,
in the same JSON format the stores write, plus one audit row per rejection. It
does not re-index; after applying, refresh vectors with
``POST /api/admin/backfill-memory-index`` so vector metadata carries the lane.

Idempotent: records already in the target state are skipped, so re-running is
safe.

    python scripts/stamp_lanes.py --corpus <repo dir> --verdicts <verdicts.jsonl> [--reject-junk] [--apply]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.agent_memory import AgentMemory  # noqa: E402
from app.models.captured_memory import CapturedMemory  # noqa: E402
from app.models.signal import MeetingSignals  # noqa: E402
from app.services.lane_admission import library_claim_fields, library_stale_after  # noqa: E402
from app.services.memory_capture import parse_instant  # noqa: E402
from app.services.memory_governance import capture_audit_store  # noqa: E402
from app.services.signal_audit import SignalAuditStore, review_with_audit  # noqa: E402

ACTOR = "stamp_lanes (ADR-003 backfill)"
DOC_KIND_TO_LANE = {"conversation": "record", "own_note": "record", "third_party": "library", "junk": "library"}
CAPTURE_LANE = {"memory": "record", "library": "library", "junk": "library"}
NOT_STANDALONE = 0.2


def load_verdicts(path: Path) -> dict[str, dict]:
    """key -> latest ok verdict (a later ok row replaces an earlier one)."""
    v: dict[str, dict] = {}
    for line in path.open():
        r = json.loads(line)
        if r.get("status") == "ok":
            v[r["key"]] = r
    return v


def capture_verdict_lane(r: dict) -> str:
    """memory | library | junk"""
    return r.get("lane") or r["lane_verdict"]["choice"]


def _created(value: str | None) -> datetime | None:
    try:
        return parse_instant(value) if value else None
    except (TypeError, ValueError):
        return None


class Plan:
    def __init__(self, apply: bool):
        self.apply = apply
        self.counts: collections.Counter = collections.Counter()
        self.examples: dict[str, list[str]] = collections.defaultdict(list)

    def note(self, what: str, example: str) -> None:
        self.counts[what] += 1
        if len(self.examples[what]) < 3:
            self.examples[what].append(example[:120])


def _reject(record, reason: str, kind: str, audit_store: SignalAuditStore, plan: Plan, apply: bool,
            superseded_by: str | None = None):
    """Audited transition; returns the new record. ``supersede`` when a keeper exists."""
    action = "supersede" if superseded_by else "reject"
    new, audit = review_with_audit(record, action, actor=ACTOR, superseded_by=superseded_by, record_kind=kind)
    audit = audit.model_copy(update={"reasoning": f"{audit.reasoning} | {reason}"})
    if apply:
        audit_store.append(audit)
    return new


def stamp_captures(corpus: Path, verdicts: dict[str, dict], plan: Plan, reject_junk: bool) -> None:
    audit_store = capture_audit_store(repo_root=corpus)
    for path in sorted((corpus / "memory" / "captures").glob("*.json")):
        cap = CapturedMemory.model_validate_json(path.read_text(encoding="utf-8"))
        r = verdicts.get(f"capture:{cap.id}")
        if r is None:
            plan.note("capture: no verdict (left as is)", cap.content)
            continue
        verdict = capture_verdict_lane(r)
        lane = CAPTURE_LANE[verdict]
        update: dict = {}
        if cap.lane != lane:
            update["lane"] = lane
        if lane == "library" and not cap.stale_after:
            update["stale_after"] = library_stale_after(r.get("durable"), _created(cap.created_at))
        new = cap.model_copy(update=update) if update else cap
        if update:
            plan.note(f"capture: lane -> {lane}", cap.content)

        if reject_junk and new.review_status not in ("rejected",) and not new.superseded_by:
            if verdict == "junk":
                reason = r.get("rule") or f"lane classifier: junk (p={r['lane_verdict']['p']:.2f})"
                new = _reject(new, reason, "capture", audit_store, plan, plan.apply)
                plan.note("capture: reject junk", cap.content)
            elif r.get("duplicate_of"):
                new = _reject(new, "cross-source duplicate URL; longer copy kept", "capture",
                              audit_store, plan, plan.apply, superseded_by=r["duplicate_of"])
                plan.note("capture: supersede duplicate", cap.content)

        if new is not cap and plan.apply:
            path.write_text(new.model_dump_json(indent=2), encoding="utf-8")


def stamp_signals(corpus: Path, verdicts: dict[str, dict], plan: Plan, reject_junk: bool) -> None:
    doc_kind = {r["id"]: r["kind_verdict"]["choice"] for r in verdicts.values()
                if r["kind"] == "document" and r.get("kind_verdict")}
    audit_store = SignalAuditStore(audit_dir=corpus / "signals" / "audit", repo_root=corpus)
    for path in sorted((corpus / "signals").glob("*.json")):
        container = MeetingSignals.model_validate_json(path.read_text(encoding="utf-8"))
        kind = doc_kind.get(container.meeting_id)
        changed = False
        new_signals = []
        for sig in container.signals:
            r = verdicts.get(f"signal:{sig.id}")
            if kind is None:
                plan.note("signal: no document verdict (left as is)", sig.content)
                new_signals.append(sig)
                continue
            lane = DOC_KIND_TO_LANE[kind]
            update: dict = {}
            if lane == "library":
                # Same conversion live ingest applies: a third-party decision or
                # action item is an attributed claim, not ours, and has no owner.
                fields = library_claim_fields(
                    sig,
                    attributed_to=sig.source_meeting_title,
                    as_of=sig.source_timestamp,
                    stale_after=library_stale_after(None, _created(sig.created_at)),
                )
                update = {k: v for k, v in fields.items() if getattr(sig, k) != v}
            elif sig.lane != lane:
                update["lane"] = lane
            new = sig.model_copy(update=update) if update else sig
            if update:
                what = "claim" if "type" in update else f"lane -> {lane}"
                plan.note(f"signal: {what}", sig.content)

            if reject_junk and new.review_status != "rejected" and not new.superseded_by:
                if kind == "junk":
                    new = _reject(new, "source document is junk", "signal", audit_store, plan, plan.apply)
                    plan.note("signal: reject (junk document)", sig.content)
                elif r and r.get("standalone", 1.0) < NOT_STANDALONE:
                    new = _reject(new, f"not standalone (p={r['standalone']:.2f})", "signal",
                                  audit_store, plan, plan.apply)
                    plan.note("signal: reject (not standalone)", sig.content)

            changed = changed or new is not sig
            new_signals.append(new)
        if changed and plan.apply:
            container = container.model_copy(update={"signals": new_signals})
            path.write_text(container.model_dump_json(indent=2), encoding="utf-8")


def stamp_agent_memories(corpus: Path, verdicts: dict[str, dict], plan: Plan, reject_junk: bool) -> None:
    audit_store = capture_audit_store(repo_root=corpus)
    for path in sorted((corpus / "memory" / "agent").glob("*/*/*.json")):
        mem = AgentMemory.model_validate_json(path.read_text(encoding="utf-8"))
        r = verdicts.get(f"agent:{mem.id}")
        new = mem
        if reject_junk and r and r.get("lane") == "junk" and mem.review_status != "rejected":
            new = _reject(mem, r.get("rule") or "junk", "agent_memory", audit_store, plan, plan.apply)
            plan.note("agent memory: reject", mem.content)
        if new is not mem and plan.apply:
            path.write_text(new.model_dump_json(indent=2), encoding="utf-8")


_FRONTMATTER = re.compile(r"\A---\n(.*?\n)---\n", re.S)


def stamp_meetings(corpus: Path, verdicts: dict[str, dict], plan: Plan) -> None:
    """Line-level frontmatter edit (not a YAML re-dump) so diffs stay minimal."""
    library_ids = {r["id"] for r in verdicts.values() if r["kind"] == "document"
                   and (r.get("kind_verdict") or {}).get("choice") in ("third_party", "junk")}
    for path in sorted((corpus / "meetings").glob("**/*.md")):
        text = path.read_text(encoding="utf-8")
        m = _FRONTMATTER.match(text)
        if not m:
            continue
        fm = m.group(1)
        mid = re.search(r"^meeting_id:\s*\"?([^\"\n]+)", fm, re.M)
        if not mid or mid.group(1).strip() not in library_ids:
            continue
        if re.search(r"^lane:", fm, re.M):
            continue  # already stamped
        new_fm = re.sub(r"^participants:", "authors:", fm, flags=re.M) + "lane: library\n"
        plan.note("meeting: frontmatter lane -> library", path.name)
        if plan.apply:
            path.write_text(text[: m.start(1)] + new_fm + text[m.end(1):], encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, type=Path, help="corpus repo dir")
    ap.add_argument("--verdicts", required=True, type=Path)
    ap.add_argument("--reject-junk", action="store_true", help="also reject junk / supersede duplicates (audited)")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    verdicts = load_verdicts(args.verdicts)
    plan = Plan(args.apply)
    stamp_captures(args.corpus, verdicts, plan, args.reject_junk)
    stamp_signals(args.corpus, verdicts, plan, args.reject_junk)
    stamp_agent_memories(args.corpus, verdicts, plan, args.reject_junk)
    stamp_meetings(args.corpus, verdicts, plan)

    print(("APPLIED" if args.apply else "DRY RUN — nothing written") + f" · corpus {args.corpus}")
    for what, n in sorted(plan.counts.items()):
        print(f"  {n:>6}  {what}")
        for ex in plan.examples[what]:
            print(f"            e.g. {ex!r}")
    if args.apply:
        print("\nNext: POST /api/admin/backfill-memory-index so vector metadata carries the lane.")


if __name__ == "__main__":
    main()
