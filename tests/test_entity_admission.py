"""Entity admission: deterministic placeholder prefilter + decision-model
(Jev) judgment on whether a NEW entity is a named thing and which type it is."""

from types import SimpleNamespace

import pytest

from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.entity_admission import apply_admission, judge_entities
from app.services.entity_resolver import build_tiebreak_state
from app.services.entity_utils import is_placeholder_entity_name
from app.services.inference.decisions import (
    ChoiceAnswer,
    DecisionResult,
    DecisionUnavailable,
    NoulAnswer,
)
from app.services.signal_store import drop_entity_refs

TYPES = {"person": "A human individual", "account": "An organisation", "team": "Internal group"}


class _FakeDecisions:
    """Scripted verdicts by mention name: name -> (p_named, type, p_type)."""

    def __init__(self, script, mode="on", fail=False):
        self.script, self._mode, self.fail = script, mode, fail
        self.calls: list[dict] = []

    def mode(self, operation):
        return self._mode

    async def decide(self, state, questions, *, operation):
        self.calls.append(state)
        if self.fail:
            raise DecisionUnavailable("down", status=503)
        p_named, etype, p_type = self.script[state["mention"]["name"]]
        label = next(k for k, v in questions["type"].criteria.items() if v.startswith(f"{etype}:"))
        # The direct type confirmation agrees with the scripted type pick.
        is_type = 0.05 if etype != state["mention"]["extracted_type"] else 0.95
        return DecisionResult(
            {
                "named": NoulAnswer(p_named),
                "type": ChoiceAnswer(choice=label, probabilities={label: p_type}, confidence=p_type),
                "is_extracted_type": NoulAnswer(is_type),
            },
            "jev", "fake", 0, 0, 0.0, 0, {},
        )


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Unnamed facilitator", True), ("Speaker 2", True), ("Participant B", True),
        ("someone", True), ("the recruiter", True), ("Others", True),
        # judgment calls are left to the model, never the stoplist
        ("Recruiter", False), ("Partners", False),
        ("The Home Depot", False), ("Dan", False), ("Anthropic", False),
    ],
)
def test_placeholder_prefilter_is_narrow(name, expected):
    assert is_placeholder_entity_name(name) is expected


def test_apply_admission_bars_are_asymmetric():
    opts = {"t1": "account", "t2": "person", "t3": "team"}
    assert apply_admission("person", 0.10, "t2", 0.9, opts).action == "drop"
    # 0.75 sure it's junk is not enough to drop a possibly-real entity
    assert apply_admission("person", 0.25, "t2", 0.9, opts).action == "keep"
    assert apply_admission("team", 0.95, "t1", 0.90, opts, p_is_extracted_type=0.05).new_type == "account"
    assert apply_admission("team", 0.95, "t1", 0.60, opts, p_is_extracted_type=0.05).action == "keep"
    # person-typed 'Anthropic': account at 0.80 with a clear "not a person" -> retype
    assert apply_admission("team", 0.95, "t1", 0.80, opts, p_is_extracted_type=0.11).new_type == "account"
    # Confident multi-way pick, but Jev also says it IS the extracted type:
    # the Atlas case (project named like a place) stays a project.
    assert apply_admission("project", 0.95, "t1", 0.90, opts, p_is_extracted_type=0.60).action == "keep"


@pytest.mark.asyncio
async def test_judge_drops_roles_and_retypes_companies():
    fake = _FakeDecisions({
        "Recruiter": (0.05, "person", 0.9),
        "Partners": (0.10, "person", 0.6),
        "Anthropic": (0.98, "account", 0.95),
        "Paul Evers": (0.99, "person", 0.99),
    })
    mentions = [
        {"type": "person", "name": "Recruiter"},
        {"type": "person", "name": "Partners"},
        {"type": "team", "name": "Anthropic", "evidence": "we're applying to Anthropic's partner program"},
        {"type": "person", "name": "Paul Evers"},
    ]
    verdicts = await judge_entities(mentions, TYPES, meeting={"title": "CCAF"}, client=fake)

    assert verdicts[("person", "Recruiter")].action == "drop"
    assert verdicts[("person", "Partners")].action == "drop"
    assert verdicts[("team", "Anthropic")].new_type == "account"
    assert ("person", "Paul Evers") not in verdicts  # keep = no verdict
    anthropic_state = next(c for c in fake.calls if c["mention"]["name"] == "Anthropic")
    assert anthropic_state["mention"]["evidence"].startswith("we're applying")
    assert anthropic_state["meeting"] == {"title": "CCAF"}


@pytest.mark.asyncio
async def test_shadow_mode_judges_but_changes_nothing():
    fake = _FakeDecisions({"Recruiter": (0.05, "person", 0.9)}, mode="shadow")
    assert await judge_entities([{"type": "person", "name": "Recruiter"}], TYPES, client=fake) == {}
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_unavailable_model_keeps_everything():
    fake = _FakeDecisions({}, fail=True)
    assert await judge_entities([{"type": "person", "name": "Recruiter"}], TYPES, client=fake) == {}


