"""Regression test for inverse-relationship disambiguation.

When two relationships connect the same pair of entity types (e.g. account
``managed_by``/``has_contacts`` both target person), the inverse map must use
the declared ``inverse_name`` to pick the correct reverse relationship rather
than the first relationship that happens to point back at the entity type.

See PR #891 (agency ``has_contacts``/``contact_for``).
"""

import pytest

from app.model_schemas.domain_config import (
    DomainConfiguration,
    DomainEntity,
    DomainRelationship,
)
from app.services.entity_file_service import EntityFileService


@pytest.fixture
def two_pair_domain_config() -> DomainConfiguration:
    """account<->person connected by two distinct relationship pairs.

    person's relationships deliberately list ``manages_accounts`` before
    ``contact_for`` so a naive first-match scan would mis-map ``has_contacts``.
    """
    return DomainConfiguration(
        id="test_agency",
        name="Test Agency",
        description="Two relationship pairs between the same entity types",
        entities={
            "account": DomainEntity(
                name="account",
                description="Account entity",
                plural="accounts",
                relationships=[
                    DomainRelationship(
                        type="managed_by",
                        target="person",
                        cardinality="many_to_one",
                        inverse_name="manages_accounts",
                    ),
                    DomainRelationship(
                        type="has_contacts",
                        target="person",
                        cardinality="one_to_many",
                        inverse_name="contact_for",
                    ),
                ],
            ),
            "person": DomainEntity(
                name="person",
                description="Person entity",
                plural="people",
                relationships=[
                    # Ordered first on purpose to trigger the ambiguity.
                    DomainRelationship(
                        type="manages_accounts",
                        target="account",
                        cardinality="one_to_many",
                        inverse_name="managed_by",
                    ),
                    DomainRelationship(
                        type="contact_for",
                        target="account",
                        cardinality="many_to_one",
                        inverse_name="has_contacts",
                    ),
                ],
            ),
        },
    )


def test_inverse_map_uses_declared_inverse_name(two_pair_domain_config):
    """has_contacts must map to contact_for, not the first person->account rel."""
    service = EntityFileService(domain_config=two_pair_domain_config)

    account_map = service._build_inverse_relationship_map("account")

    assert account_map["has_contacts"]["inverse_name"] == "contact_for"
    assert account_map["managed_by"]["inverse_name"] == "manages_accounts"


def test_inverse_map_disambiguates_reverse_direction(two_pair_domain_config):
    """The same must hold when building the map from the person side."""
    service = EntityFileService(domain_config=two_pair_domain_config)

    person_map = service._build_inverse_relationship_map("person")

    assert person_map["contact_for"]["inverse_name"] == "has_contacts"
    assert person_map["manages_accounts"]["inverse_name"] == "managed_by"


def test_inverse_map_falls_back_without_inverse_name():
    """Legacy configs without inverse_name still resolve via first-match scan."""
    config = DomainConfiguration(
        id="legacy",
        name="Legacy",
        description="No inverse_name declared",
        entities={
            "account": DomainEntity(
                name="account",
                description="Account",
                plural="accounts",
                relationships=[
                    DomainRelationship(
                        type="has_contacts",
                        target="person",
                        cardinality="one_to_many",
                    ),
                ],
            ),
            "person": DomainEntity(
                name="person",
                description="Person",
                plural="people",
                relationships=[
                    DomainRelationship(
                        type="contact_for",
                        target="account",
                        cardinality="many_to_one",
                    ),
                ],
            ),
        },
    )

    service = EntityFileService(domain_config=config)
    account_map = service._build_inverse_relationship_map("account")

    assert account_map["has_contacts"]["inverse_name"] == "contact_for"
