"""Point-in-time queries (ADR-004): how answers are assembled from evidence.

The graph client is faked with canned rows per query, so these cover the
service's logic. The Cypher itself runs against a real database in
tests/test_event_time_neo4j.py.
"""

from datetime import UTC, datetime

import pytest

from app.services import temporal_queries as tq
from app.services.temporal_queries import TemporalQueryService

T_MARCH = datetime(2026, 3, 1, tzinfo=UTC)
JAN = "2026-01-12T15:00:00+00:00"
FEB = "2026-02-10T15:00:00+00:00"
APR = "2026-04-02T15:00:00+00:00"


class FakeGraph:
    """Answers each query constant with rows built from the params."""

    def __init__(self, entities=None, **handlers):
        self.entities = entities or {}
        self.handlers = handlers
        self.calls: list[tuple[str, dict]] = []

    async def execute_read(self, query, params=None):
        params = params or {}
        self.calls.append((query, params))
        if query == tq._RESOLVE:
            lookup = params["lookup"]
            for eid, node in self.entities.items():
                if eid == lookup or node["name"].lower() == lookup.lower():
                    return [{"id": eid, "name": node["name"],
                             "entity_type": node.get("type", "person"),
                             "props": node.get("props", {"id": eid, "name": node["name"]})}]
            return []
        for name, handler in self.handlers.items():
            if query == getattr(tq, name):
                return handler(params)
        return []


def _span(n, first=None, last=None):
    return [{"n": n, "first": first, "last": last}]


ALICE = {"person-alice": {"name": "Alice", "props": {
    "id": "person-alice", "name": "Alice", "entity_type": "person",
    "canonical_name": "alice", "updated_at": "2026-09-27", "stub": False, "title": "VP",
}}}


# ---------------------------------------------------------------------------
# entity_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_with_no_evidence_by_then_is_unknown():
    """The node exists today, but nothing dated by T mentions it."""
    graph = FakeGraph(ALICE)
    assert await TemporalQueryService(graph).entity_at("person-alice", T_MARCH) is None


@pytest.mark.asyncio
async def test_missing_entity_is_unknown():
    assert await TemporalQueryService(FakeGraph()).entity_at("person-nobody", T_MARCH) is None


@pytest.mark.asyncio
async def test_entity_state_is_assembled_from_evidence():
    standing = [{"id": "s1", "type": "decision", "content": "Use Postgres",
                 "valid_from": JAN, "valid_to": None,
                 "source_meeting_id": "a", "source_meeting_title": "Kickoff"}]
    graph = FakeGraph(
        ALICE,
        _DOCUMENT_EVIDENCE=lambda p: _span(2, JAN, FEB),
        _SIGNAL_EVIDENCE=lambda p: _span(5, "2026-01-12T15:00:01+00:00", FEB),
        _ASSERTION_EVIDENCE=lambda p: _span(0),
        _STANDING_SIGNALS=lambda p: standing,
    )
    state = await TemporalQueryService(graph).entity_at("Alice", "2026-03-01T00:00:00Z")
    assert state["id"] == "person-alice"
    assert state["as_of"] == "2026-03-01T00:00:00+00:00"
    assert state["first_seen"] == JAN
    assert state["last_seen"] == FEB
    assert state["evidence"] == {"documents": 2, "signals": 5, "relationship_assertions": 0}
    assert state["standing_signals"] == standing
    # Identity and bookkeeping keys are not attributes; attributes are current.
    assert state["attributes"] == {"title": "VP"}
    assert state["attributes_are_current"] is True


