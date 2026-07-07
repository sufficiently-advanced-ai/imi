#!/usr/bin/env python3
"""rebuild_kb — rebuild a KB's derived state, or seed a KB from transcripts.

Two subcommands, both run INSIDE the app container (they import app code and
talk to Neo4j/Postgres/the signal store directly — the established pattern of
scripts/backfill_signal_index.py):

rebuild — wipe this tenant's derived state (Neo4j graph + vector index; the
    git corpus and audit JSONL are never touched) and regenerate it:

        python scripts/rebuild_kb.py rebuild --from signals   # cheap replay, no LLM
        python scripts/rebuild_kb.py rebuild --from source    # full Claude re-extraction
        python scripts/rebuild_kb.py rebuild --from signals --tenant org-acme

    --from signals replays the persisted signal store and reconstructs
    SUPERSEDES/CONFLICTS_WITH edges from persisted fields — confirmed reviews
    survive. --from source re-extracts every corpus meeting through the
    ingest pipeline; signal IDs change, so confirmed supersessions/conflicts
    reset to pending candidates needing re-review.

seed — ingest a folder of loose markdown/plain-text call transcripts, in
    chronological order (supersession detection depends on it):

        python scripts/rebuild_kb.py seed --folder /data/transcripts --dry-run
        python scripts/rebuild_kb.py seed --folder /data/transcripts --resume

    Per-file date resolution chain: YAML frontmatter (date/timestamp/
    occurred_at) → filename pattern (YYYY-MM-DD / YYYYMMDD / MM-DD-YYYY) →
    first ISO date in the first 50 lines → file mtime (WARNING: mtime makes
    supersession chronology unreliable — prefer frontmatter dates).
    Participants: frontmatter → Participants:/Attendees: section → speaker
    labels ("Name: …" line prefixes). Progress is checkpointed to
    <folder>/.kb_seed_manifest.json; --resume skips completed files.

Caveat: the running API process keeps in-memory caches (Neo4j compat cache;
FAISS in community mode). After a rebuild, restart the API container so it
sees the regenerated state. pgvector-backed vectors are live immediately.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logger = logging.getLogger("rebuild_kb")

MANIFEST_NAME = ".kb_seed_manifest.json"
MAX_CONTENT_BYTES = 500_000  # mirrors the /api/ingest size limit

# ---------------------------------------------------------------------------
# Metadata resolution (pure functions — unit-tested via importlib)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_DATE_KEYS = ("date", "timestamp", "occurred_at", "start_time", "updated_at")
_FILENAME_DATE_RES = (
    re.compile(r"(\d{4})-(\d{2})-(\d{2})"),  # YYYY-MM-DD
    re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"),  # YYYYMMDD
    re.compile(r"(?<!\d)(\d{2})-(\d{2})-(\d{4})(?!\d)"),  # MM-DD-YYYY
)
_CONTENT_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b")
_SPEAKER_LABEL_RE = re.compile(
    r"^([A-Z][\w.'-]+(?: [A-Z][\w.'-]+){0,3}):\s+\S", re.MULTILINE
)
# Lines that look like speaker labels but are transcript furniture
_SPEAKER_STOPWORDS = {
    "note",
    "notes",
    "agenda",
    "summary",
    "action",
    "actions",
    "decision",
    "decisions",
    "topic",
    "topics",
    "subject",
    "re",
    "date",
    "time",
    "participants",
    "attendees",
    "speaker",
    "transcript",
    "recording",
}


def parse_frontmatter_loose(text: str) -> dict:
    """Tolerant YAML-ish frontmatter parse (no PyYAML dependency).

    Handles ``key: value`` lines and simple block lists::

        participants:
          - Alice
          - Bob
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    out: dict = {}
    current_list_key: str | None = None
    for line in match.group(1).splitlines():
        item = re.match(r"\s+-\s*(.+)", line)
        if item and current_list_key:
            out.setdefault(current_list_key, [])
            if isinstance(out[current_list_key], list):
                out[current_list_key].append(item.group(1).strip().strip("'\""))
            continue
        kv = re.match(r"([A-Za-z_][\w-]*):\s*(.*)", line)
        if not kv:
            continue
        key, value = kv.group(1).strip().lower(), kv.group(2).strip()
        if not value:
            current_list_key = key
            out[key] = []
            continue
        current_list_key = None
        if value.startswith("[") and value.endswith("]"):
            out[key] = [
                v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()
            ]
        else:
            out[key] = value.strip("'\"")
    return out


def _validate_iso(candidate: str) -> str | None:
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).isoformat()
    except (ValueError, AttributeError):
        return None


