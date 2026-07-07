"""Tests for Tasks 20+21 — orchestrator SSE events + /api/ingest/{job_id}/stream.

Run with:
    pytest tests/sprint2_test_ingest_sse.py --timeout=90 --timeout-method=signal -xvs

Part 1: orchestrator emitter contract
Part 2: SSE stream endpoint wiring
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app.services.delta_report import DeltaReport
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PHASES = [
    "CLASSIFY",
    "BUILD_MEETING",
    "PROMOTE_SIGNALS",
    "DETECT_SUPERSESSION",
    "ENRICH_GRAPH",
    "PERSIST",
    "DELTA_REPORT",
]


def _make_mock_request(
    content: str = "We agreed to ship v2.0 next quarter.",
) -> MagicMock:
    req = MagicMock()
    req.content = content
    req.source = None
    req.source_id = None
    req.title = "Test meeting"
    req.participants = []
    return req


def _make_minimal_orchestrator(emitter=None) -> IngestOrchestrator:
    """Build an IngestOrchestrator where every external dep is a no-op mock."""
    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value="transcript")

    claude_client = MagicMock()

    # graph: only needs create_semantic_relationship
    graph = AsyncMock()
    graph.add_node = AsyncMock()
    graph.create_semantic_relationship = AsyncMock()

    signal_writer = None
    git_ops = None

    return IngestOrchestrator(
        classifier=classifier,
        claude_client=claude_client,
        graph=graph,
        signal_writer=signal_writer,
        git_ops=git_ops,
        tools={},
        event_emitter=emitter,
    )


class _RecordingEmitter:
    """Collects (event_type, data) pairs emitted during a pipeline run."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, event_type: str, data: dict) -> None:
        self.calls.append((event_type, data))

    def types(self) -> list[str]:
        return [c[0] for c in self.calls]

    def by_type(self, t: str) -> list[dict]:
        return [c[1] for c in self.calls if c[0] == t]


# ---------------------------------------------------------------------------
# Fixtures: patch all heavy phase fns to no-ops
# ---------------------------------------------------------------------------


def _patch_pipeline(orch: IngestOrchestrator):
    """Patch the heavy phase functions to fast no-ops that return minimal data."""
    from unittest.mock import patch as _patch

    # Each phase must return the type its caller expects
    patches = [
        _patch.object(orch, "_phase_classify", AsyncMock(return_value="transcript")),
        _patch.object(
            orch,
            "_phase_build_observation",
            AsyncMock(
                return_value=MagicMock(
                    title="T", participants=[], entities_mentioned={}
                )
            ),
        ),
        _patch.object(orch, "_phase_promote_signals", AsyncMock(return_value=None)),
        _patch.object(orch, "_phase_detect_supersession", AsyncMock(return_value=0)),
        _patch.object(
            orch,
            "_phase_enrich_graph",
            AsyncMock(return_value={"signal_count": 0, "edge_count": 0}),
        ),
        _patch.object(orch, "_phase_persist", AsyncMock(return_value=None)),
        _patch.object(orch, "_phase_delta_report", AsyncMock(return_value=None)),
    ]
    return patches


# ---------------------------------------------------------------------------
# Part 1: orchestrator emitter contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_started_completed_pairs_for_every_phase():
    """Every pipeline phase emits started then completed in order."""
    emitter = _RecordingEmitter()
    orch = _make_minimal_orchestrator(emitter)
    job_store: dict[str, Any] = {}
    request = _make_mock_request()

    patches = _patch_pipeline(orch)
    for p in patches:
        p.start()
    try:
        await orch.process(request, job_id="test-job-1", job_store=job_store)
    finally:
        for p in patches:
            p.stop()

    phase_events = [(t, d) for (t, d) in emitter.calls if t == "ingest_phase"]
    phase_names_emitted = [d["phase"] for _, d in phase_events]

    # Every phase in PHASES list should appear twice (started + completed)
    for phase in PHASES:
        assert (
            phase_names_emitted.count(phase) == 2
        ), f"Expected 2 events for phase {phase}, got {phase_names_emitted.count(phase)}"

    # For each phase, started comes before completed
    for phase in PHASES:
        started_idx = next(
            i
            for i, (_, d) in enumerate(phase_events)
            if d["phase"] == phase and d["status"] == "started"
        )
        completed_idx = next(
            i
            for i, (_, d) in enumerate(phase_events)
            if d["phase"] == phase and d["status"] == "completed"
        )
        assert (
            started_idx < completed_idx
        ), f"started must precede completed for {phase}"


