"""
Ingest API Routes — General-purpose content inbox (Issue #863).

Endpoints:
  POST /api/ingest          — Accept content, enqueue processing, return job_id
  GET  /api/ingest/{id}/status — Poll job progress by phase
  GET  /api/ingest/jobs     — List recent ingestion jobs
  POST /api/ingest/{id}/retry — Re-run a failed or interrupted job
"""

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from ..models.ingestion.models import (
    IngestJobStatus,
    IngestRequest,
    IngestResponse,
)
from ..services.ingest_job_store import (
    RERUNNABLE_STATUSES,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING,
    JobStore,
)
from ..services.sse_manager import sse_manager
from ..services.task_queue import TaskQueue, global_task_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingestion"])

# Job store — maps content hashes, source_ids, and job statuses.
# Keys: "job:{job_id}", "{content_hash}", "source_id:{source_id}"
# Durable once opened at startup (`start_job_store`): records are written
# through to the `ingest_jobs` table (app/services/ingest_job_store.py).
_job_store: JobStore = JobStore()

# Max content size: 500KB
MAX_CONTENT_BYTES = 500 * 1024

# Seconds a client is told to wait when the pending bound is hit (429).
RETRY_AFTER_SECONDS = 30

# A job interrupted by restarts this many times is failed instead of re-run
# automatically (a job that crashes the process must not loop forever);
# POST /api/ingest/{job_id}/retry still re-runs it.
MAX_AUTO_RECOVERY_ATTEMPTS = 3


class IngestQueueFull(Exception):
    """Raised by enqueue_ingestion when INGEST_MAX_PENDING jobs are waiting."""

    def __init__(self, pending: int, limit: int):
        self.pending = pending
        self.limit = limit
        self.retry_after = RETRY_AFTER_SECONDS
        super().__init__(
            f"Ingest queue is full ({pending} pending, limit {limit}); "
            f"retry in {RETRY_AFTER_SECONDS}s"
        )


def _settings():
    from ..config import get_settings

    return get_settings()


def _get_task_queue() -> TaskQueue:
    """Dependency: return the global task queue."""
    return global_task_queue


def _get_job_store() -> MutableMapping[str, Any]:
    """Dependency: return the shared job store."""
    return _job_store


def _check_capacity(job_store: MutableMapping[str, Any]) -> None:
    """Raise IngestQueueFull when INGEST_MAX_PENDING jobs are waiting."""
    limit = _settings().INGEST_MAX_PENDING
    pending = sum(
        1
        for key, job in job_store.items()
        if key.startswith("job:")
        and isinstance(job, dict)
        and job.get("status") == STATUS_PENDING
    )
    if pending >= limit:
        raise IngestQueueFull(pending, limit)


def _start_attempt(
    request: IngestRequest,
    job_id: str,
    job_store: MutableMapping[str, Any],
    task_queue: TaskQueue,
) -> None:
    """(Re)initialise ``job:{job_id}`` as pending and enqueue it.

    A re-run (resubmission, retry, restart recovery) keeps the job_id — so a
    client's ``poll_url`` stays valid — and bumps ``attempts``.
    """
    job_key = f"job:{job_id}"
    previous = job_store.get(job_key)
    previous = previous if isinstance(previous, dict) else None
    content_hash = hashlib.sha256(request.content.encode()).hexdigest()
    job_store[job_key] = {
        "job_id": job_id,
        "status": STATUS_PENDING,
        "content_type": None,
        "phases_completed": [],
        "phase_outcomes": [],
        "warnings": [],
        "current_phase": None,
        "result": None,
        "error": None,
        "source_id": request.source_id,
        "content_hash": content_hash,
        "attempts": (previous.get("attempts") or 1) + 1 if previous else 1,
        "interrupted": bool(previous and previous.get("interrupted")),
        "created_at": (previous or {}).get("created_at") or datetime.now(UTC).isoformat(),
        "started_at": None,
        "finished_at": None,
    }
    # Dedup entries point at this job (a resubmission may add a source_id).
    job_store[content_hash] = {"job_id": job_id, "content_hash": content_hash}
    if request.source_id:
        job_store[f"source_id:{request.source_id}"] = {"job_id": job_id}
    if isinstance(job_store, JobStore):
        job_store.save_request(job_id, request)

    task_queue.enqueue(
        _run_ingestion_pipeline,
        request,
        job_id,
        job_store,
        task_id=job_id,
        priority=1,
    )


