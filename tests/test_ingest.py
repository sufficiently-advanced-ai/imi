"""
Tests for Master Ingestion Endpoint (Issue #863).

TDD test suite covering:
- Pydantic models and validation
- Content hash computation and idempotency
- API routes (POST /api/ingest, GET status, GET jobs)
- IngestClassifier (source mapping + LLM fallback)
- IngestOrchestrator pipeline (6 phases, partial failures, graph writes)
"""

import hashlib
import pytest
import sys
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# NOTE: this file previously stubbed app.services / app.services.orchestrators /
# app.routes in sys.modules to avoid eager config imports when run outside the
# container. The stubs poisoned any later real import of those packages (e.g.
# `from app.services.orchestrators import WebhookOrchestrator` failed with
# "unknown location"), making TestModuleRegistration order-dependent. Settings
# no longer requires container-only env vars, so plain imports work everywhere.
import app.services.ingest_classifier  # noqa: E402
import app.services.task_queue  # noqa: E402
import app.services.orchestrators.base  # noqa: E402

# Pre-load agent_tools so the orchestrator tests can use ToolResult
# We need to stub the chain: agent_tools → decision_logger → config
# Instead, we create a lightweight ToolResult stand-in for tests.
from pydantic import BaseModel as _BaseModel
from typing import Dict, Any, Optional

class _MockToolResult(_BaseModel):
    """Lightweight stand-in for app.services.agent_tools.ToolResult."""
    success: bool
    data: Dict[str, Any]
    execution_time_ms: int
    error: Optional[str] = None
    metadata: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# 1. Pydantic Model Tests
# ---------------------------------------------------------------------------

class TestIngestModels:
    """Test Pydantic models for ingestion."""

    def test_content_source_enum_values(self):
        from app.models.ingestion.models import ContentSource
        assert ContentSource.FIREFLIES == "fireflies"
        assert ContentSource.OTTER == "otter"
        assert ContentSource.FATHOM == "fathom"
        assert ContentSource.SLACK == "slack"
        assert ContentSource.EMAIL == "email"
        assert ContentSource.DOCUMENT == "document"
        assert ContentSource.OTHER == "other"

    def test_ingest_request_minimal(self):
        """Only content is required."""
        from app.models.ingestion.models import IngestRequest
        req = IngestRequest(content="Some meeting transcript text")
        assert req.content == "Some meeting transcript text"
        assert req.source is None
        assert req.source_id is None
        assert req.title is None
        assert req.participants is None
        assert req.timestamp is None
        assert req.metadata is None

    def test_ingest_request_full(self):
        """All fields populated."""
        from app.models.ingestion.models import IngestRequest, ContentSource
        now = datetime.now(timezone.utc)
        req = IngestRequest(
            content="Transcript from Fireflies",
            source=ContentSource.FIREFLIES,
            source_id="ff-abc-123",
            title="Weekly Standup",
            participants=["Alice", "Bob"],
            timestamp=now,
            metadata={"duration_minutes": 30},
        )
        assert req.source == ContentSource.FIREFLIES
        assert req.source_id == "ff-abc-123"
        assert req.title == "Weekly Standup"
        assert req.participants == ["Alice", "Bob"]
        assert req.timestamp == now
        assert req.metadata == {"duration_minutes": 30}

    def test_ingest_request_content_required(self):
        """Content field is mandatory."""
        from pydantic import ValidationError
        from app.models.ingestion.models import IngestRequest
        with pytest.raises(ValidationError):
            IngestRequest()

    def test_ingest_response_fields(self):
        from app.models.ingestion.models import IngestResponse
        resp = IngestResponse(
            job_id="job-123",
            status="accepted",
            content_type="call_transcript",
            poll_url="/api/ingest/job-123/status",
        )
        assert resp.job_id == "job-123"
        assert resp.status == "accepted"
        assert resp.content_type == "call_transcript"
        assert resp.poll_url == "/api/ingest/job-123/status"

    def test_ingest_job_status_defaults(self):
        from app.models.ingestion.models import IngestJobStatus
        status = IngestJobStatus(job_id="job-1", status="pending")
        assert status.phases_completed == []
        assert status.current_phase is None
        assert status.result is None
        assert status.error is None

    def test_ingest_result_fields(self):
        from app.models.ingestion.models import IngestResult
        result = IngestResult(
            entities_extracted=5,
            relationships_created=3,
            decisions_found=2,
            insights_generated=1,
            graph_nodes_created=["person-alice", "project-x"],
            content_hash="abc123",
            processing_time_ms=1500,
        )
        assert result.entities_extracted == 5
        assert result.graph_nodes_created == ["person-alice", "project-x"]
        assert result.processing_time_ms == 1500

    def test_content_type_enum_values(self):
        from app.models.ingestion.models import ContentType
        assert ContentType.CALL_TRANSCRIPT == "call_transcript"
        assert ContentType.SLACK_THREAD == "slack_thread"
        assert ContentType.EMAIL_THREAD == "email_thread"
        assert ContentType.DOCUMENT == "document"
        assert ContentType.NOTES == "notes"


