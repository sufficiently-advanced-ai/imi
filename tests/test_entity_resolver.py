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


# ---- decision-model tiebreak ----------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from app.services.entity_resolver import (  # noqa: E402
    EntityResolver,
    apply_tiebreak,
    fuzzy_zone,
    needs_tiebreak,
)
from app.services.inference.decisions import (  # noqa: E402
    ChoiceAnswer,
    DecisionResult,
    DecisionUnavailable,
)


class _FakeDecisions:
    """Answers every tiebreak with a scripted (option label -> probability)
    chosen by candidate NAME, so tests don't depend on option ordering."""

    def __init__(self, mode="on", pick=None, p=0.95, fail=False):
        self._mode, self._pick, self._p, self._fail = mode, pick, p, fail
        self.calls: list[dict] = []

    def mode(self, operation):
        return self._mode

    async def decide(self, state, questions, *, operation):
        self.calls.append(state)
        if self._fail:
            raise DecisionUnavailable("down", status=503)
        label = "none"
        for key, c in state["candidates"].items():
            if c["name"] == self._pick:
                label = key
        answer = ChoiceAnswer(choice=label, probabilities={label: self._p}, confidence=self._p)
        return DecisionResult({"match": answer}, "jev", "fake", 0, 0, 0.0, 0, {})


def _graph(nodes):
    return SimpleNamespace(
        nodes={
            n["id"]: SimpleNamespace(
                id=n["id"], name=n["name"], type=n["type"],
                metadata={"aliases": n.get("aliases", []), **n.get("meta", {})},
            )
            for n in nodes
        }
    )


GRAPH = _graph(
    [
        {"id": "person-john-smith", "name": "John Smith", "type": "person", "meta": {"role": "CFO"}},
        {"id": "account-salesforce", "name": "Salesforce", "type": "account"},
        {"id": "account-blue-cross-blue-shield", "name": "Blue Cross Blue Shield", "type": "account"},
        {"id": "project-q3-migration", "name": "Q3 Migration", "type": "project"},
        {"id": "account-acme", "name": "Acme", "type": "account"},
    ]
)


class TestFuzzyZone:
    def test_zone_catches_acronyms_and_near_misses(self):
        accounts = [c for c in EntityResolver(GRAPH, decisions=None)._candidates("account")]
        assert [c["id"] for _, c in fuzzy_zone("account", "BCBS", accounts)] == ["account-blue-cross-blue-shield"]
        assert [c["id"] for _, c in fuzzy_zone("account", "Salesforce.com", accounts)] == ["account-salesforce"]
        assert fuzzy_zone("account", "Meridian Health", accounts) == []

    def test_digit_veto_keeps_candidates_out_of_zone(self):
        projects = EntityResolver(GRAPH, decisions=None)._candidates("project")
        assert fuzzy_zone("project", "Q4 Migration", projects) == []

    def test_only_similarity_outcomes_need_a_tiebreak(self):
        assert needs_tiebreak(resolve_against("account", "Acme Corp", ACCOUNTS)) is False  # alias
        assert needs_tiebreak(resolve_against("person", "Steve Cole", PEOPLE)) is False  # rule-based
        assert needs_tiebreak(resolve_against("person", "Sarah Chen", PEOPLE)) is True  # ratio
        assert needs_tiebreak(resolve_against("account", "Heydrick", [])) is True  # new