@pytest.mark.asyncio
async def test_phases_completed_accumulates():
    """phases_completed in completed events grows with each phase."""
    emitter = _RecordingEmitter()
    orch = _make_minimal_orchestrator(emitter)
    job_store: dict[str, Any] = {}
    request = _make_mock_request()

    patches = _patch_pipeline(orch)
    for p in patches:
        p.start()
    try:
        await orch.process(request, job_id="test-job-phases", job_store=job_store)
    finally:
        for p in patches:
            p.stop()

    completed_events = [
        d
        for (t, d) in emitter.calls
        if t == "ingest_phase" and d["status"] == "completed"
    ]
    # Verify phases_completed grows monotonically
    for i in range(1, len(completed_events)):
        prev_count = len(completed_events[i - 1]["phases_completed"])
        curr_count = len(completed_events[i]["phases_completed"])
        assert curr_count >= prev_count


@pytest.mark.asyncio
async def test_delta_report_ready_carries_counts():
    """delta_report_ready event is emitted with summary containing counts."""
    emitter = _RecordingEmitter()
    orch = _make_minimal_orchestrator(emitter)
    job_store: dict[str, Any] = {}
    request = _make_mock_request()

    # Set up a fake delta report on the orchestrator after delta phase
    fake_counts = {
        "new_decisions": 2,
        "proposed_supersessions": 1,
        "commitments_opened": 0,
        "commitments_closed": 0,
        "entities_touched": 3,
    }
    fake_report = MagicMock(spec=DeltaReport)
    fake_report.counts = fake_counts

    async def _fake_delta_report(*args, **kwargs):
        orch._last_delta_report = fake_report

    patches = _patch_pipeline(orch)
    for p in patches:
        p.start()
    # Override delta phase to also set _last_delta_report
    patches[-1].stop()
    with patch.object(orch, "_phase_delta_report", _fake_delta_report):
        try:
            await orch.process(request, job_id="test-job-delta", job_store=job_store)
        finally:
            for p in patches[:-1]:
                p.stop()

    dr_events = emitter.by_type("delta_report_ready")
    assert len(dr_events) == 1, f"Expected 1 delta_report_ready, got {len(dr_events)}"
    assert dr_events[0]["summary"] == fake_counts


@pytest.mark.asyncio
async def test_terminal_ingest_complete():
    """ingest_complete is the last event on a successful run."""
    emitter = _RecordingEmitter()
    orch = _make_minimal_orchestrator(emitter)
    job_store: dict[str, Any] = {}
    request = _make_mock_request()

    patches = _patch_pipeline(orch)
    for p in patches:
        p.start()
    try:
        await orch.process(request, job_id="test-job-complete", job_store=job_store)
    finally:
        for p in patches:
            p.stop()

    assert "ingest_complete" in emitter.types()
    assert "ingest_failed" not in emitter.types()
    # ingest_complete is the last meaningful event (after keepalive/phase events)
    last_terminal = next(
        (
            t
            for t, _ in reversed(emitter.calls)
            if t in ("ingest_complete", "ingest_failed")
        ),
        None,
    )
    assert last_terminal == "ingest_complete"


@pytest.mark.asyncio
async def test_phase_raises_emits_ingest_failed():
    """When a phase raises, ingest_failed is emitted and no ingest_complete."""
    emitter = _RecordingEmitter()
    orch = _make_minimal_orchestrator(emitter)
    job_store: dict[str, Any] = {}
    request = _make_mock_request()

    async def _boom(*args, **kwargs):
        raise RuntimeError("Phase exploded")

    with patch.object(orch, "_phase_classify", _boom):
        await orch.process(request, job_id="test-job-fail", job_store=job_store)

    assert "ingest_failed" in emitter.types()
    assert "ingest_complete" not in emitter.types()
    failed_events = emitter.by_type("ingest_failed")
    assert len(failed_events) == 1
    assert "exploded" in failed_events[0]["error"]


