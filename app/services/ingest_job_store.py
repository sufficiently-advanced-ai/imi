"""Durable ingestion job store: an in-memory cache written through to SQLite.

``JobStore`` keeps the mapping interface the ingest routes and the
orchestrator have always used (``job_store["job:{id}"]``, ``key in
job_store``, in-place mutation of the returned record) so the pipeline code is
unchanged, and persists job records to the ``ingest_jobs`` table
(``app/user_models/ingest_job_models.py``) in the app database.

Keys, as before:
  ``job:{job_id}``          the job record (a plain dict, mutated in place)
  ``{content_hash}``        dedup entry ``{"job_id", "content_hash"}``
  ``source_id:{source_id}`` dedup entry ``{"job_id"}``

Reads are served from the cache, so polling (``submit_and_wait``, the status
routes) never touches the database. Writes happen:
  * when a job record is assigned (``job_store["job:x"] = {...}``), and
  * at checkpoints: ``await persist(job_id)``, which the ingest route calls on
    every pipeline event (phase start / completion, terminal events) and when
    the pipeline returns.
In-place mutations between two checkpoints are therefore persisted at the
next checkpoint; a crash loses at most the progress since the last phase
event, and the job comes back ``interrupted`` and is re-run from the start.

All SQLite I/O runs on one dedicated worker thread (ordered, off the event
loop). The dedup entries are not stored separately: they are rebuilt from the
``source_id`` / ``content_hash`` columns when the store is opened.

Single-process assumption: one app process owns the table (as with the dict
it replaces). ``open()`` marks every pending/running row ``interrupted``.

An unopened store (no database) behaves like the old dict, plus the
request/retry bookkeeping kept in memory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Iterator, MutableMapping
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_INTERRUPTED = "interrupted"
STATUS_FAILED = "failed"

# Not finished: still owned by the task queue.
ACTIVE_STATUSES = frozenset({STATUS_PENDING, STATUS_RUNNING})
# May be re-run (resubmission or POST /api/ingest/{id}/retry).
RERUNNABLE_STATUSES = frozenset({STATUS_FAILED, STATUS_INTERRUPTED})
# Finished; eligible for the retention sweep.
TERMINAL_STATUSES = frozenset(
    {"completed", "completed_with_warnings", STATUS_FAILED, "dropped"}
)

_JOB_PREFIX = "job:"
_SOURCE_PREFIX = "source_id:"

# Job-record keys stored in their own columns; everything else goes to `extra`.
_COLUMN_KEYS = (
    "status",
    "content_type",
    "source_id",
    "content_hash",
    "phases_completed",
    "phase_outcomes",
    "result",
    "error",
    "attempts",
    "interrupted",
)
_TIME_KEYS = ("created_at", "started_at", "finished_at")

# Store-owned keys carried over when a caller replaces the whole record
# (IngestOrchestrator.process re-initialises it when the job starts running).
_CARRIED_KEYS = (
    "created_at",
    "attempts",
    "interrupted",
    "source_id",
    "content_hash",
)

_SWEEP_INTERVAL_S = 24 * 3600


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_time(value: Any) -> datetime | None:
    """ISO string / datetime -> aware UTC datetime (None if unparseable)."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _to_db_time(value: Any) -> datetime | None:
    """SQLite drops tzinfo: store naive UTC so comparisons are consistent."""
    dt = _parse_time(value)
    return dt.replace(tzinfo=None) if dt else None