@pytest.mark.asyncio
async def test_relationship_assertion_alone_is_evidence():
    graph = FakeGraph(ALICE, _ASSERTION_EVIDENCE=lambda p: _span(1, JAN, JAN))
    state = await TemporalQueryService(graph).entity_at("person-alice", T_MARCH)
    assert state["evidence"]["relationship_assertions"] == 1
    assert state["first_seen"] == JAN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "given, expected",
    [
        ("2026-03-01T00:00:00Z", datetime(2026, 3, 1, tzinfo=UTC)),
        ("2026-03-01", datetime(2026, 3, 1, tzinfo=UTC)),
        ("2026-02-28T19:00:00-05:00", datetime(2026, 3, 1, tzinfo=UTC)),
        (datetime(2026, 3, 1), datetime(2026, 3, 1, tzinfo=UTC)),
    ],
)
async def test_times_reach_the_graph_typed_and_in_utc(given, expected):
    graph = FakeGraph(ALICE, _DOCUMENT_EVIDENCE=lambda p: _span(1, JAN, JAN))
    await TemporalQueryService(graph).entity_at("person-alice", given)
    sent = [p["at"] for _q, p in graph.calls if "at" in p]
    assert sent and all(v == expected and v.tzinfo is not None for v in sent)


@pytest.mark.asyncio
async def test_unreadable_time_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="timestamp"):
        await TemporalQueryService(FakeGraph(ALICE)).entity_at("person-alice", "last tuesday")


# ---------------------------------------------------------------------------
# relationships_at
# ---------------------------------------------------------------------------


def _typed(rel, other, first, last=None, n=1, sources=("doc:a",)):
    return {"rel_type": rel, "other_id": other, "other_name": other.split("-", 1)[1].title(),
            "other_type": other.split("-")[0], "assertions": n, "first": first,
            "last": last or first, "sources": list(sources), "time_sources": ["explicit"]}


def _co(other, first, shared=1):
    return {"other_id": other, "other_name": other.split("-", 1)[1].title(),
            "other_type": other.split("-")[0], "shared_documents": shared,
            "first": first, "last": first}


@pytest.mark.asyncio
async def test_relationships_report_direction_and_evidence():
    graph = FakeGraph(
        ALICE,
        _TYPED_OUT=lambda p: [_typed("WORKS_ON_PROJECTS", "project-atlas", JAN, FEB, n=2,
                                     sources=("doc:a", "doc:b", ""))],
        _TYPED_IN=lambda p: [_typed("REPORTS_TO", "person-bob", FEB)],
        _CO_MENTIONED=lambda p: [_co("account-acme", JAN, shared=3)],
    )
    rels = await TemporalQueryService(graph).relationships_at("person-alice", T_MARCH)

    out, incoming, co = rels
    assert out["relationship_type"] == "works_on_projects"
    assert (out["direction"], out["source_id"], out["target_id"]) == (
        "outgoing", "person-alice", "project-atlas")
    assert (out["assertions"], out["valid_from"], out["last_asserted"]) == (2, JAN, FEB)
    assert out["sources"] == ["doc:a", "doc:b"]  # the empty source is dropped
    assert out["derived"] is False

    assert (incoming["direction"], incoming["source_id"], incoming["target_id"]) == (
        "incoming", "person-bob", "person-alice")

    assert co["relationship_type"] == "co_mentioned"
    assert co["derived"] is True
    assert co["assertions"] == 3
    assert co["other_id"] == "account-acme"


@pytest.mark.asyncio
async def test_co_mentions_can_be_left_out():
    graph = FakeGraph(ALICE, _CO_MENTIONED=lambda p: [_co("account-acme", JAN)])
    svc = TemporalQueryService(graph)
    assert await svc.relationships_at("person-alice", T_MARCH, include_co_mentions=False) == []
    assert not any(q == tq._CO_MENTIONED for q, _ in graph.calls)


@pytest.mark.asyncio
async def test_point_in_time_never_reads_the_materialised_co_occurrence():
    """CO_OCCURRENCE is a current-state cache; using it would leak the present."""
    for name in ("_TYPED_OUT", "_TYPED_IN", "_ASSERTION_EVIDENCE", "_RELATIONSHIPS_IN_WINDOW", "_UNDATED"):
        assert "type(r) <> $derived" in getattr(tq, name), name
    graph = FakeGraph(ALICE)
    await TemporalQueryService(graph).relationships_at("person-alice", T_MARCH)
    assert {p["derived"] for _q, p in graph.calls if "derived" in p} == {"CO_OCCURRENCE"}
    assert "CO_OCCURRENCE" not in tq._CO_MENTIONED
    assert "MENTIONED_IN" in tq._CO_MENTIONED and "d.occurred_at <= $at" in tq._CO_MENTIONED


