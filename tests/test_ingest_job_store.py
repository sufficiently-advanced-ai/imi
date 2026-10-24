"""Durable ingest job store: persistence, restart recovery, dedup release,
retry, the pending bound (429) and the retention sweep.

Runs against a temp SQLite file; no Neo4j or LLM needed (the task queue is a
fake that records what was enqueued).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.ingestion.models import ContentSource, IngestRequest
from app.routes import ingest
from app.services.ingest_job_store import JobStore


class FakeQueue:
    """Records enqueue calls instead of running the pipeline."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def enqueue(self, func, *args, task_id=None, priority=0, **kwargs):
        self.calls.append({"func": func, "args": args, "task_id": task_id})
        return task_id

    @property
    def requests(self) -> list[IngestRequest]:
        return [c["args"][0] for c in self.calls]


def _request(content: str = "Alice: we adopt microservices.", source_id: str | None = None):
    return IngestRequest(content=content, source=ContentSource.OTHER, source_id=source_id)


async def _open(path) -> JobStore:
    store = JobStore()
    await store.open(str(path))
    return store


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "imi.db"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_store_sees_first_stores_jobs(db_path):
    first = await _open(db_path)
    queue = FakeQueue()
    job_id, status = ingest.enqueue_ingestion(_request(source_id="src-1"), first, queue)
    assert status == "accepted"

    # The orchestrator replaces the record when the job starts, then mutates it.
    first[f"job:{job_id}"] = {"job_id": job_id, "status": "running", "phases_completed": []}
    job = first[f"job:{job_id}"]
    job["phases_completed"].append("ADMIT")
    job["phase_outcomes"] = [{"phase": "ADMIT", "outcome": "ok", "detail": None, "retryable": False}]
    job["status"] = "completed_with_warnings"
    job["result"] = {"content_hash": job["content_hash"], "entities_extracted": 2}
    await first.persist(job_id)
    await first.close()

    second = await _open(db_path)
    loaded = second[f"job:{job_id}"]
    assert loaded["status"] == "completed_with_warnings"
    assert loaded["phases_completed"] == ["ADMIT"]
    assert loaded["phase_outcomes"][0]["phase"] == "ADMIT"
    assert loaded["result"]["entities_extracted"] == 2
    # Store-owned fields survived the orchestrator's record replacement.
    assert loaded["created_at"]
    assert loaded["source_id"] == "src-1"
    assert loaded["finished_at"]
    # Dedup entries are rebuilt from the columns.
    assert ingest.enqueue_ingestion(_request(source_id="src-1"), second, FakeQueue()) == (
        job_id,
        "duplicate",
    )
    assert ingest.enqueue_ingestion(_request(), second, FakeQueue()) == (job_id, "duplicate")
    await second.close()


@pytest.mark.asyncio
async def test_unopened_store_behaves_like_the_old_dict():
    store = JobStore()
    queue = FakeQueue()
    job_id, status = ingest.enqueue_ingestion(_request(), store, queue)
    assert status == "accepted"
    assert store[f"job:{job_id}"]["status"] == "pending"
    assert ingest.enqueue_ingestion(_request(), store, queue) == (job_id, "duplicate")
    await store.persist(job_id)  # no-op without a database
    assert len(queue.calls) == 1


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupted_jobs_are_reenqueued_at_startup(db_path):
    first = await _open(db_path)
    request = _request(source_id="src-r")
    request._channel = "mcp"
    pending_id, _ = ingest.enqueue_ingestion(request, first, FakeQueue())
    running_id, _ = ingest.enqueue_ingestion(_request("Bob: ship it."), first, FakeQueue())
    first[f"job:{running_id}"]["status"] = "running"
    await first.persist(running_id)
    await first.close()  # "crash": neither job finished

    second = JobStore()
    opened = await second.open(str(db_path))
    assert set(opened["interrupted"]) == {pending_id, running_id}
    assert second[f"job:{pending_id}"]["status"] == "interrupted"
    assert second[f"job:{pending_id}"]["interrupted"] is True

    queue = FakeQueue()
    recovered = await ingest.recover_interrupted_jobs(second, queue)
    assert recovered == {"requeued": 2, "failed": 0}
    assert {c["task_id"] for c in queue.calls} == {pending_id, running_id}
    job = second[f"job:{pending_id}"]
    assert job["status"] == "pending"
    assert job["attempts"] == 2
    assert job["interrupted"] is True
    replayed = next(c["args"][0] for c in queue.calls if c["task_id"] == pending_id)
    assert replayed.content == request.content
    assert replayed.source_id == "src-r"
    assert replayed._channel == "mcp"  # ADR-007 channel survives the restart
    await second.close()