class JobStore(MutableMapping):
    """Mapping-compatible ingest job store; see the module docstring."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        self._engine = None
        self._executor: ThreadPoolExecutor | None = None
        # job_id -> digest of the last snapshot written (skip no-op writes)
        self._written: dict[str, str] = {}
        # job_id -> (request JSON, channel); used only while no database is open
        self._requests: dict[str, tuple[str, str | None]] = {}

    # ------------------------------------------------------------------
    # Mapping interface (what the routes and the orchestrator use)
    # ------------------------------------------------------------------

    def __getitem__(self, key: str) -> Any:
        return self._cache[key]

    def __setitem__(self, key: str, value: Any) -> None:
        if key.startswith(_JOB_PREFIX) and isinstance(value, dict):
            old = self._cache.get(key)
            if isinstance(old, dict) and old is not value:
                for carried in _CARRIED_KEYS:
                    if value.get(carried) is None and old.get(carried) is not None:
                        value[carried] = old[carried]
            self._cache[key] = value
            self.persist_soon(key[len(_JOB_PREFIX):])
        else:
            self._cache[key] = value

    def __delitem__(self, key: str) -> None:
        del self._cache[key]
        if key.startswith(_JOB_PREFIX):
            job_id = key[len(_JOB_PREFIX):]
            self._written.pop(job_id, None)
            self._requests.pop(job_id, None)
            self._submit(self._delete_sync, [job_id])

    def __iter__(self) -> Iterator[str]:
        return iter(self._cache)

    def __len__(self) -> int:
        return len(self._cache)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_persistent(self) -> bool:
        return self._engine is not None

    def jobs(self) -> list[dict[str, Any]]:
        return [
            v
            for k, v in self._cache.items()
            if k.startswith(_JOB_PREFIX) and isinstance(v, dict)
        ]

    def count_status(self, *statuses: str) -> int:
        wanted = set(statuses)
        return sum(1 for job in self.jobs() if job.get("status") in wanted)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def persist_soon(self, job_id: str) -> Future | None:
        """Snapshot the job record now and schedule its write (no-op in memory)."""
        if self._engine is None:
            return None
        job = self._cache.get(f"{_JOB_PREFIX}{job_id}")
        if not isinstance(job, dict) or not job.get("status"):
            return None
        status = job.get("status")
        if status == STATUS_RUNNING and not job.get("started_at"):
            job["started_at"] = _now_iso()
        if status in TERMINAL_STATUSES and not job.get("finished_at"):
            job["finished_at"] = _now_iso()
        try:
            # Serialised on the loop thread: the worker only sees this copy.
            text = json.dumps(job, default=str, sort_keys=True)
        except (TypeError, ValueError) as exc:
            logger.warning("[INGEST_JOBS] Job %s not serialisable: %s", job_id, exc)
            return None
        digest = _digest(text)
        if self._written.get(job_id) == digest:
            return None
        self._written[job_id] = digest
        return self._submit(self._write_tracked, job_id, json.loads(text), digest)

    async def persist(self, job_id: str) -> None:
        """Checkpoint: write the job record and wait for the write."""
        fut = self.persist_soon(job_id)
        if fut is not None:
            await asyncio.wrap_future(fut)

    def save_request(self, job_id: str, request: Any) -> None:
        """Store the job's IngestRequest (needed for retry and restart recovery)."""
        payload = request.model_dump_json()
        channel = getattr(request, "_channel", None)
        if self._engine is None:
            self._requests[job_id] = (payload, channel)
        else:
            self._submit(self._write_request_sync, job_id, payload, channel)

    async def load_request(self, job_id: str) -> Any | None:
        """Return the stored IngestRequest for ``job_id``, or None."""
        from app.models.ingestion.models import IngestRequest

        stored = self._requests.get(job_id)
        if stored is None and self._engine is not None:
            stored = await self._run(self._read_request_sync, job_id)
        if not stored or not stored[0]:
            return None
        payload, channel = stored
        try:
            request = IngestRequest.model_validate_json(payload)
        except Exception as exc:  # noqa: BLE001 — a bad row must not crash recovery
            logger.warning("[INGEST_JOBS] Stored request for %s unreadable: %s", job_id, exc)
            return None
        if channel:
            request._channel = channel  # server-set (ADR-007), never from a body
        return request

    async def flush(self) -> None:
        """Wait until every scheduled write has finished."""
        if self._executor is not None:
            await asyncio.wrap_future(self._executor.submit(lambda: None))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self, db_path: str) -> dict[str, Any]:
        """Open (creating if needed) the table, load every job, and mark jobs
        left pending/running by the previous process ``interrupted``.

        Records already in the cache (accepted before open) are kept and
        written. Returns ``{"loaded": n, "interrupted": [job_id, ...]}``.
        """
        from sqlalchemy import create_engine

        if self._engine is not None:
            return {"loaded": 0, "interrupted": []}
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ingest-jobs")
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 20.0},
        )
        try:
            rows, interrupted = await self._run(self._open_sync, engine)
        except Exception:
            # Stay an in-memory store; the caller logs the failure.
            self._executor.shutdown(wait=False)
            self._executor = None
            engine.dispose()
            raise
        self._engine = engine

        for job in rows:  # oldest first, so the newest job owns a shared key
            job_id = job["job_id"]
            key = f"{_JOB_PREFIX}{job_id}"
            if key in self._cache:
                continue
            self._cache[key] = job
            self._written[job_id] = _digest(json.dumps(job, default=str, sort_keys=True))
            if job.get("content_hash"):
                self._cache[job["content_hash"]] = {
                    "job_id": job_id,
                    "content_hash": job["content_hash"],
                }
            if job.get("source_id"):
                self._cache[f"{_SOURCE_PREFIX}{job['source_id']}"] = {"job_id": job_id}

        # Anything accepted before open() goes to the database now.
        for job_id, request in list(self._requests.items()):
            self.persist_soon(job_id)
            self._submit(self._write_request_sync, job_id, *request)
        self._requests.clear()
        for job in self.jobs():
            self.persist_soon(job["job_id"])
        await self.flush()
        return {"loaded": len(rows), "interrupted": interrupted}

    async def close(self) -> None:
        if self._executor is None:
            return
        await self.flush()
        executor, engine = self._executor, self._engine
        self._executor = None
        self._engine = None
        executor.shutdown(wait=True)
        if engine is not None:
            engine.dispose()

    async def sweep(self, retention_days: int) -> int:
        """Delete terminal jobs that finished more than ``retention_days`` ago.

        Their dedup entries go with them (intentional: ingest also dedups
        downstream by ``source_id`` / deterministic ids). Returns the count.
        """
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        expired: list[str] = []
        for job in self.jobs():
            if job.get("status") not in TERMINAL_STATUSES:
                continue
            when = _parse_time(job.get("finished_at")) or _parse_time(job.get("created_at"))
            if when is not None and when < cutoff:
                expired.append(job["job_id"])
        expired_set = set(expired)
        for key in [
            k
            for k, v in self._cache.items()
            if not k.startswith(_JOB_PREFIX)
            and isinstance(v, dict)
            and v.get("job_id") in expired_set
        ]:
            del self._cache[key]
        for job_id in expired:
            self._cache.pop(f"{_JOB_PREFIX}{job_id}", None)
            self._written.pop(job_id, None)
            self._requests.pop(job_id, None)
        if self._engine is not None:
            await self._run(self._sweep_sync, expired, cutoff.replace(tzinfo=None))
        if expired:
            logger.info(
                "[INGEST_JOBS] Retention sweep removed %d job(s) older than %d days",
                len(expired),
                retention_days,
            )
        return len(expired)

    async def retention_loop(self, retention_days_fn) -> None:
        """Sweep once a day (startup runs its own sweep)."""
        while True:
            await asyncio.sleep(_SWEEP_INTERVAL_S)
            try:
                await self.sweep(retention_days_fn())
            except Exception:  # noqa: BLE001 — keep the loop alive
                logger.exception("[INGEST_JOBS] Retention sweep failed")

    # ------------------------------------------------------------------
    # Worker-thread internals
    # ------------------------------------------------------------------

    def _submit(self, fn, *args) -> Future | None:
        if self._executor is None or self._engine is None:
            return None
        return self._executor.submit(self._guard, fn, *args)

    @staticmethod
    def _guard(fn, *args):
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001 — the cache stays authoritative
            logger.exception("[INGEST_JOBS] %s failed", getattr(fn, "__name__", fn))
            return None

    async def _run(self, fn, *args):
        """Run ``fn`` on the worker thread and return its result (raises)."""
        assert self._executor is not None
        return await asyncio.wrap_future(self._executor.submit(fn, *args))

    @staticmethod
    def _open_sync(engine) -> tuple[list[dict[str, Any]], list[str]]:
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from app.user_models.ingest_job_models import IngestJob

        IngestJob.__table__.create(engine, checkfirst=True)
        with Session(engine) as session:
            stale = [
                job_id
                for (job_id,) in session.query(IngestJob.job_id).filter(
                    IngestJob.status.in_(ACTIVE_STATUSES)
                )
            ]
            if stale:
                session.execute(
                    update(IngestJob)
                    .where(IngestJob.job_id.in_(stale))
                    .values(status=STATUS_INTERRUPTED, interrupted=True)
                )
                session.commit()
            rows = [
                _row_to_job(row)
                for row in session.query(IngestJob).order_by(IngestJob.created_at)
            ]
        return rows, stale

    def _write_sync(self, job_id: str, job: dict[str, Any]) -> None:
        from sqlalchemy.orm import Session

        from app.user_models.ingest_job_models import IngestJob

        with Session(self._engine) as session:
            row = session.get(IngestJob, job_id)
            if row is None:
                row = IngestJob(job_id=job_id)
                session.add(row)
            _job_to_row(job, row)
            session.commit()

    def _write_tracked(self, job_id: str, job: dict[str, Any], digest: str) -> None:
        try:
            self._write_sync(job_id, job)
        except Exception:
            # Forget the digest so the next checkpoint retries this snapshot.
            if self._written.get(job_id) == digest:
                self._written.pop(job_id, None)
            raise

    def _write_request_sync(self, job_id: str, payload: str, channel: str | None) -> None:
        from sqlalchemy.orm import Session

        from app.user_models.ingest_job_models import IngestJob

        with Session(self._engine) as session:
            row = session.get(IngestJob, job_id)
            if row is None:
                logger.warning("[INGEST_JOBS] No row for %s; request not stored", job_id)
                return
            row.request_payload = payload
            row.channel = channel
            session.commit()

    def _read_request_sync(self, job_id: str) -> tuple[str, str | None] | None:
        from sqlalchemy.orm import Session

        from app.user_models.ingest_job_models import IngestJob

        with Session(self._engine) as session:
            row = session.get(IngestJob, job_id)
            if row is None or not row.request_payload:
                return None
            return row.request_payload, row.channel

    def _delete_sync(self, job_ids: list[str]) -> None:
        from sqlalchemy import delete
        from sqlalchemy.orm import Session

        from app.user_models.ingest_job_models import IngestJob

        with Session(self._engine) as session:
            session.execute(delete(IngestJob).where(IngestJob.job_id.in_(job_ids)))
            session.commit()

    def _sweep_sync(self, job_ids: list[str], cutoff_naive_utc: datetime) -> None:
        from sqlalchemy import delete, func
        from sqlalchemy.orm import Session

        from app.user_models.ingest_job_models import IngestJob

        with Session(self._engine) as session:
            for i in range(0, len(job_ids), 500):
                chunk = job_ids[i : i + 500]
                session.execute(delete(IngestJob).where(IngestJob.job_id.in_(chunk)))
            # Rows not in the cache (should not exist): same rule, in SQL.
            session.execute(
                delete(IngestJob).where(
                    IngestJob.status.in_(TERMINAL_STATUSES),
                    func.coalesce(IngestJob.finished_at, IngestJob.created_at)
                    < cutoff_naive_utc,
                )
            )
            session.commit()


