"""ADR-004: event time on evidence — pure helpers, models and ingest wiring."""

from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.captured_memory import CapturedMemory
from app.models.observation import Observation
from app.utils.event_time import (
    ASSERTIONS_KEY,
    TIME_SOURCE_CONTENT,
    TIME_SOURCE_EXPLICIT,
    TIME_SOURCE_FALLBACK,
    TIME_SOURCE_INFERRED,
    TIME_SOURCE_UNRECORDED,
    assertions_for,
    document_event_time,
    drop_assertions,
    edge_event_props,
    is_observation_document,
    make_assertion,
    merge_assertion,
    normalize_temporal,
    read_assertions,
    signal_event_time,
    to_iso,
    to_utc,
    validate_time_source,
)

# ---------------------------------------------------------------------------
# to_utc: the comparison bug this ADR fixes
# ---------------------------------------------------------------------------


def test_offsets_compare_by_instant_not_by_text():
    """14:00-04:00 is 18:00 UTC: after 17:00 UTC, though it sorts before it
    as a string."""
    local = "2026-09-21T14:00:00-04:00"
    utc = "2026-09-21T17:00:00+00:00"
    assert local < utc  # the old string comparison
    assert to_utc(local) > to_utc(utc)  # the instant
    assert to_utc(local) == datetime(2026, 9, 21, 18, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-03-01T00:00:00Z", datetime(2026, 3, 1, tzinfo=UTC)),
        ("2026-03-01T00:00:00", datetime(2026, 3, 1, tzinfo=UTC)),  # naive reads as UTC
        ("2026-03-01", datetime(2026, 3, 1, tzinfo=UTC)),
        (date(2026, 3, 1), datetime(2026, 3, 1, tzinfo=UTC)),
        (datetime(2026, 3, 1, 9, 30), datetime(2026, 3, 1, 9, 30, tzinfo=UTC)),
        (
            datetime(2026, 3, 1, 9, 30, tzinfo=timezone(timedelta(hours=2))),
            datetime(2026, 3, 1, 7, 30, tzinfo=UTC),
        ),
    ],
)
def test_to_utc_accepts_file_and_api_shapes(value, expected):
    assert to_utc(value) == expected
    assert to_utc(value).utcoffset() == timedelta(0)


@pytest.mark.parametrize("value", [None, "", "   ", "not a date", 12345, {"a": 1}])
def test_to_utc_rejects_what_it_cannot_read(value):
    assert to_utc(value) is None
    assert to_iso(value) is None


def test_to_iso_is_utc():
    assert to_iso("2026-09-21T14:00:00-04:00") == "2026-09-21T18:00:00+00:00"


class _Neo4jDateTime:
    """Stands in for neo4j.time.DateTime (has to_native + iso_format)."""

    def __init__(self, native):
        self._native = native

    def to_native(self):
        return self._native

    def iso_format(self):
        return self._native.isoformat()


def test_normalize_temporal_turns_graph_times_into_strings():
    when = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)
    row = {
        "s": {"id": "s1", "valid_from": _Neo4jDateTime(when), "n": 3, "tags": ["a"]},
        "times": [_Neo4jDateTime(when), None],
        "plain": when,
    }
    assert normalize_temporal(row) == {
        "s": {"id": "s1", "valid_from": "2026-09-21T18:00:00+00:00", "n": 3, "tags": ["a"]},
        "times": ["2026-09-21T18:00:00+00:00", None],
        "plain": "2026-09-21T18:00:00+00:00",
    }


def test_to_utc_reads_graph_times():
    when = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)
    assert to_utc(_Neo4jDateTime(when)) == when


# ---------------------------------------------------------------------------
# time_source
# ---------------------------------------------------------------------------


def test_unknown_time_source_reads_as_unrecorded():
    assert validate_time_source("explicit") == TIME_SOURCE_EXPLICIT
    assert validate_time_source(" Content_Header ") == TIME_SOURCE_CONTENT
    assert validate_time_source(None) == TIME_SOURCE_UNRECORDED
    assert validate_time_source("guessed") == TIME_SOURCE_UNRECORDED


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def test_document_event_time_reads_start_time():
    props = document_event_time(
        {
            "meeting_id": "m1",
            "bot_id": "b1",
            "start_time": "2026-06-02T10:00:00-04:00",
            "updated_at": "2026-06-02T10:00:00-04:00",
            "recorded_at": "2026-09-27T22:58:30+00:00",
            "time_source": "explicit",
        }
    )
    assert props == {
        "occurred_at": datetime(2026, 6, 2, 14, 0, tzinfo=UTC),
        "recorded_at": datetime(2026, 9, 27, 22, 58, 30, tzinfo=UTC),
        "time_source": "explicit",
    }


def test_observation_without_start_time_uses_its_observation_time():
    props = document_event_time({"bot_id": "b1", "updated_at": "2026-06-02T10:00:00+00:00"})
    assert props["occurred_at"] == datetime(2026, 6, 2, 10, 0, tzinfo=UTC)
    assert props["time_source"] == TIME_SOURCE_UNRECORDED
    assert "recorded_at" not in props