@pytest.mark.asyncio
async def test_repeatedly_interrupted_job_is_failed_not_looped(db_path):
    store = await _open(db_path)
    job_id, _ = ingest.enqueue_ingestion(_request(), store, FakeQueue())
    store[f"job:{job_id}"]["attempts"] = ingest.MAX_AUTO_RECOVERY_ATTEMPTS
    await store.persist(job_id)
    await store.close()

    store = await _open(db_path)
    queue = FakeQueue()
    assert await ingest.recover_interrupted_jobs(store, queue) == {"requeued": 0, "failed": 1}
    assert queue.calls == []
    assert store[f"job:{job_id}"]["status"] == "failed"
    assert "/retry" in store[f"job:{job_id}"]["error"]
    await store.close()


# ---------------------------------------------------------------------------
# Dedup release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", ["failed", "interrupted"])
async def test_failed_or_interrupted_content_can_be_resubmitted(db_path, prior):
    store = await _open(db_path)
    queue = FakeQueue()
    job_id, _ = ingest.enqueue_ingestion(_request(source_id="src-f"), store, queue)
    store[f"job:{job_id}"]["status"] = prior
    store[f"job:{job_id}"]["error"] = "boom"

    again, status = ingest.enqueue_ingestion(_request(source_id="src-f"), store, queue)
    assert (again, status) == (job_id, "accepted")  # same job_id: poll_url stays valid
    job = store[f"job:{job_id}"]
    assert job["status"] == "pending"
    assert job["attempts"] == 2
    assert job["error"] is None
    assert len(queue.calls) == 2
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", ["pending", "running", "completed", "completed_with_warnings", "dropped"])
async def test_live_or_finished_content_stays_duplicate(prior):
    store = JobStore()
    queue = FakeQueue()
    job_id, _ = ingest.enqueue_ingestion(_request(), store, queue)
    store[f"job:{job_id}"]["status"] = prior
    assert ingest.enqueue_ingestion(_request(), store, queue) == (job_id, "duplicate")
    assert len(queue.calls) == 1


# ---------------------------------------------------------------------------
# Retention sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_sweep_removes_old_terminal_jobs_only(db_path):
    store = await _open(db_path)
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    ids = {}
    for name, status, finished in [
        ("old_done", "completed", old),
        ("old_failed", "failed", old),
        ("new_done", "completed", None),
        ("old_pending", "pending", None),
    ]:
        job_id, _ = ingest.enqueue_ingestion(_request(f"content {name}"), store, FakeQueue())
        job = store[f"job:{job_id}"]
        job["status"] = status
        if finished:
            job["created_at"] = old
            job["finished_at"] = finished
        elif name == "old_pending":
            job["created_at"] = old
        await store.persist(job_id)
        ids[name] = job_id

    removed = await store.sweep(30)
    assert removed == 2
    assert f"job:{ids['old_done']}" not in store
    assert f"job:{ids['old_failed']}" not in store
    assert f"job:{ids['new_done']}" in store
    assert f"job:{ids['old_pending']}" in store
    # Dedup entries for swept jobs expire with them.
    assert ingest.enqueue_ingestion(_request("content old_done"), store, FakeQueue())[1] == "accepted"
    await store.close()

    reopened = await _open(db_path)
    assert f"job:{ids['old_done']}" not in reopened
    assert f"job:{ids['new_done']}" in reopened
    await reopened.close()


# ---------------------------------------------------------------------------
# Routes: retry, 429
# ---------------------------------------------------------------------------


def _client(store, queue) -> TestClient:
    app = FastAPI()
    app.include_router(ingest.router, prefix="/api")
    app.dependency_overrides[ingest._get_job_store] = lambda: store
    app.dependency_overrides[ingest._get_task_queue] = lambda: queue
    return TestClient(app)


def test_retry_endpoint_reruns_failed_job(db_path):
    store = asyncio.run(_open(db_path))
    seed = FakeQueue()
    job_id, _ = ingest.enqueue_ingestion(_request(source_id="src-retry"), store, seed)
    store[f"job:{job_id}"]["status"] = "failed"
    asyncio.run(store.flush())

    queue = FakeQueue()
    with _client(store, queue) as client:
        resp = client.post(f"/api/ingest/{job_id}/retry")
        assert resp.status_code == 202, resp.text
        assert resp.json() == {
            "job_id": job_id,
            "status": "accepted",
            "content_type": None,
            "poll_url": f"/api/ingest/{job_id}/status",
        }
        status = client.get(f"/api/ingest/{job_id}/status").json()
        assert status["status"] == "pending"
        assert status["attempts"] == 2
    assert [c["task_id"] for c in queue.calls] == [job_id]
    assert queue.requests[0].source_id == "src-retry"
    asyncio.run(store.close())