def _job_to_row(job: dict[str, Any], row) -> None:
    row.status = job.get("status") or STATUS_PENDING
    row.content_type = job.get("content_type")
    row.source_id = job.get("source_id")
    row.content_hash = job.get("content_hash")
    row.phases_completed = list(job.get("phases_completed") or [])
    row.phase_outcomes = list(job.get("phase_outcomes") or [])
    row.result = job.get("result")
    row.error = job.get("error")
    row.attempts = int(job.get("attempts") or 1)
    row.interrupted = bool(job.get("interrupted"))
    row.created_at = _to_db_time(job.get("created_at")) or _to_db_time(_now_iso())
    row.started_at = _to_db_time(job.get("started_at"))
    row.finished_at = _to_db_time(job.get("finished_at"))
    row.extra = {
        k: v
        for k, v in job.items()
        if k not in _COLUMN_KEYS and k not in _TIME_KEYS and k != "job_id"
    }


def _row_to_job(row) -> dict[str, Any]:
    job: dict[str, Any] = dict(row.extra or {})
    job.update(
        {
            "job_id": row.job_id,
            "status": row.status,
            "content_type": row.content_type,
            "source_id": row.source_id,
            "content_hash": row.content_hash,
            "phases_completed": list(row.phases_completed or []),
            "phase_outcomes": list(row.phase_outcomes or []),
            "result": row.result,
            "error": row.error,
            "attempts": row.attempts or 1,
            "interrupted": bool(row.interrupted),
        }
    )
    job.setdefault("warnings", [])
    job.setdefault("current_phase", None)
    for key in _TIME_KEYS:
        dt = _parse_time(getattr(row, key))
        job[key] = dt.isoformat() if dt else None
    return job