def enqueue_ingestion(
    request: IngestRequest,
    job_store: MutableMapping[str, Any],
    task_queue: TaskQueue,
) -> tuple[str, str]:
    """Dedup, register, and enqueue an ingestion job.

    Shared by the HTTP route (`ingest_content`) and the in-process
    `submit_and_wait` helper so both surfaces use one dedup + enqueue codepath
    (avoids the two-copies-drift class of bug). Does NOT enforce the size limit
    — callers do that with the error type appropriate to their surface
    (HTTPException for the route, an error dict for the MCP tool).

    Dedup: content whose ``source_id`` (first) or content hash matches an
    existing job is a ``duplicate`` unless that job ``failed`` or was
    ``interrupted`` — then it is re-run under the same job_id
    (``attempts`` + 1) and reported ``accepted``.

    Raises:
        IngestQueueFull: INGEST_MAX_PENDING jobs are already waiting.

    Returns:
        (job_id, status) where status is "accepted" for a new or re-run job,
        or "duplicate" when the content/source_id was already seen. For a
        duplicate, job_id points at the pre-existing job.
    """
    existing: str | None = None
    # Idempotency: check source_id first
    if request.source_id:
        source_key = f"source_id:{request.source_id}"
        if source_key in job_store:
            existing = job_store[source_key]["job_id"]

    # Idempotency: check content hash
    content_hash = hashlib.sha256(request.content.encode()).hexdigest()
    if existing is None and content_hash in job_store:
        existing = job_store[content_hash]["job_id"]

    if existing is not None:
        job = job_store.get(f"job:{existing}")
        if not isinstance(job, dict) or job.get("status") not in RERUNNABLE_STATUSES:
            return existing, "duplicate"
        _check_capacity(job_store)
        _start_attempt(request, existing, job_store, task_queue)
        return existing, "accepted"

    _check_capacity(job_store)
    job_id = f"ingest-{uuid.uuid4().hex[:12]}"
    _start_attempt(request, job_id, job_store, task_queue)
    return job_id, "accepted"


async def _checkpoint(job_store: MutableMapping[str, Any], job_id: str) -> None:
    """Persist the job record (durable store only; never raises)."""
    if isinstance(job_store, JobStore):
        try:
            await job_store.persist(job_id)
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            logger.warning(f"[INGEST] Job {job_id}: checkpoint failed: {exc}")


async def retry_job(
    job_id: str,
    job_store: MutableMapping[str, Any] | None = None,
    task_queue: TaskQueue | None = None,
) -> str:
    """Re-enqueue a failed or interrupted job from its stored request.

    Returns the job's previous status. Raises KeyError (unknown job),
    ValueError (job not re-runnable, or no stored request) or IngestQueueFull.
    """
    job_store = _get_job_store() if job_store is None else job_store
    task_queue = _get_task_queue() if task_queue is None else task_queue
    job = job_store.get(f"job:{job_id}")
    if not isinstance(job, dict):
        raise KeyError(job_id)
    status = job.get("status")
    if status not in RERUNNABLE_STATUSES:
        raise ValueError(
            f"Job '{job_id}' is {status}; only failed or interrupted jobs can be retried"
        )
    request = (
        await job_store.load_request(job_id) if isinstance(job_store, JobStore) else None
    )
    if request is None:
        raise ValueError(f"Job '{job_id}' has no stored request; resubmit the content")
    # Re-check: the request load awaited, so another caller may have re-run it.
    if job_store.get(f"job:{job_id}", {}).get("status") not in RERUNNABLE_STATUSES:
        raise ValueError(f"Job '{job_id}' is already being re-run")
    _check_capacity(job_store)
    _start_attempt(request, job_id, job_store, task_queue)
    return status


