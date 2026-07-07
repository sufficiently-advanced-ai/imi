import app.services.entity_utils as eu
from app.services.entity_utils import ensure_entity_id_format


def test_ensure_id_idempotent_for_domain_only_type():
    # 'client' is NOT in the static VALID_ENTITY_TYPES, but a pre-slugged id
    # must not be double-prefixed.
    assert ensure_entity_id_format("client", "client-acme-corp") == "client-acme-corp"


def test_ensure_id_builds_from_raw_name():
    assert ensure_entity_id_format("client", "Acme Corp") == "client-acme-corp"


def test_get_active_entity_types_falls_back_on_error(monkeypatch):
    def boom():
        raise RuntimeError("no domain service")
    monkeypatch.setattr(
        "app.core.domain_config.domain_config_service.get_domain_config_service",
        boom,
    )
    assert eu.get_active_entity_types() == eu.VALID_ENTITY_TYPES