@pytest.mark.asyncio
async def test_undated_relationships_are_counted_separately():
    graph = FakeGraph(ALICE, _UNDATED=lambda p: [{"n": 4}])
    assert await TemporalQueryService(graph).undated_relationships("person-alice") == 4
    for name in ("_TYPED_OUT", "_TYPED_IN"):
        assert "r.occurred_at <= $at" in getattr(tq, name)


# ---------------------------------------------------------------------------
# what_changed
# ---------------------------------------------------------------------------


def _changes_graph():
    return FakeGraph(
        ALICE,
        _DOCUMENT_EVIDENCE=lambda p: _span(1, JAN, JAN),
        _DOCUMENTS_IN_WINDOW=lambda p: [
            {"id": "doc:b", "path": "meetings/b.md", "title": "Review", "occurred_at": APR,
             "recorded_at": APR, "time_source": "explicit"}],
        _SIGNALS_IN_WINDOW=lambda p: [
            {"id": "s2", "type": "decision", "content": "Use MySQL", "occurred_at": APR,
             "source_meeting_title": "Review"}],
        _SIGNALS_CLOSED_IN_WINDOW=lambda p: [
            {"id": "s1", "type": "decision", "content": "Use Postgres",
             "valid_to": "2026-04-02T15:00:01+00:00", "superseded_by": "s2"}],
        _RELATIONSHIPS_IN_WINDOW=lambda p: [
            {"rel_type": "REPORTS_TO", "other_id": "person-bob", "other_name": "Bob",
             "first": "2026-03-15T00:00:00+00:00", "sources": ["doc:c", ""]}],
        _CO_MENTIONED_IN_WINDOW=lambda p: [
            {"other_id": "account-acme", "other_name": "Acme", "other_type": "account",
             "first": "2026-03-20T00:00:00+00:00"}],
        _RECORDED_LATE=lambda p: [
            {"id": "doc:old", "path": "meetings/old.md", "title": "Backfilled",
             "occurred_at": JAN, "recorded_at": "2026-03-05T00:00:00+00:00",
             "time_source": "content_header"}],
    )


@pytest.mark.asyncio
async def test_changes_are_listed_in_event_order():
    result = await TemporalQueryService(_changes_graph()).what_changed_between(
        "person-alice", "2026-03-01", "2026-05-01")
    assert [(c["kind"], c["at"]) for c in result["changes"]] == [
        ("relationship", "2026-03-15T00:00:00+00:00"),
        ("co_mentioned", "2026-03-20T00:00:00+00:00"),
        ("document", APR),
        ("signal", APR),
        ("signal_superseded", "2026-04-02T15:00:01+00:00"),
    ]
    assert result["counts"] == {"relationship": 1, "co_mentioned": 1, "document": 1,
                                "signal": 1, "signal_superseded": 1}
    assert result["start"] == "2026-03-01T00:00:00+00:00"
    assert result["end"] == "2026-05-01T00:00:00+00:00"
    relationship = result["changes"][0]
    assert relationship["relationship_type"] == "reports_to"
    assert relationship["sources"] == ["doc:c"]
    assert result["changes"][-1]["superseded_by"] == "s2"


@pytest.mark.asyncio
async def test_backfilled_evidence_is_reported_as_recorded_late():
    """Something that happened before the window but was only ingested during
    it is not a change in the window — it is reported on its own."""
    result = await TemporalQueryService(_changes_graph()).what_changed_between(
        "person-alice", "2026-03-01", "2026-05-01")
    assert [d["id"] for d in result["recorded_late"]] == ["doc:old"]
    assert "doc:old" not in [c.get("id") for c in result["changes"]]
    assert "d.occurred_at <= $start" in tq._RECORDED_LATE
    assert "d.recorded_at > $start" in tq._RECORDED_LATE


@pytest.mark.asyncio
async def test_window_is_open_at_the_start_and_closed_at_the_end():
    for name in ("_DOCUMENTS_IN_WINDOW", "_SIGNALS_IN_WINDOW"):
        query = getattr(tq, name)
        assert "occurred_at > $start" in query and "occurred_at <= $end" in query
    assert "first > $start" in tq._RELATIONSHIPS_IN_WINDOW
    assert "first > $start" in tq._CO_MENTIONED_IN_WINDOW


