from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator as IO


def test_filters_out_non_domain_types():
    entities = [
        {"id": "client-acme-corp", "type": "client", "name": "Acme Corp"},
        {"id": "stakeholder-jane", "type": "stakeholder", "name": "Jane"},
        {"id": "person-sofia", "type": "person", "name": "Sofia"},
        {"id": "project-x", "type": "project", "name": "X"},
        {"id": "concept-esg", "type": "concept", "name": "ESG"},
        {"id": "date-may-20", "type": "date", "name": "May 20"},
    ]
    valid = {"client", "engagement", "stakeholder", "consultant"}
    out = IO._filter_to_domain_entities(entities, valid)
    assert {e["id"] for e in out} == {"client-acme-corp", "stakeholder-jane"}


def test_no_valid_types_returns_unchanged():
    entities = [{"id": "person-x", "type": "person", "name": "X"}]
    # Empty set → passthrough (no domain types configured → don't drop anything)
    assert IO._filter_to_domain_entities(entities, set()) == entities