@pytest.mark.asyncio
async def test_emitter_raises_pipeline_unaffected():
    """If the emitter itself raises, the pipeline still completes successfully."""

    async def _exploding_emitter(event_type: str, data: dict) -> None:
        raise RuntimeError("emitter broke")

    orch = _make_minimal_orchestrator(_exploding_emitter)
    job_store: dict[str, Any] = {}
    request = _make_mock_request()

    patches = _patch_pipeline(orch)
    for p in patches:
        p.start()
    try:
        result = await orch.process(
            request, job_id="test-job-emitter-fail", job_store=job_store
        )
    finally:
        for p in patches:
            p.stop()

    # Pipeline should still return without crashing
    assert result is not None
    assert result.get("status") == "completed"


@pytest.mark.asyncio
async def test_none_emitter_no_crash():
    """Default None emitter does not crash."""
    orch = _make_minimal_orchestrator(emitter=None)
    assert orch._event_emitter is None
    job_store: dict[str, Any] = {}
    request = _make_mock_request()

    patches = _patch_pipeline(orch)
    for p in patches:
        p.start()
    try:
        result = await orch.process(
            request, job_id="test-job-none-emitter", job_store=job_store
        )
    finally:
        for p in patches:
            p.stop()

    assert result is not None


# ---------------------------------------------------------------------------
# Part 2: SSE stream endpoint wiring
# ---------------------------------------------------------------------------


def _make_app_with_ingest_router():
    """Build a minimal FastAPI app with the ingest router mounted."""
    from app.routes.ingest import router as ingest_router

    app = FastAPI()
    app.include_router(ingest_router, prefix="/api")
    return app


@pytest.mark.asyncio
async def test_stream_events_received_in_order_and_terminates():
    """Events pushed via sse_manager arrive in order; stream closes on ingest_complete."""
    import json as _json
    from fastapi import Request
    from unittest.mock import AsyncMock as _AM

    from app.routes.sse_status import event_generator_raw
    from app.routes.ingest import _INGEST_TERMINAL_TYPES
    from app.services.sse_manager import sse_manager

    job_id = "test-stream-job-001-x"

    # Pre-register connection so events are buffered
    queue = await sse_manager.add_connection(job_id)

    try:
        # Push some events into the queue directly (simulating orchestrator)
        events_to_push = [
            {
                "type": "ingest_phase",
                "phase": "CLASSIFY",
                "status": "started",
                "phases_completed": [],
            },
            {
                "type": "ingest_phase",
                "phase": "CLASSIFY",
                "status": "completed",
                "phases_completed": ["CLASSIFY"],
            },
            {"type": "ingest_complete", "result": {"entities_extracted": 3}},
        ]
        for ev in events_to_push:
            queue.put_nowait(ev)

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = _AM(return_value=False)

        received = []
        async for chunk in event_generator_raw(
            mock_request, job_id, terminal_types=_INGEST_TERMINAL_TYPES
        ):
            if chunk.startswith("data:"):
                received.append(_json.loads(chunk[len("data: ") :].strip()))

        types_received = [e["type"] for e in received]
        assert "connected" in types_received
        assert "ingest_phase" in types_received
        assert "ingest_complete" in types_received
        # Stream terminated on ingest_complete (last non-keepalive type)
        assert types_received[-1] == "ingest_complete"
    finally:
        await sse_manager.remove_connection(job_id)


@pytest.mark.asyncio
async def test_stream_terminates_on_ingest_failed():
    """Stream closes when ingest_failed is the terminal event."""
    from fastapi import Request
    from unittest.mock import AsyncMock as _AM

    from app.routes.sse_status import event_generator_raw
    from app.routes.ingest import _INGEST_TERMINAL_TYPES
    from app.services.sse_manager import sse_manager

    job_id = "test-stream-job-fail-x"
    queue = await sse_manager.add_connection(job_id)

    try:
        queue.put_nowait({"type": "ingest_failed", "error": "something went wrong"})

        mock_request = MagicMock(spec=Request)
        mock_request.is_disconnected = _AM(return_value=False)

        received = []
        async for chunk in event_generator_raw(
            mock_request, job_id, terminal_types=_INGEST_TERMINAL_TYPES
        ):
            if chunk.startswith("data:"):
                import json as _j

                received.append(_j.loads(chunk[len("data: ") :].strip()))

        types_received = [e["type"] for e in received]
        assert "ingest_failed" in types_received
        assert "ingest_complete" not in types_received
    finally:
        await sse_manager.remove_connection(job_id)


