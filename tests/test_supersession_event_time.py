"""ADR-004 §6: supersession direction is decided by event time.

A decision ingested late but made earlier must never be proposed as
superseding a newer one; the proposal is reversed instead.
"""

from unittest.mock import MagicMock

import pytest

from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator
from app.services.supersession_candidates import (
    find_superseding_candidates,
    find_supersession_candidates,
)


def _decision(sid, when, meeting, entities=("project-atlas",), **kw):
    return Signal(
        id=sid,
        type=kw.pop("type", "decision"),
        content=kw.pop("content", f"decision {sid}"),
        source_meeting_id=meeting,
        source_timestamp=when,
        entities=[EntityRef(id=e, type=e.split("-")[0], name=e) for e in entities],
        **kw,
    )


MARCH = _decision("march", "2026-03-10T15:00:00+00:00", "m-march")
JANUARY = _decision("january", "2026-01-12T15:00:00+00:00", "m-january")


# ---------------------------------------------------------------------------
# Pure matching
# ---------------------------------------------------------------------------


def test_newer_decision_supersedes_older():
    found = find_supersession_candidates(MARCH, [JANUARY])
    assert [c["old_signal_id"] for c in found] == ["january"]


def test_backfilled_older_decision_never_supersedes_a_newer_one():
    assert find_supersession_candidates(JANUARY, [MARCH]) == []


def test_backfill_is_proposed_in_reverse():
    pairs = find_superseding_candidates(JANUARY, [MARCH])
    assert len(pairs) == 1
    newer, candidate = pairs[0]
    assert newer.id == "march"
    assert candidate["old_signal_id"] == "january"
    assert candidate["old_content"] == "decision january"
    assert candidate["status"] == "pending"
    assert candidate["backfilled"] is True
    assert candidate["matched_entities"] == ["project-atlas"]


def test_normal_order_has_nothing_to_reverse():
    assert find_superseding_candidates(MARCH, [JANUARY]) == []


def test_direction_compares_instants_across_offsets():
    """09:00-05:00 is 14:00 UTC, which is after 13:30 UTC."""
    local = _decision("local", "2026-03-10T09:00:00-05:00", "m-local")
    utc = _decision("utc", "2026-03-10T13:30:00+00:00", "m-utc")
    assert [c["old_signal_id"] for c in find_supersession_candidates(local, [utc])] == ["utc"]
    assert find_supersession_candidates(utc, [local]) == []
    assert [p[0].id for p in find_superseding_candidates(utc, [local])] == ["local"]


def test_same_instant_keeps_the_ingest_order():
    twin = _decision("twin", "2026-03-10T15:00:00+00:00", "m-twin")
    assert [c["old_signal_id"] for c in find_supersession_candidates(MARCH, [twin])] == ["twin"]
    assert find_superseding_candidates(MARCH, [twin]) == []


def test_unknown_event_time_behaves_as_before():
    undated = _decision("undated", "", "m-undated")
    assert [c["old_signal_id"] for c in find_supersession_candidates(MARCH, [undated])] == ["undated"]
    assert [c["old_signal_id"] for c in find_supersession_candidates(undated, [MARCH])] == ["march"]
    assert find_superseding_candidates(undated, [MARCH]) == []


def test_valid_from_is_the_event_time_when_set():
    restated = _decision(
        "restated", "2026-04-01T00:00:00+00:00", "m-restated",
        valid_from="2026-01-01T00:00:00+00:00",
    )
    assert find_supersession_candidates(restated, [MARCH]) == []
    assert [p[0].id for p in find_superseding_candidates(restated, [MARCH])] == ["march"]


@pytest.mark.parametrize(
    "standing",
    [
        _decision("other", "2026-03-10T15:00:00+00:00", "m-x", entities=("project-borealis",)),
        _decision("people", "2026-03-10T15:00:00+00:00", "m-x", entities=("person-alice",)),
        _decision("insight", "2026-03-10T15:00:00+00:00", "m-x", type="insight"),
        _decision("same", "2026-03-10T15:00:00+00:00", "m-january"),
        _decision("gone", "2026-03-10T15:00:00+00:00", "m-x", provenance_status="superseded"),
        _decision("no", "2026-03-10T15:00:00+00:00", "m-x", review_status="rejected"),
    ],
    ids=["no-shared-entity", "people-only", "not-a-decision", "same-meeting", "superseded", "rejected"],
)
def test_reverse_applies_the_same_exclusions(standing):
    assert find_superseding_candidates(JANUARY, [standing]) == []


def test_reverse_is_capped_and_ranked():
    newer = [
        _decision("wide", "2026-03-01T00:00:00+00:00", "m1", entities=("project-atlas", "account-x", "account-y")),
        _decision("exact", "2026-03-02T00:00:00+00:00", "m2"),
        _decision("half", "2026-03-03T00:00:00+00:00", "m3", entities=("project-atlas", "account-x")),
    ]
    pairs = find_superseding_candidates(JANUARY, newer, max_candidates=2)
    assert [p[0].id for p in pairs] == ["exact", "half"]