def test_drop_entity_refs_clears_owner_and_client():
    ms = MeetingSignals(meeting_id="m", bot_id="b", signals=[
        Signal(id="s1", type="action_item", content="Recruiter to send a write-up",
               source_meeting_id="b", source_timestamp="2026-09-21T11:55:00+00:00",
               entities=[EntityRef(id="person-recruiter", type="person", name="Recruiter"),
                         EntityRef(id="person-scott-jennings", type="person", name="Scott Jennings")],
               owner=EntityRef(id="person-recruiter", type="person", name="Recruiter"),
               client_id="person-recruiter"),
    ])
    assert drop_entity_refs(ms, {"person-recruiter"})
    sig = ms.signals[0]
    assert [r.id for r in sig.entities] == ["person-scott-jennings"]
    assert sig.owner is None and sig.client_id is None


# --- orchestrator integration -------------------------------------------------


class _Graph:
    def __init__(self, existing=()):
        self.nodes = {i: SimpleNamespace(id=i, name=n, type=t, metadata={}) for i, n, t in existing}
        self.entity_documents, self.document_entities = {}, {}
        self.added = []

    async def add_node(self, entity_type, name, entity_id=None, properties=None):
        self.added.append((entity_type, entity_id))
        self.nodes[entity_id] = SimpleNamespace(id=entity_id, name=name, type=entity_type, metadata={})

    async def create_semantic_relationship(self, **kw):
        return True


class _Writer:
    async def write_meeting_signals(self, ms):
        return len(ms.signals)


@pytest.mark.asyncio
async def test_enrich_graph_applies_admission_to_new_entities_only(monkeypatch):
    import app.services.entity_admission as adm
    import app.services.entity_resolver as er
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

    fake = _FakeDecisions({
        "Recruiter": (0.05, "person", 0.9),
        "Anthropic": (0.97, "account", 0.93),
    })
    monkeypatch.setattr(adm, "_default_client", lambda: fake)
    monkeypatch.setattr(er, "_default_decision_client", lambda: None)
    domain = SimpleNamespace(entities={t: SimpleNamespace(description=d) for t, d in TYPES.items()})
    monkeypatch.setattr(
        "app.core.domain_config.domain_config_service.get_domain_config_service",
        lambda: SimpleNamespace(get_active_domain=lambda: domain),
    )

    graph = _Graph(existing=[("person-scott-jennings", "Scott Jennings", "person")])
    orch = IngestOrchestrator(classifier=None, claude_client=None, graph=graph,
                              signal_writer=_Writer(), git_ops=None, tools={})
    orch._filter_to_domain_entities = lambda ents: ents  # domain filtering is not under test

    obs = SimpleNamespace(participants=["Scott Jennings"], title="Interview Process Discussion",
                          entities_mentioned={"person": ["Recruiter"], "team": ["Anthropic"]},
                          metadata={}, entity_ids=[])
    ms = MeetingSignals(meeting_id="m", bot_id="b", signals=[
        Signal(id="s1", type="action_item", content="Recruiter to send a write-up to Anthropic",
               source_meeting_id="b", source_timestamp="2026-09-21T11:55:00+00:00",
               entities=[EntityRef(id="team-anthropic", type="team", name="Anthropic")],
               owner=EntityRef(id="person-recruiter", type="person", name="Recruiter")),
    ])

    await orch._phase_enrich_graph(ms, "transcript", obs)

    # participant + existing node were never judged
    assert {c["mention"]["name"] for c in fake.calls} == {"Recruiter", "Anthropic"}
    assert ("person", "person-recruiter") not in graph.added
    assert ("account", "account-anthropic") in graph.added
    assert ("team", "team-anthropic") not in graph.added
    sig = ms.signals[0]
    assert sig.owner is None
    assert [(r.id, r.type) for r in sig.entities] == [("account-anthropic", "account")]
    assert obs.entity_ids == ["account-anthropic", "person-scott-jennings"]


def test_tiebreak_state_carries_meeting_and_co_mentions():
    state = build_tiebreak_state(
        {"name": "Ankit Patel", "type": "person",
         "meeting": {"title": "Call with Ankit Patel", "participants": ["Ankit Patel", "Scott Jennings"]}},
        {"c1": {"id": "person-ankit", "name": "Ankit",
                "context": {"co_mentioned_with": ["Scott Jennings", "Ann", "Rebecca"]}}},
    )
    assert state["heard_in_meeting"]["participants"] == ["Ankit Patel", "Scott Jennings"]
    assert state["candidates"]["c1"]["co_mentioned_with"] == ["Scott Jennings", "Ann", "Rebecca"]


