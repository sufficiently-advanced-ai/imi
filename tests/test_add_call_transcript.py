"""
Tests for the add_call_transcript MCP tool (call-transcript ingestion wrapper).

Covers:
- ContentSource / classifier mapping for local recorders (plaud, local_recording)
- Hard-require validation of start_time + participants (and transcript, source)
- Return shaping: completed summary, timeout -> job_id, failure -> error
- The IngestRequest the tool builds (source mapping, conversation_id passthrough)
- submit_and_wait polling of the in-memory job store

Run inside the dev container, which has the full app environment:
    docker exec main-dev pytest tests/test_add_call_transcript.py -v
"""

from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Source enum + classifier mapping
# ---------------------------------------------------------------------------

def test_content_source_has_local_recorders():
    from app.models.ingestion.models import ContentSource
    assert ContentSource.PLAUD == "plaud"
    assert ContentSource.LOCAL_RECORDING == "local_recording"


@pytest.mark.parametrize("source", ["plaud", "local_recording", "grain", "otter"])
def test_classifier_maps_recorders_to_call_transcript(source):
    from app.services.ingest_classifier import IngestClassifier
    assert IngestClassifier(claude_client=None).map_source_to_type(source) == "call_transcript"


# ---------------------------------------------------------------------------
# Validation — these reject before any pipeline work, so no mocking needed
# ---------------------------------------------------------------------------

GOOD_START = "2026-06-04T14:30:00Z"
GOOD_PARTS = ["Alice Smith", "Bob Jones"]
GOOD_TRANSCRIPT = "[00:00] Alice: Welcome. [00:05] Bob: Thanks, let's decide the launch date."


@pytest.mark.asyncio
async def test_rejects_empty_transcript():
    from app.services.chat_tools import add_call_transcript
    out = await add_call_transcript("   ", GOOD_START, GOOD_PARTS)
    assert "error" in out and "transcript" in out["error"]


@pytest.mark.asyncio
async def test_rejects_missing_start_time():
    from app.services.chat_tools import add_call_transcript
    out = await add_call_transcript(GOOD_TRANSCRIPT, None, GOOD_PARTS)
    assert "error" in out and "start_time" in out["error"]


@pytest.mark.asyncio
async def test_rejects_unparseable_start_time():
    from app.services.chat_tools import add_call_transcript
    out = await add_call_transcript(GOOD_TRANSCRIPT, "last tuesday", GOOD_PARTS)
    assert "error" in out and "start_time" in out["error"]


@pytest.mark.asyncio
async def test_rejects_empty_participants():
    from app.services.chat_tools import add_call_transcript
    out = await add_call_transcript(GOOD_TRANSCRIPT, GOOD_START, [])
    assert "error" in out and "participants" in out["error"]


@pytest.mark.asyncio
async def test_rejects_non_list_participants():
    from app.services.chat_tools import add_call_transcript
    out = await add_call_transcript(GOOD_TRANSCRIPT, GOOD_START, "Alice")
    assert "error" in out and "participants" in out["error"]


@pytest.mark.asyncio
async def test_rejects_non_transcript_source():
    from app.services.chat_tools import add_call_transcript
    out = await add_call_transcript(GOOD_TRANSCRIPT, GOOD_START, GOOD_PARTS, source="slack")
    assert "error" in out and "source" in out["error"]


