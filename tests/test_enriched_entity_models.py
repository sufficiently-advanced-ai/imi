"""Regression tests: entity models with forward refs must be constructible.

PR #5 review: ``OrganizationalContext`` (and ``SuggestedEntity``) were
imported only under ``TYPE_CHECKING``, so Pydantic could never resolve the
forward references and instantiation raised ``PydanticUserError``
("class not fully defined").
"""

def test_exported_enriched_entity_with_context_instantiates():
    # entities.py defines its own CanonicalPerson; base_entity validates
    # against that variant, not core.py's duplicate.
    from app.models.api.entities import CanonicalPerson, EnrichedEntityWithContext

    entity = EnrichedEntityWithContext(
        base_entity=CanonicalPerson(id="person-1", canonical_name="Ada")
    )
    assert entity.organizational_context is None


def test_core_enriched_entity_with_context_instantiates():
    from app.models.api.core import CanonicalPerson, EnrichedEntityWithContext

    entity = EnrichedEntityWithContext(
        base_entity=CanonicalPerson(id="person-1", canonical_name="Ada")
    )
    assert entity.organizational_context is None


def test_entity_autocomplete_response_instantiates():
    from app.models.api.entities import EntityAutocompleteResponse

    response = EntityAutocompleteResponse(suggestions=[], query="ada", total_matches=0)
    assert response.suggestions == []
