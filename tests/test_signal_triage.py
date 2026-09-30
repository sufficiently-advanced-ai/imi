"""Tests for the signal_promotion_triage decision operation."""

from datetime import UTC, datetime

import pytest

from app.models.observation import Observation
from app.models.signal import EntityRef, Signal
from app.services import signal_triage
from app.services.inference.decisions import ChoiceAnswer, DecisionResult, DecisionUnavailable
from app.services.signal_promoter import SignalPromoter
from app.services.signal_triage import (
    FIRM_MIN_PROBABILITY,
    apply_verdict,
    build_questions,
    triage_signals,
)

SARAH = EntityRef(id="person-sarah-chen", type="person", name="Sarah Chen")
SARAH_K = EntityRef(id="person-sarah-kim", type="person", name="Sarah Kim")
ACME = EntityRef(id="account-acme", type="account", name="Acme")
GLOBEX = EntityRef(id="account-globex", type="account", name="Globex")
REFS = [SARAH, SARAH_K, ACME, GLOBEX]


def _obs(**kw):
    base = dict(
        observation_id="ingest-obs1", external_id="ingest-bot1",
        observed_at=datetime(2026, 6, 4, 15, 0, tzinfo=UTC), title="Planning call",
        participants=["Sarah Chen", "Sarah Kim"], entities_mentioned={"person": ["Sarah Chen", "Sarah Kim"]},
        content="Sarah Kim will draft the Acme rollout plan.",
    )
    return Observation(**{**base, **kw})


def _sig(sid, type_, content="Something substantive happened", **kw):
    return Signal(id=sid, type=type_, content=content, source_meeting_id="ingest-bot1",
                  source_timestamp="2026-06-04T15:00:00+00:00", **kw)


def _resolve(name):
    return next(r for r in REFS if r.name == name)


def _choice(probs):
    choice = max(probs, key=probs.get)
    return ChoiceAnswer(choice, probs, probs[choice])


class _Fake:
    """Scripted Jev: answers each question from ``script[suffix]`` (e.g.
    ``type``/``firmness``/``owner``/``client``), keyed by option name."""

    def __init__(self, script, mode="on", fail=False):
        self.script, self._mode, self.fail, self.calls = script, mode, fail, []

    def mode(self, operation):
        return self._mode

    async def decide(self, state, questions, *, operation):
        assert operation == signal_triage.TRIAGE_OPERATION
        self.calls.append((state, questions))
        if self.fail:
            raise DecisionUnavailable("upstream 500")
        answers = {}
        for name, q in questions.items():
            # unscripted questions answer "none", or split evenly
            opts = list(q.criteria)
            by_label = self.script.get(name) or (
                {"none": 1.0} if "none" in opts else {o: 1 / len(opts) for o in opts})
            # map option labels (names) back to option ids
            label_to_id = {v: k for k, v in q.criteria.items()}
            probs = {label_to_id.get(k, k): v for k, v in by_label.items()}
            answers[name] = _choice(probs)
        return DecisionResult(answers, "jev", "fake", 0, 0, 0.0, 0, {})


def test_questions_offer_only_the_meetings_people_and_clients():
    q = build_questions(["Sarah Chen"], [ACME])
    assert set(q) == {"type", "firmness", "owner", "client"}
    assert q["owner"].criteria == {"p0": "Sarah Chen", "none": "No listed person owns it."}
    assert q["client"].criteria == {"c0": "Acme", "none": "It concerns no listed client."}
    assert set(build_questions([], [])) == {"type", "firmness"}