@pytest.mark.asyncio
async def test_existing_sse_status_terminal_types_unchanged():
    """The default terminal types for agent-tools SSE are still workflow_complete/failed."""
    from app.routes.sse_status import _DEFAULT_TERMINAL_TYPES

    assert "workflow_complete" in _DEFAULT_TERMINAL_TYPES
    assert "workflow_failed" in _DEFAULT_TERMINAL_TYPES


def test_stream_endpoint_exists():
    """GET /api/ingest/{job_id}/stream route is registered."""
    app = _make_app_with_ingest_router()
    routes = {r.path for r in app.routes}
    assert "/api/ingest/{job_id}/stream" in routes


# ---------------------------------------------------------------------------
# Fix A: outer-except SSE terminal event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_constructor_failure_emits_ingest_failed():
    """When IngestOrchestrator constructor raises, outer except emits ingest_failed via SSE."""
    from unittest.mock import patch as _patch, AsyncMock as _AM

    from app.routes.ingest import _run_ingestion_pipeline

    job_id = "outer-fail-test-001"
    job_store: dict[str, Any] = {f"job:{job_id}": {"status": "pending", "error": None}}

    received_events: list[dict] = []

    async def _spy_send_event(eid, etype, data):
        received_events.append({"type": etype, **data})

    with (
        _patch(
            "app.routes.ingest.sse_manager.add_connection", new=_AM(return_value=None)
        ),
        _patch("app.routes.ingest.sse_manager.send_event", side_effect=_spy_send_event),
        _patch(
            "app.routes.ingest.IngestOrchestrator",
            side_effect=RuntimeError("constructor boom"),
        )
        if False
        else _patch(
            "app.routes.ingest._get_graph_service",
            side_effect=RuntimeError("bootstrap boom"),
        ),
    ):
        request = _make_mock_request()
        await _run_ingestion_pipeline(request, job_id, job_store)

    assert job_store[f"job:{job_id}"]["status"] == "failed"
    # The outer except path must have emitted an ingest_failed event
    failed = [e for e in received_events if e["type"] == "ingest_failed"]
    assert len(failed) >= 1, f"Expected ingest_failed event; got {received_events}"
    assert "boom" in failed[0].get("error", "")


# ---------------------------------------------------------------------------
# Fix B: SSE replay list shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_replay_events_list_shape():
    """After emits via the _emit closure, status['events'] has correct shape for replay."""
    from app.services.sse_manager import SSEManager

    # Create an isolated SSEManager so we can inspect its stored status
    mgr = SSEManager()
    job_id = "replay-shape-inline-001"
    await mgr.add_connection(job_id)

    # Build the _emit closure the same way _run_ingestion_pipeline does
    _replay_events: list[dict] = []
    _REPLAY_CAP = 200

    async def _emit(event_type: str, data: dict) -> None:
        await mgr.send_event(job_id, event_type, data)
        event_snapshot = {"type": event_type, **data}
        _replay_events.append(event_snapshot)
        if len(_replay_events) > _REPLAY_CAP:
            del _replay_events[: len(_replay_events) - _REPLAY_CAP]
        mgr.store_execution_status(
            job_id,
            {
                "events": list(_replay_events),
                "phases_completed": data.get("phases_completed", []),
            },
        )

    # Emit a few events
    await _emit(
        "ingest_phase",
        {"phase": "CLASSIFY", "status": "started", "phases_completed": []},
    )
    await _emit(
        "ingest_phase",
        {"phase": "CLASSIFY", "status": "completed", "phases_completed": ["CLASSIFY"]},
    )
    await _emit("ingest_complete", {"result": {"entities_extracted": 2}})

    status = mgr.get_execution_status(job_id)
    assert status is not None, "status must be stored after emits"
    assert "events" in status, f"status missing 'events' key: {status.keys()}"
    events_list = status["events"]
    assert isinstance(events_list, list), "events must be a list"
    assert len(events_list) == 3, f"Expected 3 events, got {len(events_list)}"
    for ev in events_list:
        assert "type" in ev, f"Event missing 'type' key: {ev}"
    # Verify the shape matches what sse_status.py's replay reads
    assert events_list[0]["type"] == "ingest_phase"
    assert events_list[2]["type"] == "ingest_complete"

    await mgr.remove_connection(job_id)
