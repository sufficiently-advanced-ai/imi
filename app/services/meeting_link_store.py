"""Meeting link files (ADR-013 §5).

A call's storyline excerpts live in ``memory/meeting_links/<meeting file
stem>.json``, written by the LINK run. The graph is a projection of these
files (``app/services/graph/meeting_graph_writer.py``): a clean rebuild
reproduces every meeting ``ADVANCES`` edge from them (CLAUDE.md invariant 2).

The meeting file itself is never rewritten by linking.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.memory_capture import REPO_ROOT
from app.utils.atomic_write import atomic_write_text

logger = logging.getLogger(__name__)

# Bump when the meeting linker changes enough that existing files should be
# redone: a file below this is eligible again (ADR-013 §6).
MEETING_LINK_VERSION = 1

_lock = threading.RLock()


class Speaker(BaseModel):
    name: str | None = None
    id: str | None = None
    basis: Literal["label", "sole_other", "unknown"] = "unknown"


class PresentPerson(BaseModel):
    name: str
    id: str | None = None


class Excerpt(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    storyline_id: str
    stance: Literal["supports", "challenges", "context"]
    confidence: float
    claim: str = ""
    lines: list[int]  # [first, last], 1-based, inclusive
    quote: str
    speaker: Speaker = Field(default_factory=Speaker)
    # None = not known; reads never treat it as False (ADR-013 §2).
    speaker_is_owner: bool | None = None
    with_: list[PresentPerson] = Field(default_factory=list, alias="with")
    with_basis: Literal["participants", "unknown"] = "unknown"
    run_id: str | None = None
    linked_at: str | None = None


class MeetingUndo(BaseModel):
    """What one run replaced in a file, so ``revert_run`` can put it back."""

    excerpts: list[Excerpt] = Field(default_factory=list)
    storylines_checked: dict[str, str] = Field(default_factory=dict)
    # Set when the run re-linked the whole file: the file's state before it,
    # restored by a revert (a shadow file re-linked in mode on is shadow again,
    # and a meeting whose transcript changed is eligible again).
    mode: Literal["shadow", "on"] | None = None
    link_version: int | None = None
    run_id: str | None = None
    linked_at: str | None = None
    transcript_sha256: str | None = None
    attribution_sha256: str | None = None


class MeetingLinkFile(BaseModel):
    meeting_path: str
    document_id: str
    link_version: int = MEETING_LINK_VERSION
    mode: Literal["shadow", "on"] = "on"
    run_id: str | None = None
    linked_at: str | None = None
    occurred_at: str | None = None
    transcript_sha256: str | None = None
    attribution_sha256: str | None = None
    orphaned_at: str | None = None
    # Every storyline the call was judged against -> the run that judged it,
    # whether or not an excerpt qualified.
    storylines_checked: dict[str, str] = Field(default_factory=dict)
    excerpts: list[Excerpt] = Field(default_factory=list)
    undo: dict[str, MeetingUndo] = Field(default_factory=dict)
    reverted_runs: list[str] = Field(default_factory=list)

    @property
    def stem(self) -> str:
        return self.meeting_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    def projected(self) -> bool:
        """Whether the graph should hold this file's edges: shadow files and
        orphaned meetings project nothing."""
        return self.mode == "on" and self.orphaned_at is None


def _stem_ok(stem: str) -> bool:
    return bool(stem) and "/" not in stem and "\\" not in stem and stem not in (".", "..")


class MeetingLinkStore:
    def __init__(self, repo_root: Path = REPO_ROOT):
        self.repo_root = Path(repo_root)
        self.dir = self.repo_root / "memory" / "meeting_links"

    def _path(self, stem: str) -> Path:
        if not _stem_ok(stem):
            raise ValueError(f"Invalid meeting stem: {stem!r}")
        return self.dir / f"{stem}.json"

    def relative_path(self, stem: str) -> str:
        return str(self._path(stem).relative_to(self.repo_root))

    def get(self, stem: str) -> MeetingLinkFile | None:
        path = self._path(stem)
        if not path.is_file():
            return None
        try:
            return MeetingLinkFile.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, OSError) as e:
            logger.warning("[LINK] Unreadable meeting link file %s: %s", path.name, e)
            return None

    def exists(self, stem: str) -> bool:
        return self._path(stem).exists()

    def save(self, record: MeetingLinkFile) -> Path:
        path = self._path(record.stem)
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, record.model_dump_json(indent=2, exclude_none=True, by_alias=True))
        return path

    def mutate(self, stem: str, fn: Callable[[MeetingLinkFile | None], MeetingLinkFile | None]
               ) -> MeetingLinkFile | None:
        """Read-modify-write one file under the store lock. ``fn`` gets a copy
        of the current file (None when there is none) and returns the new one,
        or None to leave it untouched. An unreadable file raises ValueError
        rather than being replaced."""
        with _lock:
            current = self.get(stem)
            if current is None and self._path(stem).exists():
                raise ValueError(f"Meeting link file for {stem!r} is unreadable; not overwriting it")
            updated = fn(current.model_copy(deep=True) if current is not None else None)
            if updated is not None:
                self.save(updated)
            return updated

    def iter_all(self) -> Iterator[MeetingLinkFile]:
        if not self.dir.is_dir():
            return
        for path in sorted(self.dir.glob("*.json")):
            try:
                yield MeetingLinkFile.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, OSError) as e:
                logger.warning("[LINK] Skipping unreadable meeting link file %s: %s", path.name, e)
