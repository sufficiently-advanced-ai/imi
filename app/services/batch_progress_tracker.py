"""
Progress tracking for batch uploads
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.services.batch_upload import BatchPhase, BatchStatus


@dataclass
class ProgressUpdate:
    """Progress update for a batch"""

    batch_id: str
    phase: BatchPhase
    files_completed: int
    total_files: int
    entities_found: dict[str, int] = field(default_factory=dict)
    current_file: str = ""
    message: str = ""
    timestamp: float = field(default_factory=time.time)


class BatchProgressTracker:
    """Track progress of batch uploads"""

    def __init__(self):
        self._batches: dict[str, dict[str, Any]] = {}
        self._subscribers: dict[str, list] = {}
        self._lock = asyncio.Lock()

    def update_progress(
        self,
        batch_id: str,
        phase: BatchPhase,
        files_completed: int,
        total_files: int,
        entities_found: dict[str, Any] | None = None,
        current_file: str = "",
        is_complete: bool = False,
    ):
        """Update progress for a batch"""
        update = {
            "phase": phase,
            "files_completed": files_completed,
            "total_files": total_files,
            "entities_found": entities_found or {},
            "current_file": current_file,
            "is_complete": is_complete,
            "last_update": time.time(),
        }

        if batch_id not in self._batches:
            self._batches[batch_id] = {"start_time": time.time(), "updates": []}

        self._batches[batch_id].update(update)
        self._batches[batch_id]["updates"].append(update)

        # Notify subscribers
        asyncio.create_task(self._notify_subscribers(batch_id, update))

    async def _notify_subscribers(self, batch_id: str, update: dict[str, Any]):
        """Notify all subscribers of an update"""
        async with self._lock:
            if batch_id in self._subscribers:
                for queue in self._subscribers[batch_id]:
                    await queue.put(update)

    async def get_updates(self, batch_id: str):
        """Get real-time updates for a batch"""
        queue = asyncio.Queue()

        async with self._lock:
            if batch_id not in self._subscribers:
                self._subscribers[batch_id] = []
            self._subscribers[batch_id].append(queue)

        try:
            while True:
                update = await queue.get()
                yield update

                if update.get("is_complete", False):
                    break
        finally:
            async with self._lock:
                if batch_id in self._subscribers:
                    self._subscribers[batch_id].remove(queue)
                    if not self._subscribers[batch_id]:
                        del self._subscribers[batch_id]

    def get_status(self, batch_id: str) -> dict[str, Any] | None:
        """Get current status of a batch"""
        if batch_id not in self._batches:
            return None

        batch = self._batches[batch_id]
        current = batch.copy()

        # Remove internal fields
        current.pop("updates", None)

        return BatchStatus(
            batch_id=batch_id,
            phase=current.get("phase", BatchPhase.VALIDATING),
            files_completed=current.get("files_completed", 0),
            total_files=current.get("total_files", 0),
            entities_found=current.get("entities_found", {}),
            current_file=current.get("current_file", ""),
            is_complete=current.get("is_complete", False),
        )

    def get_progress_percentage(self, batch_id: str) -> float:
        """Calculate progress percentage"""
        if batch_id not in self._batches:
            return 0.0

        batch = self._batches[batch_id]
        total = batch.get("total_files", 0)
        completed = batch.get("files_completed", 0)

        if total == 0:
            return 0.0

        return (completed / total) * 100.0

    def get_time_remaining(self, batch_id: str) -> float | None:
        """Estimate time remaining in seconds"""
        if batch_id not in self._batches:
            return None

        batch = self._batches[batch_id]

        # Need at least 2 updates to estimate
        if len(batch.get("updates", [])) < 2:
            return None

        batch["updates"]
        start_time = batch["start_time"]
        current_time = time.time()
        elapsed = current_time - start_time

        completed = batch.get("files_completed", 0)
        total = batch.get("total_files", 0)

        if completed == 0 or completed >= total:
            return None

        # Estimate based on current rate
        rate = completed / elapsed  # files per second
        remaining_files = total - completed

        return remaining_files / rate

    def cleanup_completed(self, max_age_seconds: int = 3600):
        """Clean up completed batches older than max_age"""
        current_time = time.time()
        to_remove = []

        for batch_id, batch in self._batches.items():
            if batch.get("is_complete", False):
                last_update = batch.get("last_update", 0)
                if current_time - last_update > max_age_seconds:
                    to_remove.append(batch_id)

        for batch_id in to_remove:
            del self._batches[batch_id]


# Global instance
progress_tracker = BatchProgressTracker()
