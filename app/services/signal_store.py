"""
Signal Store — Persistence layer for meeting signals.

Reads and writes batched signal JSON files stored per-meeting at:
    {repo}/signals/meeting-{bot_id}.json
"""

import json
import logging
import os
import threading
from datetime import date
from pathlib import Path

from app.models.signal import MeetingSignals, Signal, SignalAuditRecord
from app.utils.atomic_write import atomic_write_text

logger = logging.getLogger(__name__)

VALID_SIGNAL_STATUSES = {"open", "in_progress", "done"}

SIGNALS_DIR = Path("/app/repo/signals")

# One re-entrant lock per container file (keyed by absolute path, so every
# SignalStore instance and tenant pointing at the same file shares it). Held
# across a read-modify-write of a container so concurrent updates to signals in
# the same meeting file cannot overwrite each other. A threading lock works for
# both sync callers and async callers (which run these sync methods on the
# event loop or in a worker thread); callers must never await while holding it.
_CONTAINER_LOCKS: dict[str, threading.RLock] = {}
_CONTAINER_LOCKS_GUARD = threading.Lock()


def _lock_for_path(path: Path) -> threading.RLock:
    key = os.path.abspath(path)
    with _CONTAINER_LOCKS_GUARD:
        lock = _CONTAINER_LOCKS.get(key)
        if lock is None:
            lock = _CONTAINER_LOCKS[key] = threading.RLock()
        return lock


def remap_entity_refs(
    meeting_signals: MeetingSignals,
    id_map: dict[str, str],
    names: dict[str, str] | None = None,
) -> bool:
    """Rewrite entity references in a meeting's signals after resolution or a
    merge: ids through ``id_map`` (entities, owner, client_id), display names
    from ``names`` (keyed by the final id), duplicate refs collapsed.

    Returns True if anything changed. Shared by ingest-time resolution and
    Neo4jKnowledgeGraph.merge_nodes so signal files never keep pointing at a
    merged-away id or a short surface form ("Paul" for person-paul-evers).
    """
    names = names or {}
    changed = False

    def _fix(ref) -> None:
        nonlocal changed
        new_id = id_map.get(ref.id, ref.id)
        if new_id != ref.id:
            ref.id = new_id
            # ids are "<type>-<slug>"; a retype (team -> account) changes both
            ref.type = new_id.split("-", 1)[0]
            changed = True
        new_name = names.get(ref.id)
        if new_name and ref.name != new_name:
            ref.name = new_name
            changed = True

    for sig in meeting_signals.signals:
        kept, seen = [], set()
        for ref in sig.entities:
            _fix(ref)
            if ref.id in seen:
                changed = True
                continue
            seen.add(ref.id)
            kept.append(ref)
        sig.entities = kept
        if sig.owner:
            _fix(sig.owner)
        if sig.client_id and sig.client_id in id_map:
            sig.client_id = id_map[sig.client_id]
            changed = True
        # ADR-006 §5: a claim's resolved attribution follows merges too.
        attributed = (sig.metadata or {}).get("attributed_to_ids")
        if isinstance(attributed, list) and any(a in id_map for a in attributed):
            sig.metadata = {
                **sig.metadata,
                "attributed_to_ids": list(dict.fromkeys(id_map.get(a, a) for a in attributed)),
            }
            changed = True
    return changed


