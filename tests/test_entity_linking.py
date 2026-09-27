"""entity_link: Jev verifies every (meeting, entity) link — mentioned? the
existing entity? a fuller name? Code gathers evidence; Jev decides."""

from types import SimpleNamespace

import pytest

from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.entity_linking import (
    apply_link,
    judge_links,
    surface_forms,
    transcript_windows,
)
from app.services.inference.decisions import ChoiceAnswer, DecisionResult, NoulAnswer

TRANSCRIPT = (
    "Dan Kauppi: So I'm talking with Brian. Um Brian Vigilani from Poley. We pitched them on "
    "using AI to help reps plan their day.\nScott Jennings: Makes sense. And Farmerica is "
    "another great example of a customer."
)


def test_windows_and_forms_are_context_only():
    windows = transcript_windows(TRANSCRIPT, ["Brian"])
    assert windows and all("Brian" in w for w in windows)
    assert transcript_windows(TRANSCRIPT, ["Aditya"]) == []
    # speech-to-text variant is found through an alias, not decided here
    assert transcript_windows(TRANSCRIPT, ["Pharmerica", "Farmerica"])
    assert surface_forms(TRANSCRIPT, "Brian") == ["Brian", "Brian Vigilani"]
    assert surface_forms(TRANSCRIPT, "Dan") == ["Dan Kauppi"]


def test_apply_link_bars():
    opts = {"n1": "Brian", "n2": "Brian Vigilani"}
    assert apply_link("Aditya", 0.10, 0.9, None, 0.0, {}).action == "unlink"
    assert apply_link("Aditya", 0.30, 0.9, None, 0.0, {}).action == "keep"  # 0.70 < 0.75 bar
    split = apply_link("Brian", 0.95, 0.10, "n2", 0.90, opts)
    assert (split.action, split.name) == ("split", "Brian Vigilani")
    # different, but no fuller name to become: unlink rather than misattribute
    assert apply_link("Brian", 0.95, 0.10, None, 0.0, {}).action == "unlink"
    rename = apply_link("Brian", 0.95, 0.95, "n2", 0.90, opts)
    assert (rename.action, rename.name) == ("rename", "Brian Vigilani")
    assert apply_link("Brian", 0.95, 0.95, "n2", 0.50, opts).action == "keep"


def test_existing_person_must_be_the_clear_answer_to_keep_a_link():
    # 'AD' in a Foley call: Jev split between Aditya, Anudeep and someone else
    assert apply_link("Aditya", 0.91, 0.31, None, 0.0, {}).action == "unlink"
    # another company's CEO 'Dave' vs Dave Link, Euler's CTO
    assert apply_link("Dave Link", 0.61, 0.52, None, 0.0, {}).action == "unlink"
    # modest but clear identity answers keep
    assert apply_link("Barry Goldberg", 0.50, 0.59, None, 0.0, {}).action == "keep"


class _Fake:
    """Scripted Jev: name -> (p_mentioned, p_same_existing, preferred form)."""

    def __init__(self, script, mode="on"):
        self.script, self._mode, self.calls = script, mode, []

    def mode(self, operation):
        return self._mode

    async def decide(self, state, questions, *, operation):
        self.calls.append((state, questions))
        p_mentioned, p_same, form = self.script[state["mention"]["name"]]
        answers = {"mentioned": NoulAnswer(p_mentioned)}
        for q in questions:
            if q.startswith("compat_"):
                answers[q] = NoulAnswer(0.9)
        if "same" in questions:
            answers["same"] = ChoiceAnswer(
                choice="existing" if p_same >= 0.5 else "different",
                probabilities={"existing": p_same, "different": 1 - p_same}, confidence=0.9)
        if "name" in questions:
            key = next(k for k, v in questions["name"].criteria.items() if v == form)
            answers["name"] = ChoiceAnswer(choice=key, probabilities={key: 0.92}, confidence=0.92)
        return DecisionResult(answers, "jev", "fake", 0, 0, 0.0, 0, {})


