"""Salient-extraction JSON parsing robustness (regression).

At production temperature the model occasionally wraps its JSON in markdown
fences AND appends commentary after the closing fence ("**Note:** I excluded
'the team'..."). The old anchored fence regex required the fence to span the
whole response, so one editorial note cost an entire meeting's entities
(observed live: eval fixture scored recall 0.000 on such a run, and ingest
logs the same shape as "Salient extraction returned nothing").
"""

import json

from app.services.salient_entity_extractor import parse_salient_entities

ENTITY_TYPES = ["person", "account", "project", "team"]

_PAYLOAD = {
    "meeting_title": "Delivery sync",
    "entities": [
        {
            "type": "person",
            "name_heard": "Elena",
            "canonical_name": "Elena Vasquez",
            "aliases_heard": [],
            "salience": "participant",
            "role": None,
            "confidence": 0.98,
            "evidence": "**Elena Vasquez**: ...",
        },
        {
            "type": "account",
            "name_heard": "Meridian Health",
            "canonical_name": "Meridian Health",
            "aliases_heard": [],
            "salience": "subject",
            "role": "client",
            "confidence": 0.95,
            "evidence": "the Meridian Health engagement",
        },
    ],
}


def _names(parsed):
    return sorted(e["canonical_name"] for e in parsed)


def test_parses_bare_json():
    parsed = parse_salient_entities(json.dumps(_PAYLOAD), ENTITY_TYPES)
    assert _names(parsed) == ["Elena Vasquez", "Meridian Health"]


def test_parses_fenced_json():
    text = "```json\n" + json.dumps(_PAYLOAD) + "\n```"
    parsed = parse_salient_entities(text, ENTITY_TYPES)
    assert _names(parsed) == ["Elena Vasquez", "Meridian Health"]


def test_parses_fenced_json_with_trailing_commentary():
    # The exact live failure shape.
    text = (
        "```json\n" + json.dumps(_PAYLOAD) + "\n```\n\n"
        '**Note:** I excluded "the team" from the final output per the '
        "instruction. The remaining entities are extracted."
    )
    parsed = parse_salient_entities(text, ENTITY_TYPES)
    assert _names(parsed) == ["Elena Vasquez", "Meridian Health"]


def test_parses_json_with_preamble_and_no_fence():
    text = "Here are the extracted entities:\n" + json.dumps(_PAYLOAD)
    parsed = parse_salient_entities(text, ENTITY_TYPES)
    assert _names(parsed) == ["Elena Vasquez", "Meridian Health"]


def test_garbage_degrades_to_empty():
    assert parse_salient_entities("no json here at all", ENTITY_TYPES) == []
    assert parse_salient_entities("", ENTITY_TYPES) == []
