"""Unit tests for app/services/entity_resolver.py — pure resolution logic."""

import pytest

from app.services.entity_resolver import (
    ResolvedEntity,
    make_slug,
    normalize_entity_name,
    resolve_against,
    surface_forms_equivalent,
)

ACCOUNTS = [
    {"id": "account-nationswell", "name": "NationSwell", "aliases": []},
    {"id": "account-execonline", "name": "ExecOnline", "aliases": ["Exec Online"]},
    {"id": "account-acme", "name": "Acme", "aliases": []},
]
PEOPLE = [
    {"id": "person-stephen-cole", "name": "Stephen Cole", "aliases": []},
    {"id": "person-sara-chen", "name": "Sara Chen", "aliases": []},
]
PROJECTS = [
    {"id": "project-q3-migration", "name": "Q3 Migration", "aliases": []},
    {"id": "project-atlas-migration", "name": "Atlas Migration", "aliases": ["Atlas"]},
]
TEAMS = [
    {"id": "team-apex", "name": "Apex Team", "aliases": []},
]


class TestNormalization:
    def test_legal_suffixes(self):
        assert normalize_entity_name("Acme Corp", "account") == "acme"
        assert normalize_entity_name("Acme, Inc.", "account") == "acme"
        assert normalize_entity_name("Brightline Inc", "account") == "brightline"

    def test_leading_article(self):
        assert normalize_entity_name("the Atlas project", "project") == "atlas"

    def test_type_words(self):
        assert normalize_entity_name("Apex Team", "team") == "apex"
        assert normalize_entity_name("Pulse Analytics engagement", "project") == "pulse analytics"

    def test_type_word_not_stripped_to_empty(self):
        assert normalize_entity_name("Team", "team") == "team"

    def test_spacing_equivalence(self):
        assert surface_forms_equivalent("Nation Swell", "NationSwell", "account")
        assert surface_forms_equivalent("Exec Online", "ExecOnline", "account")


class TestResolveAgainst:
    def test_spacing_variant_resolves(self):
        r = resolve_against("account", "Nation Swell", ACCOUNTS)
        assert r.id == "account-nationswell"
        assert r.matched_via == "alias"

    def test_suffix_variant_resolves(self):
        r = resolve_against("account", "Acme Corporation", ACCOUNTS)
        assert r.id == "account-acme"

    def test_known_alias_resolves(self):
        r = resolve_against("account", "exec-online", ACCOUNTS)
        assert r.id == "account-execonline"

    def test_unknown_creates_new_with_normalized_slug(self):
        r = resolve_against("account", "Meridian Health", ACCOUNTS)
        assert r == ResolvedEntity(
            id="account-meridian-health",
            canonical_name="Meridian Health",
            matched_via="new",
        )

    def test_person_nickname_resolves(self):
        r = resolve_against("person", "Steve Cole", PEOPLE)
        assert r.id == "person-stephen-cole"
        assert r.matched_via == "fuzzy"

    def test_person_initial_resolves(self):
        r = resolve_against("person", "S. Cole", PEOPLE)
        assert r.id == "person-stephen-cole"

    def test_person_typo_fuzzy_resolves(self):
        r = resolve_against("person", "Sarah Chen", PEOPLE)  # Sara vs Sarah
        assert r.id == "person-sara-chen"

    def test_different_person_not_merged(self):
        r = resolve_against("person", "Sam Cole", PEOPLE)
        assert r.matched_via == "new"

    def test_digit_token_veto(self):
        r = resolve_against("project", "Q4 Migration", PROJECTS)
        assert r.matched_via == "new", "Q4 must never merge into Q3"

    def test_article_and_type_word_resolves(self):
        r = resolve_against("project", "the Q3 migration", PROJECTS)
        assert r.id == "project-q3-migration"
        r = resolve_against("project", "the Atlas project", PROJECTS)
        assert r.id == "project-atlas-migration"

    def test_team_type_word(self):
        r = resolve_against("team", "Apex", TEAMS)
        assert r.id == "team-apex"

    def test_empty_candidates(self):
        r = resolve_against("account", "Brand New Co", [])
        assert r.matched_via == "new"
        assert r.id == "account-brand-new"  # legal suffix stripped in slug

    def test_conservative_on_close_but_distinct_accounts(self):
        # Heydrich vs Heydrick sits just under the account threshold — fuzzy
        # must NOT auto-merge ASR-near-miss org names; alias frontmatter or a
        # manual merge handles those.
        r = resolve_against(
            "account", "Heydrick", [{"id": "account-heydrich", "name": "Heydrich", "aliases": []}]
        )
        assert r.matched_via == "new"


class TestMakeSlug:
    @pytest.mark.parametrize(
        "etype,name,expected",
        [
            ("account", "Acme Corp", "account-acme"),
            ("account", "Nation Swell", "account-nation-swell"),
            ("person", "Dr. Sarah Chen", "person-sarah-chen"),
            ("team", "Apex Team", "team-apex"),
        ],
    )
    def test_slugs(self, etype, name, expected):
        assert make_slug(etype, name) == expected