@pytest.mark.parametrize("prior", ["running", "pending", "completed", "completed_with_warnings"])
def test_retry_endpoint_409_when_not_rerunnable(prior):
    store = JobStore()
    job_id, _ = ingest.enqueue_ingestion(_request(), store, FakeQueue())
    store[f"job:{job_id}"]["status"] = prior
    queue = FakeQueue()
    with _client(store, queue) as client:
        assert client.post(f"/api/ingest/{job_id}/retry").status_code == 409
        assert client.post("/api/ingest/ingest-nope/retry").status_code == 404
    assert queue.calls == []


def test_post_ingest_429_when_pending_bound_exceeded(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "INGEST_MAX_PENDING", 2)
    store = JobStore()
    queue = FakeQueue()
    with _client(store, queue) as client:
        for i in range(2):
            resp = client.post("/api/ingest", json={"content": f"note {i}"})
            assert resp.status_code == 202, resp.text
        resp = client.post("/api/ingest", json={"content": "note 3"})
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == str(ingest.RETRY_AFTER_SECONDS)
        # A duplicate is still answered while the queue is full.
        assert client.post("/api/ingest", json={"content": "note 0"}).json()["status"] == "duplicate"
        # A finished job frees a slot.
        first = queue.calls[0]["task_id"]
        store[f"job:{first}"]["status"] = "completed"
        assert client.post("/api/ingest", json={"content": "note 3"}).status_code == 202
    assert len(queue.calls) == 3


# ---------------------------------------------------------------------------
# submit_and_wait (MCP add_call_transcript path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_and_wait_over_durable_store(db_path, monkeypatch):
    store = await _open(db_path)

    class RunningQueue(FakeQueue):
        def enqueue(self, func, *args, task_id=None, priority=0, **kwargs):
            super().enqueue(func, *args, task_id=task_id, priority=priority)

            async def finish():
                await asyncio.sleep(0.05)
                store[f"job:{task_id}"]["status"] = "completed"
                store[f"job:{task_id}"]["result"] = {"content_hash": "ab" * 32}
                await store.persist(task_id)

            asyncio.get_running_loop().create_task(finish())
            return task_id

    monkeypatch.setattr(ingest, "_get_job_store", lambda: store)
    monkeypatch.setattr(ingest, "_get_task_queue", lambda: RunningQueue())
    out = await ingest.submit_and_wait(_request(), timeout_s=2.0, poll_interval_s=0.02)
    assert out["state"] == "completed"
    assert out["poll_url"] == f"/api/ingest/{out['job_id']}/status"
    await store.close()


@pytest.mark.asyncio
async def test_submit_and_wait_rejected_when_queue_full(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "INGEST_MAX_PENDING", 1)
    store = JobStore()
    queue = FakeQueue()
    ingest.enqueue_ingestion(_request("first"), store, queue)
    monkeypatch.setattr(ingest, "_get_job_store", lambda: store)
    monkeypatch.setattr(ingest, "_get_task_queue", lambda: queue)
    out = await ingest.submit_and_wait(_request("second"), timeout_s=0.1)
    assert out["state"] == "rejected"
    assert out["retry_after_s"] == ingest.RETRY_AFTER_SECONDS
    assert len(queue.calls) == 1


@pytest.mark.asyncio
async def test_pipeline_failure_is_checkpointed(db_path, monkeypatch):
    store = await _open(db_path)
    job_id, _ = ingest.enqueue_ingestion(_request(), store, FakeQueue())

    def boom(*_a, **_k):
        raise RuntimeError("tools exploded")

    monkeypatch.setattr(ingest, "_get_extraction_tools", boom)
    monkeypatch.setattr(ingest, "_get_claude_client", lambda: None)
    monkeypatch.setattr(ingest, "_get_graph_service", lambda: None)
    monkeypatch.setattr(ingest, "_get_signal_writer", lambda: None)
    monkeypatch.setattr(ingest, "_get_git_ops", lambda: None)
    out = await ingest._run_ingestion_pipeline(_request(), job_id, store)
    assert out["status"] == "failed"
    await store.close()

    reopened = await _open(db_path)
    job = reopened[f"job:{job_id}"]
    assert job["status"] == "failed"
    assert "tools exploded" in job["error"]
    assert job["finished_at"]
    await reopened.close()
