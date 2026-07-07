from app.model_schemas.domain_config import DomainConfiguration, DomainEntity
from app.services.semantica_extraction import SemanticaExtraction


def _domain():
    return DomainConfiguration(
        id="solo_consulting",
        name="Solo",
        version="1.0.0",
        entities={
            "client": DomainEntity(
                name="client", description="d", plural="clients",
                ner_labels=["ORG", "ORGANIZATION"],
            ),
            "stakeholder": DomainEntity(
                name="stakeholder", description="d", plural="stakeholders",
                ner_labels=["PERSON", "PER"],
            ),
        },
    )


def test_org_maps_to_client_via_domain_ner_labels():
    ext = SemanticaExtraction(ner_extractor=None, duplicate_detector=None, domain_config=_domain())
    assert ext._map_ner_label("ORG") == "client"
    assert ext._map_ner_label("PERSON") == "stakeholder"


def test_unmapped_label_falls_back_to_default_map():
    ext = SemanticaExtraction(ner_extractor=None, duplicate_detector=None, domain_config=_domain())
    assert ext._map_ner_label("MONEY") == "financial"


def test_no_domain_uses_default_map():
    ext = SemanticaExtraction(ner_extractor=None, duplicate_detector=None, domain_config=None)
    assert ext._map_ner_label("ORG") == "organization"
    assert ext._map_ner_label("PERSON") == "person"


def test_unknown_label_falls_back_to_lowercased():
    ext = SemanticaExtraction(ner_extractor=None, duplicate_detector=None, domain_config=_domain())
    # A label that no domain claims and that is absent from the default map
    assert ext._map_ner_label("NORP") == "norp"
