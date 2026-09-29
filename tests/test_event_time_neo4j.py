"""ADR-004 against a real Neo4j: the Cypher, the stored types, and rebuild parity.

Builds a small corpus on disk (meetings dated January to April, all "ingested"
on one day in September — a backfill), builds the graph from it with the real
graph service, and asks point-in-time questions.

Every node this test writes carries a run-unique token in its id, and only
those nodes are removed afterwards, so it is safe on a shared database.
"""

import json
import os
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
import yaml

from app.model_schemas.domain_config import (
    DomainAttribute,
    DomainConfiguration,
    DomainEntity,
    DomainRelationship,
)
from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.graph.neo4j_graph import Neo4jKnowledgeGraph
from app.services.graph.neo4j_schema import generate_schema_from_domain
from app.services.graph.signal_graph_writer import SignalGraphWriter
from app.services.temporal_queries import TemporalQueryService
from app.utils.event_time import ASSERTIONS_KEY

pytestmark = [pytest.mark.requires_neo4j, pytest.mark.asyncio]

INGESTED = "2026-09-27T22:00:00+00:00"  # everything was recorded on one day


def _domain() -> DomainConfiguration:
    name = [DomainAttribute(name="name", type="string", required=True)]
    return DomainConfiguration(
        id="event_time_test",
        name="Event time test",
        entities={
            "person": DomainEntity(
                name="person", description="p", plural="people", attributes=name,
                relationships=[
                    DomainRelationship(
                        type="works_on_projects", target="project",
                        cardinality="many-to-many", inverse_name="has_team_members",
                    ),
                    DomainRelationship(
                        type="reports_to", target="person", cardinality="many-to-one",
                    ),
                ],
            ),
            "project": DomainEntity(
                name="project", description="p", plural="projects", attributes=name,
                relationships=[
                    DomainRelationship(
                        type="has_team_members", target="person",
                        cardinality="many-to-many", inverse_name="works_on_projects",
                    ),
                ],
            ),
            "account": DomainEntity(
                name="account", description="a", plural="accounts", attributes=name,
                relationships=[],
            ),
        },
    )


class _File:
    def __init__(self, path, content):
        self.path, self.content = path, content


