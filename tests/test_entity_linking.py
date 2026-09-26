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
    q_org, _ = build_link_questions({"type": "account", "name": "Anthropic"}, None, forms)
    q_person, opts = build_link_questions({"type": "person", "name": "Brian"}, None, ["Brian", "Brian Vigilani"])
    assert "name" not in q_org
    assert "name" in q_person and set(opts.values()) == {"Brian", "Brian Vigilani"}
