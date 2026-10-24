"""Corpus sync status: reconcile/rebuild outcomes exposed on health + admin routes."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.lifecycle import LifecycleManager
from app.routes import admin as admin_routes
from app.routes import health as health_routes
from app.services import corpus_sync
from app.services import graph_rebuild as gr
from app.services.corpus_manifest import CorpusReconciler, Manifest, save_manifest, scan_corpus
from app.services.health_check import DependencyStatus


@pytest.fixture(autouse=True)
def _reset():
    corpus_sync.reset_corpus_sync()
    gr._running = False
    yield
    corpus_sync.reset_corpus_sync()
    gr._running = False


# ── status derivation ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("result", "status", "degraded"),
    [
        ({"action": "unchanged", "files": 3, "baseline_source": "admin"}, "synced", False),
        ({"action": "reconciled", "added": 1, "baseline_source": "startup"}, "synced", False),
        ({"action": "adopted", "reason": "manifest_missing", "files": 9}, "adopted", True),
        # an adopted baseline is still adopted on the next boot's reconcile
        ({"action": "unchanged", "baseline_source": "manifest_mismatch"}, "adopted", True),
        ({"action": "reconciled", "added": 2, "baseline_source": "graph_without_build_id"}, "adopted", True),
        ({"action": "full_rebuild_required", "rebuild": "completed"}, "synced", False),
        ({"action": "full_rebuild_required", "rebuild": "failed", "error": "boom"}, "rebuild_required", True),
        ({"action": "skipped", "reason": "graph_or_neo4j_unavailable"}, "unknown", False),
        (None, "unknown", False),
    ],
)
def test_record_reconcile_result_derives_status(result, status, degraded):
    corpus_sync.record_reconcile_result(result)
    snap = corpus_sync.get_corpus_sync()
    assert snap["status"] == status
    assert snap["degraded"] is degraded
    assert bool(snap["warnings"]) is degraded
    if status == "adopted":
        assert "rebuild-graph" in snap["warnings"][0]


def test_retry_failures_are_a_warning_but_still_synced():
    corpus_sync.record_reconcile_result({"action": "reconciled", "added": 2, "retry_next_boot": 1})
    snap = corpus_sync.get_corpus_sync()
    assert snap["status"] == "synced"
    assert snap["degraded"] is True and "retried" in snap["warnings"][0]


def test_default_is_unknown_and_not_degraded():
    snap = corpus_sync.get_corpus_sync()
    assert snap["status"] == "unknown" and snap["reason"] == "not_started"
    assert snap["degraded"] is False and snap["warnings"] == []


def test_set_rejects_unknown_status():
    with pytest.raises(ValueError):
        corpus_sync.set_corpus_sync("stale")


# ── the reconciler reports who wrote the baseline ─────────────────────────────


def _neo4j_state(build_id, source, updated_at="2026-10-01T00:00:00+00:00"):
    client = AsyncMock()
    client.execute_read = AsyncMock(
        return_value=[{"build_id": build_id, "source": source, "updated_at": updated_at}]
    )
    client.execute_write = AsyncMock(return_value=[])
    return client


@pytest.mark.asyncio
async def test_adopted_baseline_survives_a_restart(tmp_path):
    """Boot 2 after an adoption sees an unchanged corpus — but the baseline
    source on :_CorpusState keeps the status ``adopted`` until a rebuild."""
    repo = tmp_path / "repo"
    (repo).mkdir()
    (repo / "a.md").write_text("x", encoding="utf-8")
    manifest_path = tmp_path / "data" / "corpus_manifest.json"
    save_manifest(manifest_path, Manifest(build_id="b1", built_at="t0", files=scan_corpus(str(repo))))
    kg = MagicMock()
    rec = CorpusReconciler(kg=kg, neo4j=_neo4j_state("b1", "manifest_missing"),
                           repo_path=str(repo), manifest_path=manifest_path)

    result = await rec.reconcile()
    assert result["action"] == "unchanged"
    assert result["baseline_source"] == "manifest_missing"
    assert result["baseline_at"] == "2026-10-01T00:00:00+00:00"
    corpus_sync.record_reconcile_result(result)
    assert corpus_sync.get_corpus_sync()["status"] == "adopted"

    rec.neo4j = _neo4j_state("b1", "admin")
    corpus_sync.record_reconcile_result(await rec.reconcile())
    assert corpus_sync.get_corpus_sync()["status"] == "synced"


# ── startup / rebuild transitions ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_startup_adoption_yields_adopted(monkeypatch):
    seen = []
    reconciler = MagicMock()

    async def reconcile():
        seen.append(corpus_sync.get_corpus_sync()["status"])
        return {"action": "adopted", "reason": "manifest_mismatch", "files": 4, "entities": 2,
                "build_id": "abc", "baseline_at": "t1"}

    reconciler.reconcile = reconcile
    monkeypatch.setattr(gr, "make_reconciler", lambda *a, **k: reconciler)
    monkeypatch.setattr(gr, "_get_sk", lambda: None)
    corpus_sync.set_corpus_sync("pending", reason="startup_reconcile_queued")

    await gr.startup_reconcile()

    assert seen == ["reconciling"]
    snap = corpus_sync.get_corpus_sync()
    assert snap["status"] == "adopted" and snap["reason"] == "manifest_mismatch"
    assert snap["counts"] == {"files": 4, "entities": 2}
    assert snap["build_id"] == "abc" and snap["degraded"] is True


@pytest.mark.asyncio
async def test_startup_reconcile_error_is_unknown_not_stuck_reconciling(monkeypatch):
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock(side_effect=RuntimeError("neo4j down"))
    monkeypatch.setattr(gr, "make_reconciler", lambda *a, **k: reconciler)
    monkeypatch.setattr(gr, "_get_sk", lambda: None)
    with pytest.raises(RuntimeError):
        await gr.startup_reconcile()
    snap = corpus_sync.get_corpus_sync()
    assert snap["status"] == "unknown" and snap["error"] == "neo4j down"


@pytest.mark.asyncio
async def test_empty_graph_rebuild_failure_is_rebuild_required(monkeypatch):
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock(return_value={"action": "full_rebuild_required", "reason": "graph_empty"})
    monkeypatch.setattr(gr, "make_reconciler", lambda *a, **k: reconciler)
    monkeypatch.setattr(gr, "_get_sk", lambda: None)
    monkeypatch.setattr(gr, "run_full_rebuild", AsyncMock(side_effect=RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        await gr.startup_reconcile()
    snap = corpus_sync.get_corpus_sync()
    assert snap["status"] == "rebuild_required" and snap["error"] == "boom"


@pytest.mark.asyncio
async def test_empty_graph_startup_rebuild_keeps_its_build_provenance(monkeypatch):
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock(return_value={"action": "full_rebuild_required", "reason": "graph_empty"})
    reconciler.record_full_build = AsyncMock(
        return_value=Manifest(build_id="fresh", built_at="t3", files={"a.md": (1, 1), "b.md": (1, 1)})
    )
    kg = MagicMock()
    kg.build_graph = AsyncMock(return_value={})
    monkeypatch.setattr(gr, "make_reconciler", lambda *a, **k: reconciler)
    monkeypatch.setattr(gr, "_get_kg", lambda: kg)
    monkeypatch.setattr(gr, "_get_sk", lambda: None)

    result = await gr.startup_reconcile()

    assert result["rebuild"] == "completed"
    snap = corpus_sync.get_corpus_sync()
    assert snap["status"] == "synced" and snap["reason"] == "full_rebuild:startup_empty_graph"
    assert snap["build_id"] == "fresh" and snap["counts"] == {"files": 2}


def _rebuild_deps(monkeypatch, build_graph):
    kg = MagicMock()
    kg.build_graph = build_graph
    reconciler = MagicMock()
    reconciler.record_full_build = AsyncMock(
        return_value=Manifest(build_id="new", built_at="t2", files={"a.md": (1, 1)})
    )
    monkeypatch.setattr(gr, "_get_kg", lambda: kg)
    monkeypatch.setattr(gr, "_get_sk", lambda: None)
    monkeypatch.setattr(gr, "make_reconciler", lambda *a, **k: reconciler)


@pytest.mark.asyncio
async def test_completed_full_rebuild_clears_adopted(monkeypatch):
    corpus_sync.record_reconcile_result({"action": "adopted", "reason": "manifest_missing"})
    _rebuild_deps(monkeypatch, AsyncMock(return_value={}))

    await gr.run_full_rebuild(source="admin")

    snap = corpus_sync.get_corpus_sync()
    assert snap["status"] == "synced" and snap["reason"] == "full_rebuild:admin"
    assert snap["build_id"] == "new" and snap["counts"] == {"files": 1}
    assert snap["degraded"] is False


@pytest.mark.asyncio
async def test_failed_full_rebuild_is_rebuild_required(monkeypatch):
    _rebuild_deps(monkeypatch, AsyncMock(side_effect=RuntimeError("disk full")))
    with pytest.raises(RuntimeError):
        await gr.run_full_rebuild(source="admin")
    snap = corpus_sync.get_corpus_sync()
    assert snap["status"] == "rebuild_required" and snap["error"] == "disk full"
    assert snap["degraded"] is True


# ── routes ──────────────────────────────────────────────────────────────────


def _client(monkeypatch, ready=True, dep_status=DependencyStatus.HEALTHY):
    lm = LifecycleManager()
    if ready:
        lm.mark_ready()
    monkeypatch.setattr(health_routes, "_startup_state", lambda: {
        "ready": lm.is_ready(), "state": lm.state.value, "startup": "stateful", "failures": [],
    })
    service = MagicMock()
    service.cache.ttl_seconds = 5
    service.readiness_check = AsyncMock(return_value=SimpleNamespace(
        status=dep_status, timestamp=datetime.now(tz=UTC), checks={},
        performance_metrics={}, version="test", uptime_seconds=1.0,
    ))
    monkeypatch.setattr(health_routes, "get_health_service", lambda: service)
    app = FastAPI()
    app.include_router(health_routes.router)
    app.include_router(admin_routes.router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("setup", "status", "degraded"),
    [
        (lambda: corpus_sync.set_corpus_sync("pending", reason="startup_reconcile_queued"), "pending", False),
        (lambda: corpus_sync.set_corpus_sync("reconciling", reason="startup_reconcile"), "reconciling", False),
        (lambda: corpus_sync.record_reconcile_result({"action": "unchanged"}), "synced", False),
        (lambda: corpus_sync.record_reconcile_result({"action": "adopted", "reason": "manifest_missing"}),
         "adopted", True),
        (lambda: corpus_sync.record_reconcile_result({"action": "full_rebuild_required"}),
         "rebuild_required", True),
        (lambda: None, "unknown", False),
    ],
)
def test_health_routes_report_each_status(monkeypatch, setup, status, degraded):
    setup()
    client = _client(monkeypatch)

    ready = client.get("/health/ready")
    assert ready.status_code == 200  # sync never changes readiness
    body = ready.json()
    assert body["corpus_sync"]["status"] == status
    assert body["degraded"] is degraded
    assert bool(body["warnings"]) is degraded

    startup = client.get("/health/startup")
    assert startup.status_code == 200 and startup.json()["ready"] is True
    assert startup.json()["corpus_sync"]["status"] == status
    assert startup.json()["degraded"] is degraded

    admin = client.get("/api/admin/corpus-sync")
    assert admin.status_code == 200 and admin.json()["status"] == status


def test_readiness_degraded_dependency_is_degraded_without_corpus_warnings(monkeypatch):
    corpus_sync.record_reconcile_result({"action": "unchanged"})
    body = _client(monkeypatch, dep_status=DependencyStatus.DEGRADED).get("/health/ready").json()
    assert body["degraded"] is True and body["warnings"] == []


def test_starting_response_also_carries_corpus_sync(monkeypatch):
    corpus_sync.set_corpus_sync("pending", reason="startup_reconcile_queued")
    r = _client(monkeypatch, ready=False).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "starting"
    assert r.json()["corpus_sync"]["status"] == "pending"
