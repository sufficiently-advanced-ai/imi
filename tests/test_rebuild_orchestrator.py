"""RebuildOrchestrator — phase sequencing, tiers, dry-run, guards.

All external dependencies are faked; per-phase behavior (wipe → entities →
replay → edges → reindex → evaluate) is asserted through call recording.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.signal import MeetingSignals, Signal
from app.services.orchestrators.rebuild_orchestrator import (
    PHASES,
    RebuildOrchestrator,
)

_T1 = "2026-06-01T10:00:00+00:00"
_T2 = "2026-06-02T10:00:00+00:00"


def _ms(bot_id: str, extracted_at: str, n_signals: int = 1) -> MeetingSignals:
    return MeetingSignals(
        meeting_id=f"meet-{bot_id}",
        bot_id=bot_id,
        extracted_at=extracted_at,
        signal_count=n_signals,
        signals=[
            Signal(
                id=f"{bot_id}-sig-{i}",
                type="decision",
                content=f"Decision {i} from {bot_id}",
                source_meeting_id=bot_id,
                source_timestamp=extracted_at,
                confidence=0.9,
            )
            for i in range(n_signals)
        ],
    )


class FakeNeo4j:
    def __init__(self, node_count: int = 5):
        self.node_count = node_count

    async def execute_read(self, query, params=None):
        return [{"nodes": self.node_count}]

    async def execute_write(self, query, params=None):
        deleted = min((params or {}).get("batch", 1000), self.node_count)
        self.node_count -= deleted
        return [{"deleted": deleted}]


def _orchestrator(store=None, tier_source=False, **overrides):
    kg = AsyncMock()
    kg.build_graph = AsyncMock(return_value={"entities": 4})
    writer = MagicMock()
    writer.write_meeting_signals = AsyncMock(side_effect=lambda ms: len(ms.signals))
    store = store if store is not None else MagicMock()
    ingest = AsyncMock() if tier_source else None
    kwargs = dict(
        neo4j_client=FakeNeo4j(),
        knowledge_graph=kg,
        semantica=None,
        signal_writer=writer,
        signal_store=store,
        tenant_id="tenant-a",
        ingest_orchestrator=ingest,
    )
    kwargs.update(overrides)
    return RebuildOrchestrator(**kwargs)


@pytest.fixture(autouse=True)
def _stub_externals(monkeypatch):
    """Stub the module-level imports the phases resolve lazily."""
    import app.services.signal_indexing as si

    monkeypatch.setattr(
        si, "reset_vector_index", lambda: {"backend": "pgvector", "deleted": 3}
    )
    monkeypatch.setattr(si, "backfill_signals", lambda store=None: (2, 2, 0))

    import app.services.staleness_evaluator as se

    monkeypatch.setattr(
        se,
        "run_staleness_evaluation",
        AsyncMock(return_value={"evaluated": 2, "transitions": 0}),
    )
    import app.services.constitution as constitution

    monkeypatch.setattr(
        constitution, "export_constitution", AsyncMock(return_value={"committed": True})
    )


# ---------------------------------------------------------------------------
# signals tier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signals_tier_runs_all_phases_in_order():
    store = MagicMock()
    store.load_all = MagicMock(return_value=[_ms("b1", _T1), _ms("b2", _T2)])
    orch = _orchestrator(store=store)
    job_store = {}

    result = await orch.process("signals", "job-1", job_store)

    assert result["status"] == "completed"
    assert job_store["job:job-1"]["phases_completed"] == PHASES
    assert result["replay"]["signals_written"] == 2
    assert result["wipe_vectors"]["backend"] == "pgvector"
    assert result["reindex_signals"] == {"total": 2, "indexed": 2, "skipped": 0}


@pytest.mark.asyncio
async def test_signals_replay_is_chronological_ascending():
    """Meetings replay oldest-first regardless of store order."""
    store = MagicMock()
    store.load_all = MagicMock(return_value=[_ms("newer", _T2), _ms("older", _T1)])
    orch = _orchestrator(store=store)

    replay_order = []
    orch._writer.write_meeting_signals = AsyncMock(
        side_effect=lambda ms: replay_order.append(ms.bot_id) or len(ms.signals)
    )

    await orch.process("signals", "job-2", {})
    assert replay_order == ["older", "newer"]


@pytest.mark.asyncio
async def test_entities_rebuilt_before_replay():
    """REBUILD_ENTITIES must precede REPLAY (edges no-op on missing entities)."""
    store = MagicMock()
    store.load_all = MagicMock(return_value=[_ms("b1", _T1)])
    orch = _orchestrator(store=store)

    calls = []
    orch._kg.build_graph = AsyncMock(
        side_effect=lambda **kw: calls.append("entities") or {}
    )
    orch._writer.write_meeting_signals = AsyncMock(
        side_effect=lambda ms: calls.append("replay") or 1
    )

    await orch.process("signals", "job-3", {})
    assert calls.index("entities") < calls.index("replay")


@pytest.mark.asyncio
async def test_dry_run_makes_no_writes():
    store = MagicMock()
    store.load_all = MagicMock(return_value=[_ms("b1", _T1, n_signals=3)])
    orch = _orchestrator(store=store)
    client_before = orch._client.node_count

    result = await orch.process("signals", "job-dry", {}, dry_run=True)

    assert result["status"] == "completed"
    assert result["wipe_graph"]["dry_run"] is True
    assert result["wipe_graph"]["nodes_that_would_be_deleted"] == client_before
    assert result["replay"] == {"dry_run": True, "meetings": 1, "signals": 3}
    orch._writer.write_meeting_signals.assert_not_awaited()
    orch._kg.build_graph.assert_not_awaited()
    assert orch._client.node_count == client_before  # nothing deleted


# ---------------------------------------------------------------------------
# source tier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_tier_requires_ingest_orchestrator():
    orch = _orchestrator(tier_source=False)
    with pytest.raises(ValueError, match="ingest_orchestrator"):
        await orch.process("source", "job-4", {})


@pytest.mark.asyncio
async def test_source_tier_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    orch = _orchestrator(tier_source=True)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        await orch.process("source", "job-5", {})


@pytest.mark.asyncio
async def test_source_tier_replays_meetings_chronologically(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    meetings_dir = tmp_path / "meetings"
    meetings_dir.mkdir()
    older = (
        "---\nmeeting_id: m-old\nbot_id: ingest-old\n"
        "updated_at: 2026-01-01T10:00:00+00:00\nupdate_count: 1\n"
        "is_finalized: true\nstatus: completed\ntitle: Old\n---\n\n# Old\nBody A"
    )
    newer = (
        "---\nmeeting_id: m-new\nbot_id: ingest-new\n"
        "updated_at: 2026-02-01T10:00:00+00:00\nupdate_count: 1\n"
        "is_finalized: true\nstatus: completed\ntitle: New\n---\n\n# New\nBody B"
    )
    # Filenames sort against chronology on purpose (a- prefix on the newer one)
    (meetings_dir / "meeting-a-new.md").write_text(newer)
    (meetings_dir / "meeting-z-old.md").write_text(older)

    orch = _orchestrator(tier_source=True)
    orch._meetings_glob = lambda: str(meetings_dir / "meeting-*.md")

    replayed = []

    async def record(obs, bot_id, job_id, job_store):
        replayed.append(bot_id)
        return {"status": "completed"}

    orch._ingest.process_observation = AsyncMock(side_effect=record)

    result = await orch.process("source", "job-6", {})

    assert replayed == ["ingest-old", "ingest-new"]  # chronological, not filename order
    assert result["replay"]["reextracted"] == 2
    assert result["backfill_edges"]["skipped"]
    assert result["reindex_signals"]["skipped"]


@pytest.mark.asyncio
async def test_failure_marks_job_failed():
    store = MagicMock()
    store.load_all = MagicMock(side_effect=RuntimeError("store unavailable"))
    orch = _orchestrator(store=store)
    job_store = {}

    result = await orch.process("signals", "job-7", job_store)

    assert result["status"] == "failed"
    assert "store unavailable" in result["error"]
    assert job_store["job:job-7"]["status"] == "failed"


@pytest.mark.asyncio
async def test_unknown_tier_rejected():
    orch = _orchestrator()
    with pytest.raises(ValueError, match="tier"):
        await orch.process("everything", "job-8", {})


@pytest.mark.asyncio
async def test_vector_wipe_error_fails_job(monkeypatch):
    """A failed vector wipe must stop the rebuild before replay."""
    import app.services.signal_indexing as si

    monkeypatch.setattr(
        si,
        "reset_vector_index",
        lambda: {"backend": "pgvector", "deleted": 0, "error": "pg down"},
    )
    store = MagicMock()
    store.load_all = MagicMock(return_value=[_ms("b1", _T1)])
    orch = _orchestrator(store=store)

    result = await orch.process("signals", "job-vec-fail", {})

    assert result["status"] == "failed"
    assert "vector index wipe failed" in result["error"]
    orch._writer.write_meeting_signals.assert_not_awaited()  # replay never ran


@pytest.mark.asyncio
async def test_source_replay_partial_failure_marks_degraded(monkeypatch, tmp_path):
    """Per-meeting failures must not read as a clean rebuild."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    meetings_dir = tmp_path / "meetings"
    meetings_dir.mkdir()
    for name, bot in (("meeting-a.md", "ingest-aa"), ("meeting-b.md", "ingest-bb")):
        (meetings_dir / name).write_text(
            f"---\nmeeting_id: m\nbot_id: {bot}\n"
            "updated_at: 2026-01-01T10:00:00+00:00\nupdate_count: 1\n"
            "is_finalized: true\nstatus: completed\n---\n\n# M\nBody"
        )

    orch = _orchestrator(tier_source=True)
    orch._meetings_glob = lambda: str(meetings_dir / "meeting-*.md")

    async def one_fails(obs, bot_id, job_id, job_store):
        return {"status": "completed" if bot_id == "ingest-aa" else "failed"}

    orch._ingest.process_observation = AsyncMock(side_effect=one_fails)

    job_store = {}
    result = await orch.process("source", "job-partial", job_store)

    assert result["status"] == "degraded"
    assert "ingest-bb" in result["error"]
    assert job_store["job:job-partial"]["status"] == "degraded"


@pytest.mark.asyncio
async def test_evaluate_states_failure_does_not_fail_job():
    """Lifecycle artifact regeneration is best-effort after a good rebuild."""
    store = MagicMock()
    store.load_all = MagicMock(return_value=[])
    orch = _orchestrator(store=store)

    import app.services.staleness_evaluator as se

    with patch.object(
        se, "run_staleness_evaluation", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        result = await orch.process("signals", "job-9", {})

    assert result["status"] == "completed"
    assert "staleness_error" in result["evaluate_states"]
