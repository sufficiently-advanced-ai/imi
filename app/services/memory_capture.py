"""General memory capture layer (G4 of the memory-governance PRD).

Brings openbrain's ``sources`` + fingerprint-dedup model into imi as a
capture surface for web / mail / manual content, distinct from the meeting
ingest pipeline but feeding the *same* governance ladder: captured content
enters as ``imported`` evidence (not instruction-grade until confirmed).

  - ``content_fingerprint`` — normalized sha256 dedup key, mirroring openbrain's
    ``normalizeForFingerprint`` (lowercase, collapse whitespace, trim).
  - ``capture_memory`` — build a CapturedMemory with its fingerprint.
  - ``CaptureStore`` — persist captures and dedup by (source, source_id) and by
    content fingerprint (advisory, like openbrain — duplicates are folded, not
    rejected).

See docs/prd/memory-governance-and-retrieval-prd.md §8 (G4).
"""

# Postponed annotation evaluation: CaptureStore defines a ``list`` method,
# which would otherwise shadow the builtin in later method annotations.
from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.models.captured_memory import CapturedMemory

logger = logging.getLogger(__name__)

REPO_ROOT = Path("/app/repo")
CAPTURE_DIR = REPO_ROOT / "memory" / "captures"

# Guards the dedup check-then-save against thread-executor callers (the
# section is synchronous, so it is already atomic on the event loop).
# Cross-process writers are out of scope: the git corpus assumes a single
# writer per repo, and fingerprint dedup is advisory by design (openbrain).
_capture_write_lock = threading.Lock()


def content_fingerprint(text: str) -> str:
    """Normalized sha256 of content for advisory dedup.

    Normalization mirrors openbrain ``normalizeForFingerprint``: lowercase,
    collapse all whitespace runs to a single space, and trim. JS and this
    implementation must stay in lockstep.
    """
    normalized = " ".join(text.split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def capture_memory(
    content: str,
    source: str,
    source_id: str | None = None,
    *,
    tenant_id: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
    source_date: str | None = None,
) -> CapturedMemory:
    """Build a CapturedMemory (imported, evidence-grade) with its fingerprint."""
    return CapturedMemory(
        content=content,
        source=source,
        source_id=source_id,
        tenant_id=tenant_id,
        summary=summary,
        tags=tags or [],
        source_date=source_date,
        content_fingerprint=content_fingerprint(content),
        provenance_status="imported",
    )


class CaptureResult(BaseModel):
    """Outcome of a capture attempt."""

    deduped: bool
    memory: CapturedMemory


class CaptureStore:
    """Persist captured memories and dedup re-captures.

    Dedup precedence mirrors openbrain: an external (source, source_id) match is
    a hard duplicate; otherwise a content-fingerprint match is an advisory
    duplicate. In both cases the existing record is returned unchanged.
    """

    def __init__(self, capture_dir: Path = CAPTURE_DIR, repo_root: Path = REPO_ROOT):
        self.capture_dir = Path(capture_dir)
        self.repo_root = Path(repo_root)

    def _file_path(self, memory_id: str) -> Path:
        return self.capture_dir / f"{memory_id}.json"

    def relative_path(self, memory_id: str) -> str:
        """Repo-relative path for git operations, derived from capture_dir."""
        return str(self._file_path(memory_id).relative_to(self.repo_root))

    def _iter_memories(self) -> Iterator[CapturedMemory]:
        if not self.capture_dir.is_dir():
            return
        for path in sorted(self.capture_dir.glob("*.json")):
            try:
                yield CapturedMemory.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (ValidationError, OSError) as e:
                # ValidationError: malformed JSON or schema/invariant failure;
                # OSError: file unreadable/removed mid-scan. Other errors surface.
                logger.warning(
                    "[CAPTURE] Skipping unreadable capture %s: %s", path.name, e
                )

    def iter_all(self) -> Iterator[CapturedMemory]:
        """Every readable capture, unfiltered (backfill / bulk re-index)."""
        yield from self._iter_memories()

    def _find_existing(
        self, source: str, source_id: str | None, fingerprint: str
    ) -> CapturedMemory | None:
        for mem in self._iter_memories():
            if source_id and mem.source == source and mem.source_id == source_id:
                return mem
            if mem.content_fingerprint == fingerprint:
                return mem
        return None

    def _save(self, memory: CapturedMemory) -> Path:
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        path = self._file_path(memory.id)
        path.write_text(memory.model_dump_json(indent=2), encoding="utf-8")
        return path

    def get(self, memory_id: str) -> CapturedMemory | None:
        """Load a single capture by id, or None if absent/unreadable."""
        path = self._file_path(memory_id)
        if not path.is_file():
            return None
        try:
            return CapturedMemory.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (ValidationError, OSError) as e:
            logger.warning("[CAPTURE] Unreadable capture %s: %s", path.name, e)
            return None

    def update(self, memory: CapturedMemory) -> Path:
        """Overwrite a capture record (enrichment, governance transitions)."""
        return self._save(memory)

    def list(
        self,
        *,
        review_status: str | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> list[CapturedMemory]:
        """List captures, newest first, with optional exact-field filters."""
        records = [
            mem
            for mem in self._iter_memories()
            if (review_status is None or mem.review_status == review_status)
            and (source is None or mem.source == source)
        ]
        records.sort(key=lambda m: m.created_at, reverse=True)
        return records[:limit]

    def count(
        self,
        *,
        review_status: str | None = None,
        source: str | None = None,
    ) -> int:
        """Full match count for the same filters as ``list`` (pre-truncation)."""
        return sum(
            1
            for mem in self._iter_memories()
            if (review_status is None or mem.review_status == review_status)
            and (source is None or mem.source == source)
        )

    def capture(
        self,
        content: str,
        source: str,
        source_id: str | None = None,
        *,
        tenant_id: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
        source_date: str | None = None,
    ) -> CaptureResult:
        """Capture content, returning the existing record if it is a duplicate."""
        fingerprint = content_fingerprint(content)
        with _capture_write_lock:
            existing = self._find_existing(source, source_id, fingerprint)
            if existing is not None:
                return CaptureResult(deduped=True, memory=existing)

            memory = capture_memory(
                content,
                source,
                source_id,
                tenant_id=tenant_id,
                summary=summary,
                tags=tags,
                source_date=source_date,
            )
            self._save(memory)
        return CaptureResult(deduped=False, memory=memory)