class World:
    """A corpus on disk plus the graph built from it."""

    def __init__(self, root, client):
        self.root = root
        self.client = client
        self.token = "et" + uuid.uuid4().hex[:10]
        self.kg = Neo4jKnowledgeGraph(neo4j_client=client, domain_config=_domain())
        git = MagicMock()
        git.repo_path = str(root)
        git.commit_and_push = AsyncMock()
        git.invalidate_markdown_files_cache = MagicMock()
        git.read_markdown_files = AsyncMock(side_effect=self._read)
        self.kg._git_ops = git
        self.kg._record_type_usage = AsyncMock()
        self.kg._is_entity_archived = lambda _eid: False
        self.kg.process_stubs = AsyncMock(return_value={})
        self.svc = TemporalQueryService(client)
        self.signals: list[MeetingSignals] = []

    # ids -------------------------------------------------------------
    def eid(self, kind, name):
        return f"{kind}-{self.token}-{name}"

    def doc(self, bot):
        return f"doc:meetings/meeting-{self.token}-{bot}.md"

    # files -----------------------------------------------------------
    async def _read(self, wanted=None):
        files = []
        for base, _dirs, names in os.walk(self.root):
            for name in sorted(names):
                if name.endswith(".md"):
                    full = os.path.join(base, name)
                    rel = os.path.relpath(full, self.root)
                    if wanted is None or rel in wanted:
                        files.append(_File(rel, open(full, encoding="utf-8").read()))
        return files

    def meeting(self, bot, when, entities, title, time_source=None):
        meta = {
            "meeting_id": f"m-{bot}",
            "bot_id": f"{self.token}-{bot}",
            "updated_at": when,
            "title": title,
            "start_time": when,
            "recorded_at": INGESTED,
            "entity_ids": [self.eid(*e) for e in entities],
        }
        if time_source:
            meta["time_source"] = time_source
        path = self.root / "meetings" / f"meeting-{self.token}-{bot}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\n" + yaml.dump(meta, sort_keys=False) + "---\n\nbody\n")

    def entity(self, kind, name, folder, **frontmatter):
        eid = self.eid(kind, name)
        meta = {"id": eid, "entity_type": kind, "name": f"{name.title()} {self.token}", **frontmatter}
        path = self.root / folder / f"{self.token}-{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\n" + yaml.dump(meta, sort_keys=False) + "---\n\n# profile\n")
        return path

    def assertion(self, rel, target, bot, when, time_source="explicit"):
        return {
            "type": rel, "target": self.eid(*target), "source_id": self.doc(bot),
            "occurred_at": when, "time_source": time_source, "recorded_at": INGESTED,
        }

    def signal(self, name, bot, when, entities, kind="decision", **kw):
        sig = Signal(
            id=f"{self.token}-sig-{name}", type=kind, content=f"{name} content",
            source_meeting_id=f"{self.token}-{bot}", source_timestamp=when,
            created_at=INGESTED,
            entities=[EntityRef(id=self.eid(*e), type=e[0], name=e[1]) for e in entities],
            **kw,
        )
        self.signals.append(
            MeetingSignals(meeting_id=f"m-{bot}", bot_id=f"{self.token}-{bot}", signals=[sig])
        )
        return sig

    # graph -----------------------------------------------------------
    async def build(self):
        files = await self._read()
        await self.kg._ingest_documents_batched([(f.path, f.content) for f in files])
        await self.kg._build_co_occurrence_relationships()
        writer = SignalGraphWriter(self.client)
        for batch in self.signals:
            await writer.write_meeting_signals(batch)

    async def edges(self):
        return await self.client.execute_read(
            "MATCH (a:Entity)-[r]->(b:Entity) "
            "WHERE a.id CONTAINS $t AND b.id CONTAINS $t AND type(r) <> 'CO_OCCURRENCE' "
            "RETURN a.id AS a, type(r) AS t, b.id AS b, r.source_id AS source_id, "
            "r.occurred_at AS occurred_at, r.time_source AS time_source "
            "ORDER BY a, t, b, source_id",
            {"t": self.token},
        )

    async def cleanup(self):
        await self.client.execute_write(
            "MATCH (n) WHERE n.id CONTAINS $t DETACH DELETE n", {"t": self.token}
        )


@pytest_asyncio.fixture
async def world(tmp_path):
    from app.neo4j_client import Neo4jClient

    # A dedicated client, not the module singleton (see
    # test_type_registry_serialization: the singleton binds to the first loop).
    client = Neo4jClient()
    await client.initialize()
    await client.execute_many(generate_schema_from_domain(_domain()))
    w = World(tmp_path, client)
    try:
        yield w
    finally:
        await w.cleanup()
        await client.close()


ALICE, BOB, CARA = ("person", "alice"), ("person", "bob"), ("person", "cara")
ATLAS, ACME = ("project", "atlas"), ("account", "acme")


