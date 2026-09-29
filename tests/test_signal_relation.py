"""Tests for the signal_relation decision operation (supersession gate)."""

from unittest.mock import MagicMock

import pytest

from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services import signal_relation
from app.services.inference.decisions import ChoiceAnswer, DecisionResult, DecisionUnavailable
from app.services.signal_relation import (
    SUPERSEDE_MIN_PROBABILITY,
    apply_relation,
    build_relation_state,
    judge_candidates,
)


def _entity(eid, name):
    return EntityRef(id=eid, type=eid.split("-")[0], name=name)


def _decision(sid, content, meeting, ts, entities):
    return Signal(
        id=sid, type="decision", content=content, source_meeting_id=meeting,
        source_meeting_title=f"Meeting {meeting}", source_timestamp=ts, entities=entities,
    )


ACME = _entity("account-acme", "Acme")
WAREHOUSE = _entity("account-datavault", "DataVault")

OLD = _decision("old-1", "Build the quoting prototype on DataVault with a web front-end.",
                "m-old", "2026-03-01T10:00:00Z", [ACME, WAREHOUSE])
NEW = _decision("new-1", "Remove the admin role from the new service account.",
                "m-new", "2026-03-08T10:00:00Z", [ACME, WAREHOUSE])


def _candidate(old_id="old-1", status="pending", confidence=1.0):
    return {"old_signal_id": old_id, "old_content": "...", "matched_entities": ["account-acme", "account-datavault"],
            "reason": "Shared entities: Acme, DataVault", "confidence": confidence,
            "status": status, "proposed_at": "2026-03-08T10:00:00Z"}


class _Fake:
    """Scripted Jev: old signal id -> {relation: probability}."""

    def __init__(self, script, mode="on", fail=()):
        self.script, self._mode, self.fail, self.calls = script, mode, set(fail), []

    def mode(self, operation):
        return self._mode

    async def decide(self, state, questions, *, operation):
        assert operation == signal_relation.RELATION_OPERATION
        self.calls.append(state)
        old_text = state["old"]["decision"]
        if old_text in self.fail:
            raise DecisionUnavailable("upstream 500")
        probs = self.script[old_text]
        choice = max(probs, key=probs.get)
        return DecisionResult({"relation": ChoiceAnswer(choice, probs, probs[choice])},
                              "jev", "fake", 0, 0, 0.0, 0, {})


def test_apply_relation_on_mode_keeps_only_confident_supersedes():
    kept = apply_relation(_candidate(confidence=0.33), "supersedes", {"supersedes": 0.81, "refines": 0.19}, "on")
    assert kept["status"] == "pending"
    assert kept["confidence"] == 0.81
    assert kept["entity_overlap"] == 0.33

    weak = apply_relation(_candidate(), "supersedes",
                          {"supersedes": SUPERSEDE_MIN_PROBABILITY - 0.01, "refines": 0.41}, "on")
    assert weak["status"] == "dismissed"
    assert weak["dismissed_by"] == "decision_model"

    refines = apply_relation(_candidate(confidence=1.0), "refines", {"refines": 0.9, "supersedes": 0.05}, "on")
    assert refines["status"] == "dismissed"
    assert refines["relation"] == "refines"
    assert refines["confidence"] == 0.05  # entity overlap no longer reads as "100% confidence"


def test_apply_relation_shadow_annotates_without_changing_the_queue():
    out = apply_relation(_candidate(confidence=1.0), "unrelated", {"unrelated": 0.92, "supersedes": 0.02}, "shadow")
    assert out["status"] == "pending"
    assert out["confidence"] == 1.0
    assert out["relation"] == "unrelated"
    assert out["relation_probability"] == 0.92
    assert "dismissed_by" not in out


def test_apply_relation_never_redecides_reviewed_candidates():
    out = apply_relation(_candidate(status="confirmed"), "unrelated", {"unrelated": 0.95}, "on")
    assert out["status"] == "confirmed"
    assert out["relation"] == "unrelated"


def test_state_orders_old_then_new_with_dates_and_shared_names():
    state = build_relation_state(NEW, OLD, ["Acme", "DataVault"])
    assert state["old"]["decision"] == OLD.content
    assert state["new"]["date"] == "2026-03-08"
    assert state["shared_entities"] == ["Acme", "DataVault"]


@pytest.mark.asyncio
async def test_judge_candidates_gates_and_tolerates_failures():
    other = _decision("old-2", "Ship the pilot to two reps before expanding.", "m-old", "2026-03-01T10:00:00Z", [ACME])
    broken = _decision("old-3", "Use the hosted vector store.", "m-old", "2026-03-01T10:00:00Z", [ACME])
    fake = _Fake({OLD.content: {"unrelated": 0.9, "supersedes": 0.05, "refines": 0.05},
                  other.content: {"supersedes": 0.85, "refines": 0.15}},
                 fail={broken.content})
    cands = [_candidate("old-1"), _candidate("old-2"), _candidate("old-3"), _candidate("missing")]
    by_id = {s.id: s for s in (OLD, other, broken)}

    out = await judge_candidates(NEW, cands, by_id, client=fake)

    assert [c["old_signal_id"] for c in out] == ["old-1", "old-2", "old-3", "missing"]
    assert out[0]["status"] == "dismissed" and out[0]["relation"] == "unrelated"
    assert out[1]["status"] == "pending" and out[1]["confidence"] == 0.85
    assert out[2] == cands[2]  # judgment failed: untouched
    assert out[3] == cands[3]  # old signal unknown: untouched, no call
    assert len(fake.calls) == 3
    assert fake.calls[0]["shared_entities"] == ["Acme", "DataVault"]


@pytest.mark.asyncio
async def test_judge_candidates_off_mode_makes_no_calls():
    fake = _Fake({}, mode="off")
    cands = [_candidate()]
    assert await judge_candidates(NEW, cands, {"old-1": OLD}, client=fake) is cands
    assert fake.calls == []


@pytest.mark.asyncio
async def test_detect_supersession_phase_counts_only_pending(tmp_path, monkeypatch):
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator
    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path / "signals")
    store.save(MeetingSignals(meeting_id="m-old", bot_id="m-old", signals=[OLD]))
    fake = _Fake({OLD.content: {"refines": 0.7, "supersedes": 0.3}})
    monkeypatch.setattr(signal_relation, "_default_client", lambda: fake)

    orch = IngestOrchestrator(classifier=MagicMock(), claude_client=MagicMock(), graph=MagicMock(),
                              signal_writer=MagicMock(), git_ops=MagicMock())
    new = NEW.model_copy(deep=True)
    count = await orch._phase_detect_supersession(
        MeetingSignals(meeting_id="m-new", bot_id="m-new", signals=[new]), store)

    assert count == 0
    [cand] = new.metadata["supersession_candidates"]
    assert cand["status"] == "dismissed"
    assert cand["relation"] == "refines"
