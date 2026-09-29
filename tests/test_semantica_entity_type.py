"""SemanticaKnowledge only indexes files whose type is a configured domain entity."""

from types import SimpleNamespace

import pytest

from app.services.semantica_knowledge import SemanticaKnowledge


def _kg(domain):
    kg = object.__new__(SemanticaKnowledge)
    kg.domain = domain
    return kg


DOMAIN = SimpleNamespace(entities={"person": SimpleNamespace(plural="people")})


@pytest.mark.parametrize(
    "path,metadata,expected",
    [
        ("x.md", {"type": "person"}, "person"),
        ("x.md", {"entity_type": "person"}, "person"),
        ("x.md", {"id": "person-tom"}, "person"),
        ("repo/people/tom.md", {}, "person"),
        ("x.md", {"type": "widget"}, None),  # graph writes this as a Document
        ("x.md", {"type": "widget", "id": "person-tom"}, "person"),
    ],
)
def test_entity_type_must_be_configured(path, metadata, expected):
    assert _kg(DOMAIN)._resolve_entity_type(path, metadata) == expected


def test_no_domain_means_no_entity_types():
    assert _kg(None)._resolve_entity_type("x.md", {"type": "person"}) is None


@pytest.mark.asyncio
async def test_unknown_type_is_skipped_not_failed():
    kg = _kg(DOMAIN)

    async def _no_query(*a, **k):
        raise AssertionError("an unconfigured type must not reach the graph query")

    kg._query = _no_query
    assert await kg.ingest_file("notes/x.md", "---\ntype: widget\nname: X\n---\nbody") is False
