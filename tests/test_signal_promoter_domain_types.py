from datetime import datetime, timezone

from app.models.observation import Observation
from app.services.signal_promoter import SignalPromoter


def test_resolves_client_typed_entities(monkeypatch):
    # Active domain declares a 'client' entity type
    monkeypatch.setattr(
        "app.services.signal_promoter.get_active_entity_types",
        lambda: {"client", "engagement", "stakeholder", "person", "meeting", "document"},
    )
    obs = Observation(
        observation_id="m1",
        external_id="b1",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        entities_mentioned={"client": ["Acme Corp"], "stakeholder": ["Jane Doe"]},
        content="## Decisions\n- Signed renewal with Acme Corp",
    )
    promoter = SignalPromoter(claude_client=None, knowledge_graph=None)
    refs = promoter._resolve_entities_from_state(obs)
    ids = {r.id for r in refs}
    assert "client-acme-corp" in ids
    assert "stakeholder-jane-doe" in ids
