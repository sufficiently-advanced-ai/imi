#!/usr/bin/env python3
"""Event time on evidence (ADR-004): audit a corpus, or bring an existing one up to date.

Files are the source of truth, so the time data a rebuild needs must be in
them. This script works on the corpus files only; rebuild the graph afterwards
(``POST /api/admin/rebuild-graph`` or ``scripts/rebuild_kb.py``).

Audit
-----
    python scripts/event_time.py audit --corpus /app/repo

Reports, without changing anything:

  observation_without_time   an ingested document with no usable event time
  observation_fallback_now   event time is the ingest time (no date was found)
  observation_unrecorded     how the event time was obtained is not recorded
  observation_not_recorded   no recorded_at
  relationship_unattributed  a typed relationship with no assertion behind it
  assertion_without_time     an assertion with no occurred_at
  capture_without_date       a capture with no source_date
  capture_naive_date         a capture whose source_date has no timezone

Exit code 1 when any *blocking* gap is found (observation_without_time,
relationship_unattributed, assertion_without_time): those leave evidence out of
point-in-time queries.

Migrate
-------
    python scripts/event_time.py migrate --corpus /app/repo            # dry run
    python scripts/event_time.py migrate --corpus /app/repo --apply

  1. Adds ``recorded_at`` to observation documents that lack it, taken from
     the signal file's ``extracted_at`` (else the file's modification time).
  2. Attributes every typed relationship that has no assertion to the earliest
     observation document that names both entities, with
     ``time_source: inferred``. A relationship no document supports is
     reported and left unattributed.

Idempotent: a second run changes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.utils.event_time import (  # noqa: E402
    TIME_SOURCE_FALLBACK,
    TIME_SOURCE_INFERRED,
    TIME_SOURCE_UNRECORDED,
    assertions_for,
    document_event_time,
    is_observation_document,
    make_assertion,
    merge_assertion,
    read_assertions,
    to_iso,
    to_utc,
    validate_time_source,
)

BLOCKING = {"observation_without_time", "relationship_unattributed", "assertion_without_time"}
_SKIP_DIRS = {".git", "deltas", "signals", "memory", "node_modules"}


# ---------------------------------------------------------------------------
# Corpus reading
# ---------------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, str]:
    """``(metadata, raw frontmatter block, body)``; metadata None when absent."""
    if not text.startswith("---"):
        return None, "", text
    end = text.find("\n---", 3)
    if end == -1:
        return None, "", text
    raw = text[3:end].lstrip("\n")
    body = text[end + 4 :]
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None, raw, body
    return (meta if isinstance(meta, dict) else None), raw, body


def markdown_files(corpus: Path):
    for root, dirs, files in os.walk(corpus):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in sorted(files):
            if name.endswith(".md") and name != "README.md":
                yield Path(root) / name


def relationship_types(domain: Any) -> dict[str, set[str]]:
    """entity type -> relationship type names it may hold in frontmatter."""
    out: dict[str, set[str]] = {}
    for etype, edef in (domain.entities or {}).items():
        out[etype] = {r.type for r in (edef.relationships or [])}
    return out


def load_domain(name: str | None):
    import asyncio

    from app.core.domain_config.domain_config_service import get_domain_config_service

    service = get_domain_config_service()
    domain = asyncio.run(service.load_domain(name)) if name else service.get_active_domain()
    if domain is None:
        sys.exit("No domain configuration found (set ACTIVE_DOMAIN or pass --domain)")
    return domain


def entity_type_of(meta: dict[str, Any], types: dict[str, set[str]]) -> str | None:
    declared = str(meta.get("entity_type") or meta.get("type") or "").strip()
    if declared in types:
        return declared
    eid = str(meta.get("id") or "")
    for etype in sorted(types, key=len, reverse=True):
        if eid.startswith(f"{etype}-"):
            return etype
    return None


def targets_of(meta: dict[str, Any], rel_type: str) -> list[str]:
    value = meta.get(rel_type)
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, str) and v.strip()]


class Corpus:
    def __init__(self, root: Path, domain: Any):
        self.root = root
        self.types = relationship_types(domain)
        self.observations: list[dict[str, Any]] = []
        self.entities: list[dict[str, Any]] = []
        self.merged: dict[str, str] = {}
        for path in markdown_files(root):
            text = path.read_text(encoding="utf-8")
            meta, _raw, _body = split_frontmatter(text)
            if not meta:
                continue
            rel = str(path.relative_to(root))
            if is_observation_document(meta):
                self.observations.append({"path": path, "rel": rel, "meta": meta, "text": text})
                continue
            etype = entity_type_of(meta, self.types)
            if etype and not meta.get("is_archived"):
                self.entities.append(
                    {"path": path, "rel": rel, "meta": meta, "text": text, "type": etype}
                )
                for mid in meta.get("merged_ids") or []:
                    if isinstance(mid, str) and meta.get("id"):
                        self.merged[mid] = str(meta["id"])

    def canonical(self, entity_id: str) -> str:
        seen = {entity_id}
        while entity_id in self.merged and self.merged[entity_id] not in seen:
            entity_id = self.merged[entity_id]
            seen.add(entity_id)
        return entity_id

    def earliest_shared(self, a: str, b: str) -> dict[str, Any] | None:
        """Earliest dated observation that names both entities."""
        best = None
        for obs in self.observations:
            ids = {self.canonical(i) for i in obs["meta"].get("entity_ids") or [] if isinstance(i, str)}
            if a not in ids or b not in ids:
                continue
            when = document_event_time(obs["meta"]).get("occurred_at")
            if when is None:
                continue
            if best is None or when < best["occurred_at"]:
                best = {"occurred_at": when, "obs": obs}
        return best


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def audit(corpus: Corpus, root: Path) -> tuple[Counter, dict[str, list[str]]]:
    counts: Counter = Counter()
    examples: dict[str, list[str]] = {}

    def note(kind: str, where: str) -> None:
        counts[kind] += 1
        examples.setdefault(kind, [])
        if len(examples[kind]) < 5:
            examples[kind].append(where)

    counts["observations"] = len(corpus.observations)
    for obs in corpus.observations:
        meta = obs["meta"]
        event = document_event_time(meta)
        if not event:
            note("observation_without_time", obs["rel"])
            continue
        source = validate_time_source(meta.get("time_source"))
        if source == TIME_SOURCE_FALLBACK:
            note("observation_fallback_now", obs["rel"])
        elif source == TIME_SOURCE_UNRECORDED:
            note("observation_unrecorded", obs["rel"])
        if to_utc(meta.get("recorded_at")) is None:
            note("observation_not_recorded", obs["rel"])

    counts["entities"] = len(corpus.entities)
    for ent in corpus.entities:
        meta = ent["meta"]
        for rel_type in sorted(corpus.types.get(ent["type"], ())):
            for target in targets_of(meta, rel_type):
                counts["relationships"] += 1
                found = assertions_for(meta, rel_type, target)
                if not found:
                    note("relationship_unattributed", f"{ent['rel']}: {rel_type} -> {target}")
        for assertion in read_assertions(meta):
            counts["assertions"] += 1
            if to_utc(assertion.get("occurred_at")) is None:
                note(
                    "assertion_without_time",
                    f"{ent['rel']}: {assertion['type']} -> {assertion['target']}",
                )

    captures = root / "memory" / "captures"
    if captures.is_dir():
        for path in captures.rglob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            counts["captures"] += 1
            raw = data.get("source_date")
            when = to_utc(raw)
            if when is None:
                note("capture_without_date", str(path.relative_to(root)))
            elif isinstance(raw, str) and not _has_timezone(raw):
                note("capture_naive_date", str(path.relative_to(root)))
    return counts, examples


def _has_timezone(text: str) -> bool:
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def print_report(counts: Counter, examples: dict[str, list[str]]) -> int:
    print("Corpus")
    for key in ("observations", "entities", "relationships", "assertions", "captures"):
        print(f"  {key:<14} {counts.get(key, 0)}")
    gaps = [k for k in counts if k not in {"observations", "entities", "relationships", "assertions", "captures"}]
    if not gaps:
        print("\nNo gaps.")
        return 0
    print("\nGaps")
    for kind in sorted(gaps):
        flag = "BLOCKING" if kind in BLOCKING else "info"
        print(f"  {kind:<28} {counts[kind]:>6}  [{flag}]")
        for where in examples.get(kind, []):
            print(f"      {where}")
    return 1 if any(k in BLOCKING for k in gaps) else 0


# ---------------------------------------------------------------------------
# Migrate
# ---------------------------------------------------------------------------


def recorded_at_for(obs: dict[str, Any], root: Path) -> str:
    """When this observation was ingested: the signal file's extraction time,
    else the document's modification time."""
    bot_id = str(obs["meta"].get("bot_id") or "")
    signals = root / "signals" / f"meeting-{bot_id}.json"
    if bot_id and signals.is_file():
        try:
            extracted = json.loads(signals.read_text(encoding="utf-8")).get("extracted_at")
            iso = to_iso(extracted)
            if iso:
                return iso
        except (OSError, ValueError):
            pass
    return datetime.fromtimestamp(obs["path"].stat().st_mtime, tz=UTC).isoformat()