# ---------------------------------------------------------------------------
# Return shaping — patch submit_and_wait to control the pipeline outcome
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_returns_summary_and_builds_request():
    from app.services.chat_tools import add_call_transcript

    fake = AsyncMock(return_value={
        "state": "completed",
        "job_id": "ingest-abc123def456",
        "poll_url": "/api/ingest/ingest-abc123def456/status",
        "content_type": "call_transcript",
        "result": {
            "content_hash": "abc123def4567890",
            "entities_extracted": 3,
            "decisions_found": 1,
            "insights_generated": 2,
            "relationships_created": 4,
            "graph_nodes_created": ["signal-aaaa", "signal-bbbb"],
            "processing_time_ms": 1234,
        },
    })

    with patch("app.routes.ingest.submit_and_wait", fake):
        out = await add_call_transcript(
            GOOD_TRANSCRIPT, GOOD_START, GOOD_PARTS,
            title="Launch planning", source="plaud",
            duration_minutes=18, conversation_id="conv-42",
        )

    assert out["status"] == "completed"
    assert out["bot_id"] == "ingest-abc123def456"  # ingest-{content_hash[:12]}
    assert out["content_type"] == "call_transcript"
    assert out["entities_extracted"] == 3
    assert out["decisions_found"] == 1

    # Inspect the IngestRequest the tool constructed
    built = fake.await_args.args[0]
    assert built.source.value == "plaud"
    assert built.participants == GOOD_PARTS
    assert built.timestamp is not None
    assert built.metadata["conversation_id"] == "conv-42"
    assert built.metadata["duration_minutes"] == 18


@pytest.mark.asyncio
async def test_timeout_returns_job_id():
    from app.services.chat_tools import add_call_transcript

    fake = AsyncMock(return_value={
        "state": "pending",
        "job_id": "ingest-pending00001",
        "poll_url": "/api/ingest/ingest-pending00001/status",
    })
    with patch("app.routes.ingest.submit_and_wait", fake):
        out = await add_call_transcript(GOOD_TRANSCRIPT, GOOD_START, GOOD_PARTS)

    assert out["status"] == "processing"
    assert out["job_id"] == "ingest-pending00001"
    assert out["poll_url"].endswith("/status")


@pytest.mark.asyncio
async def test_failure_returns_error():
    from app.services.chat_tools import add_call_transcript

    fake = AsyncMock(return_value={
        "state": "failed",
        "job_id": "ingest-failed00001",
        "poll_url": "/api/ingest/ingest-failed00001/status",
        "error": "boom",
    })
    with patch("app.routes.ingest.submit_and_wait", fake):
        out = await add_call_transcript(GOOD_TRANSCRIPT, GOOD_START, GOOD_PARTS)

    assert "error" in out and "boom" in out["error"]
    assert out["job_id"] == "ingest-failed00001"


# ---------------------------------------------------------------------------
# submit_and_wait polling behaviour (independent of the tool)
# ---------------------------------------------------------------------------

def _make_request():
    from app.models.ingestion.models import ContentSource, IngestRequest
    return IngestRequest(content="hello world", source=ContentSource.LOCAL_RECORDING)


@pytest.mark.asyncio
async def test_submit_and_wait_returns_completed(monkeypatch):
    from app.routes import ingest

    store: dict = {}

    def fake_enqueue(request, job_store, task_queue):
        job_store["job:ingest-x"] = {
            "status": "completed",
            "content_type": "call_transcript",
            "result": {"content_hash": "deadbeefcafe0000", "entities_extracted": 1},
        }
        return "ingest-x", "accepted"

    monkeypatch.setattr(ingest, "enqueue_ingestion", fake_enqueue)
    monkeypatch.setattr(ingest, "_get_job_store", lambda: store)
    monkeypatch.setattr(ingest, "_get_task_queue", lambda: None)

    out = await ingest.submit_and_wait(_make_request(), timeout_s=1.0)
    assert out["state"] == "completed"
    assert out["content_type"] == "call_transcript"
    assert out["result"]["entities_extracted"] == 1


@pytest.mark.asyncio
async def test_submit_and_wait_times_out_to_pending(monkeypatch):
    from app.routes import ingest

    store: dict = {}

    def fake_enqueue(request, job_store, task_queue):
        # Job never reaches a terminal state -> should time out.
        job_store["job:ingest-y"] = {"status": "running"}
        return "ingest-y", "accepted"

    monkeypatch.setattr(ingest, "enqueue_ingestion", fake_enqueue)
    monkeypatch.setattr(ingest, "_get_job_store", lambda: store)
    monkeypatch.setattr(ingest, "_get_task_queue", lambda: None)

    out = await ingest.submit_and_wait(_make_request(), timeout_s=0.2, poll_interval_s=0.05)
    assert out["state"] == "pending"
    assert out["job_id"] == "ingest-y"