def test_entity_profile_carries_no_event_time():
    """A profile's updated_at/created_at is when imi wrote the file."""
    profile = {
        "id": "person-alice",
        "entity_type": "person",
        "created_at": "2026-09-27T22:58:30",
        "updated_at": "2026-09-27T22:58:30",
    }
    assert not is_observation_document(profile)
    assert document_event_time(profile) == {}


def test_hand_written_note_with_a_date_is_dated():
    assert document_event_time({"title": "Note", "date": "2026-02-10"}) == {
        "occurred_at": datetime(2026, 2, 10, tzinfo=UTC),
        "time_source": TIME_SOURCE_UNRECORDED,
    }


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def test_signal_event_time_prefers_valid_from():
    sig = SimpleNamespace(
        valid_from="2026-06-01T00:00:00+00:00", source_timestamp="2026-06-05T00:00:00+00:00"
    )
    assert signal_event_time(sig) == datetime(2026, 6, 1, tzinfo=UTC)
    sig = SimpleNamespace(valid_from=None, source_timestamp="2026-06-05T09:00:00-04:00")
    assert signal_event_time(sig) == datetime(2026, 6, 5, 13, 0, tzinfo=UTC)
    assert signal_event_time(SimpleNamespace(valid_from=None, source_timestamp="")) is None


# ---------------------------------------------------------------------------
# Relationship assertions
# ---------------------------------------------------------------------------


def _assertion(source="doc:meetings/meeting-a.md", when="2026-06-02T14:00:00+00:00", **kw):
    return make_assertion(
        kw.pop("rel_type", "works_on_projects"),
        kw.pop("target", "project-atlas"),
        source_id=source,
        occurred_at=when,
        time_source=kw.pop("time_source", TIME_SOURCE_EXPLICIT),
        **kw,
    )


def test_make_assertion_stores_utc_iso_strings():
    a = _assertion(when="2026-06-02T10:00:00-04:00", recorded_at=datetime(2026, 9, 27, 12, 0))
    assert a == {
        "type": "works_on_projects",
        "target": "project-atlas",
        "source_id": "doc:meetings/meeting-a.md",
        "occurred_at": "2026-06-02T14:00:00+00:00",
        "time_source": "explicit",
        "recorded_at": "2026-09-27T12:00:00+00:00",
    }


def test_merge_assertion_is_idempotent_per_source():
    meta = {"works_on_projects": ["project-atlas"]}
    assert merge_assertion(meta, _assertion()) is True
    assert merge_assertion(meta, _assertion()) is False  # re-ingest of the same source
    assert merge_assertion(meta, _assertion(source="doc:meetings/meeting-b.md")) is True
    assert len(meta[ASSERTIONS_KEY]) == 2
    # The typed list every existing reader parses is untouched.
    assert meta["works_on_projects"] == ["project-atlas"]


def test_assertions_for_matches_type_and_target():
    meta = {}
    merge_assertion(meta, _assertion())
    merge_assertion(meta, _assertion(target="project-borealis"))
    merge_assertion(meta, _assertion(rel_type="reports_to", target="person-bob"))
    assert [a["target"] for a in assertions_for(meta, "works_on_projects", "project-atlas")] == [
        "project-atlas"
    ]
    assert assertions_for(meta, "Works_On_Projects", "project-atlas")  # case-insensitive type
    assert assertions_for(meta, "works_on_projects", "project-missing") == []


def test_read_assertions_skips_malformed_entries():
    meta = {
        ASSERTIONS_KEY: [
            "project-atlas",
            {"type": "works_on_projects", "target": "project-atlas"},  # no source_id
            {"type": "", "target": "x", "source_id": "y"},
            {"type": "works_on_projects", "target": "project-atlas", "source_id": "doc:a"},
        ]
    }
    assert [a["source_id"] for a in read_assertions(meta)] == ["doc:a"]
    assert read_assertions({ASSERTIONS_KEY: "nope"}) == []
    assert read_assertions({}) == []


def test_drop_assertions_removes_every_source_for_the_pair():
    meta = {}
    merge_assertion(meta, _assertion())
    merge_assertion(meta, _assertion(source="doc:meetings/meeting-b.md"))
    merge_assertion(meta, _assertion(target="project-borealis"))
    assert drop_assertions(meta, "works_on_projects", "project-atlas") is True
    assert [a["target"] for a in meta[ASSERTIONS_KEY]] == ["project-borealis"]
    assert drop_assertions(meta, "works_on_projects", "project-atlas") is False
    assert drop_assertions(meta, "works_on_projects", "project-borealis") is True
    assert ASSERTIONS_KEY not in meta


def test_edge_props_for_an_attributed_edge():
    props = edge_event_props(_assertion(when="2026-06-02T10:00:00-04:00"))
    assert props == {
        "source_id": "doc:meetings/meeting-a.md",
        "time_source": "explicit",
        "occurred_at": datetime(2026, 6, 2, 14, 0, tzinfo=UTC),
    }


