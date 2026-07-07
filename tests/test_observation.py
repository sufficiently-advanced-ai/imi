"""Observation — the source-agnostic ingest model (open-core P1a).

The serialization contract is the load-bearing part: Observation.to_markdown()
must keep emitting the legacy meeting frontmatter keys so existing repos and
the meeting readers (MeetingState.from_markdown) parse it unchanged.
"""

from datetime import UTC, datetime

import pytest

from app.models.observation import Observation


def make_observation(**overrides):
    defaults = dict(
        observation_id="ingest-abc12345",
        external_id="ingest-deadbeef9012",
        observed_at=datetime(2026, 6, 4, 14, 30, tzinfo=UTC),
        content="## Decisions\n- We will ship the thing",
        entities_mentioned={"person": ["Sarah Chen"], "project": ["CRM Modernization"]},
        participants=["Sarah Chen", "David Kim"],
        title="Pipeline sync",
        raw_content="[00:01] Sarah: let's ship it",
        occurred_at=datetime(2026, 6, 4, 14, 30, tzinfo=UTC),
        is_finalized=True,
        status="completed",
        update_count=1,
    )
    defaults.update(overrides)
    return Observation(**defaults)


class TestObservationModel:
    def test_constructs_with_generic_fields(self):
        obs = make_observation()
        assert obs.observation_id == "ingest-abc12345"
        assert obs.external_id == "ingest-deadbeef9012"
        assert obs.source == "ingest"  # default producer tag
        assert obs.content.startswith("## Decisions")
        assert obs.raw_content.startswith("[00:01]")

    def test_minimal_construction(self):
        obs = Observation(
            observation_id="o1",
            external_id="e1",
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            content="body",
            entities_mentioned={},
        )
        assert obs.participants == []
        assert obs.key_points == []
        assert obs.title is None
        assert obs.raw_content is None
        assert obs.status == "completed"


class TestMarkdownCompat:
    """to_markdown() must stay parseable by the legacy meeting reader."""

    def test_roundtrip_through_meeting_state_reader(self):
        pytest.importorskip(
            "app.models.meeting",
            reason="meeting models are hosted-edition only; adapter tested there",
        )
        from app.models.meeting.state import MeetingState

        obs = make_observation()
        parsed = MeetingState.from_markdown(obs.to_markdown())

        assert parsed.meeting_id == obs.observation_id
        assert parsed.bot_id == obs.external_id
        assert parsed.updated_at == obs.observed_at
        assert parsed.body == obs.content
        assert parsed.transcript == obs.raw_content
        assert parsed.title == obs.title
        assert parsed.start_time == obs.occurred_at
        assert parsed.participants == obs.participants
        assert parsed.entities_mentioned == obs.entities_mentioned
        assert parsed.is_finalized is True
        assert parsed.status == "completed"

    def test_roundtrip_through_own_reader(self):
        obs = make_observation()
        parsed = Observation.from_markdown(obs.to_markdown())
        # source and metadata are runtime producer tags — not persisted.
        # Everything else must survive the roundtrip.
        assert parsed.model_dump(exclude={"source", "metadata"}) == obs.model_dump(
            exclude={"source", "metadata"}
        )

    def test_source_is_not_persisted_in_markdown(self):
        """source is a runtime producer tag; the on-disk format has no source key."""
        obs = make_observation(source="meeting")
        parsed = Observation.from_markdown(obs.to_markdown())
        assert parsed.source == "ingest"

    def test_empty_entities_mentioned_roundtrip(self):
        obs = make_observation(entities_mentioned={})
        parsed = Observation.from_markdown(obs.to_markdown())
        assert parsed.entities_mentioned == {}

    def test_from_markdown_rejects_missing_updated_at(self):
        doc = "---\nmeeting_id: o1\nbot_id: e1\nupdated_at:\n---\n\nbody"
        with pytest.raises(ValueError, match="updated_at"):
            Observation.from_markdown(doc)

    def test_yaml_escaping_of_special_titles(self):
        pytest.importorskip(
            "app.models.meeting",
            reason="meeting models are hosted-edition only; adapter tested there",
        )
        from app.models.meeting.state import MeetingState

        obs = make_observation(title='Q3: "kickoff" — scope & risks')
        parsed = MeetingState.from_markdown(obs.to_markdown())
        assert parsed.title == 'Q3: "kickoff" — scope & risks'

    def test_no_transcript_section_when_raw_content_absent(self):
        obs = make_observation(raw_content=None)
        md = obs.to_markdown()
        assert "## Full Transcript" not in md


class TestMeetingStateAdapter:
    def test_to_observation_maps_fields(self):
        pytest.importorskip(
            "app.models.meeting",
            reason="meeting models are hosted-edition only; adapter tested there",
        )
        from app.models.meeting.state import MeetingState

        state = MeetingState(
            meeting_id="meet-1",
            bot_id="bot-77",
            updated_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            entities_mentioned={"person": ["Ada"]},
            body="## Key Points\n- hi",
            transcript="Ada: hi",
            title="Standup",
            start_time=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
            participants=["Ada"],
            key_points=["hi"],
            is_finalized=True,
            status="completed",
            update_count=3,
        )
        obs = state.to_observation()
        assert obs.source == "meeting"
        assert obs.observation_id == "meet-1"
        assert obs.external_id == "bot-77"
        assert obs.observed_at == state.updated_at
        assert obs.content == state.body
        assert obs.raw_content == state.transcript
        assert obs.occurred_at == state.start_time
        assert obs.participants == ["Ada"]
        assert obs.entities_mentioned == {"person": ["Ada"]}
        assert obs.update_count == 3
        assert obs.title == "Standup"
        assert obs.key_points == ["hi"]
        assert obs.is_finalized is True
        assert obs.status == "completed"
