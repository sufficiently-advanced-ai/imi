"""Capture link files and the link audit log (ADR-011 §1, §6).

A capture's links live in ``memory/capture_links/<capture_id>.json`` beside the
governed capture record, which linking never rewrites. The graph is a
projection of these files (``app/services/graph/capture_graph_writer.py``), so
a clean rebuild loses nothing the linker wrote (invariant 2).

A link file also keeps the names the linker could not resolve. Read across
all files, those are the corroboration ledger: no separate store to drift.

Every link and entity a run writes (or, in shadow mode, would write) is also
appended to ``memory/audit/links-YYYY-MM.jsonl``.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.services.memory_capture import REPO_ROOT
from app.utils.atomic_write import atomic_write_text

logger = logging.getLogger(__name__)

# Bump when the linker changes enough that existing links should be redone:
# a link file older than this is "unlinked" again (ADR-011 §2).
LINK_VERSION = 1

# Fixed edge vocabulary (ADR-011 §4). Every edge but MENTIONED_IN runs
# Document -> target; MENTIONED_IN keeps the existing entity -> Document
# direction so the temporal reads see library mentions unchanged.
MENTIONED_IN = "MENTIONED_IN"
FROM_SOURCE = "FROM_SOURCE"
ABOUT = "ABOUT"
ADVANCES = "ADVANCES"
EVIDENCE_FOR = "EVIDENCE_FOR"
EDGE_TYPES = (MENTIONED_IN, FROM_SOURCE, ABOUT, ADVANCES, EVIDENCE_FOR)
STANCES = ("supports", "challenges", "context")

LINK_PHASE = "link_phase"
OWNER = "owner"
CAPTURE_TARGET = "capture"  # target_type of an owner ABOUT edge to another capture

_lock = threading.RLock()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class CaptureLink(BaseModel):
    edge: Literal["MENTIONED_IN", "FROM_SOURCE", "ABOUT", "ADVANCES", "EVIDENCE_FOR"]
    target_id: str
    target_type: str
    confidence: float = 1.0
    created_by: Literal["link_phase", "owner"] = LINK_PHASE
    run_id: str | None = None
    linked_at: str = Field(default_factory=now_iso)
    stance: Literal["supports", "challenges", "context"] | None = None

    def key(self) -> tuple[str, str]:
        return (self.edge, self.target_id)


class UnresolvedName(BaseModel):
    """A name below the bar: text on the document, and one row of the
    corroboration ledger. ``kind`` is ``mention`` (a create_types entity) or
    ``topic`` (an unlisted theme)."""

    name: str
    entity_type: str
    kind: Literal["mention", "topic"] = "mention"
    confidence: float = 1.0


class CaptureLinkFile(BaseModel):
    capture_id: str
    link_version: int = LINK_VERSION
    mode: Literal["shadow", "on", "owner"] = "on"
    run_id: str | None = None
    linked_at: str | None = None
    lane: str | None = None
    occurred_at: str | None = None  # the capture's source_date (ADR-004)
    source_key: str | None = None  # deterministic source identity, for corroboration
    links: list[CaptureLink] = Field(default_factory=list)
    unresolved: list[UnresolvedName] = Field(default_factory=list)
    reverted_runs: list[str] = Field(default_factory=list)

    def owner_links(self) -> list[CaptureLink]:
        return [link for link in self.links if link.created_by == OWNER]

    def is_linked(self, mode: str) -> bool:
        """Whether the LINK phase has nothing left to do for this capture.

        An owner-only file (a record note attached by ``about``) was never
        through the linker; neither was a shadow file once the mode is on."""
        if self.linked_at is None or self.link_version < LINK_VERSION:
            return False
        if self.mode == "owner":
            return False
        return not (mode == "on" and self.mode == "shadow")

    def projected(self) -> bool:
        """Whether the graph should hold this file's links: shadow files are
        audit-only (ADR-011 §2)."""
        return self.mode != "shadow" or bool(self.owner_links())

    def graph_links(self) -> list[CaptureLink]:
        if self.mode == "shadow":
            return self.owner_links()
        return list(self.links)


class CaptureLinkStore:
    def __init__(self, repo_root: Path = REPO_ROOT):
        self.repo_root = Path(repo_root)
        self.dir = self.repo_root / "memory" / "capture_links"

    def _path(self, capture_id: str) -> Path:
        if not capture_id or "/" in capture_id or "\\" in capture_id or capture_id in (".", ".."):
            raise ValueError(f"Invalid capture id: {capture_id!r}")
        return self.dir / f"{capture_id}.json"

    def relative_path(self, capture_id: str) -> str:
        return str(self._path(capture_id).relative_to(self.repo_root))

    def get(self, capture_id: str) -> CaptureLinkFile | None:
        path = self._path(capture_id)
        if not path.is_file():
            return None
        try:
            return CaptureLinkFile.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, OSError) as e:
            logger.warning("[LINK] Unreadable link file %s: %s", path.name, e)
            return None

    def save(self, record: CaptureLinkFile) -> Path:
        path = self._path(record.capture_id)
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, record.model_dump_json(indent=2, exclude_none=True))
        return path

    def mutate(
        self, capture_id: str, fn: Callable[[CaptureLinkFile], CaptureLinkFile | None]
    ) -> CaptureLinkFile | None:
        """Read-modify-write one link file under the store lock. ``fn`` gets
        the current file (a fresh owner-mode one when none exists) and returns
        the new record, or None to leave the file untouched."""
        with _lock:
            current = self.get(capture_id) or CaptureLinkFile(capture_id=capture_id, mode="owner")
            updated = fn(current.model_copy(deep=True))
            if updated is not None:
                self.save(updated)
            return updated

    def iter_all(self) -> Iterator[CaptureLinkFile]:
        if not self.dir.is_dir():
            return
        for path in sorted(self.dir.glob("*.json")):
            try:
                yield CaptureLinkFile.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, OSError) as e:
                logger.warning("[LINK] Skipping unreadable link file %s: %s", path.name, e)


class LinkAuditLog:
    """Append-only JSONL, one file per month (ADR-011 §6)."""

    def __init__(self, repo_root: Path = REPO_ROOT):
        self.repo_root = Path(repo_root)
        self.dir = self.repo_root / "memory" / "audit"

    def path(self, now: datetime | None = None) -> Path:
        return self.dir / f"links-{(now or datetime.now(UTC)).strftime('%Y-%m')}.jsonl"

    def relative_path(self, now: datetime | None = None) -> str:
        return str(self.path(now).relative_to(self.repo_root))

    def iter_rows(self) -> Iterator[dict[str, Any]]:
        """Every row of every month, oldest file first. Unreadable lines are skipped."""
        if not self.dir.is_dir():
            return
        for path in sorted(self.dir.glob("links-*.jsonl")):
            with path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        yield row

    def append_many(self, rows: list[dict[str, Any]], now: datetime | None = None) -> Path | None:
        if not rows:
            return None
        path = self.path(now)
        at = (now or datetime.now(UTC)).isoformat()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps({"at": at, **row}, default=str) + "\n")
        return path
