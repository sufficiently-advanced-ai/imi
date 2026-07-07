"""AgentMemoryStore — git-corpus persistence for agent memories (Phase 2).

Records live at ``memory/agent/YYYY/MM/{id}.json`` (sharded by created_at so
directories stay bounded under writeback volume). The idempotency-key index
is an in-process, lazily-built, rebuildable cache over the files — the plan's
answer to CaptureStore's O(n) rescan before Phase 2 volume.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from app.models.agent_memory import AgentMemory

logger = logging.getLogger(__name__)

REPO_ROOT = Path("/app/repo")
AGENT_DIR = REPO_ROOT / "memory" / "agent"


def _shard(created_at: str) -> tuple[str, str]:
    """(YYYY, MM) from an ISO timestamp; tolerant of odd values."""
    year, month = created_at[0:4], created_at[5:7]
    if year.isdigit() and month.isdigit():
        return year, month
    return "0000", "00"


class AgentMemoryStore:
    """Persist typed agent memories with idempotency-key lookup."""

    def __init__(self, agent_dir: Path = AGENT_DIR, repo_root: Path = REPO_ROOT):
        self.agent_dir = Path(agent_dir)
        self.repo_root = Path(repo_root)
        # idempotency_key -> file path; None until first lookup builds it.
        self._key_index: dict[str, Path] | None = None
        # id -> file path, maintained opportunistically (rebuildable).
        self._id_index: dict[str, Path] = {}

    # -- paths ---------------------------------------------------------------

    def _file_path(self, memory: AgentMemory) -> Path:
        year, month = _shard(memory.created_at)
        return self.agent_dir / year / month / f"{memory.id}.json"

    def relative_path(self, memory: AgentMemory) -> str:
        """Repo-relative path for git operations."""
        return str(self._file_path(memory).relative_to(self.repo_root))

    # -- iteration / indexing --------------------------------------------------

    def _iter_paths(self) -> Iterator[Path]:
        if not self.agent_dir.is_dir():
            return
        yield from sorted(self.agent_dir.glob("*/*/*.json"))

    def _load(self, path: Path) -> AgentMemory | None:
        try:
            return AgentMemory.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, OSError) as e:
            logger.warning("[AGENT_MEMORY] Skipping unreadable %s: %s", path.name, e)
            return None

    def _iter_memories(self) -> Iterator[AgentMemory]:
        for path in self._iter_paths():
            mem = self._load(path)
            if mem is not None:
                self._id_index[mem.id] = path
                yield mem

    def iter_all(self) -> Iterator[AgentMemory]:
        """Every readable agent memory, unfiltered (backfill / bulk re-index)."""
        yield from self._iter_memories()

    def _ensure_key_index(self) -> dict[str, Path]:
        if self._key_index is None:
            index: dict[str, Path] = {}
            for mem in self._iter_memories():
                if mem.idempotency_key:
                    index[mem.idempotency_key] = self._id_index[mem.id]
            self._key_index = index
        return self._key_index

    # -- CRUD ------------------------------------------------------------------

    def save(self, memory: AgentMemory) -> Path:
        path = self._file_path(memory)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(memory.model_dump_json(indent=2), encoding="utf-8")
        self._id_index[memory.id] = path
        if self._key_index is not None and memory.idempotency_key:
            self._key_index[memory.idempotency_key] = path
        return path

    def update(self, memory: AgentMemory) -> Path:
        """Overwrite a record (governance transitions keep created_at/shard)."""
        return self.save(memory)

    def get(self, memory_id: str) -> AgentMemory | None:
        cached = self._id_index.get(memory_id)
        if cached is not None and cached.is_file():
            return self._load(cached)
        for path in self._iter_paths():
            if path.stem == memory_id:
                return self._load(path)
        return None

    def find_by_idempotency_key(self, key: str) -> AgentMemory | None:
        path = self._ensure_key_index().get(key)
        return self._load(path) if path is not None else None

    def list(
        self,
        *,
        memory_type: str | None = None,
        review_status: str | None = None,
        runtime_name: str | None = None,
        task_id_prefix: str | None = None,
        limit: int = 50,
    ) -> list[AgentMemory]:
        """List agent memories, newest first, with exact-field filters."""
        records = [
            mem
            for mem in self._iter_memories()
            if (memory_type is None or mem.memory_type == memory_type)
            and (review_status is None or mem.review_status == review_status)
            and (runtime_name is None or mem.runtime_name == runtime_name)
            and (
                task_id_prefix is None
                or (mem.task_id or "").startswith(task_id_prefix)
            )
        ]
        records.sort(key=lambda m: m.created_at, reverse=True)
        return records[:limit]

    def count(
        self,
        *,
        memory_type: str | None = None,
        review_status: str | None = None,
        runtime_name: str | None = None,
        task_id_prefix: str | None = None,
    ) -> int:
        """Full match count for the same filters as ``list`` (pre-truncation)."""
        return sum(
            1
            for mem in self._iter_memories()
            if (memory_type is None or mem.memory_type == memory_type)
            and (review_status is None or mem.review_status == review_status)
            and (runtime_name is None or mem.runtime_name == runtime_name)
            and (
                task_id_prefix is None
                or (mem.task_id or "").startswith(task_id_prefix)
            )
        )