def drop_entity_refs(meeting_signals: MeetingSignals, ids: set[str]) -> bool:
    """Remove references to entities that were rejected at admission: entity
    refs dropped, a matching owner cleared, a matching client_id cleared."""
    changed = False
    for sig in meeting_signals.signals:
        kept = [ref for ref in sig.entities if ref.id not in ids]
        if len(kept) != len(sig.entities):
            sig.entities = kept
            changed = True
        if sig.owner and sig.owner.id in ids:
            sig.owner = None
            changed = True
        if sig.client_id and sig.client_id in ids:
            sig.client_id = None
            changed = True
    return changed


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

    def container_lock(self, bot_id: str) -> threading.RLock:
        """Lock serializing read-modify-write of one meeting's signal file.

        Re-entrant, so a holder may call ``save``/``replace_signal`` (which
        take it again). Hold it only around synchronous file work.
        """
        return _lock_for_path(self._file_path(bot_id))

    def _find_in_container(
        self, bot_id: str, signal_id: str
    ) -> tuple[Signal, MeetingSignals] | None:
        """Re-read one container from disk and find ``signal_id`` in it."""
        container = self.load(bot_id)
        if container is None:
            return None
        for signal in container.signals:
            if signal.id == signal_id:
                return signal, container
        return None

    def save(self, meeting_signals: MeetingSignals) -> Path:
        """Write meeting signals to disk atomically (temp file + fsync + rename).

        A crash mid-write leaves the previous file intact rather than a
        truncated one. Creates the signals/ dir if needed.
        """
        path = self._file_path(meeting_signals.bot_id)
        with _lock_for_path(path):
            atomic_write_text(path, meeting_signals.model_dump_json(indent=2))
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
        actor: str | None = None,
        claimed_actor: str | None = None,
        audit_store=None,
    ) -> Signal | None:
        """Update fields on an existing signal and persist to disk.

        Thin wrapper over ``update_signal_governed`` that drops the audit row.
        A content edit that voids a review is still audited (ADR-009).
        """
        result = self.update_signal_governed(
            signal_id,
            status=status,
            content=content,
            owner_id=owner_id,
            owner_name=owner_name,
            owner_type=owner_type,
            due_date=due_date,
            actor=actor,
            claimed_actor=claimed_actor,
            audit_store=audit_store,
        )
        return None if result is None else result[0]

    def update_signal_governed(
        self,
        signal_id: str,
        *,
        status: str | None = None,
        content: str | None = None,
        owner_id: str | None = None,
        owner_name: str | None = None,
        owner_type: str | None = None,
        due_date: str | None = None,
        actor: str | None = None,
        claimed_actor: str | None = None,
        audit_store=None,
    ) -> tuple[Signal, SignalAuditRecord | None] | None:
        """Update fields on an existing signal and persist to disk.

        Every content edit passes through the governance chokepoint (ADR-009):
        if the edit changes the content of a reviewed (or instruction-grade)
        signal, the signal goes back to ``pending`` without instruction-grade
        authority, and a ``content_edited`` audit row is appended (fsynced)
        BEFORE the signal is persisted. If that append fails nothing is
        persisted and ``AuditAppendError`` is raised; if the persist fails
        after the row was written, a compensating ``content_edited_failed``
        row is appended and the error propagates. Status / owner / due_date
        edits and identical-content edits never demote.

        Args:
            signal_id: The signal's UUID5 identifier.
            status: New status (must be one of VALID_SIGNAL_STATUSES).
            content: New content text.
            owner_id: New owner entity ID.
            owner_name: New owner display name (used with owner_id).
            owner_type: New owner entity type (used with owner_id).
            due_date: New due date string.
            actor / claimed_actor: server-stamped actor and the caller's
                unverified claim, recorded on a ``content_edited`` row.
            audit_store: ``SignalAuditStore`` for that row (default store
                when None).

        Returns:
            ``(updated_signal, audit_record_or_None)``, or None if not found.

        Raises:
            ValueError: If status is not a valid value, signal_id is empty,
                owner_id is empty, or due_date is not YYYY-MM-DD.
            AuditAppendError: a demoting edit's audit row could not be written
                (nothing was persisted).
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

        located = self.find_signal_by_id(signal_id)
        if located is None:
            return None
        bot_id = located[1].bot_id

        with self.container_lock(bot_id):
            # Re-read under the lock so a concurrent write to this container
            # (another signal in the same meeting file) is not overwritten.
            result = self._find_in_container(bot_id, signal_id)
            if result is None:
                return None
            signal, container = result
            field_updates = dict(
                status=status,
                content=content,
                owner_id=owner_id,
                owner_name=owner_name,
                owner_type=owner_type,
                due_date=due_date,
            )
            audit_record = None
            if content is not None:
                from app.services.signal_audit import content_edit_with_audit

                signal, audit_record = content_edit_with_audit(
                    signal, content, actor=actor, claimed_actor=claimed_actor
                )
            if audit_record is None:
                return self._apply_field_updates(signal, container, **field_updates), None
            return (
                self._persist_audited_edit(
                    signal, container, audit_record, audit_store, field_updates
                ),
                audit_record,
            )

    def _persist_audited_edit(
        self,
        signal: Signal,
        container: MeetingSignals,
        audit_record: SignalAuditRecord,
        audit_store,
        field_updates: dict,
    ) -> Signal:
        """Audit-first persistence of a demoting content edit (caller holds the lock)."""
        from app.services.signal_audit import (
            AuditAppendError,
            SignalAuditStore,
            persist_failed_record,
        )

        audit_store = audit_store if audit_store is not None else SignalAuditStore()
        try:
            audit_store.append(audit_record)
        except Exception as e:
            logger.error(
                "[SIGNALS] AUDIT APPEND FAILED for content edit of %s; edit NOT applied: %s",
                signal.id, e, exc_info=True,
            )
            raise AuditAppendError(
                f"Audit append failed; content edit not applied: {e}"
            ) from e
        try:
            return self._apply_field_updates(signal, container, **field_updates)
        except Exception as e:
            logger.error(
                "[SIGNALS] SIGNAL PERSIST FAILED for content edit of %s after audit "
                "row %s; edit NOT applied: %s",
                signal.id, audit_record.id, e, exc_info=True,
            )
            try:
                audit_store.append(persist_failed_record(audit_record, str(e)))
            except Exception as ce:
                logger.error(
                    "[SIGNALS] Compensating audit row FAILED for %s (audit row %s "
                    "records an edit that was not applied): %s",
                    signal.id, audit_record.id, ce, exc_info=True,
                )
            raise

    def _apply_field_updates(
        self,
        signal: Signal,
        container: MeetingSignals,
        *,
        status: str | None,
        content: str | None,
        owner_id: str | None,
        owner_name: str | None,
        owner_type: str | None,
        due_date: str | None,
    ) -> Signal:
        """Apply validated field updates to ``signal`` and persist ``container``.

        Caller holds ``container_lock(container.bot_id)``.
        """
        signal_id = signal.id
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
        located = self.find_signal_by_id(signal_id)
        if located is None:
            return None
        bot_id = located[1].bot_id

        with self.container_lock(bot_id):
            result = self._find_in_container(bot_id, signal_id)
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