@pytest.mark.asyncio
async def test_entity_first_heard_of_inside_the_window():
    graph = _changes_graph()
    graph.handlers["_DOCUMENT_EVIDENCE"] = lambda p: _span(0)
    result = await TemporalQueryService(graph).what_changed_between(
        "person-alice", "2026-03-01", "2026-05-01")
    assert result["first_known_in_window"] is True


@pytest.mark.asyncio
async def test_what_changed_runs_to_now():
    before = datetime.now(UTC)
    result = await TemporalQueryService(_changes_graph()).what_changed("person-alice", "2026-03-01")
    assert result["since"] == "2026-03-01T00:00:00+00:00"
    assert datetime.fromisoformat(result["now"]) >= before
    assert "start" not in result and "end" not in result
    assert len(result["changes"]) == 5


@pytest.mark.asyncio
async def test_changes_for_unknown_entity_or_backwards_window():
    svc = TemporalQueryService(FakeGraph(ALICE))
    missing = await svc.what_changed_between("person-nobody", "2026-03-01", "2026-05-01")
    assert missing["changes"] == [] and missing["error"] == "Entity not found"
    backwards = await svc.what_changed_between("person-alice", "2026-05-01", "2026-03-01")
    assert backwards["changes"] == [] and "before start" in backwards["error"]


# ---------------------------------------------------------------------------
# graph_as_of / temporal_blast_radius
# ---------------------------------------------------------------------------

WORLD = {
    "person-alice": {"name": "Alice"},
    "project-atlas": {"name": "Atlas", "type": "project"},
    "person-bob": {"name": "Bob"},
    "account-acme": {"name": "Acme", "type": "account"},
    "person-late": {"name": "Late"},
}
# Entities with evidence by T; person-late is only mentioned afterwards.
KNOWN = {"person-alice", "project-atlas", "person-bob", "account-acme"}
OUT = {
    "person-alice": [_typed("WORKS_ON_PROJECTS", "project-atlas", JAN),
                     _typed("COLLABORATES_WITH", "person-late", JAN)],
    "project-atlas": [_typed("MANAGED_BY", "person-bob", FEB)],
}
IN = {"project-atlas": [_typed("HAS_TEAM_MEMBERS", "person-alice", JAN)]}
CO = {"person-alice": [_co("account-acme", JAN)], "account-acme": [_co("person-alice", JAN)]}


def _world():
    return FakeGraph(
        WORLD,
        _DOCUMENT_EVIDENCE=lambda p: _span(1 if p["id"] in KNOWN else 0, JAN, JAN),
        _TYPED_OUT=lambda p: OUT.get(p["id"], []),
        _TYPED_IN=lambda p: IN.get(p["id"], []),
        _CO_MENTIONED=lambda p: CO.get(p["id"], []),
    )


@pytest.mark.asyncio
async def test_graph_as_of_follows_evidence_outward():
    result = await TemporalQueryService(_world()).graph_as_of("person-alice", T_MARCH, depth=2)
    assert [n["id"] for n in result["nodes"]] == [
        "person-alice", "project-atlas", "account-acme", "person-bob"]
    edges = {(e["source"], e["relationship_type"], e["target"]) for e in result["edges"]}
    assert ("person-alice", "works_on_projects", "project-atlas") in edges
    assert ("project-atlas", "managed_by", "person-bob") in edges
    assert ("person-alice", "has_team_members", "project-atlas") in edges
    assert result["timestamp"] == "2026-03-01T00:00:00+00:00"
    assert result["depth"] == 2
    first = next(e for e in result["edges"] if e["relationship_type"] == "works_on_projects")
    assert first["properties"] == {"first_asserted": JAN, "last_asserted": JAN,
                                   "assertions": 1, "derived": False}


@pytest.mark.asyncio
async def test_entity_not_yet_known_is_not_a_node():
    """An edge to something nothing had mentioned by T leads nowhere."""
    result = await TemporalQueryService(_world()).graph_as_of("person-alice", T_MARCH, depth=3)
    assert "person-late" not in [n["id"] for n in result["nodes"]]
    assert all("person-late" not in (e["source"], e["target"]) for e in result["edges"])


