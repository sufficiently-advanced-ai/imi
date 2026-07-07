"""Tests for IngestOrchestrator._phase_enrich_profiles.

The ingest path otherwise leaves every entity with a stub body and no signal
grounding. This phase saves signals to the canonical signal_store, then reuses
the live-meeting enrichment chain to write grounded narratives.
"""

import pytest

from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator


def _make_orch():
    return IngestOrchestrator(
        classifier=None,
        claude_client=object(),
        graph=object(),
        signal_writer=None,
        git_ops=None,
    )


class _Obs:
    def __init__(self):
        self.entities_mentioned = {"person": ["Jeff Jennings"], "project": ["Apollo"]}
        self.raw_content = "Jeff discussed Apollo timeline."
        self.content = self.raw_content
        self.title = "Standup"


class _Signals:
    bot_id = "ingest-abc123"
    signal_count = 2




@pytest.mark.asyncio
async def test_enrich_profiles_is_non_fatal_on_error(monkeypatch):
    class _BoomStore:
        def save(self, ms):
            raise RuntimeError("disk full")

    monkeypatch.setattr(
        "app.services.signal_store.signal_store", _BoomStore(), raising=True
    )
    orch = _make_orch()
    # Must not raise — phase is best-effort.
    result = await orch._phase_enrich_profiles(_Obs(), _Signals(), "ingest-abc123")
    assert result == {"rich_profiles_generated": 0}


@pytest.mark.asyncio
async def test_enrich_profiles_skips_when_no_entities():
    orch = _make_orch()
    obs = _Obs()
    obs.entities_mentioned = {}
    result = await orch._phase_enrich_profiles(obs, _Signals(), "ingest-abc123")
    assert result == {"rich_profiles_generated": 0}