@pytest.mark.asyncio
async def test_judge_links_brian_split_aditya_unlink_pharmerica_keep():
    fake = _Fake({
        "Brian": (0.97, 0.08, "Brian Vigilani"),
        "Aditya": (0.05, 0.9, None),
        "Pharmerica": (0.93, 0.95, None),
    })
    links = [
        {"id": "person-brian", "type": "person", "name": "Brian",
         "candidate": {"name": "Brian", "context": {"title": "Consultant / Entrepreneur"}}},
        {"id": "person-aditya", "type": "person", "name": "Aditya",
         "candidate": {"name": "Aditya", "context": {}}},
        {"id": "account-pharmerica", "type": "account", "name": "Pharmerica",
         "names": ["Farmerica"], "candidate": {"name": "Pharmerica", "context": {}}},
    ]
    verdicts = await judge_links(links, TRANSCRIPT, meeting={"title": "Dan / Scott"}, client=fake)

    assert verdicts["person-brian"].action == "split"
    assert verdicts["person-brian"].name == "Brian Vigilani"
    assert verdicts["person-aditya"].action == "unlink"
    assert "account-pharmerica" not in verdicts
    aditya_state = next(s for s, _ in fake.calls if s["mention"]["name"] == "Aditya")
    assert aditya_state["transcript_excerpts"] == ["(no occurrence of the name found in the transcript)"]
    brian_q = next(q for s, q in fake.calls if s["mention"]["name"] == "Brian")
    assert "Consultant / Entrepreneur" in brian_q["same"].criteria["existing"]


@pytest.mark.asyncio
async def test_shadow_mode_logs_but_changes_nothing():
    fake = _Fake({"Aditya": (0.05, 0.9, None)}, mode="shadow")
    links = [{"id": "person-aditya", "type": "person", "name": "Aditya", "candidate": {"name": "Aditya"}}]
    assert await judge_links(links, TRANSCRIPT, client=fake) == {}
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_enrich_graph_applies_link_verdicts(monkeypatch):
    import app.services.entity_admission as adm
    import app.services.entity_linking as lnk
    import app.services.entity_resolver as er
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

    fake = _Fake({"Brian": (0.97, 0.08, "Brian Vigilani"), "Aditya": (0.05, 0.9, None)})
    monkeypatch.setattr(lnk, "_default_client", lambda: fake)
    monkeypatch.setattr(adm, "_default_client", lambda: None)
    monkeypatch.setattr(er, "_default_decision_client", lambda: None)

    class _Graph:
        def __init__(self):
            self.nodes = {
                i: SimpleNamespace(id=i, name=n, type="person", metadata={})
                for i, n in [("person-brian", "Brian"), ("person-aditya", "Aditya"),
                             ("person-dan-kauppi", "Dan Kauppi")]
            }
            self.entity_documents, self.document_entities, self.added = {}, {}, []

        async def add_node(self, entity_type, name, entity_id=None, properties=None):
            self.added.append(entity_id)
            self.nodes.setdefault(entity_id, SimpleNamespace(id=entity_id, name=name, type=entity_type, metadata={}))

        async def create_semantic_relationship(self, **kw):
            return True

    class _Writer:
        async def write_meeting_signals(self, ms):
            return len(ms.signals)

    graph = _Graph()
    orch = IngestOrchestrator(classifier=None, claude_client=None, graph=graph,
                              signal_writer=_Writer(), git_ops=None, tools={})
    orch._filter_to_domain_entities = lambda ents: ents
    obs = SimpleNamespace(participants=["Dan Kauppi", "Scott Jennings"], title="Dan / Scott",
                          raw_content=TRANSCRIPT, content=TRANSCRIPT, entity_ids=[],
                          entities_mentioned={"person": ["Brian", "Aditya"]}, metadata={},
                          external_id="ingest-x")
    ms = MeetingSignals(meeting_id="m", bot_id="ingest-x", signals=[
        Signal(id="s1", type="action_item", content="Show Brian the quoting app demo",
               source_meeting_id="ingest-x", source_timestamp="2026-09-18T11:30:00+00:00",
               entities=[EntityRef(id="person-brian", type="person", name="Brian"),
                         EntityRef(id="person-aditya", type="person", name="Aditya")]),
    ])

    await orch._phase_enrich_graph(ms, TRANSCRIPT, obs)

    assert "person-brian" not in graph.added and "person-aditya" not in graph.added
    assert "person-brian-vigilani" in graph.added
    assert [(r.id, r.name) for r in ms.signals[0].entities] == [("person-brian-vigilani", "Brian Vigilani")]
    assert "person-brian-vigilani" in obs.entity_ids and "person-aditya" not in obs.entity_ids
    assert obs.entities_mentioned["person"] == ["Brian"]  # Aditya removed; Brian's id now split