@pytest.mark.asyncio
async def test_shadow_records_verdicts_and_changes_nothing():
    owner_heuristic = SARAH  # first-name match picked the wrong Sarah
    item = _sig("a", "action_item", owner=owner_heuristic, status="open", client_id="account-globex")
    fake = _Fake({"type": {"action_item": 0.95, "none": 0.05},
                  "owner": {"Sarah Kim": 0.9, "none": 0.1},
                  "client": {"Acme": 0.92, "none": 0.08}}, mode="shadow")
    kept = await triage_signals([item], _obs(), REFS, {"account"}, _resolve, client=fake)
    assert kept == [item]
    assert item.owner == SARAH and item.client_id == "account-globex"
    t = item.metadata["triage"]
    assert t["mode"] == "shadow"
    assert t["heuristic"]["owner"] == "person-sarah-chen"
    assert t["owner"]["name"] == "Sarah Kim"
    assert t["client"]["client_id"] == "account-acme"
    assert "applied" not in t


@pytest.mark.asyncio
async def test_on_mode_reassigns_owner_and_client():
    item = _sig("a", "action_item", owner=SARAH, status="open", client_id="account-globex")
    fake = _Fake({"type": {"action_item": 0.95, "none": 0.05},
                  "owner": {"Sarah Kim": 0.9, "none": 0.1},
                  "client": {"Acme": 0.92, "none": 0.08}})
    await triage_signals([item], _obs(), REFS, {"account"}, _resolve, client=fake)
    assert item.owner == SARAH_K
    assert item.client_id == "account-acme"
    assert item.metadata["triage"]["applied"] == ["owner", "client"]


def test_firmness_bars_are_asymmetric():
    firm_weak = _sig("a", "decision", metadata={"tier": "candidate"})
    apply_verdict(firm_weak, {"firmness": {"choice": "firm", "probabilities": {"firm": FIRM_MIN_PROBABILITY - 0.01,
                                                                                "proposed": 0.16}}}, "on", _resolve)
    assert firm_weak.metadata["tier"] == "candidate"  # not confident enough to promote

    firm_strong = _sig("b", "decision", metadata={"tier": "candidate"})
    apply_verdict(firm_strong, {"firmness": {"choice": "firm", "probabilities": {"firm": 0.97, "proposed": 0.03}}},
                  "on", _resolve)
    assert "tier" not in firm_strong.metadata

    proposed = _sig("c", "decision")
    apply_verdict(proposed, {"firmness": {"choice": "proposed", "probabilities": {"firm": 0.07, "proposed": 0.93}}},
                  "on", _resolve)
    assert proposed.metadata["tier"] == "candidate"

    fact = _sig("d", "decision")  # "the budget is $40k": a fact, drawn toward proposed
    apply_verdict(fact, {"firmness": {"choice": "proposed", "probabilities": {"firm": 0.2, "proposed": 0.8}}},
                  "on", _resolve)
    assert "tier" not in fact.metadata


def test_on_mode_retypes_and_drops_only_when_confident():
    retyped = _sig("a", "decision", metadata={"tier": "candidate"})
    assert apply_verdict(retyped, {"type": {"choice": "action_item",
                                            "probabilities": {"action_item": 0.9, "decision": 0.1}}}, "on", _resolve)
    assert retyped.type == "action_item" and retyped.status == "open" and "tier" not in retyped.metadata

    unsure = _sig("b", "key_point")
    assert apply_verdict(unsure, {"type": {"choice": "none", "probabilities": {"none": 0.6, "key_point": 0.4}}},
                         "on", _resolve)
    assert not apply_verdict(_sig("c", "key_point"),
                             {"type": {"choice": "none", "probabilities": {"none": 0.9, "key_point": 0.1}}},
                             "on", _resolve)


def test_reviewed_signals_are_annotated_not_redecided():
    reviewed = _sig("a", "key_point", review_status="confirmed")
    assert apply_verdict(reviewed, {"type": {"choice": "none", "probabilities": {"none": 0.99}}}, "on", _resolve)
    assert reviewed.type == "key_point"
    assert reviewed.metadata["triage"]["type"]["choice"] == "none"


