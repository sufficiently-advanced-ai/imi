from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator
from app.services.entity_utils import ensure_entity_id_format


class _MS:
    def __init__(self, participants, entities_mentioned):
        self.participants = participants
        self.entities_mentioned = entities_mentioned


def test_collect_entities_ids_match_ensure_entity_id_format():
    ms = _MS(
        participants=["Jane Okoye"],
        entities_mentioned={"client": ["Foo_Bar Industries"], "stakeholder": ["Jane Okoye"]},
    )
    entities = IngestOrchestrator._collect_entities(None, ms)
    by_id = {e["id"]: e for e in entities}
    # The client node id must equal what ensure_entity_id_format produces,
    # so it matches the Signal.client_id derived by the promoter.
    expected = ensure_entity_id_format("client", "Foo_Bar Industries")
    assert expected in by_id
    # And participant/person ids likewise
    assert ensure_entity_id_format("person", "Jane Okoye") in by_id
