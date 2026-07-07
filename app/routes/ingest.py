"""
Ingest API Routes — General-purpose content inbox (Issue #863).

Endpoints:
  POST /api/ingest          — Accept content, enqueue processing, return job_id
  GET  /api/ingest/{id}/status — Poll job progress by phase
  GET  /api/ingest/jobs     — List recent ingestion jobs
"""

import asyncio
import hashlib
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from ..models.ingestion.models import (
    IngestJobStatus,
    IngestRequest,
    IngestResponse,
)
from ..services.sse_manager import sse_manager
from ..services.task_queue import TaskQueue, global_task_queue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingestion"])

# In-memory job store — maps content hashes, source_ids, and job statuses.
# Keys: "job:{job_id}", "{content_hash}", "source_id:{source_id}"
# NOTE: Ephemeral — lost on restart. Acceptable for MVP single-tenant deployment.
# For production/bulk ingestion, swap for Redis or database-backed store.
_job_store: dict[str, Any] = {}

# Max content size: 500KB
MAX_CONTENT_BYTES = 500 * 1024


def _get_task_queue() -> TaskQueue:
    """Dependency: return the global task queue."""
    return global_task_queue


def _get_job_store() -> dict[str, Any]:
    """Dependency: return the shared job store."""
    return _job_store


def enqueue_ingestion(
    request: IngestRequest,
    job_store: dict[str, Any],
    task_queue: TaskQueue,
) -> tuple[str, str]:
    """Dedup, register, and enqueue an ingestion job.

    Shared by the HTTP route (`ingest_content`) and the in-process
    `submit_and_wait` helper so both surfaces use one dedup + enqueue codepath
    (avoids the two-copies-drift class of bug). Does NOT enforce the size limit
    — callers do that with the error type appropriate to their surface
    (HTTPException for the route, an error dict for the MCP tool).

    Returns:
        (job_id, status) where status is "accepted" for a new job or
        "duplicate" when the content/source_id was already seen. For a
        duplicate, job_id points at the pre-existing job.
    """
    # Idempotency: check source_id first
    if request.source_id:
        source_key = f"source_id:{request.source_id}"
        if source_key in job_store:
            return job_store[source_key]["job_id"], "duplicate"

    # Idempotency: check content hash
    content_hash = hashlib.sha256(request.content.encode()).hexdigest()
    if content_hash in job_store:
        return job_store[content_hash]["job_id"], "duplicate"

    # Create new job
    job_id = f"ingest-{uuid.uuid4().hex[:12]}"

    # Store hash and source_id mappings for dedup
    job_store[content_hash] = {"job_id": job_id, "content_hash": content_hash}
    if request.source_id:
        job_store[f"source_id:{request.source_id}"] = {"job_id": job_id}

    # Initialize job status
    job_store[f"job:{job_id}"] = {
        "job_id": job_id,
        "status": "pending",
        "content_type": None,
        "phases_completed": [],
        "current_phase": None,
        "result": None,
        "error": None,
        "created_at": datetime.now(UTC).isoformat(),
    }

    # Enqueue async processing
    task_queue.enqueue(
        _run_ingestion_pipeline,
        request,
        job_id,
        job_store,
        task_id=job_id,
        priority=1,
    )

    return job_id, "accepted"


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
      - "completed": includes `job_id`, `poll_url`, `content_type`, `result`
      - "failed":    includes `job_id`, `poll_url`, `error`
      - "pending":   timed out before a terminal state; includes `job_id`,
                     `poll_url` so the caller can poll later
    """
    job_store = _get_job_store()
    task_queue = _get_task_queue()

    job_id, _status = enqueue_ingestion(request, job_store, task_queue)
    job_key = f"job:{job_id}"
    poll_url = f"/api/ingest/{job_id}/status"

    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        job = job_store.get(job_key)
        if job:
            state = job.get("status")
            if state == "completed":
                return {
                    "state": "completed",
                    "job_id": job_id,
                    "poll_url": poll_url,
                    "content_type": job.get("content_type"),
                    "result": job.get("result") or {},
                }
            if state == "failed":
                return {
                    "state": "failed",
                    "job_id": job_id,
                    "poll_url": poll_url,
                    "error": job.get("error") or "unknown error",
                }
        if time.monotonic() >= deadline:
            return {"state": "pending", "job_id": job_id, "poll_url": poll_url}
        await asyncio.sleep(poll_interval_s)


@router.post("", status_code=202, response_model=IngestResponse)
async def ingest_content(
    request: IngestRequest,
    response: Response,
    task_queue: TaskQueue = Depends(_get_task_queue),
    job_store: dict[str, Any] = Depends(_get_job_store),
) -> IngestResponse:
    """Accept content for ingestion and enqueue processing.

    Returns 202 Accepted with job_id for new content,
    200 OK with status "duplicate" for already-processed content.
    413 if content exceeds size limit.
    """
    # Size check
    if len(request.content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise HTTPException(status_code=413, detail="Content exceeds 500KB limit")

    job_id, status = enqueue_ingestion(request, job_store, task_queue)
    if status == "duplicate":
        response.status_code = 200

    return IngestResponse(
        job_id=job_id,
        status=status,
        poll_url=f"/api/ingest/{job_id}/status",
    )


@router.get("/jobs")
async def list_jobs(
    job_store: dict[str, Any] = Depends(_get_job_store),
) -> list:
    """List recent ingestion jobs."""
    jobs = []
    for key, value in job_store.items():
        if key.startswith("job:") and isinstance(value, dict) and "job_id" in value:
            jobs.append(value)

    # Sort by created_at descending if available
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return jobs


@router.get("/{job_id}/delta")
async def get_job_delta(
    job_id: str,
    job_store: dict[str, Any] = Depends(_get_job_store),
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

    return delta_report


@router.get("/{job_id}/status", response_model=IngestJobStatus)
async def get_job_status(
    job_id: str,
    job_store: dict[str, Any] = Depends(_get_job_store),
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
        current_phase=job_data.get("current_phase"),
        result=job_data.get("result"),
        error=job_data.get("error"),
    )


_INGEST_TERMINAL_TYPES = frozenset({"ingest_complete", "ingest_failed"})


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
    job_store: dict[str, Any],
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

        return await orchestrator.process(request, job_id=job_id, job_store=job_store)

    except Exception as e:
        logger.exception(f"[INGEST] Pipeline failed for job {job_id}: {e}")
        job_store[f"job:{job_id}"]["status"] = "failed"
        job_store[f"job:{job_id}"]["error"] = str(e)
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
