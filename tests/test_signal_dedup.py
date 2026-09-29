"""Tests for signal dedup (keep the richest statement, link overlaps)."""

from unittest.mock import MagicMock

import pytest

from app.models.signal import MeetingSignals, Signal
from app.services import signal_dedup
from app.services.inference.decisions import ChoiceAnswer, DecisionResult, DecisionUnavailable
from app.services.signal_dedup import (
    DuplicateCandidate,
    corroborations,
    decide_action,
    find_duplicate_candidates,
    judge_duplicates,
    resolve_hidden,
)


def _sig(sid, content, meeting="m1", ts="2026-03-01T10:00:00Z", stype="key_point", **kw):
    return Signal(id=sid, type=stype, content=content, source_meeting_id=meeting,
                  source_meeting_title=f"Meeting {meeting}", source_timestamp=ts, **kw)


class _Fake:
    """Scripted Jev: (earlier text, later text) -> (relation, p)."""

    def __init__(self, script, mode="on", fail=()):
        self.script, self._mode, self.fail, self.calls = script, mode, set(fail), []

    def mode(self, operation):
        return self._mode

    async def decide(self, state, questions, *, operation):
        assert operation == signal_dedup.DEDUP_OPERATION
        key = (state["earlier"]["statement"], state["later"]["statement"])
        self.calls.append(key)
        if key in self.fail:
            raise DecisionUnavailable("upstream 500")
        relation, p = self.script[key]
        return DecisionResult({"relation": ChoiceAnswer(relation, {relation: p}, p)},
                              "jev", "fake", 0, 0, 0.0, 0, {})


# --- candidates -------------------------------------------------------------


def test_candidates_same_type_in_window_above_threshold():
    old = _sig("old", "Team of 12 engineers", ts="2026-03-01T10:00:00Z")
    stale = _sig("stale", "Team of 12 engineers", ts="2025-12-01T10:00:00Z")
    other_type = _sig("act", "Team of 12 engineers", stype="action_item")
    hidden = _sig("hid", "Team of 12 engineers", metadata={"duplicate_of": "old"})
    rejected = _sig("rej", "Team of 12 engineers", review_status="rejected")
    new = _sig("new", "Led a team of 12 (8 onshore, 4 offshore)", meeting="m2", ts="2026-03-10T10:00:00Z")
    standing = {s.id: s for s in (old, stale, other_type, hidden, rejected)}

    def similar(text, stype):
        return [("old", 0.9), ("stale", 0.95), ("act", 0.99), ("hid", 0.97), ("rej", 0.96), ("gone", 0.99)]

    cands = find_duplicate_candidates([new], similar, standing)
    assert [(c.old.id, c.similarity) for c in cands] == [("old", 0.9)]

    low = find_duplicate_candidates([new], lambda t, s: [("old", 0.7)], standing)
    assert low == []


def test_candidates_include_earlier_signals_of_the_same_batch():
    a = _sig("a", "Grant repo access", meeting="m2", stype="action_item")
    b = _sig("b", "Give him access to the repo", meeting="m2", stype="action_item")
    cands = find_duplicate_candidates([a, b], lambda t, s: [], {},
                                      batch_similarity=lambda x, y: 0.9)
    assert [(c.new.id, c.old.id) for c in cands] == [("b", "a")]


# --- action rules -----------------------------------------------------------


def test_decide_action_rules():
    old = _sig("o", "x", ts="2026-03-01T10:00:00Z")
    confirmed = _sig("o", "x", ts="2026-03-01T10:00:00Z", review_status="confirmed",
                     provenance_status="user_confirmed", can_use_as_instruction=True)
    new = _sig("n", "y", meeting="m2", ts="2026-03-10T10:00:00Z")
    c = DuplicateCandidate(new, old, 0.9)
    assert decide_action("same", 0.8, c) == "hide_new"
    assert decide_action("earlier_richer", 0.6, c) == "hide_new"
    assert decide_action("later_richer", 0.6, c) == "hide_old"
    # A confirmed record is never retired behind a fresh extraction.
    assert decide_action("later_richer", 0.9, DuplicateCandidate(new, confirmed, 0.9)) == "hide_new"
    assert decide_action("later_richer", 0.4, c) == "none"
    assert decide_action("overlap", 0.7, c) == "link"
    assert decide_action("different", 0.99, c) == "none"


