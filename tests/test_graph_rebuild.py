"""graph_rebuild: full rebuild runner, stateful-boot helpers, background plumbing."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import graph_rebuild as gr


@pytest.fixture(autouse=True)
def _reset_status():
    gr._status.update(state="idle", source=None, clean=False, started_at=None, finished_at=None, stats={}, error=None)
    yield


def _kg():
    kg = MagicMock()
    kg.build_graph = AsyncMock(return_value={"total_nodes": 3, "total_edges": 2, "ignored": [1]})
    return kg


@pytest.mark.asyncio
async def test_run_full_rebuild_builds_graph_semantica_and_manifest(monkeypatch):
    kg = _kg()
    sk = MagicMock()
    sk.build_graph = AsyncMock(return_value={"nodes": 3, "edges": 1, "status": "built"})
    reconciler = MagicMock()
    reconciler.record_full_build = AsyncMock()
    monkeypatch.setattr(gr, "_get_kg", lambda: kg)
    monkeypatch.setattr(gr, "_get_sk", lambda: sk)
    monkeypatch.setattr(gr, "make_reconciler", lambda *a, **k: reconciler)

    status = await gr.run_full_rebuild(clean=True, source="test", reingest_signals=False)

    kg.build_graph.assert_awaited_once_with(force_rebuild=True, clean=True, reingest_signals=False)
    sk.build_graph.assert_awaited_once_with(force_rebuild=True, clean=False)
    reconciler.record_full_build.assert_awaited_once_with(source="test")
    assert status["state"] == "completed"
    assert status["stats"]["graph"] == {"total_nodes": 3, "total_edges": 2}
    assert status["stats"]["manifest"] == "recorded"
    assert gr.is_rebuild_running() is False


@pytest.mark.asyncio
async def test_run_full_rebuild_records_failure_and_rejects_concurrent(monkeypatch):
    kg = MagicMock()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_build(**_):
        started.set()
        await release.wait()
        raise RuntimeError("boom")

    kg.build_graph = slow_build
    monkeypatch.setattr(gr, "_get_kg", lambda: kg)
    monkeypatch.setattr(gr, "_get_sk", lambda: None)
    monkeypatch.setattr(gr, "make_reconciler", lambda *a, **k: None)

    task = asyncio.create_task(gr.run_full_rebuild(source="first"))
    await started.wait()
    assert gr.is_rebuild_running() is True
    with pytest.raises(RuntimeError, match="already running"):
        await gr.run_full_rebuild(source="second")
    release.set()
    with pytest.raises(RuntimeError, match="boom"):
        await task
    assert gr.get_rebuild_status()["state"] == "failed"
    assert gr.get_rebuild_status()["error"] == "boom"


@pytest.mark.asyncio
async def test_startup_reconcile_triggers_full_rebuild_on_empty_graph(monkeypatch):
    reconciler = MagicMock()
    reconciler.reconcile = AsyncMock(return_value={"action": "full_rebuild_required", "reason": "graph_empty"})
    monkeypatch.setattr(gr, "make_reconciler", lambda *a, **k: reconciler)
    monkeypatch.setattr(gr, "_get_sk", lambda: None)
    run = AsyncMock(return_value={"state": "completed", "stats": {"graph": {}}})
    monkeypatch.setattr(gr, "run_full_rebuild", run)

    result = await gr.startup_reconcile()
    run.assert_awaited_once_with(source="startup_empty_graph")
    assert result["rebuild"] == "completed"


@pytest.mark.asyncio
async def test_startup_reconcile_skips_without_graph(monkeypatch):
    monkeypatch.setattr(gr, "make_reconciler", lambda *a, **k: None)
    monkeypatch.setattr(gr, "_get_sk", lambda: None)
    assert (await gr.startup_reconcile())["action"] == "skipped"


@pytest.mark.asyncio
async def test_bootstrap_vector_index_reindexes_only_when_empty(monkeypatch):
    sk = MagicMock()
    sk.search.entity_vector_count = AsyncMock(return_value=42)
    sk.reindex_entities_from_graph = AsyncMock(return_value=7)
    monkeypatch.setattr(gr, "_get_sk", lambda: sk)
    assert await gr.bootstrap_vector_index() == {"action": "unchanged", "entity_vectors": 42}

    sk.search.entity_vector_count = AsyncMock(return_value=0)
    assert (await gr.bootstrap_vector_index())["action"] == "reindexed"
    sk.reindex_entities_from_graph.assert_awaited_once()

    # in-memory store can't count → always reindex after a restart
    sk.search.entity_vector_count = AsyncMock(return_value=None)
    result = await gr.bootstrap_vector_index()
    assert result["action"] == "reindexed" and result["store_counts"] is False


@pytest.mark.asyncio
async def test_spawn_background_swallows_and_logs_exceptions(caplog):
    async def bad():
        raise ValueError("nope")

    task = gr.spawn_background(bad(), "unit-bad")
    assert await task is None
    assert any("unit-bad failed" in r.message for r in caplog.records)
