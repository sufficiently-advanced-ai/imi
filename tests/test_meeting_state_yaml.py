"""Regression tests for MeetingState markdown round-trip (PR #5 review).

Two findings: ``speaker_mappings`` was serialized without YAML escaping
(unlike every sibling field), and ``from_markdown`` read
``frontmatter["updated_at"]`` directly instead of going through
``_parse_datetime`` like ``start_time``.
"""

from datetime import datetime

import pytest

from app.models.meeting.state import MeetingState


def _state(**overrides) -> MeetingState:
    defaults = dict(
        meeting_id="m-1",
        bot_id="b-1",
        updated_at=datetime(2026, 7, 2, 12, 0, 0),
        entities_mentioned={},
        body="Notes body",
    )
    defaults.update(overrides)
    return MeetingState(**defaults)


def test_speaker_mappings_round_trip_with_special_characters():
    mappings = {
        "Dr. Smith: PhD": {
            "entity_id": "person-1",
            "note": "says: hello",
            "confidence": 0.9,
        }
    }
    state = _state(speaker_mappings=mappings)

    parsed = MeetingState.from_markdown(state.to_markdown())

    assert parsed.speaker_mappings == mappings


def test_speaker_mappings_round_trip_plain_values():
    mappings = {"Alice": {"entity_id": "person-2", "is_verified": True}}
    state = _state(speaker_mappings=mappings)

    parsed = MeetingState.from_markdown(state.to_markdown())

    assert parsed.speaker_mappings == mappings


def test_from_markdown_missing_updated_at_raises_value_error():
    content = "---\nmeeting_id: m-1\nbot_id: b-1\n---\nBody"
    with pytest.raises(ValueError):
        MeetingState.from_markdown(content)


def test_from_markdown_malformed_updated_at_raises_value_error():
    content = "---\nmeeting_id: m-1\nbot_id: b-1\nupdated_at: not-a-date\n---\nBody"
    with pytest.raises(ValueError):
        MeetingState.from_markdown(content)


def test_from_markdown_accepts_iso_string_updated_at():
    content = (
        "---\nmeeting_id: m-1\nbot_id: b-1\n"
        "updated_at: '2026-07-02T12:00:00'\nentities_mentioned:\n---\nBody"
    )
    parsed = MeetingState.from_markdown(content)
    assert parsed.updated_at == datetime(2026, 7, 2, 12, 0, 0)