def test_backfilled_older_signal_is_earlier_in_event_time():
    """ADR-004: an ingested signal that happened before a standing one is EARLIER."""
    standing = _sig("s", "Team of 12 (8 onshore, 4 offshore)", ts="2026-03-10T10:00:00Z")
    backfill = _sig("b", "Team of 12", meeting="m0", ts="2026-01-05T10:00:00Z")
    c = DuplicateCandidate(backfill, standing, 0.9)
    assert not c.new_is_later
    state = signal_dedup.build_duplicate_state(c)
    assert state["earlier"]["statement"] == backfill.content
    # LATER (the standing one) is richer: hide the incoming, earlier one.
    assert decide_action("later_richer", 0.8, c) == "hide_new"
    # EARLIER (the incoming one) richer: hide the standing one if unreviewed.
    assert decide_action("earlier_richer", 0.8, c) == "hide_old"


# --- judgment ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_hides_richest_links_overlaps_and_survives_failures():
    old_short = _sig("o1", "Built EME, a knowledge graph tool")
    old_rich = _sig("o2", "Team of 12 across CRM, ERP and data engineering")
    old_overlap = _sig("o3", "Targets companies hiring an AI lead")
    old_broken = _sig("o4", "Unrelated but similar")
    n1 = _sig("n1", "Built and open-sourced EME: transcripts and email into Neo4j via MCP", meeting="m2")
    n2 = _sig("n2", "Led a team of 12", meeting="m2")
    n3 = _sig("n3", "Runs two outbound strategies incl. AI-lead job posts", meeting="m2")
    fake = _Fake({
        (old_short.content, n1.content): ("later_richer", 0.6),
        (old_rich.content, n2.content): ("earlier_richer", 0.7),
        (old_overlap.content, n3.content): ("overlap", 0.8),
    }, fail={(old_broken.content, n3.content)})
    cands = [DuplicateCandidate(n1, old_short, 0.91), DuplicateCandidate(n2, old_rich, 0.9),
             DuplicateCandidate(n3, old_overlap, 0.8), DuplicateCandidate(n3, old_broken, 0.79)]

    out = await judge_duplicates(cands, client=fake)

    assert out.hidden_new == 1 and n2.metadata["duplicate_of"] == "o2"
    assert out.hidden_old == [old_short] and old_short.metadata["duplicate_of"] == "n1"
    assert "duplicate_of" not in n1.metadata
    assert out.linked == 1 and n3.metadata["related_signals"] == [{"id": "o3", "relation": "overlap", "p": 0.8}]
    assert [c["of"] for c in n3.metadata["duplicate_check"]] == ["o3"]  # failed pair not recorded


@pytest.mark.asyncio
async def test_hiding_the_new_signal_wins_over_hiding_an_old_one():
    a, b = _sig("a", "short"), _sig("b", "same info")
    new = _sig("n", "fuller", meeting="m2")
    fake = _Fake({("short", "fuller"): ("later_richer", 0.9), ("same info", "fuller"): ("same", 0.6)})
    out = await judge_duplicates([DuplicateCandidate(new, a, 0.9), DuplicateCandidate(new, b, 0.85)], client=fake)
    assert new.metadata["duplicate_of"] == "b"
    assert out.hidden_old == [] and "duplicate_of" not in a.metadata


@pytest.mark.asyncio
async def test_shadow_records_verdicts_and_changes_nothing():
    old, new = _sig("o", "short"), _sig("n", "fuller", meeting="m2")
    fake = _Fake({("short", "fuller"): ("later_richer", 0.9)}, mode="shadow")
    out = await judge_duplicates([DuplicateCandidate(new, old, 0.9)], client=fake)
    assert (out.hidden_new, out.hidden_old, out.linked) == (0, [], 0)
    assert "duplicate_of" not in old.metadata and "duplicate_of" not in new.metadata
    assert new.metadata["duplicate_check"][0]["action"] == "hide_old"