@pytest.mark.asyncio
async def test_co_mention_is_one_edge_seen_from_both_sides():
    result = await TemporalQueryService(_world()).graph_as_of("person-alice", T_MARCH, depth=2)
    co = [e for e in result["edges"] if e["relationship_type"] == "co_mentioned"]
    assert len(co) == 1 and co[0]["properties"]["derived"] is True


@pytest.mark.asyncio
async def test_depth_limits_the_traversal():
    svc = TemporalQueryService(_world())
    one = await svc.graph_as_of("person-alice", T_MARCH, depth=1)
    assert "person-bob" not in [n["id"] for n in one["nodes"]]
    zero = await svc.graph_as_of("person-alice", T_MARCH, depth=0)
    assert [n["id"] for n in zero["nodes"]] == ["person-alice"] and zero["edges"] == []


@pytest.mark.asyncio
async def test_graph_as_of_before_anything_was_known_is_empty():
    graph = FakeGraph(WORLD)
    result = await TemporalQueryService(graph).graph_as_of("person-alice", "2025-01-01")
    assert result["nodes"] == [] and result["edges"] == []


@pytest.mark.asyncio
async def test_blast_radius_follows_stated_relationships_only():
    result = await TemporalQueryService(_world()).temporal_blast_radius(
        "person-alice", T_MARCH, max_depth=3)
    assert result["depth_map"] == {"person-alice": 0, "project-atlas": 1, "person-bob": 2}
    assert "account-acme" not in result["depth_map"]  # co-mentioned, not related
    assert all(e["relationship_type"] != "co_mentioned" for e in result["edges"])
    assert result["at_time"] == "2026-03-01T00:00:00+00:00"
    assert result["max_depth"] == 3


@pytest.mark.asyncio
async def test_blast_radius_of_an_unknown_entity_is_empty():
    result = await TemporalQueryService(FakeGraph(WORLD)).temporal_blast_radius(
        "person-alice", T_MARCH)
    assert result == {"nodes": [], "edges": [], "depth_map": {}}


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provenance_reports_both_times_and_the_time_source():
    graph = FakeGraph(ALICE, _PROVENANCE=lambda p: [
        {"source": "meetings/a.md", "title": "Kickoff", "action": "MENTIONED_IN",
         "occurred_at": JAN, "recorded_at": "2026-09-27T22:00:00+00:00",
         "time_source": "explicit"},
        {"source": "s1", "title": "Use Postgres", "action": "MENTIONS",
         "occurred_at": JAN, "recorded_at": None, "time_source": None},
    ])
    result = await TemporalQueryService(graph).provenance("Alice")
    assert result["entity_id"] == "person-alice"
    assert result["history"] == [
        {"source": "meetings/a.md", "title": "Kickoff", "action": "MENTIONED_IN",
         "timestamp": JAN, "recorded_at": "2026-09-27T22:00:00+00:00", "time_source": "explicit"},
        {"source": "s1", "title": "Use Postgres", "action": "MENTIONS",
         "timestamp": JAN, "recorded_at": "", "time_source": ""},
    ]
    assert "ORDER BY occurred_at" in tq._PROVENANCE


@pytest.mark.asyncio
async def test_provenance_of_unknown_entity_is_empty():
    result = await TemporalQueryService(FakeGraph()).provenance("person-nobody")
    assert result == {"entity_id": "person-nobody", "history": []}


# ---------------------------------------------------------------------------
# No validity windows on entities
# ---------------------------------------------------------------------------


def test_no_query_reads_a_validity_window_from_an_entity_or_edge():
    """valid_from/valid_to exist on signals only (ADR-004 §1)."""
    import re

    for name, value in vars(tq).items():
        if not (name.startswith("_") and isinstance(value, str) and "MATCH" in value):
            continue
        for var in re.findall(r"\b([a-z])\.valid_(?:from|to)\b", value):
            assert var == "s", f"{name} reads a validity window from '{var}'"