async def _story(w: World):
    """January: Alice, Atlas and Acme. February: Bob joins. April: Cara, and
    the January decision is replaced. Alice's relationships are dated by the
    meetings that stated them; one is restated in April."""
    w.meeting("jan", "2026-01-12T10:00:00-05:00", [ALICE, ATLAS, ACME], "Kickoff", "explicit")
    w.meeting("feb", "2026-02-10T15:00:00+00:00", [ALICE, BOB, ATLAS], "Staffing", "content_header")
    w.meeting("apr", "2026-04-02T09:00:00-04:00", [ALICE, CARA, ATLAS], "Review")
    w.entity("person", "alice", "people",
             works_on_projects=[w.eid(*ATLAS)], reports_to=[w.eid(*BOB), w.eid(*CARA)],
             **{ASSERTIONS_KEY: [
                 w.assertion("works_on_projects", ATLAS, "jan", "2026-01-12T10:00:00-05:00"),
                 w.assertion("works_on_projects", ATLAS, "apr", "2026-04-02T09:00:00-04:00"),
                 w.assertion("reports_to", BOB, "feb", "2026-02-10T15:00:00+00:00", "inferred"),
                 # reports_to Cara has no assertion: an unattributed edge
             ]})
    w.entity("person", "bob", "people")
    w.entity("person", "cara", "people")
    w.entity("project", "atlas", "projects")
    w.entity("account", "acme", "accounts")
    w.signal("postgres", "jan", "2026-01-12T10:00:00-05:00", [ATLAS],
             valid_to="2026-04-02T13:00:00+00:00", provenance_status="superseded",
             superseded_by=f"{w.token}-sig-mysql")
    w.signal("mysql", "apr", "2026-04-02T09:00:00-04:00", [ATLAS])
    w.signal("hire", "feb", "2026-02-10T15:00:00+00:00", [ALICE, BOB], kind="action_item")
    await w.build()


# ---------------------------------------------------------------------------
# What is stored
# ---------------------------------------------------------------------------


async def test_times_are_stored_as_datetime_in_utc(world):
    await _story(world)
    rows = await world.client.execute_read(
        "MATCH (d:Document {id: $d}) MATCH (s:Signal {id: $s}) "
        "MATCH (:Entity {id: $a})-[r:WORKS_ON_PROJECTS {source_id: $d}]->() "
        "RETURN valueType(d.occurred_at) AS doc, valueType(d.recorded_at) AS doc_rec, "
        "valueType(s.valid_from) AS sig_from, valueType(s.valid_to) AS sig_to, "
        "valueType(s.occurred_at) AS sig, valueType(r.occurred_at) AS edge, "
        "d.occurred_at AS occurred_at, d.time_source AS time_source, "
        "valueType(s.source_timestamp) AS mirror",
        {"d": world.doc("jan"), "s": f"{world.token}-sig-postgres", "a": world.eid(*ALICE)},
    )
    (row,) = rows
    for key in ("doc", "doc_rec", "sig_from", "sig_to", "sig", "edge"):
        assert row[key].startswith("ZONED DATETIME"), (key, row[key])
    assert row["mirror"].startswith("STRING")  # the file mirror is untouched
    # 10:00-05:00 was written; the instant is 15:00 UTC, returned as a string.
    assert row["occurred_at"] == "2026-01-12T15:00:00+00:00"
    assert row["time_source"] == "explicit"


async def test_one_edge_per_assertion(world):
    await _story(world)
    a, atlas, bob, cara = (world.eid(*x) for x in (ALICE, ATLAS, BOB, CARA))
    got = [(e["a"], e["t"], e["b"], e["source_id"], e["occurred_at"]) for e in await world.edges()]
    assert got == sorted([
        (a, "REPORTS_TO", bob, world.doc("feb"), "2026-02-10T15:00:00+00:00"),
        (a, "REPORTS_TO", cara, "", None),
        (a, "WORKS_ON_PROJECTS", atlas, world.doc("apr"), "2026-04-02T13:00:00+00:00"),
        (a, "WORKS_ON_PROJECTS", atlas, world.doc("jan"), "2026-01-12T15:00:00+00:00"),
        (atlas, "HAS_TEAM_MEMBERS", a, world.doc("apr"), "2026-04-02T13:00:00+00:00"),
        (atlas, "HAS_TEAM_MEMBERS", a, world.doc("jan"), "2026-01-12T15:00:00+00:00"),
    ], key=lambda e: (e[0], e[1], e[2], e[3]))


async def test_rebuilding_twice_adds_nothing(world):
    """Idempotency (invariant 3): the same files give the same edges."""
    await _story(world)
    first = await world.edges()
    await world.build()
    await world.build()
    assert await world.edges() == first


