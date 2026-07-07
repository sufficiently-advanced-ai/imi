"""
Signal Store — Persistence layer for meeting signals.

Reads and writes batched signal JSON files stored per-meeting at:
    {repo}/signals/meeting-{bot_id}.json
"""

import json
import logging
from datetime import date
from pathlib import Path

from app.models.signal import MeetingSignals, Signal

logger = logging.getLogger(__name__)

VALID_SIGNAL_STATUSES = {"open", "in_progress", "done"}

SIGNALS_DIR = Path("/app/repo/signals")


class SignalStore:
    """Read/write signal JSON files from the repo signals directory."""

    def __init__(self, signals_dir: Path = SIGNALS_DIR):
        self.signals_dir = signals_dir

    def _file_path(self, bot_id: str) -> Path:
        return self.signals_dir / f"meeting-{bot_id}.json"

    def relative_path(self, bot_id: str) -> str:
        """Return the repo-relative path for git operations."""
        return f"signals/meeting-{bot_id}.json"

    def exists(self, bot_id: str) -> bool:
        return self._file_path(bot_id).is_file()

    def save(self, meeting_signals: MeetingSignals) -> Path:
        """Write meeting signals to disk. Creates the signals/ dir if needed."""
        self.signals_dir.mkdir(parents=True, exist_ok=True)
        path = self._file_path(meeting_signals.bot_id)
        path.write_text(meeting_signals.model_dump_json(indent=2), encoding="utf-8")
        logger.info(
            f"[SIGNALS] Saved {meeting_signals.signal_count} signals to {path.name}"
        )
        # G3 wiring: index-on-write (best-effort — search must lag, not break, saves)
        try:
            from app.services.signal_indexing import index_meeting_signals
            index_meeting_signals(meeting_signals)
        except Exception:  # pragma: no cover - indexing must never fail a save
            logger.warning("signal indexing skipped", exc_info=True)
        return path

    def load(self, bot_id: str) -> MeetingSignals | None:
        """Load signals for a single meeting. Returns None on missing/corrupt files."""
        path = self._file_path(bot_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return MeetingSignals.model_validate(data)
        except Exception as e:
            logger.warning(f"[SIGNALS] Failed to parse {path.name}: {e}")
            return None

    def load_all(self) -> list[MeetingSignals]:
        """Load all meeting signal files, sorted by extracted_at descending.

        Skips any files that fail to parse.
        """
        if not self.signals_dir.is_dir():
            return []

        results: list[MeetingSignals] = []
        for path in sorted(self.signals_dir.glob("meeting-*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                ms = MeetingSignals.model_validate(data)
                results.append(ms)
            except Exception as e:
                logger.warning(f"[SIGNALS] Skipping {path.name}: {e}")
                continue

        # Sort newest first
        results.sort(key=lambda ms: ms.extracted_at, reverse=True)
        return results

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def find_signal_by_id(self, signal_id: str) -> tuple[Signal, MeetingSignals] | None:
        """Find a signal by its ID across all meeting files.

        Returns (signal, container) on first match, or None if not found.
        """
        for ms in self.load_all():
            for signal in ms.signals:
                if signal.id == signal_id:
                    return signal, ms
        return None

    def update_signal(
        self,
        signal_id: str,
        *,
        status: str | None = None,
        content: str | None = None,
        owner_id: str | None = None,
        owner_name: str | None = None,
        owner_type: str | None = None,
        due_date: str | None = None,
    ) -> Signal | None:
        """Update fields on an existing signal and persist to disk.

        Args:
            signal_id: The signal's UUID5 identifier.
            status: New status (must be one of VALID_SIGNAL_STATUSES).
            content: New content text.
            owner_id: New owner entity ID.
            owner_name: New owner display name (used with owner_id).
            owner_type: New owner entity type (used with owner_id).
            due_date: New due date string.

        Returns:
            The updated Signal, or None if not found.

        Raises:
            ValueError: If status is not a valid value, signal_id is empty,
                owner_id is empty, or due_date is not YYYY-MM-DD.
        """
        if not signal_id or not signal_id.strip():
            raise ValueError("signal_id must be a non-empty string")
        if owner_id is not None and not owner_id.strip():
            raise ValueError("owner_id must be a non-empty string")
        if due_date is not None:
            try:
                date.fromisoformat(due_date)
            except ValueError:
                raise ValueError("due_date must be in YYYY-MM-DD format")
        if status is not None and status not in VALID_SIGNAL_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_SIGNAL_STATUSES))}"
            )

        result = self.find_signal_by_id(signal_id)
        if result is None:
            return None

        signal, container = result

        # Build update dict for model_copy
        updates: dict = {}
        if status is not None:
            updates["status"] = status
        if content is not None:
            updates["content"] = content
        if due_date is not None:
            updates["due_date"] = due_date
        if owner_id is not None:
            from app.models.signal import EntityRef

            updates["owner"] = EntityRef(
                id=owner_id,
                name=owner_name or owner_id.split("-", 1)[-1].replace("-", " ").title(),
                type=owner_type or "person",
            )

        if not updates:
            return signal  # Nothing to change

        updated_signal = signal.model_copy(update=updates)

        # Replace in the container's signal list
        container.signals = [
            updated_signal if s.id == signal_id else s for s in container.signals
        ]
        # save() already indexes the whole container (index-on-write), which
        # covers the updated signal — a separate index_one here would append a
        # duplicate vector on every update (FAISS has no upsert).
        self.save(container)

        logger.info(f"[SIGNALS] Updated signal {signal_id}: {list(updates.keys())}")
        return updated_signal

    def replace_signal(self, new_signal: Signal, container: MeetingSignals) -> None:
        """Replace a signal in its container and persist to disk.

        Used by the governance transition path in chat_tools.update_signal to
        persist the result of review_with_audit without mutating field-by-field.
        The container must already contain a signal with new_signal.id; if not,
        the signal is appended (defensive, should not occur in normal flow).
        """
        signal_id = new_signal.id
        found = any(s.id == signal_id for s in container.signals)
        if found:
            container.signals = [
                new_signal if s.id == signal_id else s for s in container.signals
            ]
        else:
            # Defensive — should not occur in normal governance flow
            logger.warning(
                "[SIGNALS] replace_signal: %s not found in container %s — appending",
                signal_id, container.bot_id,
            )
            container.signals.append(new_signal)
        self.save(container)
        logger.info("[SIGNALS] Replaced signal %s in %s", signal_id, container.bot_id)

    def delete_signal(self, signal_id: str) -> Signal | None:
        """Remove a signal from its meeting file and persist to disk.

        Returns the deleted Signal for confirmation, or None if not found.
        """
        result = self.find_signal_by_id(signal_id)
        if result is None:
            return None

        signal, container = result

        # Filter out the signal
        container.signals = [s for s in container.signals if s.id != signal_id]
        object.__setattr__(container, "signal_count", len(container.signals))
        self.save(container)

        logger.info(f"[SIGNALS] Deleted signal {signal_id} from {container.bot_id}")
        return signal


# Tenant-scoped module global (mirrors app/git_ops.py _ContainerProxy pattern).
# ``signal_store`` resolves to the CURRENT tenant's SignalStore at call time so
# every consumer that imports and uses it (signal_feed, chat_tools, etc.) is
# automatically isolated per tenant.  In single-tenant mode ``current_tenant()``
# always returns the one default container — behavior is byte-identical.
from app.core.tenancy.proxy import _ContainerProxy  # noqa: E402

signal_store = _ContainerProxy(lambda c: c.signal_store, "signal_store")
