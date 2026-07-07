from app.model_schemas.domain_config import DomainConfiguration, DomainEntity, DomainRelationship
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator as IO


def _solo():
    return DomainConfiguration(
        id="solo_consulting", name="Solo", version="1.0.0",
        entities={
            "client": DomainEntity(name="client", description="d", plural="clients", relationships=[
                DomainRelationship(type="has_engagements", target="engagement", cardinality="one_to_many"),
                DomainRelationship(type="has_stakeholders", target="stakeholder", cardinality="one_to_many"),
            ]),
            "engagement": DomainEntity(name="engagement", description="d", plural="engagements", relationships=[
                DomainRelationship(type="for_client", target="client", cardinality="many_to_one"),
            ]),
            "stakeholder": DomainEntity(name="stakeholder", description="d", plural="stakeholders", relationships=[
                DomainRelationship(type="works_at", target="client", cardinality="many_to_one"),
            ]),
        },
    )


def test_forward_resolution():
    assert IO._resolve_domain_relationship("client-acme", "engagement-x", _solo()) == ("client-acme", "engagement-x", "has_engagements")

def test_reverse_resolution_flips_edge():
    # stakeholder.works_at -> client is defined forward
    assert IO._resolve_domain_relationship("stakeholder-jane", "client-acme", _solo()) == ("stakeholder-jane", "client-acme", "works_at")
    # client->engagement reverse (engagement has for_client) still resolves, flipped
    assert IO._resolve_domain_relationship("engagement-x", "client-acme", _solo()) == ("engagement-x", "client-acme", "for_client")

def test_no_relationship_returns_none():
    assert IO._resolve_domain_relationship("engagement-x", "stakeholder-y", _solo()) is None

def test_type_from_id():
    assert IO._entity_type_from_id("client-acme-corp") == "client"
