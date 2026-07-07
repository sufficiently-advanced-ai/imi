"""Tests for entity-name validation that keeps extraction junk out of the graph.

The salient extractor / NER occasionally emit junk "entities": phone numbers
typed as person, and transcript fragments with embedded newlines that slugify
into IDs like ``person-order\\r\\nto`` or ``person-range-control...``. These
become permanent graph nodes + stub files. ``is_valid_entity_name`` is the
single shared predicate that gates both the extractor and the ingest path.
"""

import pytest

from app.services.entity_utils import is_valid_entity_name


@pytest.mark.parametrize(
    "name",
    [
        "Jeff Jennings",
        "Acme Corp",
        "International Brotherhood of Electrical Workers",
        "Jean-Luc Picard",
        "O'Brien",
        "C-3PO",  # has digits but also letters
        "3M",
        "iPhone 15",
        "AT&T",
    ],
)
def test_valid_names_accepted(name):
    assert is_valid_entity_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "",  # empty
        "   ",  # whitespace only
        "+1-571-583-8135",  # phone number (no alpha)
        "1-800-858-3616",  # phone number
        "12345",  # numeric only
        "!!!",  # punctuation only
        "range-control\r\nelectrical-equipment-repair",  # newline contamination
        "main-st\nlocation",
        "future-business\n\nis",
        "improvised-electronics\r\n\r\nyou",
        "tab\tseparated",  # control char (tab)
        "x" * 101,  # absurdly long (sentence fragment, not a name)
    ],
)
def test_junk_names_rejected(name):
    assert is_valid_entity_name(name) is False


def test_non_string_rejected():
    assert is_valid_entity_name(None) is False
    assert is_valid_entity_name(12345) is False


def test_boundary_length_accepted():
    # Exactly 100 chars with letters is allowed; 101 is not.
    assert is_valid_entity_name("a" * 100) is True
    assert is_valid_entity_name("a" * 101) is False