def test_unattributed_edge_has_empty_source_and_no_time():
    assert edge_event_props(None) == {"source_id": ""}


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


def _obs(**kw):
    base = dict(
        observation_id="m1",
        external_id="ingest-abc",
        observed_at=datetime(2026, 6, 2, 14, 0, tzinfo=UTC),
        occurred_at=datetime(2026, 6, 2, 14, 0, tzinfo=UTC),
        content="body",
        entities_mentioned={},
    )
    base.update(kw)
    return Observation(**base)


def test_observation_round_trips_recorded_at_and_time_source():
    obs = _obs(recorded_at=datetime(2026, 9, 27, 22, 0, tzinfo=UTC), time_source="content_header")
    parsed = Observation.from_markdown(obs.to_markdown())
    assert parsed.recorded_at == datetime(2026, 9, 27, 22, 0, tzinfo=UTC)
    assert parsed.time_source == "content_header"
    assert parsed.occurred_at == datetime(2026, 6, 2, 14, 0, tzinfo=UTC)


def test_observation_without_the_new_fields_is_unchanged_on_disk():
    markdown = _obs().to_markdown()
    assert "recorded_at" not in markdown and "time_source" not in markdown
    parsed = Observation.from_markdown(markdown)
    assert parsed.recorded_at is None
    assert parsed.time_source == TIME_SOURCE_UNRECORDED


def test_observation_rejects_an_invented_time_source():
    assert _obs(time_source="made_up").time_source == TIME_SOURCE_UNRECORDED


# ---------------------------------------------------------------------------
# Ingest: how the event time was obtained
# ---------------------------------------------------------------------------


def _orchestrator():
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

    return IngestOrchestrator(
        classifier=None, claude_client=None, graph=None, signal_writer=None, git_ops=None, tools={}
    )


def _request(**kw):
    base = dict(content="Hello", timestamp=None, title="T", participants=[])
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_kwargs, expected_source",
    [
        ({"timestamp": datetime(2026, 1, 15, 9, 0, tzinfo=UTC)}, TIME_SOURCE_EXPLICIT),
        ({"content": "Date: 2026-02-10T08:00:00Z\n\nBody"}, TIME_SOURCE_CONTENT),
        ({"content": "No date anywhere"}, TIME_SOURCE_FALLBACK),
    ],
)
async def test_ingest_records_the_time_source(monkeypatch, request_kwargs, expected_source):
    from app.services.orchestrators import ingest_orchestrator as mod

    monkeypatch.setattr(mod, "get_semantica_knowledge", lambda: None)
    before = datetime.now(UTC)
    obs = await _orchestrator()._phase_build_observation(
        _request(**request_kwargs), "ingest-abc", "call_transcript"
    )
    assert obs.time_source == expected_source
    assert obs.recorded_at >= before  # always the ingest clock
    if expected_source == TIME_SOURCE_EXPLICIT:
        assert obs.occurred_at == datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
    if expected_source == TIME_SOURCE_CONTENT:
        assert obs.occurred_at == datetime(2026, 2, 10, 8, 0, tzinfo=UTC)
        assert obs.recorded_at > obs.occurred_at  # backfill: recorded long after it happened
    if expected_source == TIME_SOURCE_FALLBACK:
        assert abs((obs.occurred_at - obs.recorded_at).total_seconds()) < 5


def test_assertion_evidence_points_at_the_observation_document():
    obs = _obs(recorded_at=datetime(2026, 9, 27, 22, 0, tzinfo=UTC), time_source="explicit")
    evidence = _orchestrator()._assertion_evidence(obs)
    assert evidence == {
        "source_id": "doc:meetings/meeting-ingest-abc.md",
        "occurred_at": datetime(2026, 6, 2, 14, 0, tzinfo=UTC),
        "recorded_at": datetime(2026, 9, 27, 22, 0, tzinfo=UTC),
        "time_source": "explicit",
    }
    assert _orchestrator()._assertion_evidence(SimpleNamespace()) is None


# ---------------------------------------------------------------------------
# Captures
# ---------------------------------------------------------------------------


def _capture(**kw):
    base = dict(id="c1", content="text", source="web")
    base.update(kw)
    return CapturedMemory(**base)


def test_capture_validity_opens_at_its_source_date():
    assert _capture(source_date="2026-03-04T10:00:00+00:00").valid_from == "2026-03-04T10:00:00+00:00"


def test_capture_keeps_an_explicit_valid_from():
    cap = _capture(source_date="2026-03-04T10:00:00+00:00", valid_from="2026-01-01T00:00:00+00:00")
    assert cap.valid_from == "2026-01-01T00:00:00+00:00"


def test_capture_without_source_date_has_no_window():
    assert _capture().valid_from is None


def test_inferred_is_a_known_time_source():
    assert validate_time_source(TIME_SOURCE_INFERRED) == "inferred"