class TestApplyTiebreak:
    HEUR_NEW = ResolvedEntity(id="account-bcbs", canonical_name="BCBS", matched_via="new")
    OPTIONS = {"c1": {"id": "account-blue-cross-blue-shield", "name": "Blue Cross Blue Shield"}}

    def test_confident_pick_merges(self):
        r = apply_tiebreak(self.HEUR_NEW, self.OPTIONS, "c1", 0.9, "account", "BCBS")
        assert (r.id, r.matched_via, r.canonical_name) == ("account-blue-cross-blue-shield", "decision", "Blue Cross Blue Shield")

    def test_unsure_pick_keeps_heuristic(self):
        assert apply_tiebreak(self.HEUR_NEW, self.OPTIONS, "c1", 0.6, "account", "BCBS") is self.HEUR_NEW

    def test_confident_none_splits_a_fuzzy_merge(self):
        fuzzy = ResolvedEntity(id="person-john-smith", canonical_name="John Smith", matched_via="fuzzy", score=0.9)
        r = apply_tiebreak(fuzzy, {}, "none", 0.97, "person", "Joan Smith")
        assert (r.id, r.matched_via) == ("person-joan-smith", "new")


class TestPrefetch:
    @pytest.mark.asyncio
    async def test_on_mode_overrides_and_carries_context(self):
        fake = _FakeDecisions(pick="Blue Cross Blue Shield")
        r = EntityResolver(GRAPH, decisions=fake)
        changed = await r.prefetch([{"type": "account", "name": "BCBS", "evidence": "our BCBS renewal"}])
        assert changed == 1
        assert r.resolve("account", "BCBS").id == "account-blue-cross-blue-shield"
        assert fake.calls[0]["mention"]["evidence"] == "our BCBS renewal"

    @pytest.mark.asyncio
    async def test_on_mode_splits_false_fuzzy_merge(self):
        # Joan vs John Smith clears the 0.85 person threshold on ratio alone.
        assert resolve_against("person", "Joan Smith", EntityResolver(GRAPH, decisions=None)._candidates("person")).id == "person-john-smith"
        fake = _FakeDecisions(pick=None)
        r = EntityResolver(GRAPH, decisions=fake)
        await r.prefetch([{"type": "person", "name": "Joan Smith"}])
        assert r.resolve("person", "Joan Smith").id == "person-joan-smith"
        assert fake.calls[0]["candidates"]["c1"]["role"] == "CFO"

    @pytest.mark.asyncio
    async def test_shadow_mode_calls_but_never_changes_outcome(self):
        fake = _FakeDecisions(mode="shadow", pick="Blue Cross Blue Shield")
        r = EntityResolver(GRAPH, decisions=fake)
        assert await r.prefetch([{"type": "account", "name": "BCBS"}]) == 0
        assert len(fake.calls) == 1
        assert r.resolve("account", "BCBS").matched_via == "new"

    @pytest.mark.asyncio
    async def test_failures_and_deterministic_matches_skip_or_fall_back(self):
        fake = _FakeDecisions(fail=True)
        r = EntityResolver(GRAPH, decisions=fake)
        await r.prefetch([{"type": "account", "name": "BCBS"}, {"type": "account", "name": "Acme Inc"}])
        assert len(fake.calls) == 1  # Acme Inc is an alias match, never asked
        assert r.resolve("account", "BCBS").matched_via == "new"

    @pytest.mark.asyncio
    async def test_no_client_is_pure_heuristic(self):
        r = EntityResolver(GRAPH, decisions=None)
        assert await r.prefetch([{"type": "account", "name": "BCBS"}]) == 0


def test_split_bar_is_lower_than_merge_bar():
    fuzzy = ResolvedEntity(id="person-dan-brown", canonical_name="Dan Brown", matched_via="fuzzy", score=0.95)
    opts = {"c1": {"id": "person-dan-brown", "name": "Dan Brown"}}
    # 0.66 "none" is enough to undo a similarity-only merge...
    assert apply_tiebreak(fuzzy, opts, "none", 0.66, "person", "Dana Brown").matched_via == "new"
    # ...but 0.66 for a candidate is not enough to create one.
    new = ResolvedEntity(id="person-dana-brown", canonical_name="Dana Brown", matched_via="new")
    assert apply_tiebreak(new, opts, "c1", 0.66, "person", "Dana Brown") is new
    # And a weak "none" leaves the heuristic alone.
    assert apply_tiebreak(fuzzy, opts, "none", 0.5, "person", "Dana Brown") is fuzzy