def test_name_question_is_only_asked_for_people():
    from app.services.entity_linking import build_link_questions

    forms = ["Anthropic", "Anthropic Academy"]
    q_org, _, _, _ = build_link_questions({"type": "account", "name": "Anthropic"}, None, forms)
    q_person, opts, _, _ = build_link_questions({"type": "person", "name": "Brian"}, None, ["Brian", "Brian Vigilani"])
    assert "name" not in q_org
    assert "name" in q_person and set(opts.values()) == {"Brian", "Brian Vigilani"}


def test_identity_question_is_only_asked_for_people():
    from app.services.entity_linking import build_link_questions

    q_org, _, _, _ = build_link_questions({"type": "account", "name": "Anthropic"},
                                       {"name": "Anthropic", "context": {}}, [])
    q_person, _, _, _ = build_link_questions({"type": "person", "name": "Brian"},
                                          {"name": "Brian", "context": {}}, [])
    assert set(q_org) == {"mentioned"}
    assert set(q_person) == {"mentioned", "same"}


def test_participants_are_offered_as_identity_answers():
    from app.services.entity_linking import build_link_questions

    q, _, parts, _ = build_link_questions(
        {"type": "person", "name": "Aditya"}, {"name": "Aditya", "context": {}}, [],
        ["Scott Jennings", "Paul Evers", "Anudeep", "Aditya"],
    )
    assert set(parts.values()) == {"Scott Jennings", "Paul Evers", "Anudeep"}
    assert set(q["same"].criteria) == {"existing", *parts, "different"}


def test_apply_link_reassigns_to_participant():
    v = apply_link("Aditya", 0.91, 0.20, None, 0.0, {}, participant="Anudeep", participant_probability=0.82)
    assert (v.action, v.name) == ("reassign", "Anudeep")
    # weak participant pick falls back to the existing-vs-different logic
    assert apply_link("Aditya", 0.91, 0.65, None, 0.0, {}, "Anudeep", 0.30).action == "keep"


@pytest.mark.asyncio
async def test_enrich_graph_moves_initials_mention_onto_participant(monkeypatch):
    """Foley Check In: 'ask AD some questions' was extracted as the cohort's
    Aditya; Jev says it is participant Anudeep, so the link moves to him."""
    import app.services.entity_admission as adm
    import app.services.entity_linking as lnk
    import app.services.entity_resolver as er
    from app.services.inference.decisions import ChoiceAnswer, DecisionResult, NoulAnswer
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

    class _PartFake:
        def mode(self, operation):
            return "on"

        async def decide(self, state, questions, *, operation):
            answers = {"mentioned": NoulAnswer(0.91)}
            for q in questions:
                if q.startswith("compat_"):
                    answers[q] = NoulAnswer(0.8)
            if "same" in questions:
                key = next(k for k, v in questions["same"].criteria.items() if v.startswith("Anudeep"))
                answers["same"] = ChoiceAnswer(choice=key, probabilities={key: 0.84, "existing": 0.1}, confidence=0.84)
            return DecisionResult(answers, "jev", "fake", 0, 0, 0.0, 0, {})

    monkeypatch.setattr(lnk, "_default_client", lambda: _PartFake())
    monkeypatch.setattr(adm, "_default_client", lambda: None)
    monkeypatch.setattr(er, "_default_decision_client", lambda: None)

    class _Graph:
        def __init__(self):
            self.nodes = {i: SimpleNamespace(id=i, name=n, type="person", metadata={})
                          for i, n in [("person-aditya", "Aditya"), ("person-anudeep", "Anudeep")]}
            self.entity_documents, self.document_entities, self.added = {}, {}, []

        async def add_node(self, entity_type, name, entity_id=None, properties=None):
            self.added.append(entity_id)

        async def create_semantic_relationship(self, **kw):
            return True

    class _Writer:
        async def write_meeting_signals(self, ms):
            return len(ms.signals)

    graph = _Graph()
    orch = IngestOrchestrator(classifier=None, claude_client=None, graph=graph,
                              signal_writer=_Writer(), git_ops=None, tools={})
    orch._filter_to_domain_entities = lambda ents: ents
    text = "Paul Evers: we mostly want to ask AD some questions about the quote rules."
    obs = SimpleNamespace(participants=["Paul Evers", "Anudeep"], title="Foley Quoting Check In",
                          raw_content=text, content=text, entity_ids=[],
                          entities_mentioned={"person": ["Aditya"]}, metadata={}, external_id="ingest-y")
    ms = MeetingSignals(meeting_id="m", bot_id="ingest-y", signals=[
        Signal(id="s1", type="action_item", content="Ask AD about the quote rules",
               source_meeting_id="ingest-y", source_timestamp="2026-09-24T16:00:00+00:00",
               entities=[EntityRef(id="person-aditya", type="person", name="Aditya")]),
    ])

    await orch._phase_enrich_graph(ms, text, obs)

    assert "person-aditya" not in obs.entity_ids and "person-anudeep" in obs.entity_ids
    assert [(r.id, r.name) for r in ms.signals[0].entities] == [("person-anudeep", "Anudeep")]