async def recover_interrupted_jobs(
    job_store: JobStore | None = None,
    task_queue: TaskQueue | None = None,
) -> dict[str, int]:
    """Re-enqueue jobs a restart interrupted (startup). Each re-runs from the
    first phase: the pipeline's writes are idempotent (meeting id from the
    content hash, deterministic uuid5 signal ids, MERGE on source_id, corpus
    files overwritten at the same paths). Not subject to INGEST_MAX_PENDING —
    these were accepted."""
    job_store = _job_store if job_store is None else job_store
    task_queue = _get_task_queue() if task_queue is None else task_queue
    requeued = failed = 0
    for job in list(job_store.jobs()):
        if job.get("status") != STATUS_INTERRUPTED:
            continue
        job_id = job["job_id"]
        attempts = job.get("attempts") or 1
        request = None
        if attempts < MAX_AUTO_RECOVERY_ATTEMPTS:
            request = await job_store.load_request(job_id)
        if job.get("status") != STATUS_INTERRUPTED:
            continue  # resubmitted while the request was loading
        if request is None:
            job["status"] = STATUS_FAILED
            job["error"] = (
                f"Interrupted by a restart after {attempts} attempt(s); not re-run "
                f"automatically. POST /api/ingest/{job_id}/retry to run it again."
                if attempts >= MAX_AUTO_RECOVERY_ATTEMPTS
                else "Interrupted by a restart and the stored request is unavailable; "
                "resubmit the content."
            )
            await job_store.persist(job_id)
            failed += 1
            continue
        _start_attempt(request, job_id, job_store, task_queue)
        requeued += 1
    if requeued or failed:
        logger.info(
            f"[INGEST] Restart recovery: re-enqueued {requeued} interrupted job(s), "
            f"failed {failed}"
        )
    return {"requeued": requeued, "failed": failed}


async def start_job_store(db_path: str | None = None) -> dict[str, Any]:
    """Startup: open the durable job store, sweep expired jobs, re-enqueue
    interrupted ones, and schedule the daily retention sweep."""
    settings = _settings()
    opened = await _job_store.open(db_path or settings.DATABASE_PATH)
    swept = await _job_store.sweep(settings.INGEST_JOB_RETENTION_DAYS)
    recovered = await recover_interrupted_jobs(_job_store)
    logger.info(
        f"[INGEST] Job store opened: {opened['loaded']} job(s) loaded, "
        f"{len(opened['interrupted'])} interrupted by the last shutdown, "
        f"{recovered['requeued']} re-enqueued, {swept} expired job(s) swept"
    )

    from ..services.graph_rebuild import spawn_background

    spawn_background(
        _job_store.retention_loop(lambda: _settings().INGEST_JOB_RETENTION_DAYS),
        "ingest-job-retention",
    )
    return {**opened, **recovered, "swept": swept}


async def submit_and_wait(
    request: IngestRequest,
    *,
    timeout_s: float = 30.0,
    poll_interval_s: float = 0.25,
) -> dict[str, Any]:
    """Enqueue an ingestion job and block until it finishes or times out.

    Bridges the pipeline's async (enqueue + poll) design to a synchronous
    caller such as the `add_call_transcript` MCP tool. Reuses the module-level
    `_job_store` and `global_task_queue`, which the FastAPI app populates — the
    MCP server runs in the same process, so polling the in-memory job store
    sees the orchestrator's updates directly.

    Returns a dict with `state` one of:
      - "rejected":  the pending bound (INGEST_MAX_PENDING) was hit; includes
                     `error` and `retry_after_s`; nothing was enqueued
      - "completed": includes `job_id`, `poll_url`, `content_type`, `result`
      - "completed_with_warnings": as "completed"; a phase degraded (e.g.
                     signal promotion crashed) but the content was persisted
      - "failed":    includes `job_id`, `poll_url`, `error`

    Terminal states also carry `phase_outcomes` and `warnings` (one line per
    failed phase) so callers such as the MCP tool can surface them.
      - "dropped":   lane admission (ADR-003) rejected the content as junk;
                     includes `job_id`, `poll_url`, `result` (with the reason)
      - "pending":   timed out before a terminal state; includes `job_id`,
                     `poll_url` so the caller can poll later
    """
    job_store = _get_job_store()
    task_queue = _get_task_queue()

    try:
        job_id, _status = enqueue_ingestion(request, job_store, task_queue)
    except IngestQueueFull as full:
        return {"state": "rejected", "error": str(full), "retry_after_s": full.retry_after}
    job_key = f"job:{job_id}"
    poll_url = f"/api/ingest/{job_id}/status"

    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        job = job_store.get(job_key)
        if job:
            state = job.get("status")
            outcomes = {
                "phase_outcomes": list(job.get("phase_outcomes") or []),
                "warnings": list(job.get("warnings") or []),
            }
            if state in ("completed", "completed_with_warnings"):
                return {
                    "state": state,
                    "job_id": job_id,
                    "poll_url": poll_url,
                    "content_type": job.get("content_type"),
                    "result": job.get("result") or {},
                    **outcomes,
                }
            if state == "dropped":
                return {
                    "state": "dropped",
                    "job_id": job_id,
                    "poll_url": poll_url,
                    "result": job.get("result") or {},
                }
            if state == "failed":
                return {
                    "state": "failed",
                    "job_id": job_id,
                    "poll_url": poll_url,
                    "error": job.get("error") or "unknown error",
                    **outcomes,
                }
        if time.monotonic() >= deadline:
            return {"state": "pending", "job_id": job_id, "poll_url": poll_url}
        await asyncio.sleep(poll_interval_s)


