import pytest
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator
from app.models.ingestion.models import IngestRequest, ContentSource


class _FakeSK:
    async def extract_entities_grouped(self, text):
        return {
            "client": [{"id": "client-acme-corp", "name": "Acme Corp", "type": "client"}],
            "stakeholder": [{"id": "stakeholder-jane-doe", "name": "Jane Doe", "type": "stakeholder"}],
        }


@pytest.mark.asyncio
async def test_build_meeting_seeds_domain_entities(monkeypatch):
    monkeypatch.setattr(
        "app.services.orchestrators.ingest_orchestrator.get_semantica_knowledge",
        lambda: _FakeSK(),
    )
    orch = IngestOrchestrator(
        classifier=None, claude_client=None, graph=None,
        signal_writer=None, git_ops=None,
    )
    req = IngestRequest(content="Call with Acme Corp and Jane Doe", source=ContentSource.OTTER)
    observation = await orch._phase_build_observation(req, "ingest-abc", "call_transcript")
    assert "client" in observation.entities_mentioned
    assert "Acme Corp" in observation.entities_mentioned["client"]
    assert "Jane Doe" in observation.entities_mentioned["stakeholder"]