@pytest.mark.asyncio
async def test_failure_leaves_heuristics_untouched():
    item = _sig("a", "action_item", owner=SARAH, status="open")
    kept = await triage_signals([item], _obs(), REFS, {"account"}, _resolve, client=_Fake({}, fail=True))
    assert kept == [item] and item.owner == SARAH and "triage" not in item.metadata


@pytest.mark.asyncio
async def test_one_call_per_signal_with_only_that_signal_in_state():
    sigs = [_sig(f"s{i}", "insight", content=f"Insight number {i} about the plan") for i in range(3)]
    fake = _Fake({"type": {"insight": 0.9, "none": 0.1}, "firmness": {"firm": 0.5, "proposed": 0.5},
                  "owner": {"none": 0.9, "Sarah Chen": 0.1}, "client": {"none": 0.9, "Acme": 0.1}}, mode="shadow")
    kept = await triage_signals(sigs, _obs(), REFS, {"account"}, _resolve, client=fake)
    assert len(kept) == 3 and len(fake.calls) == 3
    assert sorted(state["signal"]["content"] for state, _ in fake.calls) == [s.content for s in sigs]
    assert all("triage" in s.metadata for s in sigs)


@pytest.mark.asyncio
async def test_promoter_skips_library_signals(monkeypatch):
    fake = _Fake({"type": {"key_point": 0.9, "none": 0.1}}, mode="shadow")
    monkeypatch.setattr(signal_triage, "_default_client", lambda: fake)
    obs = _obs(content="## Key Points\n- The market for widgets grew 12% last year\n")
    promoter = SignalPromoter(claude_client=None, knowledge_graph=None)

    library = await promoter.promote(obs.model_copy(update={"lane": "library"}))
    assert library is not None and not fake.calls

    record = await promoter.promote(obs)
    assert record is not None and fake.calls
    assert all("triage" in s.metadata for s in record.signals)


def test_long_meetings_send_the_segments_that_match_the_signal():
    filler = "We talked about the weather and lunch plans. " * 80  # ~3.6k chars
    body = (filler * 10) + "Omar agreed to configure the Initech SSO integration. " + (filler * 10)
    assert len(body) > signal_triage.MAX_BODY_CHARS
    sig = _sig("a", "action_item", content="Configure Initech SSO integration",
               entities=[EntityRef(id="account-initech", type="account", name="Initech")])
    text = signal_triage.meeting_text(body, sig)
    assert "configure the Initech SSO integration" in text
    assert len(text) <= signal_triage.SEGMENT_CHARS * signal_triage.MAX_SEGMENTS + 200
    short = "Omar will configure SSO."
    assert signal_triage.meeting_text(short, sig) == short


def test_owner_options_keep_named_heuristic_owners_but_not_minted_phrases():
    named = _sig("a", "action_item", owner=EntityRef(id="person-sam", type="person", name="Sam"))
    minted = _sig("b", "action_item", owner=EntityRef(id="person-initech-it-team", type="person",
                                                      name="Initech IT team"))
    body = "Sam is building the ad app. Someone from Initech's IT team will send the list."
    people = signal_triage._person_options([SARAH], ["Scott J"], [named, minted], body)
    assert people == ["Scott J", "Sarah Chen", "Sam"]


def test_clearing_an_owner_needs_more_confidence_than_assigning_one():
    def owner_verdict(choice, name, p):
        return {"owner": {"choice": choice, "name": name, "probabilities": {choice: p}}}

    kept = _sig("a", "action_item", owner=SARAH, status="open")
    apply_verdict(kept, owner_verdict("none", None, 0.79), "on", _resolve)
    assert kept.owner == SARAH

    cleared = _sig("b", "action_item", owner=SARAH, status="open")
    apply_verdict(cleared, owner_verdict("none", None, 0.95), "on", _resolve)
    assert cleared.owner is None

    moved = _sig("c", "action_item", owner=SARAH, status="open")
    apply_verdict(moved, owner_verdict("p1", "Sarah Kim", 0.8), "on", _resolve)
    assert moved.owner == SARAH_K