def extract_date_from_filename(name: str) -> str | None:
    for i, pattern in enumerate(_FILENAME_DATE_RES):
        m = pattern.search(name)
        if not m:
            continue
        if i == 2:  # MM-DD-YYYY
            mm, dd, yyyy = m.groups()
        else:
            yyyy, mm, dd = m.groups()
        validated = _validate_iso(f"{yyyy}-{mm}-{dd}")
        if validated:
            return validated
    return None


def extract_date_from_content(text: str, max_lines: int = 50) -> str | None:
    head = "\n".join(text.splitlines()[:max_lines])
    for m in _CONTENT_DATE_RE.finditer(head):
        validated = _validate_iso(m.group(0).replace(" ", "T"))
        if validated:
            return validated
    return None


def extract_participants_heuristic(text: str) -> list[str]:
    """Participants from an Attendees/Participants section, else speaker labels."""
    section = re.search(
        r"^#*\s*(?:participants|attendees)\s*:?\s*$\n((?:\s*[-*]\s*.+\n?)+)",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if section:
        names = re.findall(r"[-*]\s*(.+)", section.group(1))
        return [n.strip() for n in names if n.strip()]
    inline = re.search(
        r"^(?:participants|attendees)\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE
    )
    if inline:
        return [n.strip() for n in inline.group(1).split(",") if n.strip()]
    # Speaker labels: "Alice Smith: said something"
    seen: list[str] = []
    for m in _SPEAKER_LABEL_RE.finditer(text):
        name = m.group(1).strip()
        if name.lower() in _SPEAKER_STOPWORDS or name in seen:
            continue
        seen.append(name)
    return seen[:25]


def resolve_file_metadata(path: Path, content: str) -> dict:
    """Resolve title/timestamp/participants for one transcript file.

    Returns: {title, timestamp, timestamp_source, participants, participants_source}
    timestamp_source ∈ frontmatter | filename | content | mtime.
    """
    fm = parse_frontmatter_loose(content)

    timestamp = None
    timestamp_source = None
    for key in _DATE_KEYS:
        if fm.get(key):
            timestamp = _validate_iso(str(fm[key]))
            if timestamp:
                timestamp_source = "frontmatter"
                break
    if not timestamp:
        timestamp = extract_date_from_filename(path.name)
        timestamp_source = "filename" if timestamp else None
    if not timestamp:
        timestamp = extract_date_from_content(content)
        timestamp_source = "content" if timestamp else None
    if not timestamp:
        timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
        timestamp_source = "mtime"

    participants: list[str] = []
    participants_source = "none"
    fm_participants = fm.get("participants") or fm.get("attendees")
    if fm_participants:
        if isinstance(fm_participants, str):
            fm_participants = [p.strip() for p in fm_participants.split(",")]
        participants = [p for p in fm_participants if p]
        participants_source = "frontmatter"
    else:
        participants = extract_participants_heuristic(content)
        participants_source = "heuristic" if participants else "none"

    title = fm.get("title") or path.stem.replace("_", " ").replace("-", " ").strip()

    return {
        "title": title,
        "timestamp": timestamp,
        "timestamp_source": timestamp_source,
        "participants": participants,
        "participants_source": participants_source,
    }


# ---------------------------------------------------------------------------
# Manifest (seed resumability)
# ---------------------------------------------------------------------------


def load_manifest(folder: Path) -> dict:
    path = folder / MANIFEST_NAME
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: unreadable manifest {path} ({e}) — starting fresh")
    return {}


def save_manifest(folder: Path, manifest: dict) -> None:
    (folder / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Tenant context
# ---------------------------------------------------------------------------


def set_tenant(tenant: str | None) -> str:
    from app.core.middleware.request_context import current_tenant_id

    if tenant:
        current_tenant_id.set(tenant)
    return current_tenant_id.get()


def confirm_or_exit(prompt: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    reply = input(f"{prompt} [y/N] ").strip().lower()
    if reply not in ("y", "yes"):
        print("Aborted.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# rebuild subcommand
# ---------------------------------------------------------------------------


async def _run_rebuild(args) -> int:
    tenant_id = set_tenant(args.tenant)

    from app.core.tenancy.context import current_tenant
    from app.neo4j_client import get_neo4j_client
    from app.services.graph.factory import get_knowledge_graph, get_semantica_knowledge
    from app.services.graph.signal_graph_writer import SignalGraphWriter
    from app.services.orchestrators.rebuild_orchestrator import RebuildOrchestrator

    client = get_neo4j_client()
    if not client.is_initialized:
        try:
            await client.initialize()
        except Exception as exc:
            print(f"ERROR: Neo4j unreachable — {exc}", file=sys.stderr)
            return 1

    ingest_orchestrator = None
    if args.tier == "source":
        ingest_orchestrator = _build_ingest_orchestrator()

    try:
        semantica = get_semantica_knowledge()
    except Exception:
        semantica = None

    orchestrator = RebuildOrchestrator(
        neo4j_client=client,
        knowledge_graph=get_knowledge_graph(),
        semantica=semantica,
        signal_writer=SignalGraphWriter(client),
        signal_store=current_tenant().signal_store,
        tenant_id=tenant_id,
        ingest_orchestrator=ingest_orchestrator,
    )

    from app.services.graph.factory import is_multi_tenant_graph_backend
    from app.services.graph.tenant_graph_wipe import tenant_match_clause

    match = tenant_match_clause("", not is_multi_tenant_graph_backend())
    rows = await client.execute_read(
        match + "RETURN count(n) AS nodes", {"tenant_id": tenant_id}
    )
    node_count = rows[0]["nodes"] if rows else 0
    print(f"Rebuild plan — tenant: {tenant_id}, tier: {args.tier}")
    print(f"  WIPES:  {node_count} graph nodes (tenant-scoped), vector index")
    print("  KEEPS:  git corpus (meetings/signals markdown+JSON), audit JSONL")
    if args.tier == "source":
        print(
            "  WARNING: re-extraction mints NEW signal IDs — confirmed\n"
            "           supersessions/conflicts reset to pending candidates."
        )
    if not args.dry_run:
        confirm_or_exit("Proceed with rebuild?", args.yes)

    job_id = f"rebuild-{uuid.uuid4().hex[:12]}"
    job_store: dict = {}
    result = await orchestrator.process(
        args.tier,
        job_id,
        job_store,
        dry_run=args.dry_run,
        signals_only_wipe=args.signals_only_wipe,
    )

    print(json.dumps(result, indent=2, default=str))
    if result.get("status") == "completed" and not args.dry_run:
        print(
            "\nNOTE: restart the API container so its in-memory graph/vector "
            "caches pick up the rebuilt state (pgvector is live already)."
        )
    return 0 if result.get("status") == "completed" else 1


def _build_ingest_orchestrator():
    """Mirror app/routes/ingest._run_ingestion_pipeline's construction (no SSE)."""
    from app.routes.ingest import (
        _get_claude_client,
        _get_extraction_tools,
        _get_git_ops,
        _get_graph_service,
        _get_signal_writer,
    )
    from app.services.ingest_classifier import IngestClassifier
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

    claude_client = _get_claude_client()
    git_ops = _get_git_ops()
    return IngestOrchestrator(
        classifier=IngestClassifier(claude_client=claude_client),
        claude_client=claude_client,
        graph=_get_graph_service(),
        signal_writer=_get_signal_writer(),
        git_ops=git_ops,
        tools=_get_extraction_tools(claude_client, git_ops),
    )


# ---------------------------------------------------------------------------
# seed subcommand
# ---------------------------------------------------------------------------


def _scan_folder(folder: Path, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(
        p
        for p in folder.glob(pattern)
        if p.is_file() and p.suffix.lower() in (".md", ".txt")
    )


async def _run_seed(args) -> int:
    set_tenant(args.tenant)
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"ERROR: {folder} is not a directory", file=sys.stderr)
        return 1

    # Standalone process: the Neo4j client is lazy and starts uninitialized.
    # Without this, ENRICH_GRAPH writes silently no-op (git persistence still
    # succeeds, but signals don't reach the graph until a later rebuild).
    if not args.dry_run:
        from app.neo4j_client import get_neo4j_client

        client = get_neo4j_client()
        if not client.is_initialized:
            try:
                await client.initialize()
            except Exception as exc:
                print(
                    f"WARNING: Neo4j unreachable ({exc}) — signals will be "
                    "persisted to git only; run 'rebuild --from signals' "
                    "later to populate the graph."
                )

    files = _scan_folder(folder, args.recursive)
    if not files:
        print(f"No .md/.txt files found in {folder}")
        return 0

    manifest = load_manifest(folder) if args.resume else {}

    # Resolve metadata for every file, then sort chronologically ascending —
    # supersession/conflict detection compares each meeting against prior ones.
    entries = []
    for path in files:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content.encode("utf-8", errors="replace")) > MAX_CONTENT_BYTES:
            print(
                f"  SKIP {path.name}: exceeds {MAX_CONTENT_BYTES // 1000}KB ingest limit"
            )
            continue
        meta = resolve_file_metadata(path, content)
        entries.append((path, content, meta))
    entries.sort(key=lambda e: e[2]["timestamp"])

    print(f"Seed plan — {len(entries)} files, chronological order:")
    mtime_warned = False
    for path, _content, meta in entries:
        marker = ""
        if meta["timestamp_source"] == "mtime":
            marker = "  ⚠ mtime"
            mtime_warned = True
        done = (
            " (done — will skip)"
            if manifest.get(path.name, {}).get("status") == "completed"
            else ""
        )
        print(
            f"  {meta['timestamp'][:19]}  [{meta['timestamp_source']:<11}] "
            f"{path.name}  participants={len(meta['participants'])}{marker}{done}"
        )
    if mtime_warned:
        print(
            "\nWARNING: files dated by mtime — supersession chronology may be "
            "wrong. Add a 'date:' frontmatter key for reliable ordering."
        )
    if args.dry_run:
        print("Dry run complete — nothing ingested.")
        return 0
    confirm_or_exit(f"Ingest {len(entries)} files?", args.yes)

    from app.models.ingestion.models import IngestRequest
    from app.routes.ingest import _run_ingestion_pipeline

    ok = dupes = failures = 0
    for i, (path, content, meta) in enumerate(entries, 1):
        if args.resume and manifest.get(path.name, {}).get("status") == "completed":
            print(f"[{i}/{len(entries)}] {path.name}: already completed — skipped")
            continue

        sha = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        request = IngestRequest(
            content=content,
            source_id=f"seed:{path.name}",
            title=meta["title"],
            participants=meta["participants"] or None,
            timestamp=meta["timestamp"],
        )
        job_id = f"seed-{uuid.uuid4().hex[:12]}"
        job_store: dict = {}
        try:
            result = await _run_ingestion_pipeline(request, job_id, job_store)
        except Exception as e:
            result = {"status": "failed", "error": str(e)}

        status = (result or {}).get("status", "failed")
        manifest[path.name] = {
            "sha256": sha,
            "job_id": job_id,
            "status": status,
            "bot_id": f"ingest-{(result or {}).get('content_hash', '')[:12]}",
        }
        save_manifest(folder, manifest)

        if status == "completed":
            ok += 1
            print(
                f"[{i}/{len(entries)}] {path.name}: completed — "
                f"{result.get('signals_written', 0)} signals, "
                f"{result.get('decisions_found', 0)} decisions"
            )
        elif status == "duplicate":
            dupes += 1
            print(f"[{i}/{len(entries)}] {path.name}: duplicate — skipped")
        else:
            failures += 1
            print(
                f"[{i}/{len(entries)}] {path.name}: FAILED — "
                f"{(result or {}).get('error', job_store.get(f'job:{job_id}', {}).get('error'))}"
            )
            if not args.continue_on_error:
                print(
                    "Stopping (use --continue-on-error to keep going; --resume to retry)."
                )
                return 1

    print(f"\nSeed complete: {ok} ingested, {dupes} duplicates, {failures} failed.")
    return 0 if failures == 0 else 1


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rebuild_kb",
        description="Rebuild a KB's derived state or seed it from transcripts",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rebuild = sub.add_parser("rebuild", help="Wipe + regenerate graph and embeddings")
    rebuild.add_argument(
        "--from",
        dest="tier",
        required=True,
        choices=("signals", "source"),
        help="signals: replay persisted signals (no LLM). source: re-extract via Claude.",
    )
    rebuild.add_argument(
        "--tenant", default=None, help="Tenant id (omit for single-tenant)"
    )
    rebuild.add_argument(
        "--signals-only-wipe",
        action="store_true",
        help="Wipe only :Signal nodes, keep entity nodes",
    )
    rebuild.add_argument(
        "--dry-run", action="store_true", help="Report counts, no writes"
    )
    rebuild.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    seed = sub.add_parser("seed", help="Ingest a folder of markdown/text transcripts")
    seed.add_argument(
        "--folder", required=True, help="Folder containing .md/.txt transcripts"
    )
    seed.add_argument(
        "--tenant", default=None, help="Tenant id (omit for single-tenant)"
    )
    seed.add_argument(
        "--recursive", action="store_true", help="Recurse into subfolders"
    )
    seed.add_argument(
        "--resume", action="store_true", help="Skip files completed in the manifest"
    )
    seed.add_argument(
        "--continue-on-error", action="store_true", help="Keep going past failed files"
    )
    seed.add_argument(
        "--dry-run", action="store_true", help="Show the plan, ingest nothing"
    )
    seed.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)
    if args.command == "rebuild":
        sys.exit(asyncio.run(_run_rebuild(args)))
    sys.exit(asyncio.run(_run_seed(args)))


if __name__ == "__main__":
    main()
