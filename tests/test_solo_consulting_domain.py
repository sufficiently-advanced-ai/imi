from pathlib import Path
from app.core.domain_config.active_domain import _parse_yaml


def test_solo_consulting_domain_loads_and_validates():
    domain = _parse_yaml(Path("config/domains/solo_consulting.yaml"))
    assert domain.id == "solo_consulting"
    assert set(domain.entities.keys()) >= {"client", "engagement", "stakeholder", "consultant"}
    assert "ORG" in domain.entities["client"].ner_labels
    assert "PERSON" in domain.entities["stakeholder"].ner_labels
    # symmetric inverse relationships the model validates
    assert any(r.type == "has_engagements" for r in domain.entities["client"].relationships)
    assert any(r.type == "for_client" for r in domain.entities["engagement"].relationships)
    assert any(r.type == "advises" for r in domain.entities["consultant"].relationships)
    assert any(r.type == "involved_in" for r in domain.entities["stakeholder"].relationships)
    assert any(r.type == "has_participants" for r in domain.entities["engagement"].relationships)
    assert "MSCI" in domain.entities["client"].ner_exclude