def test_resolver_candidates_list_co_mentioned_entities():
    from app.services.entity_resolver import EntityResolver

    kg = SimpleNamespace(
        nodes={
            "person-ankit": SimpleNamespace(id="person-ankit", name="Ankit", type="person", metadata={}),
            "person-ann": SimpleNamespace(id="person-ann", name="Ann", type="person", metadata={}),
            "person-scott-jennings": SimpleNamespace(
                id="person-scott-jennings", name="Scott Jennings", type="person", metadata={}),
        },
        entity_documents={"person-ankit": {"doc:m1", "doc:m2"}},
        document_entities={"doc:m1": {"person-ankit", "person-ann", "person-scott-jennings"},
                           "doc:m2": {"person-ankit", "person-scott-jennings"}},
    )
    ankit = next(c for c in EntityResolver(kg, decisions=None)._candidates("person") if c["id"] == "person-ankit")
    assert ankit["context"]["co_mentioned_with"] == ["Scott Jennings", "Ann"]


def test_salient_prompt_renders_type_descriptions():
    from app.services.salient_entity_extractor import build_salient_extraction_prompt

    prompt = build_salient_extraction_prompt(
        "**Scott**: hello", ["account", "team"],
        type_descriptions={"account": "An external organization", "team": ""},
    )
    assert "- account: An external organization" in prompt
    assert "- team\n" in prompt or prompt.rstrip().endswith("- team")
    assert "never a team" in prompt


# --- iteration 2: one tiebreak per mention, co-mention noise, name upgrade ---


class _TiebreakFake:
    """Answers 'none' when the evidence quote is present, else merges."""

    def __init__(self):
        self.calls = []

    def mode(self, operation):
        return "on"

    async def decide(self, state, questions, *, operation):
        self.calls.append(state)
        label = "none" if state["mention"].get("evidence") else "c1"
        answer = ChoiceAnswer(choice=label, probabilities={label: 0.9}, confidence=0.9)
        return DecisionResult({"match": answer}, "jev", "fake", 0, 0, 0.0, 0, {})


@pytest.mark.asyncio
async def test_a_mention_is_judged_once_per_resolver():
    """The Brian case: the evidence-bearing first ask said none; a later
    thinner re-ask must not get to override it with a merge."""
    from app.services.entity_resolver import EntityResolver

    kg = SimpleNamespace(nodes={"person-brian": SimpleNamespace(
        id="person-brian", name="Brian", type="person", metadata={})})
    fake = _TiebreakFake()
    r = EntityResolver(kg, decisions=fake)
    await r.prefetch([{"type": "person", "name": "Brian Vigilani", "evidence": "Brian Vigilani from Foley"}])
    await r.prefetch([{"type": "person", "name": "Brian Vigilani"}])
    assert len(fake.calls) == 1
    assert r.resolve("person", "Brian Vigilani").id == "person-brian-vigilani"


def test_ubiquitous_entities_are_not_co_mention_evidence():
    from app.services.entity_resolver import EntityResolver

    names = {"person-brian": "Brian", "person-scott": "Scott Jennings", "person-ann": "Ann"}
    nodes = {i: SimpleNamespace(id=i, name=n, type="person", metadata={}) for i, n in names.items()}
    docs = {f"doc:{i}": {"person-scott"} for i in range(5)}
    docs["doc:0"] |= {"person-brian", "person-ann"}
    ent_docs = {}
    for d, ents in docs.items():
        for e in ents:
            ent_docs.setdefault(e, set()).add(d)
    kg = SimpleNamespace(nodes=nodes, entity_documents=ent_docs, document_entities=docs)
    brian = next(c for c in EntityResolver(kg, decisions=None)._candidates("person") if c["id"] == "person-brian")
    # Scott is in all 5 meetings -> not evidence; Ann shares a meeting -> is.
    assert brian["context"]["co_mentioned_with"] == ["Ann"]


def test_fuller_name_detection():
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator as IO

    assert IO._is_fuller_name("Ankit Patel", "Ankit", "person")
    assert not IO._is_fuller_name("Ankit", "Ankit Patel", "person")
    assert not IO._is_fuller_name("Brian Vigilani", "Bryan", "person")
    assert not IO._is_fuller_name("Dan Kauppi", "Dan Kauppi", "person")


@pytest.mark.asyncio
async def test_resolving_a_fuller_name_upgrades_the_entity(monkeypatch):
    import app.services.entity_resolver as er
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

    monkeypatch.setattr(er, "_default_decision_client", lambda: None)
    graph = _Graph(existing=[("person-ankit", "Ankit", "person")])
    graph.nodes["person-ankit"].metadata = {"aliases": ["Ankit Patel"]}  # alias -> resolves
    upgrades = []

    async def upgrade(eid, name):
        upgrades.append((eid, name))
        return True

    graph.upgrade_entity_name = upgrade
    orch = IngestOrchestrator.__new__(IngestOrchestrator)
    orch._graph = graph
    resolved, id_map = await orch._resolve_collected_entities(
        [{"id": "person-ankit-patel", "name": "Ankit Patel", "type": "person"}]
    )
    assert upgrades == [("person-ankit", "Ankit Patel")]
    assert resolved == [{"id": "person-ankit", "name": "Ankit Patel", "type": "person",
                         "surface": "Ankit Patel"}]
    assert id_map == {"person-ankit-patel": "person-ankit"}