async def test_live_write_through_equals_rebuild(world):
    """A relationship written during ingest, then rebuilt from the file it
    wrote, is the same edge."""
    await _story(world)
    path = world.root / "people" / f"{world.token}-bob.md"
    world.kg._find_entity_file = lambda _eid: str(path)
    rel = await world.kg.add_frontmatter_relationships(
        world.eid(*BOB),
        {"works_on_projects": [world.eid(*ATLAS)]},
        evidence={"source_id": world.doc("feb"), "occurred_at": "2026-02-10T15:00:00+00:00",
                  "recorded_at": INGESTED, "time_source": "content_header"},
    )
    assert rel
    await world.kg.ingest_files([rel])
    live = [e for e in await world.edges() if e["a"] == world.eid(*BOB)]
    assert [(e["t"], e["source_id"], e["occurred_at"], e["time_source"]) for e in live] == [
        ("WORKS_ON_PROJECTS", world.doc("feb"), "2026-02-10T15:00:00+00:00", "content_header")]

    await world.client.execute_write(
        "MATCH (:Entity {id: $b})-[r]->() DELETE r", {"b": world.eid(*BOB)})
    await world.build()
    assert [e for e in await world.edges() if e["a"] == world.eid(*BOB)] == live


# ---------------------------------------------------------------------------
# Point in time
# ---------------------------------------------------------------------------


async def test_rewind_shows_only_what_had_happened(world):
    await _story(world)
    svc, alice = world.svc, world.eid(*ALICE)

    assert await svc.entity_at(alice, "2026-01-01") is None  # before anything

    january = await svc.entity_at(alice, "2026-01-31")
    assert january["evidence"] == {"documents": 1, "signals": 0, "relationship_assertions": 2}
    assert january["first_seen"] == "2026-01-12T15:00:00+00:00"

    march = await svc.entity_at(alice, "2026-03-01")
    assert march["evidence"]["documents"] == 2
    assert march["evidence"]["signals"] == 1
    assert march["last_seen"] == "2026-02-10T15:00:00+00:00"

    today = await svc.entity_at(alice, "2026-09-29")
    assert today["evidence"]["documents"] == 3
    assert today["first_seen"] == "2026-01-12T15:00:00+00:00"


async def test_backfill_is_placed_by_event_time(world):
    """Everything was ingested in September. Bob appears in February, Cara in
    April — not in September."""
    await _story(world)
    svc = world.svc
    assert await svc.entity_at(world.eid(*BOB), "2026-02-01") is None
    assert await svc.entity_at(world.eid(*BOB), "2026-02-11") is not None
    assert await svc.entity_at(world.eid(*CARA), "2026-03-31") is None
    assert await svc.entity_at(world.eid(*CARA), "2026-04-03") is not None


async def test_instant_comparison_across_offsets(world):
    """The April meeting is 09:00-04:00 = 13:00 UTC. As text it sorts before
    '…T12:00:00+00:00'; as an instant it is after."""
    await _story(world)
    svc, cara = world.svc, world.eid(*CARA)
    assert await svc.entity_at(cara, "2026-04-02T12:00:00+00:00") is None
    assert await svc.entity_at(cara, "2026-04-02T13:00:00+00:00") is not None
    assert await svc.entity_at(cara, "2026-04-02T08:59:00-04:00") is None
    assert await svc.entity_at(cara, "2026-04-02T09:00:00-04:00") is not None


