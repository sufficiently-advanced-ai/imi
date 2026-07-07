"""IngestOrchestrator.process_observation — rebuild-replay seam.

Runs phases 3-9 (PROMOTE_SIGNALS → COMPLETE) for a pre-built Observation,
preserving the caller-supplied bot_id so re-extraction overwrites the
meeting's signals/markdown in place. Mirrors the mock conventions of
tests/test_ingest.py::TestIngestOrchestrator.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.observation import Observation
from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator


@pytest.fixture
def mock_meeting_signals():
    return MeetingSignals(
        meeting_id="replay-test",
        bot_id="ingest-legacy01",
        meeting_title="Legacy Meeting",
        signal_count=2,
        signals=[
            Signal(
                id="sig-1",
                type="decision",
                content="Adopt microservices",
                source_meeting_id="ingest-legacy01",
                source_timestamp="2026-03-24T00:00:00Z",
                entities=[EntityRef(id="person-alice", type="person", name="Alice")],
                confidence=0.9,
            ),
            Signal(
                id="sig-2",
                type="key_point",
                content="Onboarding takes 14 days",
                source_meeting_id="ingest-legacy01",
                source_timestamp="2026-03-24T00:00:00Z",
                confidence=0.8,
            ),
        ],
    )


@pytest.fixture
def orchestrator(tmp_path, mock_meeting_signals):
    classifier = Mock()
    classifier.classify = AsyncMock(return_value="call_transcript")
    signal_writer = AsyncMock()
    signal_writer.write_meeting_signals = AsyncMock(return_value=2)
    git_ops = Mock()
    git_ops.repo_path = str(tmp_path / "test_repo")
    git_ops.commit_file = AsyncMock()

    orch = IngestOrchestrator(
        classifier=classifier,
        claude_client=Mock(),
        graph=AsyncMock(),
        signal_writer=signal_writer,
        git_ops=git_ops,
    )

    async def mock_promote(observation):
        return mock_meeting_signals

    orch._phase_promote_signals = lambda ms: mock_promote(ms)
    return orch


def _observation(bot_id: str = "ingest-legacy01") -> Observation:
    return Observation(
        observation_id=bot_id,
        external_id=bot_id,
        observed_at=datetime(2026, 3, 24, tzinfo=UTC),
        content="# Legacy Meeting\n\n## Discussion\nAlice: We adopt microservices.",
        raw_content="Alice: We adopt microservices.",
        entities_mentioned={"person": ["Alice"]},
        title="Legacy Meeting",
        participants=["Alice"],
    )


@pytest.mark.asyncio
async def test_process_observation_runs_phases_3_through_9(orchestrator):
    job_store = {}
    result = await orchestrator.process_observation(
        _observation(), "ingest-legacy01", "job-replay", job_store
    )

    assert result["status"] == "completed"
    phases = job_store["job:job-replay"]["phases_completed"]
    assert "PROMOTE_SIGNALS" in phases
    assert "ENRICH_GRAPH" in phases
    assert "PERSIST" in phases
    assert "COMPLETE" in phases
    # Phases 1-2 must NOT run — bot_id identity is preserved, not re-derived
    assert "CLASSIFY" not in phases
    assert "BUILD_MEETING" not in phases


@pytest.mark.asyncio
async def test_process_observation_aggregates_counts(orchestrator):
    result = await orchestrator.process_observation(
        _observation(), "ingest-legacy01", "job-counts", {}
    )
    assert result["decisions_found"] == 1
    assert result["insights_generated"] == 1
    assert result["signals_written"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_bot_id",
    ["../escape", "a/b", "x" * 129, "", "id with spaces", "semi;colon"],
)
async def test_process_observation_rejects_unsafe_bot_ids(orchestrator, bad_bot_id):
    """bot_id reaches persistence file paths — traversal characters refused."""
    with pytest.raises(ValueError, match="bot_id"):
        await orchestrator.process_observation(
            _observation(), bad_bot_id, "job-bad-id", {}
        )


@pytest.mark.asyncio
async def test_process_observation_failure_marks_job_failed(orchestrator):
    async def failing_promote(observation):
        raise RuntimeError("promoter exploded")

    orchestrator._phase_promote_signals = lambda ms: failing_promote(ms)
    job_store = {}
    result = await orchestrator.process_observation(
        _observation(), "ingest-legacy01", "job-fail", job_store
    )
    assert result["status"] == "failed"
    assert job_store["job:job-fail"]["status"] == "failed"
    assert "promoter exploded" in job_store["job:job-fail"]["error"]


@pytest.mark.asyncio
async def test_process_returns_same_result_shape_as_before(orchestrator):
    """The process() path still aggregates identically after the extraction."""
    from app.models.ingestion.models import IngestRequest

    request = IngestRequest(content="Alice: We should adopt microservices.")
    result = await orchestrator.process(request, job_id="job-shape", job_store={})
    assert result["status"] == "completed"
    assert result["decisions_found"] == 1
    assert result["signals_written"] == 2
    assert result["content_hash"]