@router.post("", status_code=202, response_model=IngestResponse)
async def ingest_content(
    request: IngestRequest,
    response: Response,
    task_queue: TaskQueue = Depends(_get_task_queue),
    job_store: MutableMapping[str, Any] = Depends(_get_job_store),
) -> IngestResponse:
    """Accept content for ingestion and enqueue processing.

    Returns 202 Accepted with job_id for new content (or a re-run of a
    failed / interrupted job with the same content or source_id),
    200 OK with status "duplicate" for already-processed content.
    413 if content exceeds size limit.
    429 (with Retry-After) when INGEST_MAX_PENDING jobs are waiting.
    """
    # Size check
    if len(request.content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise HTTPException(status_code=413, detail="Content exceeds 500KB limit")

    try:
        job_id, status = enqueue_ingestion(request, job_store, task_queue)
    except IngestQueueFull as full:
        raise HTTPException(
            status_code=429,
            detail=str(full),
            headers={"Retry-After": str(full.retry_after)},
        ) from full
    if status == "duplicate":
        response.status_code = 200

    return IngestResponse(
        job_id=job_id,
        status=status,
        poll_url=f"/api/ingest/{job_id}/status",
    )


@router.get("/jobs")
async def list_jobs(
    job_store: MutableMapping[str, Any] = Depends(_get_job_store),
) -> list:
    """List recent ingestion jobs."""
    jobs = []
    for key, value in job_store.items():
        if key.startswith("job:") and isinstance(value, dict) and "job_id" in value:
            jobs.append(value)

    # Sort by created_at descending if available
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return jobs


@router.post("/{job_id}/retry", status_code=202, response_model=IngestResponse)
async def retry_ingest_job(
    job_id: str,
    task_queue: TaskQueue = Depends(_get_task_queue),
    job_store: MutableMapping[str, Any] = Depends(_get_job_store),
) -> IngestResponse:
    """Re-run a failed or interrupted job from its stored request.

    Same job_id and poll_url; ``attempts`` goes up by one. 404 for an unknown
    job, 409 when the job is pending, running, completed or dropped (or its
    request was not stored), 429 when the pending bound is hit.
    """
    try:
        await retry_job(job_id, job_store, task_queue)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except IngestQueueFull as full:
        raise HTTPException(
            status_code=429,
            detail=str(full),
            headers={"Retry-After": str(full.retry_after)},
        ) from full
    return IngestResponse(
        job_id=job_id,
        status="accepted",
        poll_url=f"/api/ingest/{job_id}/status",
    )


@router.get("/{job_id}/delta")
async def get_job_delta(
    job_id: str,
    job_store: MutableMapping[str, Any] = Depends(_get_job_store),
) -> dict:
    """Return the delta report for a completed ingestion job.

    Returns 200 with the delta report dict when the job exists and the
    DELTA_REPORT phase has completed (i.e. ``result.delta_report`` is present).
    Returns 404 when the job is unknown or the delta report is not yet available.
    """
    job_key = f"job:{job_id}"
    if job_key not in job_store:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    job_data = job_store[job_key]
    result = job_data.get("result") or {}
    delta_report = result.get("delta_report")
    if delta_report is None:
        raise HTTPException(
            status_code=404,
            detail=f"Delta report not available for job '{job_id}' — job may still be running",
        )

    # The job record is authoritative for outcomes: the report is built
    # before the last phases run.
    return {
        **delta_report,
        "phase_outcomes": list(job_data.get("phase_outcomes") or []),
        "warnings": list(job_data.get("warnings") or []),
    }


@router.get("/{job_id}/status", response_model=IngestJobStatus)
async def get_job_status(
    job_id: str,
    job_store: MutableMapping[str, Any] = Depends(_get_job_store),
) -> IngestJobStatus:
    """Get the current status of an ingestion job."""
    job_key = f"job:{job_id}"
    if job_key not in job_store:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    job_data = job_store[job_key]
    return IngestJobStatus(
        job_id=job_data["job_id"],
        status=job_data["status"],
        content_type=job_data.get("content_type"),
        phases_completed=job_data.get("phases_completed", []),
        phase_outcomes=job_data.get("phase_outcomes") or [],
        warnings=job_data.get("warnings") or [],
        current_phase=job_data.get("current_phase"),
        result=job_data.get("result"),
        error=job_data.get("error"),
        attempts=job_data.get("attempts") or 1,
        interrupted=bool(job_data.get("interrupted")),
    )


# ingest_dropped: lane admission (ADR-003) rejected the content — terminal.
_INGEST_TERMINAL_TYPES = frozenset({"ingest_complete", "ingest_failed", "ingest_dropped"})


@router.get("/{job_id}/stream")
async def stream_ingest_job(job_id: str, request: Request):
    """Stream live phase-transition events for an ingestion job via SSE.

    Connects immediately and waits for events — the job may not have started
    yet when the client connects (matches existing sse_status.py semantics).
    The stream closes automatically after ``ingest_complete`` or
    ``ingest_failed`` is received.
    """
    from ..routes.sse_status import event_generator_raw

    return StreamingResponse(
        event_generator_raw(request, job_id, terminal_types=_INGEST_TERMINAL_TYPES),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_ingestion_pipeline(
    request: IngestRequest,
    job_id: str,
    job_store: MutableMapping[str, Any],
) -> dict[str, Any]:
    """Background task: run the full ingestion pipeline.

    Lazily imports and constructs the orchestrator to avoid circular imports
    and allow dependency injection in tests.
    """
    try:
        from ..services.ingest_classifier import IngestClassifier
        from ..services.orchestrators.ingest_orchestrator import IngestOrchestrator

        # Lazy-load dependencies (log when unavailable to aid debugging)
        claude_client = _get_claude_client()
        if not claude_client:
            logger.warning(f"[INGEST] Job {job_id}: Claude client unavailable")
        graph = _get_graph_service()
        if not graph:
            logger.warning(f"[INGEST] Job {job_id}: Graph service unavailable")
        signal_writer = _get_signal_writer()
        if not signal_writer:
            logger.debug(f"[INGEST] Job {job_id}: Signal writer unavailable")
        git_ops = _get_git_ops()

        tools = _get_extraction_tools(claude_client, git_ops)
        classifier = IngestClassifier(claude_client=claude_client)

        # Build the SSE emitter for this job and pre-register the connection so
        # clients that connect before the pipeline starts can receive all events.
        await sse_manager.add_connection(job_id)

        # Capped event list for reconnect replay — matches the shape read by
        # sse_status.py's event_generator_raw which iterates status["events"].
        _replay_events: list[dict] = []
        _REPLAY_CAP = 200

        async def _emit(event_type: str, data: dict) -> None:
            # Every pipeline event is a phase boundary: checkpoint the record.
            await _checkpoint(job_store, job_id)
            await sse_manager.send_event(job_id, event_type, data)
            # Append to replay list (capped) so reconnecting clients get history.
            # Shape must match what send_event puts on the queue: type + data keys.
            event_snapshot = {"type": event_type, **data}
            _replay_events.append(event_snapshot)
            if len(_replay_events) > _REPLAY_CAP:
                del _replay_events[: len(_replay_events) - _REPLAY_CAP]
            sse_manager.store_execution_status(
                job_id,
                {
                    "events": list(_replay_events),
                    "phases_completed": data.get("phases_completed", []),
                },
            )

        orchestrator = IngestOrchestrator(
            classifier=classifier,
            claude_client=claude_client,
            graph=graph,
            signal_writer=signal_writer,
            git_ops=git_ops,
            tools=tools,
            event_emitter=_emit,
        )

        result = await orchestrator.process(request, job_id=job_id, job_store=job_store)
        await _checkpoint(job_store, job_id)
        return result

    except Exception as e:
        logger.exception(f"[INGEST] Pipeline failed for job {job_id}: {e}")
        job_store[f"job:{job_id}"]["status"] = "failed"
        job_store[f"job:{job_id}"]["error"] = str(e)
        await _checkpoint(job_store, job_id)
        # Best-effort: notify any SSE clients waiting on this job.
        # This handles the case where the orchestrator itself never got a chance
        # to emit (e.g. import errors, constructor failures before the inner try).
        try:
            await sse_manager.send_event(job_id, "ingest_failed", {"error": str(e)})
        except Exception:
            pass
        return {"status": "failed", "error": str(e)}


def _get_claude_client():
    """Lazy-load Claude client singleton."""
    try:
        from ..services.claude_client import ClaudeClient

        return ClaudeClient()
    except Exception:
        return None


def _get_graph_service():
    """Lazy-load knowledge graph service."""
    try:
        from ..services.graph.factory import get_knowledge_graph

        return get_knowledge_graph()
    except Exception:
        return None


def _get_signal_writer():
    """Lazy-load signal graph writer."""
    try:
        from ..neo4j_client import get_neo4j_client
        from ..services.graph.signal_graph_writer import SignalGraphWriter

        client = get_neo4j_client()
        return SignalGraphWriter(client) if client else None
    except Exception:
        return None


def _get_git_ops():
    """Get the already-initialized git_ops singleton."""
    try:
        from ..git_ops import git_ops

        return git_ops
    except Exception:
        return None


def _get_extraction_tools(claude_client, git_ops):
    """Lazy-load extraction tools."""
    tools = {}
    try:
        from ..services.file_cache import file_cache
    except Exception:
        file_cache = None

    try:
        from ..services.tools.extract_entities import ExtractEntitiesTool

        tools["extract_entities"] = ExtractEntitiesTool(
            claude_client, git_ops, file_cache
        )
    except Exception as e:
        logger.warning(f"[INGEST] Failed to load extract_entities: {e}")

    try:
        from ..services.tools.extract_decisions import ExtractDecisionsTool

        tools["extract_decisions"] = ExtractDecisionsTool(
            claude_client, git_ops, file_cache
        )
    except Exception as e:
        logger.warning(f"[INGEST] Failed to load extract_decisions: {e}")

    try:
        from ..services.tools.infer_relationships import InferRelationshipsTool

        tools["infer_relationships"] = InferRelationshipsTool(
            claude_client, git_ops, file_cache
        )
    except Exception as e:
        logger.warning(f"[INGEST] Failed to load infer_relationships: {e}")

    try:
        from ..services.tools.enrich_decisions import EnrichDecisionsTool

        tools["enrich_decisions"] = EnrichDecisionsTool(
            claude_client, git_ops, file_cache
        )
    except Exception as e:
        logger.warning(f"[INGEST] Failed to load enrich_decisions: {e}")

    try:
        from ..services.tools.generate_insights import GenerateInsightsTool

        tools["generate_insights"] = GenerateInsightsTool(
            claude_client, git_ops, file_cache
        )
    except Exception as e:
        logger.warning(f"[INGEST] Failed to load generate_insights: {e}")

    try:
        from ..services.tools.extract_patterns import ExtractPatternsTool

        tools["extract_patterns"] = ExtractPatternsTool(
            claude_client, git_ops, file_cache
        )
    except Exception as e:
        logger.warning(f"[INGEST] Failed to load extract_patterns: {e}")

    return tools
