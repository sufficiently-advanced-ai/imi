"""Corpus manifest — filesystem-keyed change detection for stateful startup.

With a persistent Neo4j volume there is no reason to wipe and rebuild the
graph from the corpus on every boot. But "trust the store" needs an answer to
*what changed while the container was down*. This module keys that off the
**filesystem**, not git history, because the corpus working tree routinely
contains files git has never seen (on LCARS: ~3.8k untracked ``memory/``
records) and "files are the source of truth" (CLAUDE.md invariant #2) means
the disk, not the index.

Mechanism:

* ``scan_corpus`` walks the corpus for markdown files and records
  ``(mtime_ns, size)`` per relative path — stat only, ~1 s for 12k files.
* The manifest is persisted beside the SQLite database (``DATABASE_PATH``'s
  directory), stamped with a ``build_id``.
* The same ``build_id`` is stored in Neo4j on a ``:_CorpusState`` node. If
  the two disagree (data dir replaced, graph wiped by hand) the manifest is
  not trusted and the current disk state is adopted as the new baseline.
* ``CorpusReconciler.reconcile`` diffs manifest vs disk and re-ingests only
  the added/modified files (removing graph artefacts for deleted ones), then
  writes the new manifest.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "corpus_manifest.json"
CORPUS_STATE_ID = "default"

FileStamp = tuple[int, int]  # (mtime_ns, size)


def is_markdown(path: str) -> bool:
    return path.lower().endswith(".md")


def scan_corpus(repo_path: str) -> dict[str, FileStamp]:
    """Stat every markdown file under ``repo_path`` (skipping ``.git``)."""
    stamps: dict[str, FileStamp] = {}
    root_path = os.path.abspath(repo_path)
    if not os.path.isdir(root_path):
        return stamps
    for root, dirs, files in os.walk(root_path):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            if not is_markdown(name):
                continue
            full = os.path.join(root, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root_path)
            stamps[rel] = (st.st_mtime_ns, st.st_size)
    return stamps


@dataclass
class ManifestDiff:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> list[str]:
        return self.added + self.modified

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.modified or self.removed)

    def counts(self) -> dict[str, int]:
        return {
            "added": len(self.added),
            "modified": len(self.modified),
            "removed": len(self.removed),
        }


def diff_stamps(old: dict[str, FileStamp], new: dict[str, FileStamp]) -> ManifestDiff:
    d = ManifestDiff()
    for path, stamp in new.items():
        prev = old.get(path)
        if prev is None:
            d.added.append(path)
        elif tuple(prev) != tuple(stamp):
            d.modified.append(path)
    for path in old:
        if path not in new:
            d.removed.append(path)
    d.added.sort()
    d.modified.sort()
    d.removed.sort()
    return d


@dataclass
class Manifest:
    build_id: str
    built_at: str
    files: dict[str, FileStamp]
    version: int = MANIFEST_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "build_id": self.build_id,
            "built_at": self.built_at,
            "files": {p: [s[0], s[1]] for p, s in self.files.items()},
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Manifest:
        files = {p: (int(v[0]), int(v[1])) for p, v in (data.get("files") or {}).items()}
        return cls(
            build_id=str(data.get("build_id", "")),
            built_at=str(data.get("built_at", "")),
            files=files,
            version=int(data.get("version", MANIFEST_VERSION)),
        )


def default_manifest_path() -> Path:
    from app.config import settings

    return Path(settings.DATABASE_PATH).parent / MANIFEST_FILENAME


def load_manifest(path: Path) -> Manifest | None:
    try:
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        manifest = Manifest.from_json(data)
        if manifest.version != MANIFEST_VERSION or not manifest.build_id:
            logger.warning("Corpus manifest at %s is unusable (version/build_id) — ignoring", path)
            return None
        return manifest
    except Exception as e:
        logger.warning("Could not read corpus manifest at %s: %s", path, e)
        return None


def save_manifest(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest.to_json(), f)
    os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


# ── Neo4j-side build marker ───────────────────────────────────────────────


async def read_graph_build_id(neo4j: Any) -> str | None:
    rows = await neo4j.execute_read(
        "MATCH (s:_CorpusState {id: $id}) RETURN s.build_id AS build_id",
        {"id": CORPUS_STATE_ID},
    )
    if not rows:
        return None
    value = rows[0].get("build_id") if isinstance(rows[0], dict) else None
    return str(value) if value else None


async def write_graph_build_id(neo4j: Any, build_id: str, extra: dict[str, Any] | None = None) -> None:
    props = {"build_id": build_id, "updated_at": _now_iso()}
    if extra:
        props.update(extra)
    await neo4j.execute_write(
        "MERGE (s:_CorpusState {id: $id}) SET s += $props",
        {"id": CORPUS_STATE_ID, "props": props},
    )


# ── Reconciler ────────────────────────────────────────────────────────────


class CorpusReconciler:
    """Bring the persistent graph up to date with the corpus on disk.

    ``kg`` must provide ``has_entities()``, ``ingest_files(paths)`` and
    ``remove_files(paths)`` (``Neo4jKnowledgeGraph``); ``sk`` (optional
    ``SemanticaKnowledge``) provides ``ingest_file(path, content)`` and
    ``remove_entities_for_file(path)``.
    """

    def __init__(
        self,
        *,
        kg: Any,
        neo4j: Any,
        repo_path: str,
        sk: Any | None = None,
        git_ops: Any | None = None,
        manifest_path: Path | None = None,
    ):
        self.kg = kg
        self.sk = sk
        self.neo4j = neo4j
        self.repo_path = repo_path
        self.git_ops = git_ops
        self.manifest_path = manifest_path or default_manifest_path()

    # -- baseline -----------------------------------------------------------

    async def record_full_build(self, *, source: str = "full_build") -> Manifest:
        """After a full rebuild: stamp Neo4j and write a fresh manifest."""
        build_id = uuid.uuid4().hex
        stamps = scan_corpus(self.repo_path)
        await write_graph_build_id(self.neo4j, build_id, {"source": source, "files": len(stamps)})
        manifest = Manifest(build_id=build_id, built_at=_now_iso(), files=stamps)
        save_manifest(self.manifest_path, manifest)
        logger.info(
            "[RECONCILE] baseline recorded: build_id=%s files=%d (%s)",
            build_id[:8], len(stamps), source,
        )
        return manifest

    # -- reconcile ----------------------------------------------------------

    async def reconcile(self) -> dict[str, Any]:
        """Diff manifest vs disk and apply the delta. Returns a summary dict."""
        graph_build_id = await read_graph_build_id(self.neo4j)
        manifest = load_manifest(self.manifest_path)

        if graph_build_id is None:
            if await self.kg.has_entities():
                # Graph exists but predates manifest support (or was built by a
                # rebuild-mode boot). Adopt the current disk state as baseline.
                return await self._adopt("graph_without_build_id")
            return {"action": "full_rebuild_required", "reason": "graph_empty"}

        if manifest is None or manifest.build_id != graph_build_id:
            reason = "manifest_missing" if manifest is None else "manifest_mismatch"
            return await self._adopt(reason, graph_build_id=graph_build_id)

        disk = scan_corpus(self.repo_path)
        delta = diff_stamps(manifest.files, disk)
        if delta.is_empty:
            return {"action": "unchanged", "files": len(disk)}

        logger.info("[RECONCILE] corpus changed since last build: %s", delta.counts())
        summary: dict[str, Any] = {"action": "reconciled", **delta.counts()}
        failed: set[str] = set()  # keep their old stamps so the next boot retries

        if delta.removed:
            try:
                summary["graph_removed"] = await self.kg.remove_files(delta.removed)
            except Exception as e:
                logger.warning("[RECONCILE] removing %d files from graph failed: %s", len(delta.removed), e)
                summary["graph_removed_error"] = str(e)
                failed.update(delta.removed)

        if delta.changed:
            try:
                summary["graph_ingested"] = await self.kg.ingest_files(delta.changed)
            except Exception as e:
                logger.warning("[RECONCILE] ingesting %d files into graph failed: %s", len(delta.changed), e)
                summary["graph_ingest_error"] = str(e)
                failed.update(delta.changed)

            if self.sk is not None and hasattr(self.sk, "ingest_file"):
                indexed = 0
                for path in delta.changed:
                    try:
                        content = await self._read(path)
                        if content is None:
                            continue
                        if await self.sk.ingest_file(path, content):
                            indexed += 1
                    except Exception as e:
                        logger.debug("[RECONCILE] semantica ingest skipped for %s: %s", path, e)
                summary["semantica_indexed"] = indexed

        # A full rebuild may have run concurrently (admin endpoint) and written
        # a new baseline; never overwrite it with a manifest for the old graph.
        current_build_id = await read_graph_build_id(self.neo4j)
        if current_build_id != graph_build_id:
            logger.warning(
                "[RECONCILE] graph build_id changed during reconcile (%s → %s); "
                "leaving the newer manifest in place",
                graph_build_id[:8], (current_build_id or "none")[:8],
            )
            summary["manifest"] = "skipped_build_id_changed"
            return summary

        # Retain the previous stamp for anything that failed so the next
        # reconcile sees it as changed again instead of silently moving on.
        next_files = dict(disk)
        for path in failed:
            prev = manifest.files.get(path)
            if prev is not None:
                next_files[path] = prev
            else:
                next_files.pop(path, None)
        if failed:
            summary["retry_next_boot"] = len(failed)
        save_manifest(
            self.manifest_path,
            Manifest(build_id=graph_build_id, built_at=_now_iso(), files=next_files),
        )
        return summary

    async def _adopt(self, reason: str, graph_build_id: str | None = None) -> dict[str, Any]:
        """Bless the current disk state as the baseline for the existing graph.

        Deliberately loud: this is the one moment where files the graph has
        never seen (e.g. thousands of uncommitted memory records) become
        canonical without being ingested, so the counts must be visible.
        """
        entities = await self._count_entities()
        manifest = await self.record_full_build(source=reason)
        logger.warning(
            "[RECONCILE] ADOPTED disk state as baseline (%s%s): %d markdown files on disk, "
            "%s entities in Neo4j, new build_id=%s. Files changed before this point were NOT "
            "ingested — run POST /api/admin/rebuild-graph if the graph looks stale.",
            reason,
            f", previous graph build_id={graph_build_id[:8]}" if graph_build_id else "",
            len(manifest.files),
            entities if entities is not None else "?",
            manifest.build_id[:8],
        )
        return {
            "action": "adopted",
            "reason": reason,
            "files": len(manifest.files),
            "entities": entities,
        }

    async def _count_entities(self) -> int | None:
        try:
            rows = await self.neo4j.execute_read(
                "MATCH (n:Entity) WHERE NOT n:Signal RETURN count(n) AS n"
            )
            if rows and isinstance(rows[0], dict) and "n" in rows[0]:
                return int(rows[0]["n"])
        except Exception as e:
            logger.debug("[RECONCILE] entity count unavailable: %s", e)
        return None

    async def _read(self, rel_path: str) -> str | None:
        full = os.path.join(self.repo_path, rel_path)
        try:
            with open(full, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None