# --- read-side resolution ---------------------------------------------------


def test_resolve_hidden_follows_chains_and_ignores_dangling_or_cycles():
    sigs = [
        _sig("a", "x", metadata={"duplicate_of": "b"}),
        _sig("b", "x", metadata={"duplicate_of": "c"}),
        _sig("c", "x"),
        _sig("d", "x", metadata={"duplicate_of": "missing"}),
        _sig("e", "x", metadata={"duplicate_of": "f"}),
        _sig("f", "x", metadata={"duplicate_of": "e"}),
    ]
    assert resolve_hidden(sigs) == {"a": "c", "b": "c"}
    corr = corroborations(sigs)
    assert sorted(c["signal_id"] for c in corr["c"]) == ["a", "b"]


# --- phase + read paths -----------------------------------------------------


@pytest.mark.asyncio
async def test_phase_persists_hidden_standing_signal_and_feed_hides_it(tmp_path, monkeypatch):
    from app.routes import signal_feed
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator
    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path / "signals")
    old = _sig("old", "Built EME, a knowledge graph tool", meeting="m-old")
    store.save(MeetingSignals(meeting_id="m-old", bot_id="m-old", signals=[old]))
    new = _sig("new", "Built and open-sourced EME into Neo4j via MCP", meeting="m-new",
               ts="2026-03-05T10:00:00Z")
    fake = _Fake({(old.content, new.content): ("later_richer", 0.7)})
    monkeypatch.setattr(signal_dedup, "_default_client", lambda: fake)

    orch = IngestOrchestrator(classifier=MagicMock(), claude_client=MagicMock(), graph=MagicMock(),
                              signal_writer=MagicMock(), git_ops=MagicMock())
    batch = MeetingSignals(meeting_id="m-new", bot_id="m-new", signals=[new])
    result = await orch._phase_detect_duplicates(
        batch, store, similar=lambda t, s: [("old", 0.91)], batch_similarity=lambda a, b: 0.0)

    assert result == {"hidden": 1, "linked": 0}
    assert store.load("m-old").signals[0].metadata["duplicate_of"] == "new"
    store.save(batch)

    monkeypatch.setattr(signal_feed, "SignalStore", lambda: store)
    feed = await signal_feed.get_signal_feed(signal_type=None, entity_id=None, date_from=None,
                                             date_to=None, limit=100, include_duplicates=False)
    shown = [s for d in feed.days for s in d.signals]
    assert [s.id for s in shown] == ["new"]
    assert shown[0].metadata["corroborated_by"][0]["signal_id"] == "old"

    everything = await signal_feed.get_signal_feed(signal_type=None, entity_id=None, date_from=None,
                                                   date_to=None, limit=100, include_duplicates=True)
    assert everything.total_signals == 2


@pytest.mark.asyncio
async def test_phase_is_a_noop_when_operation_is_off(monkeypatch):
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

    monkeypatch.setattr(signal_dedup, "_default_client", lambda: None)
    orch = IngestOrchestrator(classifier=MagicMock(), claude_client=MagicMock(), graph=MagicMock(),
                              signal_writer=MagicMock(), git_ops=MagicMock())
    batch = MeetingSignals(meeting_id="m", bot_id="m", signals=[_sig("n", "x")])
    assert await orch._phase_detect_duplicates(batch, MagicMock()) is None


def test_decision_views_and_search_skip_duplicates(tmp_path):
    from app.services.decision_view import load_decision_signals
    from app.services.signal_retrieval import _passes_governance
    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path / "signals")
    kept = _sig("d1", "Use starter licenses", stype="decision")
    dup = _sig("d2", "Stay on starter licenses", stype="decision", metadata={"duplicate_of": "d1"})
    store.save(MeetingSignals(meeting_id="m", bot_id="m", signals=[kept, dup]))

    assert [s.id for s in load_decision_signals(store)] == ["d1"]
    assert len(load_decision_signals(store, include_duplicates=True)) == 2
    assert _passes_governance({"duplicate_of": "d1"}, "evidence", False) is False
    assert _passes_governance({"duplicate_of": "d1"}, "evidence", True) is True