async def test_relationships_at_a_point_in_time(world):
    await _story(world)
    svc, alice = world.svc, world.eid(*ALICE)

    def typed(rels):
        return sorted(
            (r["relationship_type"], r["direction"], r["other_id"], r["assertions"])
            for r in rels if not r["derived"]
        )

    january = await svc.relationships_at(alice, "2026-01-31")
    assert typed(january) == [
        ("has_team_members", "incoming", world.eid(*ATLAS), 1),
        ("works_on_projects", "outgoing", world.eid(*ATLAS), 1),
    ]
    march = await svc.relationships_at(alice, "2026-03-01")
    assert ("reports_to", "outgoing", world.eid(*BOB), 1) in typed(march)

    today = await svc.relationships_at(alice, "2026-09-29")
    works = next(r for r in today if r["relationship_type"] == "works_on_projects")
    assert works["assertions"] == 2
    assert works["valid_from"] == "2026-01-12T15:00:00+00:00"
    assert works["last_asserted"] == "2026-04-02T13:00:00+00:00"
    assert set(works["sources"]) == {world.doc("apr"), world.doc("jan")}
    reports = next(r for r in today if r["relationship_type"] == "reports_to")
    assert reports["time_sources"] == ["inferred"]

    # The undated edge to Cara is in the current graph and in no rewind.
    assert all(r["other_id"] != world.eid(*CARA) for r in today if not r["derived"])
    assert await svc.undated_relationships(alice) == 1


async def test_co_mentions_are_derived_from_dated_documents(world):
    await _story(world)
    svc, alice = world.svc, world.eid(*ALICE)

    def co(rels):
        return {r["other_id"]: r["assertions"] for r in rels if r["derived"]}

    assert co(await svc.relationships_at(alice, "2026-01-31")) == {
        world.eid(*ATLAS): 1, world.eid(*ACME): 1}
    assert co(await svc.relationships_at(alice, "2026-03-01")) == {
        world.eid(*ATLAS): 2, world.eid(*ACME): 1, world.eid(*BOB): 1}
    # The entity's own profile document is not evidence of anything.
    assert world.eid(*ALICE) not in co(await svc.relationships_at(alice, "2026-09-29"))


async def test_standing_signals_follow_supersession(world):
    await _story(world)
    svc, atlas = world.svc, world.eid(*ATLAS)

    def ids(state):
        return sorted(s["id"].rsplit("-", 1)[1] for s in state["standing_signals"])

    assert ids(await svc.entity_at(atlas, "2026-03-01")) == ["postgres"]
    assert ids(await svc.entity_at(atlas, "2026-04-02T12:59:59Z")) == ["postgres"]
    # The old decision's window closes at the instant the new one opens.
    assert ids(await svc.entity_at(atlas, "2026-04-02T13:00:00Z")) == ["mysql"]
    assert ids(await svc.entity_at(atlas, "2026-09-29")) == ["mysql"]


async def test_what_changed_between_two_dates(world):
    await _story(world)
    result = await world.svc.what_changed_between(world.eid(*ALICE), "2026-02-01", "2026-03-01")
    assert sorted(c["kind"] for c in result["changes"]) == [
        "co_mentioned", "document", "relationship", "signal"]
    assert {c["at"] for c in result["changes"]} == {"2026-02-10T15:00:00+00:00"}
    relationship = next(c for c in result["changes"] if c["kind"] == "relationship")
    assert relationship["relationship_type"] == "reports_to"
    assert relationship["other_id"] == world.eid(*BOB)
    assert relationship["sources"] == [world.doc("feb")]
    assert result["recorded_late"] == []

    # A relationship restated in April is not new in April.
    april = await world.svc.what_changed_between(world.eid(*ALICE), "2026-03-01", "2026-05-01")
    assert [c for c in april["changes"] if c["kind"] == "relationship"] == []
    assert [c["other_id"] for c in april["changes"] if c["kind"] == "co_mentioned"] == [
        world.eid(*CARA)]


async def test_superseded_signal_is_a_change(world):
    await _story(world)
    result = await world.svc.what_changed_between(world.eid(*ATLAS), "2026-03-01", "2026-05-01")
    kinds = {c["kind"]: c for c in result["changes"]}
    assert kinds["signal"]["id"].endswith("sig-mysql")
    assert kinds["signal_superseded"]["id"].endswith("sig-postgres")
    assert kinds["signal_superseded"]["at"] == "2026-04-02T13:00:00+00:00"