def add_frontmatter_line(text: str, after_keys: tuple[str, ...], line: str) -> str:
    """Insert ``line`` into the frontmatter after the last of ``after_keys``
    present (else before the closing fence). Text-level, so the rest of the
    file stays byte-identical."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return text
    at = close
    for i in range(1, close):
        if any(lines[i].startswith(f"{k}:") for k in after_keys):
            at = i + 1
    lines.insert(at, line)
    return "\n".join(lines)


def join_frontmatter(meta: dict[str, Any], body: str) -> str:
    """Same serialisation the graph's file write-through uses."""
    dumped = yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{dumped}---{body}"


def migrate(corpus: Corpus, root: Path, apply: bool) -> tuple[Counter, list[str]]:
    done: Counter = Counter()
    changed: list[str] = []

    for obs in corpus.observations:
        if to_utc(obs["meta"].get("recorded_at")) is not None:
            continue
        recorded = recorded_at_for(obs, root)
        done["observation_recorded_at"] += 1
        if apply:
            text = add_frontmatter_line(
                obs["text"], ("start_time", "updated_at"), f"recorded_at: {recorded}"
            )
            obs["path"].write_text(text, encoding="utf-8")
            obs["text"] = text
        obs["meta"]["recorded_at"] = recorded
        changed.append(obs["rel"])

    for ent in corpus.entities:
        meta = ent["meta"]
        holder = str(meta.get("id") or "")
        touched = False
        for rel_type in sorted(corpus.types.get(ent["type"], ())):
            for target in targets_of(meta, rel_type):
                if assertions_for(meta, rel_type, target):
                    continue
                shared = corpus.earliest_shared(corpus.canonical(holder), corpus.canonical(target))
                if shared is None:
                    done["relationship_no_evidence"] += 1
                    print(f"  no shared document: {ent['rel']}: {rel_type} -> {target}")
                    continue
                obs = shared["obs"]
                assertion = make_assertion(
                    rel_type,
                    target,
                    source_id=f"doc:{obs['rel']}",
                    occurred_at=shared["occurred_at"],
                    time_source=TIME_SOURCE_INFERRED,
                    recorded_at=obs["meta"].get("recorded_at"),
                )
                if merge_assertion(meta, assertion):
                    done["relationship_attributed"] += 1
                    touched = True
        if touched:
            changed.append(ent["rel"])
            if apply:
                _meta, _raw, body = split_frontmatter(ent["text"])
                ent["path"].write_text(join_frontmatter(meta, body), encoding="utf-8")
    return done, changed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["audit", "migrate"])
    ap.add_argument("--corpus", required=True, type=Path, help="corpus repo dir")
    ap.add_argument("--domain", help="domain id (default: the active domain)")
    ap.add_argument("--apply", action="store_true", help="migrate: write changes (default: dry run)")
    args = ap.parse_args()

    root = args.corpus.resolve()
    if not root.is_dir():
        sys.exit(f"Not a directory: {root}")
    corpus = Corpus(root, load_domain(args.domain))

    if args.command == "migrate":
        done, changed = migrate(corpus, root, args.apply)
        mode = "applied" if args.apply else "dry run"
        print(f"Migration ({mode})")
        for key in ("observation_recorded_at", "relationship_attributed", "relationship_no_evidence"):
            print(f"  {key:<28} {done.get(key, 0)}")
        print(f"  files {'changed' if args.apply else 'to change'}: {len(set(changed))}")
        for rel in sorted(set(changed)):
            print(f"      {rel}")
        if not args.apply:
            return 0
        corpus = Corpus(root, load_domain(args.domain))
        print()

    counts, examples = audit(corpus, root)
    return print_report(counts, examples)


if __name__ == "__main__":
    sys.exit(main())
