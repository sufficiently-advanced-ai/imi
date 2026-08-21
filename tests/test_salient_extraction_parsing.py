"""Salient-extraction JSON parsing robustness (regression).

At production temperature the model occasionally wraps its JSON in markdown
fences AND appends commentary after the closing fence ("**Note:** I excluded
'the team'..."). The old anchored fence regex required the fence to span the
whole response, so one editorial note cost an entire meeting's entities
(observed live: eval fixture scored recall 0.000 on such a run, and ingest
logs the same shape as "Salient extraction returned nothing").
"""

import json

from app.services.salient_entity_extractor import (
    MAX_NEW_PROMOTED_PER_DOC,
    filter_salient_entities,
    parse_salient_entities,
)

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


# --- per-doc promotion cap -------------------------------------------------


def _labeled(name, salience="subject", confidence=0.5, etype="topic"):
    return {
        "type": etype,
        "canonical_name": name,
        "salience": salience,
        "confidence": confidence,
    }


class _Resolved:
    def __init__(self, canonical_name, matched_via):
        self.canonical_name = canonical_name
        self.matched_via = matched_via


class _Resolver:
    """Resolves anything in `known` to an existing entity, else 'new'."""

    def __init__(self, known=()):
        self.known = set(known)

    def resolve(self, _type, name):
        via = "exact" if name in self.known else "new"
        return _Resolved(name, via)


def test_new_promotions_are_capped_per_doc():
    labeled = [_labeled(f"Topic {i}") for i in range(MAX_NEW_PROMOTED_PER_DOC + 4)]
    promoted = filter_salient_entities(labeled, resolver=_Resolver())
    assert len(promoted) == MAX_NEW_PROMOTED_PER_DOC


def test_cap_keeps_the_highest_confidence_entities():
    # Ascending confidence in input order — the cap must not just take the
    # first N as supplied.
    labeled = [
        _labeled(f"Topic {i}", confidence=i / 100)
        for i in range(MAX_NEW_PROMOTED_PER_DOC + 4)
    ]
    promoted = filter_salient_entities(labeled, resolver=_Resolver())
    kept = {e["canonical_name"] for e in promoted}
    expected = {
        f"Topic {i}"
        for i in range(len(labeled) - MAX_NEW_PROMOTED_PER_DOC, len(labeled))
    }
    assert kept == expected


def test_existing_entities_are_exempt_from_the_cap():
    """Linking to already-known entities is never pollution, so it must not
    consume the new-entity budget."""
    known = [f"Known {i}" for i in range(5)]
    labeled = [_labeled(n) for n in known] + [
        _labeled(f"Fresh {i}") for i in range(MAX_NEW_PROMOTED_PER_DOC)
    ]
    promoted = filter_salient_entities(labeled, resolver=_Resolver(known=known))
    names = {e["canonical_name"] for e in promoted}
    assert set(known) <= names, "resolver-matched entities were dropped by the cap"
    assert len(promoted) == len(known) + MAX_NEW_PROMOTED_PER_DOC


def test_mentions_are_dropped_without_a_resolver():
    labeled = [_labeled("Passing Co", salience="mention")]
    assert filter_salient_entities(labeled, resolver=None) == []
