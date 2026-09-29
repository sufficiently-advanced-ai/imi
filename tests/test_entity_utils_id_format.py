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


def test_slugify_folds_accents_instead_of_truncating():
    from app.services.entity_utils import slugify

    assert slugify("Emma Deloné") == "emma-delone"
    assert slugify("José Muñoz") == "jose-munoz"
    assert slugify("  O'Brien & Co. ") == "o-brien-co"
    assert slugify("") == ""


def test_id_generators_agree_on_accented_names():
    from app.services.entity_resolver import make_slug

    for name in ("Emma Deloné", "Zoë Ångström", "Dan Kauppi"):
        assert ensure_entity_id_format("person", name) == make_slug("person", name)