def test_non_decision_has_no_reverse_candidates():
    insight = _decision("i", "2026-01-12T15:00:00+00:00", "m-i", type="insight")
    assert find_superseding_candidates(insight, [MARCH]) == []


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class _Store:
    def __init__(self, batches):
        self.batches = batches
        self.replaced = []

    def load_all(self):
        return self.batches

    def find_signal_by_id(self, signal_id):
        for batch in self.batches:
            for sig in batch.signals:
                if sig.id == signal_id:
                    return sig, batch
        return None

    def replace_signal(self, signal, container):
        self.replaced.append((signal.id, container.bot_id))


def _orchestrator():
    return IngestOrchestrator(
        classifier=None, claude_client=None, graph=None, signal_writer=None, git_ops=None, tools={}
    )


def _batch(bot_id, *signals):
    return MeetingSignals(meeting_id=bot_id, bot_id=bot_id, signals=list(signals))


@pytest.fixture(autouse=True)
def _no_decision_model(monkeypatch):
    """Keep the signal_relation gate out of these tests."""
    import app.services.signal_relation as relation

    monkeypatch.setattr(relation, "_default_client", lambda: None)


@pytest.mark.asyncio
async def test_backfilled_decision_is_attached_to_the_newer_signal():
    march = MARCH.model_copy(deep=True)
    january = JANUARY.model_copy(deep=True)
    store = _Store([_batch("m-march", march)])
    orch = _orchestrator()

    count = await orch._phase_detect_supersession(_batch("m-january", january), store=store)

    assert count == 1
    # Nothing on the backfilled signal: it supersedes nothing.
    assert "supersession_candidates" not in january.metadata
    # Nothing written yet: the signal the candidate names is not stored.
    assert store.replaced == []
    assert "supersession_candidates" not in march.metadata

    assert orch._persist_reversed_supersessions(store=store) == 1
    assert store.replaced == [("march", "m-march")]
    candidates = march.metadata["supersession_candidates"]
    assert [(c["old_signal_id"], c["status"]) for c in candidates] == [("january", "pending")]


@pytest.mark.asyncio
async def test_normal_order_is_unchanged():
    march = MARCH.model_copy(deep=True)
    store = _Store([_batch("m-january", JANUARY.model_copy(deep=True))])
    orch = _orchestrator()

    assert await orch._phase_detect_supersession(_batch("m-march", march), store=store) == 1
    assert [c["old_signal_id"] for c in march.metadata["supersession_candidates"]] == ["january"]
    assert orch._persist_reversed_supersessions(store=store) == 0
    assert store.replaced == []


@pytest.mark.asyncio
async def test_reversed_candidate_is_not_added_twice():
    march = MARCH.model_copy(deep=True)
    march.metadata["supersession_candidates"] = [
        {"old_signal_id": "january", "status": "dismissed", "confidence": 1.0}
    ]
    store = _Store([_batch("m-march", march)])
    orch = _orchestrator()

    await orch._phase_detect_supersession(
        _batch("m-january", JANUARY.model_copy(deep=True)), store=store
    )
    assert orch._persist_reversed_supersessions(store=store) == 0
    # The reviewer's dismissal stands.
    assert [c["status"] for c in march.metadata["supersession_candidates"]] == ["dismissed"]


@pytest.mark.asyncio
async def test_reversed_candidates_are_judged_with_the_roles_swapped(monkeypatch):
    """The decision model must see the standing signal as NEW and the
    backfilled one as OLD."""
    import app.services.signal_relation as relation

    seen = []

    async def fake_judge(new_signal, candidates, standing_by_id, client=None):
        seen.append((new_signal.id, [c["old_signal_id"] for c in candidates], sorted(standing_by_id)))
        return [{**c, "status": "dismissed", "relation": "refines"} for c in candidates]

    monkeypatch.setattr(relation, "judge_candidates", fake_judge)
    march = MARCH.model_copy(deep=True)
    store = _Store([_batch("m-march", march)])
    orch = _orchestrator()

    count = await orch._phase_detect_supersession(
        _batch("m-january", JANUARY.model_copy(deep=True)), store=store
    )
    assert seen == [("march", ["january"], ["january"])]
    assert count == 0  # dismissed by the model: not pending
    orch._persist_reversed_supersessions(store=store)
    # Recorded, never deleted, so a reviewer can still see it.
    assert march.metadata["supersession_candidates"][0]["status"] == "dismissed"


@pytest.mark.asyncio
async def test_persist_phase_writes_reversed_candidates_after_the_signals():
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from app.models.observation import Observation

    order = []
    git = MagicMock()

    async def commit_file(path, _content, _message):
        order.append(path)

    git.commit_file = commit_file
    orch = IngestOrchestrator(
        classifier=None, claude_client=None, graph=None, signal_writer=None, git_ops=git, tools={}
    )
    orch._link_document_in_graph = AsyncMock()
    orch._persist_reversed_supersessions = MagicMock(side_effect=lambda: order.append("reversed"))
    obs = Observation(
        observation_id="m1", external_id="ingest-abc",
        observed_at=datetime(2026, 1, 12, tzinfo=UTC), content="b", entities_mentioned={},
    )
    await orch._phase_persist(obs, _batch("ingest-abc", JANUARY.model_copy(deep=True)), "ingest-abc")
    assert order == [
        "meetings/meeting-ingest-abc.md",
        "signals/meeting-ingest-abc.json",
        "reversed",
    ]