# ---------------------------------------------------------------------------
# 2. Content Hash & Idempotency Tests
# ---------------------------------------------------------------------------

class TestIdempotency:
    """Test content deduplication logic."""

    def test_content_hash_computation(self):
        """SHA256 of content field used for dedup."""
        from app.services.ingest_classifier import compute_content_hash
        content = "Hello, this is a test transcript."
        expected = hashlib.sha256(content.encode()).hexdigest()
        assert compute_content_hash(content) == expected

    def test_same_content_same_hash(self):
        from app.services.ingest_classifier import compute_content_hash
        content = "Identical content"
        assert compute_content_hash(content) == compute_content_hash(content)

    def test_different_content_different_hash(self):
        from app.services.ingest_classifier import compute_content_hash
        assert compute_content_hash("Content A") != compute_content_hash("Content B")


# ---------------------------------------------------------------------------
# 3. IngestClassifier Tests
# ---------------------------------------------------------------------------

class TestIngestClassifier:
    """Test content type classification."""

    def test_source_hint_fireflies_maps_to_call_transcript(self):
        from app.services.ingest_classifier import IngestClassifier
        classifier = IngestClassifier(claude_client=None)
        assert classifier.map_source_to_type("fireflies") == "call_transcript"

    def test_source_hint_otter_maps_to_call_transcript(self):
        from app.services.ingest_classifier import IngestClassifier
        classifier = IngestClassifier(claude_client=None)
        assert classifier.map_source_to_type("otter") == "call_transcript"

    def test_source_hint_fathom_maps_to_call_transcript(self):
        from app.services.ingest_classifier import IngestClassifier
        classifier = IngestClassifier(claude_client=None)
        assert classifier.map_source_to_type("fathom") == "call_transcript"

    def test_source_hint_slack(self):
        from app.services.ingest_classifier import IngestClassifier
        classifier = IngestClassifier(claude_client=None)
        assert classifier.map_source_to_type("slack") == "slack_thread"

    def test_source_hint_email(self):
        from app.services.ingest_classifier import IngestClassifier
        classifier = IngestClassifier(claude_client=None)
        assert classifier.map_source_to_type("email") == "email_thread"

    def test_source_hint_document(self):
        from app.services.ingest_classifier import IngestClassifier
        classifier = IngestClassifier(claude_client=None)
        assert classifier.map_source_to_type("document") == "document"

    def test_source_hint_other_maps_to_document(self):
        from app.services.ingest_classifier import IngestClassifier
        classifier = IngestClassifier(claude_client=None)
        assert classifier.map_source_to_type("other") == "document"

    @pytest.mark.asyncio
    async def test_classify_with_source_hint_skips_llm(self):
        """When source is provided, no Claude call is made."""
        from app.services.ingest_classifier import IngestClassifier
        mock_claude = Mock()
        mock_claude.generate_message = AsyncMock()
        classifier = IngestClassifier(claude_client=mock_claude)

        result = await classifier.classify("Some content", source_hint="fireflies")
        assert result == "call_transcript"
        mock_claude.generate_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_classify_without_source_calls_llm(self):
        """When no source, Claude is called to classify."""
        from app.services.ingest_classifier import IngestClassifier
        mock_response = Mock()
        mock_response.content = [Mock()]
        mock_response.content[0].text = "call_transcript"

        mock_claude = Mock()
        mock_claude.generate_message = AsyncMock(return_value=mock_response)

        classifier = IngestClassifier(claude_client=mock_claude)
        result = await classifier.classify("Alice: Let's discuss the roadmap.\nBob: Sure, I think...")
        assert result == "call_transcript"
        mock_claude.generate_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_classify_llm_failure_falls_back_to_document(self):
        """LLM failure gracefully falls back to 'document'."""
        from app.services.ingest_classifier import IngestClassifier
        mock_claude = Mock()
        mock_claude.generate_message = AsyncMock(side_effect=Exception("API error"))

        classifier = IngestClassifier(claude_client=mock_claude)
        result = await classifier.classify("Some content")
        assert result == "document"