async def test_recorded_late_lists_the_backfill(world):
    """Asked in September what was learned since August: nothing happened in
    that window, but three meetings about earlier months were added."""
    await _story(world)
    result = await world.svc.what_changed_between(world.eid(*ALICE), "2026-08-01", "2026-09-29")
    assert result["changes"] == []
    assert [d["id"] for d in result["recorded_late"]] == [
        world.doc("jan"), world.doc("feb"), world.doc("apr")]
    assert [d["time_source"] for d in result["recorded_late"]] == [
        "explicit", "content_header", "unrecorded"]


async def test_graph_grows_month_by_month(world):
    await _story(world)
    sizes = []
    for when in ("2026-01-01", "2026-01-31", "2026-03-01", "2026-05-01", "2026-09-29"):
        graph = await world.svc.graph_as_of(world.eid(*ALICE), when, depth=2)
        sizes.append((len(graph["nodes"]), len(graph["edges"])))
    assert sizes[0] == (0, 0)
    assert [n for n, _ in sizes] == [0, 3, 4, 5, 5]
    assert all(a[1] <= b[1] for a, b in zip(sizes, sizes[1:], strict=False))
    march = await world.svc.graph_as_of(world.eid(*ALICE), "2026-03-01", depth=2)
    assert {n["id"] for n in march["nodes"]} == {
        world.eid(*x) for x in (ALICE, ATLAS, ACME, BOB)}


async def test_blast_radius_uses_stated_relationships_only(world):
    await _story(world)
    result = await world.svc.temporal_blast_radius(world.eid(*ALICE), "2026-03-01", max_depth=3)
    assert result["depth_map"] == {
        world.eid(*ALICE): 0, world.eid(*ATLAS): 1, world.eid(*BOB): 1}


async def test_provenance_is_in_event_order(world):
    await _story(world)
    history = (await world.svc.provenance(world.eid(*ALICE)))["history"]
    dated = [h for h in history if h["timestamp"]]
    assert [h["timestamp"] for h in dated] == sorted(h["timestamp"] for h in dated)
    assert [h["source"] for h in dated if h["action"] == "MENTIONED_IN"] == [
        f"meetings/meeting-{world.token}-{b}.md" for b in ("jan", "feb", "apr")]
    assert all(h["recorded_at"] == INGESTED for h in dated)
    # The profile document mentions Alice but carries no event time: listed last.
    assert history[-1]["timestamp"] == ""


async def test_lookup_by_name(world):
    await _story(world)
    state = await world.svc.entity_at(f"alice {world.token}", "2026-03-01")
    assert state["id"] == world.eid(*ALICE)


async def test_json_round_trip(world):
    """Nothing the service returns is a driver type."""
    await _story(world)
    alice = world.eid(*ALICE)
    for value in (
        await world.svc.entity_at(alice, "2026-09-29"),
        await world.svc.relationships_at(alice, "2026-09-29"),
        await world.svc.what_changed_between(alice, "2026-01-01", "2026-09-29"),
        await world.svc.graph_as_of(alice, "2026-09-29"),
        await world.svc.provenance(alice),
    ):
        json.dumps(value)


async def test_contradictions_window_is_typed(world, monkeypatch):
    await _story(world)
    from app.services import temporal_queries as tq

    store = MagicMock()
    store.find_signal_by_id.return_value = None
    monkeypatch.setattr(tq, "signal_store", store)
    atlas = world.eid(*ATLAS)
    every = await world.svc.find_contradictions(atlas)
    assert every["signals_analyzed"] == 2
    window = await world.svc.find_contradictions(
        atlas, date_from=datetime(2026, 3, 1, tzinfo=UTC), date_to=datetime(2026, 5, 1, tzinfo=UTC))
    assert window["signals_analyzed"] == 1
