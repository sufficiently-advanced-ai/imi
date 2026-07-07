from app.model_schemas.domain_config import DomainEntity


def test_entity_accepts_ner_labels():
    entity = DomainEntity(
        name="client",
        description="A client organization",
        plural="clients",
        ner_labels=["ORG", "ORGANIZATION"],
    )
    assert entity.ner_labels == ["ORG", "ORGANIZATION"]


def test_entity_ner_labels_default_empty():
    entity = DomainEntity(name="client", description="d", plural="clients")
    assert entity.ner_labels == []