@pytest.mark.asyncio
async def test_heard_form_reaches_link_verification():
    """F&G merged into Faulkner Media Group must be checked against the
    transcript for 'F and G', not for the canonical name (which never occurs)."""
    seen = {}

    class _Fake:
        def mode(self, operation):
            return "on"

        async def decide(self, state, questions, *, operation):
            seen.update(state)
            return DecisionResult({"mentioned": NoulAnswer(0.9)}, "jev", "fake", 0, 0, 0.0, 0, {})

    text = "Scott: Share a little bit about F and G, what we're doing."
    link = {"id": "account-faulkner-media-group", "type": "account",
            "name": "Faulkner Media Group", "names": ["F and G"], "heard_as": "F and G"}
    await judge_links([link], text, client=_Fake())
    assert seen["mention"]["heard_as"] == "F and G"
    assert any("F and G" in w for w in seen["transcript_excerpts"])



@pytest.mark.asyncio
async def test_participant_reassignment_needs_compatible_names():
    """Wendy (an unlisted speaker) must not be moved onto Scott Jennings, the
    only listed participant, even if the identity pick favours him."""
    class _Fake:
        def mode(self, operation):
            return "on"

        async def decide(self, state, questions, *, operation):
            key = next(k for k, v in questions["same"].criteria.items() if v.startswith("Scott Jennings"))
            return DecisionResult({
                "mentioned": NoulAnswer(0.88),
                "same": ChoiceAnswer(choice=key, probabilities={key: 0.74}, confidence=0.74),
                f"compat_{key}": NoulAnswer(0.03),
            }, "jev", "fake", 0, 0, 0.0, 0, {})

    link = {"id": "person-wendy", "type": "person", "name": "Wendy"}
    verdicts = await judge_links([link], "Wendy, you're in the room now.",
                                 meeting={"participants": ["Scott Jennings"]}, client=_Fake())
    assert verdicts == {}  # kept, not reassigned


@pytest.mark.asyncio
async def test_namesake_in_kb_can_take_the_link():
    """'Brian' resolved to the cohort Brian; the Foley context points to the
    existing Brian Vigilani, offered as a namesake option."""
    class _Fake:
        def mode(self, operation):
            return "on"

        async def decide(self, state, questions, *, operation):
            key = next(k for k, v in questions["same"].criteria.items() if v.startswith("Brian Vigilani"))
            return DecisionResult({
                "mentioned": NoulAnswer(0.95),
                "same": ChoiceAnswer(choice=key, probabilities={key: 0.86, "existing": 0.1}, confidence=0.86),
            }, "jev", "fake", 0, 0, 0.0, 0, {})

    link = {"id": "person-brian", "type": "person", "name": "Brian",
            "candidate": {"id": "person-brian", "name": "Brian", "context": {"title": "Consultant"}},
            "namesakes": [{"id": "person-brian-vigilani", "name": "Brian Vigilani",
                           "context": {"company": "Foley"}}]}
    verdicts = await judge_links([link], "present it as a product to Brian at Foley", client=_Fake())
    v = verdicts["person-brian"]
    assert (v.action, v.target_id, v.name) == ("reassign", "person-brian-vigilani", "Brian Vigilani")


def test_windows_match_an_ampersand_spelled_out():
    # "F&G" is heard as "F and G"; without an excerpt the judge unlinked a correct match
    transcript = "Share a little bit about F and G, what we're doing, what this role means."
    assert transcript_windows(transcript, ["Faulkner Media Group", "F&G"])
    assert transcript_windows("we use FNG for that", ["F&G"])  # "n" contraction too


def test_mentioned_question_asks_about_the_heard_form():
    from app.services.entity_linking import build_link_questions

    mention = {"name": "Faulkner Media Group", "type": "account", "heard_as": "F&G"}
    questions, *_ = build_link_questions(mention, {"name": "Faulkner Media Group"}, [])
    assert "'F&G'" in questions["mentioned"].instructions
    plain, *_ = build_link_questions({"name": "Foley", "type": "account"}, None, [])
    assert "heard as '" not in plain["mentioned"].instructions
