"""Per-phase ingest outcomes and the completed_with_warnings status.

A phase that degrades without raising (signal promotion crashed, the Neo4j
signal write failed) must not leave the job looking clean: it is recorded as
a ``failed`` phase outcome, the job ends ``completed_with_warnings`` and the
warning reaches the status endpoint, the delta report, the SSE phase events,
``submit_and_wait`` and the ``add_call_transcript`` MCP tool.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.ingestion.models import IngestRequest
from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.orchestrators import phase_outcomes as po
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

# ---------------------------------------------------------------------------
# phase_outcomes unit behaviour
# ---------------------------------------------------------------------------


def test_report_defaults_to_ok():
    report = po.PhaseReport("CLASSIFY")
    assert report.as_record() == {
        "phase": "CLASSIFY", "outcome": "ok", "detail": None, "retryable": False,
    }


def test_failed_outranks_empty_and_keeps_details():
    report = po.PhaseReport("ENRICH_GRAPH")
    report.empty("nothing new")
    report.failed("Neo4j signal write: boom", retryable=True)
    rec = report.as_record()
    assert rec["outcome"] == "failed"
    assert rec["retryable"] is True
    assert "Neo4j signal write: boom" in rec["detail"]


def test_retryable_only_reported_for_failures():
    report = po.PhaseReport("PERSIST")
    report.skipped("git storage unavailable")
    assert report.as_record() == {
        "phase": "PERSIST", "outcome": "skipped",
        "detail": "git storage unavailable", "retryable": False,
    }


def test_phase_report_outside_a_phase_is_detached():
    # Direct phase calls (unit tests, helpers) must not crash or leak.
    po.phase_report().failed("ignored")
    assert po.phase_report().outcome == "ok"


def test_derive_job_status():
    ok = [{"phase": "A", "outcome": "ok"}, {"phase": "B", "outcome": "skipped"}]
    warn = ok + [{"phase": "C", "outcome": "failed", "detail": "x"}]
    assert po.derive_job_status(ok) == "completed"
    assert po.derive_job_status(warn) == "completed_with_warnings"
    assert po.derive_job_status(warn, ["C"]) == "failed"
    assert po.warnings_from(warn) == ["C: x"]


def test_describe_error_is_one_bounded_line():
    detail = po.describe_error("signal promotion", RuntimeError("line one\nline two " + "x" * 900))
    assert detail.startswith("signal promotion: RuntimeError: line one line two")
    assert "\n" not in detail and len(detail) <= 500


# ---------------------------------------------------------------------------
# Pipeline: the orchestrator with mocked services
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_signal_store(monkeypatch):
    store = Mock()
    store.load_all = Mock(return_value=[])
    monkeypatch.setattr("app.services.signal_store.signal_store", store)
    return store


def _signals() -> MeetingSignals:
    return MeetingSignals(
        meeting_id="ingest-outcomes",
        bot_id="ingest-outcomes",
        meeting_title="Outcomes",
        signal_count=1,
        signals=[
            Signal(
                id="sig-1",
                type="decision",
                content="Adopt microservices",
                source_meeting_id="ingest-outcomes",
                source_timestamp="2026-03-24T00:00:00Z",
                entities=[EntityRef(id="person-alice", type="person", name="Alice")],
                confidence=0.9,
            )
        ],
    )


class _Emitter:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, event_type: str, data: dict) -> None:
        self.calls.append((event_type, data))

    def by_type(self, t: str) -> list[dict]:
        return [d for et, d in self.calls if et == t]


def _orchestrator(signal_writer=None, git_ops=None, emitter=None) -> IngestOrchestrator:
    classifier = Mock()
    classifier.classify = AsyncMock(return_value="call_transcript")
    graph = AsyncMock()
    graph.nodes = {}
    if signal_writer is None:
        signal_writer = AsyncMock()
        signal_writer.write_meeting_signals = AsyncMock(return_value=1)
    if git_ops is None:
        git_ops = Mock()
        git_ops.repo_path = "/nonexistent-test-repo"
        git_ops.commit_file = AsyncMock()
    return IngestOrchestrator(
        classifier=classifier,
        claude_client=None,  # no LLM: LLM-backed phases report skipped
        graph=graph,
        signal_writer=signal_writer,
        git_ops=git_ops,
        event_emitter=emitter,
    )


def _promote_returns(signals):
    return patch(
        "app.services.signal_promoter.SignalPromoter.promote",
        AsyncMock(return_value=signals),
    )


def _outcome(job: dict, phase: str) -> dict:
    return next(r for r in job["phase_outcomes"] if r["phase"] == phase)


@pytest.mark.asyncio
async def test_happy_path_is_completed_without_warnings():
    orch = _orchestrator()
    job_store: dict[str, Any] = {}
    with _promote_returns(_signals()):
        result = await orch.process(
            IngestRequest(content="Alice: we adopt microservices."), "job-ok", job_store
        )

    job = job_store["job:job-ok"]
    assert result["status"] == "completed"
    assert job["status"] == "completed"
    assert job["warnings"] == [] and result["warnings"] == []
    assert {r["outcome"] for r in job["phase_outcomes"]} <= {"ok", "empty", "skipped"}
    assert _outcome(job, "PROMOTE_SIGNALS")["outcome"] == "ok"
    assert _outcome(job, "ENRICH_GRAPH")["outcome"] == "ok"
    assert _outcome(job, "PERSIST")["outcome"] == "ok"
    assert result["delta_report"]["warnings"] == []


@pytest.mark.asyncio
async def test_signal_promotion_crash_completes_with_warnings():
    git_ops = Mock()
    git_ops.repo_path = "/nonexistent-test-repo"
    git_ops.commit_file = AsyncMock()
    orch = _orchestrator(git_ops=git_ops)
    job_store: dict[str, Any] = {}
    with patch(
        "app.services.signal_promoter.SignalPromoter.promote",
        AsyncMock(side_effect=RuntimeError("LLM overloaded")),
    ):
        result = await orch.process(
            IngestRequest(content="Alice: we adopt microservices."), "job-warn", job_store
        )

    job = job_store["job:job-warn"]
    assert result["status"] == "completed_with_warnings"
    assert job["status"] == "completed_with_warnings"
    promo = _outcome(job, "PROMOTE_SIGNALS")
    assert promo["outcome"] == "failed"
    assert promo["retryable"] is True
    assert "LLM overloaded" in promo["detail"]
    assert any(w.startswith("PROMOTE_SIGNALS:") for w in job["warnings"])
    # The delta report names the phase too
    assert any("PROMOTE_SIGNALS" in w for w in result["delta_report"]["warnings"])
    # ...and the rest of the output still persisted: the meeting file was written
    written = [c.args[0] for c in git_ops.commit_file.await_args_list]
    assert any(p.startswith("meetings/meeting-ingest-") for p in written)
    assert "COMPLETE" in job["phases_completed"]


@pytest.mark.asyncio
async def test_neo4j_signal_write_failure_is_a_warning():
    writer = AsyncMock()
    writer.write_meeting_signals = AsyncMock(side_effect=ConnectionError("neo4j down"))
    orch = _orchestrator(signal_writer=writer)
    job_store: dict[str, Any] = {}
    with _promote_returns(_signals()):
        result = await orch.process(
            IngestRequest(content="Alice: we adopt microservices."), "job-neo", job_store
        )

    job = job_store["job:job-neo"]
    assert result["status"] == "completed_with_warnings"
    graph = _outcome(job, "ENRICH_GRAPH")
    assert graph["outcome"] == "failed"
    assert "Neo4j signal write" in graph["detail"] and "neo4j down" in graph["detail"]
    assert job["warnings"] == [f"ENRICH_GRAPH: {graph['detail']}"]


@pytest.mark.asyncio
async def test_corpus_write_failure_fails_the_job():
    git_ops = Mock()
    git_ops.repo_path = "/nonexistent-test-repo"
    git_ops.commit_file = AsyncMock(side_effect=OSError("disk full"))
    emitter = _Emitter()
    orch = _orchestrator(git_ops=git_ops, emitter=emitter)
    job_store: dict[str, Any] = {}
    with _promote_returns(_signals()):
        result = await orch.process(
            IngestRequest(content="Alice: we adopt microservices."), "job-persist", job_store
        )

    job = job_store["job:job-persist"]
    assert result["status"] == "failed"
    assert job["status"] == "failed"
    assert "PERSIST" in job["error"] and "disk full" in job["error"]
    assert _outcome(job, "PERSIST")["outcome"] == "failed"
    assert emitter.by_type("ingest_failed") and not emitter.by_type("ingest_complete")


@pytest.mark.asyncio
async def test_raising_phase_is_recorded_before_the_job_fails():
    orch = _orchestrator()
    orch._phase_classify = AsyncMock(side_effect=RuntimeError("classifier exploded"))
    job_store: dict[str, Any] = {}
    result = await orch.process(IngestRequest(content="hello"), "job-raise", job_store)

    job = job_store["job:job-raise"]
    assert result["status"] == "failed"
    assert _outcome(job, "CLASSIFY")["outcome"] == "failed"
    assert job["warnings"][0].startswith("CLASSIFY:")


@pytest.mark.asyncio
async def test_sse_phase_events_carry_outcomes():
    emitter = _Emitter()
    orch = _orchestrator(emitter=emitter)
    with patch(
        "app.services.signal_promoter.SignalPromoter.promote",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await orch.process(IngestRequest(content="hello there"), "job-sse", {})

    completed = [e for e in emitter.by_type("ingest_phase") if e["status"] == "completed"]
    promo = next(e for e in completed if e["phase"] == "PROMOTE_SIGNALS")
    assert promo["outcome"] == "failed"
    assert promo["retryable"] is True
    assert "boom" in promo["detail"]
    assert any(w.startswith("PROMOTE_SIGNALS:") for w in promo["warnings"])
    assert {"phase", "outcome", "detail", "retryable"} <= set(promo["phase_outcomes"][-1])
    done = emitter.by_type("ingest_complete")[0]
    assert done["status"] == "completed_with_warnings"
    assert done["warnings"] and done["phase_outcomes"]


# ---------------------------------------------------------------------------
# HTTP payloads
# ---------------------------------------------------------------------------

_OUTCOMES = [
    {"phase": "CLASSIFY", "outcome": "ok", "detail": None, "retryable": False},
    {
        "phase": "PROMOTE_SIGNALS", "outcome": "failed",
        "detail": "signal promotion: RuntimeError: boom", "retryable": True,
    },
]
_WARNINGS = ["PROMOTE_SIGNALS: signal promotion: RuntimeError: boom"]


def _client(job_store: dict) -> TestClient:
    from app.routes.ingest import _get_job_store, router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[_get_job_store] = lambda: job_store
    return TestClient(app)


def _warned_job() -> dict:
    return {
        "job:job-w": {
            "job_id": "job-w",
            "status": "completed_with_warnings",
            "content_type": "call_transcript",
            "phases_completed": ["CLASSIFY", "PROMOTE_SIGNALS", "COMPLETE"],
            "phase_outcomes": _OUTCOMES,
            "warnings": _WARNINGS,
            "current_phase": None,
            "result": {"content_hash": "abc", "delta_report": {"job_id": "job-w", "counts": {}}},
            "error": None,
        }
    }


def test_status_endpoint_includes_outcomes_and_warnings():
    body = _client(_warned_job()).get("/api/ingest/job-w/status").json()
    assert body["status"] == "completed_with_warnings"
    assert body["phase_outcomes"] == _OUTCOMES
    assert body["warnings"] == _WARNINGS


def test_status_endpoint_defaults_for_legacy_job_records():
    store = _warned_job()
    del store["job:job-w"]["phase_outcomes"], store["job:job-w"]["warnings"]
    body = _client(store).get("/api/ingest/job-w/status").json()
    assert body["phase_outcomes"] == [] and body["warnings"] == []


def test_delta_endpoint_includes_outcomes_and_warnings():
    body = _client(_warned_job()).get("/api/ingest/job-w/delta").json()
    assert body["job_id"] == "job-w"
    assert body["phase_outcomes"] == _OUTCOMES
    assert body["warnings"] == _WARNINGS


# ---------------------------------------------------------------------------
# submit_and_wait + add_call_transcript
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_and_wait_surfaces_warnings(monkeypatch):
    from app.models.ingestion.models import ContentSource
    from app.routes import ingest

    store: dict = {}

    def fake_enqueue(request, job_store, task_queue):
        job_store["job:ingest-w"] = {
            "status": "completed_with_warnings",
            "content_type": "call_transcript",
            "phase_outcomes": _OUTCOMES,
            "warnings": _WARNINGS,
            "result": {"content_hash": "deadbeefcafe0000"},
        }
        return "ingest-w", "accepted"

    monkeypatch.setattr(ingest, "enqueue_ingestion", fake_enqueue)
    monkeypatch.setattr(ingest, "_get_job_store", lambda: store)
    monkeypatch.setattr(ingest, "_get_task_queue", lambda: None)

    out = await ingest.submit_and_wait(
        IngestRequest(content="hi", source=ContentSource.LOCAL_RECORDING), timeout_s=1.0
    )
    assert out["state"] == "completed_with_warnings"
    assert out["warnings"] == _WARNINGS
    assert out["phase_outcomes"] == _OUTCOMES


@pytest.mark.asyncio
async def test_add_call_transcript_surfaces_warnings():
    from app.services.chat_tools import add_call_transcript

    fake = AsyncMock(return_value={
        "state": "completed_with_warnings",
        "job_id": "ingest-w",
        "poll_url": "/api/ingest/ingest-w/status",
        "content_type": "call_transcript",
        "result": {"content_hash": "abc123def4567890"},
        "phase_outcomes": _OUTCOMES,
        "warnings": _WARNINGS,
    })
    with patch("app.routes.ingest.submit_and_wait", fake):
        out = await add_call_transcript(
            "[00:00] Alice: hello", "2026-06-04T14:30:00Z", ["Alice"]
        )
    assert out["status"] == "completed_with_warnings"
    assert out["warnings"] == _WARNINGS
    assert "warnings" in out["next"]


@pytest.mark.asyncio
async def test_add_call_transcript_clean_run_has_empty_warnings():
    from app.services.chat_tools import add_call_transcript

    fake = AsyncMock(return_value={
        "state": "completed",
        "job_id": "ingest-c",
        "poll_url": "/api/ingest/ingest-c/status",
        "content_type": "call_transcript",
        "result": {"content_hash": "abc123def4567890"},
        "phase_outcomes": [],
        "warnings": [],
    })
    with patch("app.routes.ingest.submit_and_wait", fake):
        out = await add_call_transcript(
            "[00:00] Alice: hello", "2026-06-04T14:30:00Z", ["Alice"]
        )
    assert out["status"] == "completed"
    assert out["warnings"] == []
