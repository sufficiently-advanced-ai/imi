import pytest
from app.model_schemas.domain_config import DomainConfiguration, DomainEntity
from app.services.semantica_extraction import SemanticaExtraction


def test_domain_entity_accepts_ner_exclude():
    e = DomainEntity(name="client", description="d", plural="clients",
                     ner_labels=["ORG"], ner_exclude=["MSCI"])
    assert e.ner_exclude == ["MSCI"]


class _Ent:
    def __init__(self, text, label):
        self.text = text
        self.label = label
        self.confidence = 0.9
        self.start_char = 0
        self.end_char = len(text)
        self.metadata = {}


class _NER:
    def __init__(self, ents):
        self._e = ents

    def extract_entities(self, text):
        return self._e


def _domain():
    return DomainConfiguration(
        id="solo_consulting", name="Solo", version="1.0.0",
        entities={"client": DomainEntity(
            name="client", description="d", plural="clients",
            ner_labels=["ORG", "ORGANIZATION"], ner_exclude=["MSCI", "GRI", "SBTi"])},
    )


@pytest.mark.asyncio
async def test_ner_exclude_filters_framework_orgs():
    ner = _NER([_Ent("Acme Corp", "ORG"), _Ent("MSCI", "ORG"), _Ent("gri", "ORG")])
    ext = SemanticaExtraction(ner_extractor=ner, duplicate_detector=None, domain_config=_domain())
    ents = await ext.extract_entities("irrelevant text")
    names = {e["name"] for e in ents}
    assert "Acme Corp" in names           # real client kept
    assert "MSCI" not in names            # excluded (exact)
    assert "gri" not in names             # excluded (case-insensitive)


@pytest.mark.asyncio
async def test_ner_exclude_is_per_type_not_global():
    # 'MSCI' is excluded for client, but a PERSON-labelled 'MSCI' maps to a
    # different type whose ner_exclude is empty -> must NOT be dropped.
    from app.model_schemas.domain_config import DomainConfiguration, DomainEntity
    domain = DomainConfiguration(
        id="solo_consulting", name="Solo", version="1.0.0",
        entities={
            "client": DomainEntity(name="client", description="d", plural="clients",
                                   ner_labels=["ORG"], ner_exclude=["MSCI"]),
            "stakeholder": DomainEntity(name="stakeholder", description="d", plural="stakeholders",
                                        ner_labels=["PERSON"]),
        },
    )
    ner = _NER([_Ent("MSCI", "PERSON")])  # tagged PERSON -> stakeholder, not client
    ext = SemanticaExtraction(ner_extractor=ner, duplicate_detector=None, domain_config=domain)
    ents = await ext.extract_entities("x")
    names = {e["name"] for e in ents}
    assert "MSCI" in names  # not excluded, because it's a stakeholder here, not a client


@pytest.mark.asyncio
async def test_ner_exclude_multiword_term():
    from app.model_schemas.domain_config import DomainConfiguration, DomainEntity
    domain = DomainConfiguration(
        id="solo_consulting", name="Solo", version="1.0.0",
        entities={"client": DomainEntity(name="client", description="d", plural="clients",
                                         ner_labels=["ORG"], ner_exclude=["GHG Protocol"])},
    )
    ner = _NER([_Ent("Acme Corp", "ORG"), _Ent("GHG Protocol", "ORG")])
    ext = SemanticaExtraction(ner_extractor=ner, duplicate_detector=None, domain_config=domain)
    names = {e["name"] for e in await ext.extract_entities("x")}
    assert "Acme Corp" in names
    assert "GHG Protocol" not in names