# ---------------------------------------------------------------------------
# 4. API Route Tests
# ---------------------------------------------------------------------------

class TestIngestRoutes:
    """Test the ingest API endpoints."""

    @pytest.fixture
    def mock_task_queue(self):
        queue = MagicMock()
        queue.enqueue = Mock(return_value="job-test-123")
        queue.get_task = Mock(return_value=None)
        queue.tasks = {}
        return queue

    @pytest.fixture
    def app_client(self, mock_task_queue):
        """Create a test client with mocked dependencies."""
        from fastapi.testclient import TestClient
        from app.routes.ingest import router, _get_task_queue, _get_job_store

        # Create a fresh job store for each test
        job_store = {}

        app = self._create_test_app(router)

        # Override dependencies
        app.dependency_overrides[_get_task_queue] = lambda: mock_task_queue
        app.dependency_overrides[_get_job_store] = lambda: job_store

        client = TestClient(app)
        client._job_store = job_store
        client._task_queue = mock_task_queue
        return client

    def _create_test_app(self, router):
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router, prefix="/api")
        return app

    def test_post_ingest_accepts_content(self, app_client):
        """POST /api/ingest with valid content returns 202 with job_id."""
        response = app_client.post("/api/ingest", json={"content": "Meeting transcript text"})
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "accepted"
        assert "poll_url" in data

    def test_post_ingest_returns_poll_url(self, app_client):
        """Poll URL should point to the status endpoint."""
        response = app_client.post("/api/ingest", json={"content": "Test content"})
        data = response.json()
        assert data["poll_url"].startswith("/api/ingest/")
        assert data["poll_url"].endswith("/status")

    def test_post_ingest_with_source_hint(self, app_client):
        """Source hint is accepted and returned in response."""
        response = app_client.post("/api/ingest", json={
            "content": "Call transcript",
            "source": "fireflies",
        })
        assert response.status_code == 202

    def test_post_ingest_validation_error(self, app_client):
        """Missing content returns 422."""
        response = app_client.post("/api/ingest", json={})
        assert response.status_code == 422

    def test_post_ingest_content_too_large(self, app_client):
        """Content over 500KB returns 413."""
        large_content = "x" * (500 * 1024 + 1)
        response = app_client.post("/api/ingest", json={"content": large_content})
        assert response.status_code == 413

    def test_post_ingest_duplicate_content(self, app_client):
        """Duplicate content returns 200 with status: duplicate."""
        content = "This is duplicate content for testing"

        # First submission
        resp1 = app_client.post("/api/ingest", json={"content": content})
        assert resp1.status_code == 202
        job_id_1 = resp1.json()["job_id"]

        # Store the hash manually in the job store to simulate dedup
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        app_client._job_store[content_hash] = {
            "job_id": job_id_1,
            "content_hash": content_hash,
        }

        # Second submission with same content
        resp2 = app_client.post("/api/ingest", json={"content": content})
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "duplicate"
        assert resp2.json()["job_id"] == job_id_1

    def test_post_ingest_duplicate_source_id(self, app_client):
        """Duplicate source_id returns 200 with status: duplicate."""
        # First submission
        resp1 = app_client.post("/api/ingest", json={
            "content": "Content A",
            "source_id": "ext-123",
        })
        assert resp1.status_code == 202
        job_id_1 = resp1.json()["job_id"]

        # Store the source_id mapping
        app_client._job_store["source_id:ext-123"] = {
            "job_id": job_id_1,
        }

        # Second submission with same source_id
        resp2 = app_client.post("/api/ingest", json={
            "content": "Different content",
            "source_id": "ext-123",
        })
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "duplicate"

    def test_get_job_status_not_found(self, app_client):
        """Unknown job_id returns 404."""
        response = app_client.get("/api/ingest/nonexistent/status")
        assert response.status_code == 404

    def test_get_job_status_found(self, app_client):
        """Known job returns status info."""
        # Pre-populate a job
        app_client._job_store["job:test-job-1"] = {
            "job_id": "test-job-1",
            "status": "running",
            "content_type": "call_transcript",
            "phases_completed": ["CLASSIFY"],
            "current_phase": "EXTRACT",
            "result": None,
            "error": None,
        }
        response = app_client.get("/api/ingest/test-job-1/status")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "test-job-1"
        assert data["status"] == "running"
        assert data["current_phase"] == "EXTRACT"
        assert "CLASSIFY" in data["phases_completed"]

    def test_get_jobs_list(self, app_client):
        """GET /api/ingest/jobs returns list of recent jobs."""
        # Pre-populate some jobs
        for i in range(3):
            app_client._job_store[f"job:job-{i}"] = {
                "job_id": f"job-{i}",
                "status": "completed",
                "content_type": "document",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        response = app_client.get("/api/ingest/jobs")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 3


# ---------------------------------------------------------------------------
# 5. IngestOrchestrator Tests
# ---------------------------------------------------------------------------

class TestIngestOrchestrator:
    """Test the orchestration pipeline using the observation value chain."""

    @pytest.fixture
    def mock_classifier(self):
        classifier = Mock()
        classifier.classify = AsyncMock(return_value="call_transcript")
        return classifier

    @pytest.fixture
    def mock_claude_client(self):
        return Mock()

    @pytest.fixture
    def mock_graph(self):
        graph = AsyncMock()
        return graph

    @pytest.fixture
    def mock_signal_writer(self):
        writer = AsyncMock()
        writer.write_meeting_signals = AsyncMock(return_value=3)
        return writer

    @pytest.fixture
    def mock_git_ops(self, tmp_path):
        git_ops = Mock()
        git_ops.repo_path = str(tmp_path / "test_repo")
        git_ops.commit_file = AsyncMock()
        return git_ops

    @pytest.fixture
    def mock_meeting_signals(self):
        """Build a realistic MeetingSignals mock."""
        from app.models.signal import Signal, EntityRef, MeetingSignals
        signals = MeetingSignals(
            meeting_id="ingest-test",
            bot_id="ingest-abc123",
            meeting_title="Test Meeting",
            signal_count=3,
            signals=[
                Signal(
                    id="sig-1", type="decision",
                    content="Adopt microservices",
                    source_meeting_id="ingest-abc123",
                    source_timestamp="2026-03-24T00:00:00Z",
                    entities=[EntityRef(id="person-alice", type="person", name="Alice")],
                    confidence=0.9,
                ),
                Signal(
                    id="sig-2", type="action_item",
                    content="Alice to draft migration plan",
                    source_meeting_id="ingest-abc123",
                    source_timestamp="2026-03-24T00:00:00Z",
                    entities=[EntityRef(id="person-alice", type="person", name="Alice")],
                    owner=EntityRef(id="person-alice", type="person", name="Alice"),
                    confidence=0.85,
                ),
                Signal(
                    id="sig-3", type="key_point",
                    content="Current onboarding takes 14 days",
                    source_meeting_id="ingest-abc123",
                    source_timestamp="2026-03-24T00:00:00Z",
                    entities=[],
                    confidence=0.8,
                ),
            ],
        )
        return signals

    @pytest.fixture
    def orchestrator(self, mock_classifier, mock_claude_client, mock_graph, mock_signal_writer, mock_git_ops, mock_meeting_signals):
        from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator
        orch = IngestOrchestrator(
            classifier=mock_classifier,
            claude_client=mock_claude_client,
            graph=mock_graph,
            signal_writer=mock_signal_writer,
            git_ops=mock_git_ops,
        )
        # Patch SignalPromoter.promote to return our mock signals
        async def mock_promote(observation):
            return mock_meeting_signals
        orch._phase_promote_signals = lambda ms: mock_promote(ms)
        return orch

    @pytest.mark.asyncio
    async def test_full_pipeline_success(self, orchestrator):
        """Complete pipeline runs all phases and returns results."""
        from app.models.ingestion.models import IngestRequest
        request = IngestRequest(content="Alice: We should adopt microservices for Project X.")
        job_store = {}

        result = await orchestrator.process(request, job_id="job-1", job_store=job_store)

        assert result["status"] == "completed"
        assert result["decisions_found"] >= 1
        assert result["content_hash"]

    @pytest.mark.asyncio
    async def test_pipeline_tracks_phases(self, orchestrator):
        """Job store is updated with phase progression."""
        from app.models.ingestion.models import IngestRequest
        request = IngestRequest(content="Some transcript text")
        job_store = {}

        await orchestrator.process(request, job_id="job-phase", job_store=job_store)

        job_data = job_store.get("job:job-phase", {})
        phases = job_data.get("phases_completed", [])
        assert "CLASSIFY" in phases
        assert "BUILD_MEETING" in phases
        assert "PROMOTE_SIGNALS" in phases
        assert "ENRICH_GRAPH" in phases
        assert "PERSIST" in phases
        assert "COMPLETE" in phases

    @pytest.mark.asyncio
    async def test_classification_with_source_hint(self, orchestrator, mock_classifier):
        """Source hint is passed to classifier, skipping LLM."""
        from app.models.ingestion.models import IngestRequest, ContentSource
        request = IngestRequest(content="Call recording", source=ContentSource.FIREFLIES)
        job_store = {}

        await orchestrator.process(request, job_id="job-hint", job_store=job_store)
        mock_classifier.classify.assert_called_once()

    @pytest.mark.asyncio
    async def test_observation_built_correctly(self, orchestrator):
        """BUILD_MEETING phase builds a proper Observation."""
        from app.models.ingestion.models import IngestRequest
        request = IngestRequest(
            content="Alice: Let's discuss the roadmap.",
            title="Roadmap Discussion",
            participants=["Alice", "Bob"],
        )
        job_store = {}

        await orchestrator.process(request, job_id="job-ms", job_store=job_store)
        # Pipeline should complete — meeting state was built
        assert job_store["job:job-ms"]["status"] == "completed"



    @pytest.mark.asyncio
    async def test_persist_skipped_when_git_ops_unavailable(self, mock_classifier, mock_claude_client, mock_graph, mock_signal_writer, mock_meeting_signals):
        """Pipeline completes even without git_ops."""
        from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator
        orch = IngestOrchestrator(
            classifier=mock_classifier,
            claude_client=mock_claude_client,
            graph=mock_graph,
            signal_writer=mock_signal_writer,
            git_ops=None,
        )
        async def mock_promote(ms):
            return mock_meeting_signals
        orch._phase_promote_signals = lambda ms: mock_promote(ms)

        from app.models.ingestion.models import IngestRequest
        request = IngestRequest(content="Content without git persist")
        job_store = {}

        result = await orch.process(request, job_id="job-no-git", job_store=job_store)
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_signal_promotion_failure_doesnt_crash(self, mock_classifier, mock_claude_client, mock_graph, mock_signal_writer, mock_git_ops):
        """If signal promotion fails, pipeline still completes."""
        from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator
        orch = IngestOrchestrator(
            classifier=mock_classifier,
            claude_client=mock_claude_client,
            graph=mock_graph,
            signal_writer=mock_signal_writer,
            git_ops=mock_git_ops,
        )
        # Make promotion return None (failure)
        async def failing_promote(ms):
            return None
        orch._phase_promote_signals = lambda ms: failing_promote(ms)

        from app.models.ingestion.models import IngestRequest
        request = IngestRequest(content="Content that fails signal promotion")
        job_store = {}

        result = await orch.process(request, job_id="job-promo-fail", job_store=job_store)
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_result_contains_processing_time(self, orchestrator):
        """Result includes processing_time_ms."""
        from app.models.ingestion.models import IngestRequest
        request = IngestRequest(content="Quick test content")
        job_store = {}

        result = await orchestrator.process(request, job_id="job-time", job_store=job_store)
        assert "processing_time_ms" in result
        assert result["processing_time_ms"] >= 0

    @pytest.mark.asyncio
    async def test_result_contains_content_hash(self, orchestrator):
        """Result includes the content hash."""
        from app.models.ingestion.models import IngestRequest
        content = "Content for hash test"
        request = IngestRequest(content=content)
        job_store = {}

        result = await orchestrator.process(request, job_id="job-hash", job_store=job_store)
        expected_hash = hashlib.sha256(content.encode()).hexdigest()
        assert result["content_hash"] == expected_hash


# ---------------------------------------------------------------------------
# 6. Module Registration Test
# ---------------------------------------------------------------------------

class TestModuleRegistration:
    """Test that ingestion module registers correctly."""

    def test_ingestion_module_has_router(self):
        from app.modules.ingestion import router
        assert router is not None

    def test_ingestion_routes_registered(self):
        """Router has the expected routes."""
        from app.modules.ingestion import router
        route_paths = [r.path for r in router.routes if hasattr(r, 'path')]
        assert any("/ingest" in p for p in route_paths)
