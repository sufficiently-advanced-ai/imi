"""ADR-004: what the graph write path stores — one edge per assertion, DATETIME
values, and time data written through to the files a rebuild reads."""

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from app.models.signal import MeetingSignals, Signal
from app.services.graph.batch_writer import Neo4jBatchWriter
from app.services.graph.neo4j_graph import Neo4jKnowledgeGraph, edge_properties
from app.services.graph.neo4j_schema import generate_schema_from_domain
from app.services.graph.signal_graph_writer import SignalGraphWriter
from app.utils.event_time import ASSERTIONS_KEY
from tests.test_neo4j_graph import _make_domain_config

JUNE = datetime(2026, 6, 2, 14, 0, tzinfo=UTC)
AUGUST = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)


class _Client:
    def __init__(self):
        self.writes: list[tuple[str, dict]] = []

    async def execute_write(self, query, params=None):
        self.writes.append((query, params or {}))
        return [{"id": "x", "rel_type": "X"}]

    async def execute_read(self, query, params=None):
        return []


def _graph(client=None, repo=None):
    kg = Neo4jKnowledgeGraph(neo4j_client=client or _Client(), domain_config=_make_domain_config())
    git = MagicMock()
    git.repo_path = str(repo) if repo else "/tmp/none"
    git.commit_and_push = AsyncMock()
    kg._git_ops = git
    kg._record_type_usage = AsyncMock()
    return kg


# ---------------------------------------------------------------------------
# edge_properties
# ---------------------------------------------------------------------------


def test_edge_properties_keep_times_typed():
    props = edge_properties(
        {
            "source": "metadata",
            "source_id": "doc:meetings/meeting-a.md",
            "occurred_at": "2026-06-02T10:00:00-04:00",
            "recorded_at": datetime(2026, 9, 27, 12, 0),
            "time_source": "explicit",
        }
    )
    assert props["occurred_at"] == JUNE
    assert props["recorded_at"] == datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    assert props["source_id"] == "doc:meetings/meeting-a.md"
    assert props["time_source"] == "explicit"


def test_edge_properties_default_to_an_unattributed_edge():
    assert edge_properties(None) == {"source_id": ""}
    assert edge_properties({"strength": 0.5}) == {"strength": 0.5, "source_id": ""}
    # A time that does not parse is dropped, not stored as text.
    assert "occurred_at" not in edge_properties({"occurred_at": "soon"})


# ---------------------------------------------------------------------------
# One edge per assertion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relationship_merge_is_keyed_on_source():
    client = _Client()
    kg = _graph(client)
    await kg._upsert_relationship(
        "person-alice",
        "project-atlas",
        "HAS_PROJECTS",
        {"source_id": "doc:meetings/meeting-a.md", "occurred_at": JUNE},
    )
    query, params = client.writes[0]
    assert "MERGE (a)-[r:HAS_PROJECTS {source_id: $source_id}]->(b)" in query
    assert params["source_id"] == "doc:meetings/meeting-a.md"
    assert params["props"]["occurred_at"] == JUNE


def test_batch_writer_keeps_one_row_per_source():
    batch = Neo4jBatchWriter(_Client())
    for source, when in (("doc:a", JUNE), ("doc:b", AUGUST), ("doc:a", JUNE)):
        batch.add_relationship(
            "person-alice", "project-atlas", "HAS_PROJECTS",
            {"source_id": source, "occurred_at": when},
        )
    assert batch.pending()["relationships"] == 2


@pytest.mark.asyncio
async def test_batch_writer_merges_on_source_id():
    client = _Client()
    batch = Neo4jBatchWriter(client)
    batch.add_relationship(
        "person-alice", "project-atlas", "HAS_PROJECTS", {"source_id": "doc:a", "occurred_at": JUNE}
    )
    batch.add_relationship("person-alice", "project-atlas", "HAS_PROJECTS", {"source_id": ""})
    await batch.flush()
    query, params = next((q, p) for q, p in client.writes if "HAS_PROJECTS" in q)
    assert "MERGE (a)-[r:HAS_PROJECTS {source_id: row.source_id}]->(b)" in query
    assert sorted(r["source_id"] for r in params["rows"]) == ["", "doc:a"]
    dated = next(r for r in params["rows"] if r["source_id"] == "doc:a")
    assert dated["props"]["occurred_at"] == JUNE


# ---------------------------------------------------------------------------
# Rebuild from files
# ---------------------------------------------------------------------------


async def _ingest(metadata, path="people/alice.md"):
    kg = _graph()
    kg._upsert_node = AsyncMock()
    kg._ensure_entity_exists = AsyncMock()
    kg._link_entity_to_document = AsyncMock()
    kg._upsert_relationship = AsyncMock()
    await kg._ingest_file(path, metadata)
    return [c.kwargs for c in kg._upsert_relationship.call_args_list]


@pytest.mark.asyncio
async def test_rebuild_writes_one_edge_per_assertion():
    edges = await _ingest(
        {
            "id": "person-alice",
            "entity_type": "person",
            "name": "Alice",
            "has_projects": ["project-atlas"],
            ASSERTIONS_KEY: [
                {
                    "type": "has_projects",
                    "target": "project-atlas",
                    "source_id": "doc:meetings/meeting-a.md",
                    "occurred_at": "2026-06-02T10:00:00-04:00",
                    "time_source": "explicit",
                },
                {
                    "type": "has_projects",
                    "target": "project-atlas",
                    "source_id": "doc:meetings/meeting-b.md",
                    "occurred_at": "2026-08-11T09:00:00+00:00",
                    "time_source": "content_header",
                },
            ],
        }
    )
    assert [(e["properties"]["source_id"], e["properties"]["occurred_at"]) for e in edges] == [
        ("doc:meetings/meeting-a.md", JUNE),
        ("doc:meetings/meeting-b.md", AUGUST),
    ]
    assert all(e["rel_type"] == "HAS_PROJECTS" for e in edges)


@pytest.mark.asyncio
async def test_bare_id_with_no_assertion_is_an_unattributed_edge():
    """Files from before ADR-004 still build their relationships."""
    edges = await _ingest(
        {
            "id": "person-alice",
            "entity_type": "person",
            "name": "Alice",
            "has_projects": ["project-atlas"],
        }
    )
    assert len(edges) == 1
    assert edges[0]["properties"]["source_id"] == ""
    assert "occurred_at" not in edges[0]["properties"]


@pytest.mark.asyncio
async def test_assertion_for_a_target_not_in_the_typed_list_builds_nothing():
    """The typed list is the relationship; an assertion only dates it."""
    edges = await _ingest(
        {
            "id": "person-alice",
            "entity_type": "person",
            "name": "Alice",
            ASSERTIONS_KEY: [
                {"type": "has_projects", "target": "project-atlas", "source_id": "doc:a",
                 "occurred_at": "2026-06-02T14:00:00+00:00"},
            ],
        }
    )
    assert edges == []


@pytest.mark.asyncio
async def test_meeting_document_gets_typed_event_time():
    client = _Client()
    kg = _graph(client)
    await kg._upsert_document_node(
        "doc:meetings/meeting-a.md",
        "meetings/meeting-a.md",
        {
            "meeting_id": "m1",
            "bot_id": "a",
            "title": "Kickoff",
            "start_time": "2026-06-02T10:00:00-04:00",
            "recorded_at": "2026-09-27T22:58:30+00:00",
            "time_source": "explicit",
        },
    )
    props = client.writes[0][1]["props"]
    assert props["occurred_at"] == JUNE
    assert props["recorded_at"] == datetime(2026, 9, 27, 22, 58, 30, tzinfo=UTC)
    assert props["time_source"] == "explicit"


@pytest.mark.asyncio
async def test_entity_profile_document_gets_no_event_time():
    client = _Client()
    await _graph(client)._upsert_document_node(
        "doc:notes/x.md", "notes/x.md", {"title": "Profile", "updated_at": "2026-09-27T22:58:30"}
    )
    assert "occurred_at" not in client.writes[0][1]["props"]


# ---------------------------------------------------------------------------
# Files first: write-through
# ---------------------------------------------------------------------------


def _entity_file(repo, body="# Alice\n", **frontmatter):
    meta = {"id": "person-alice", "entity_type": "person", "name": "Alice", **frontmatter}
    os.makedirs(repo / "people", exist_ok=True)
    path = repo / "people" / "alice.md"
    path.write_text("---\n" + yaml.dump(meta, sort_keys=False) + "---\n" + body, encoding="utf-8")
    return path


def _frontmatter(path):
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])


EVIDENCE = {
    "source_id": "doc:meetings/meeting-a.md",
    "occurred_at": JUNE,
    "recorded_at": datetime(2026, 9, 27, 12, 0, tzinfo=UTC),
    "time_source": "explicit",
}


@pytest.mark.asyncio
async def test_frontmatter_relationship_records_its_evidence(tmp_path):
    path = _entity_file(tmp_path)
    kg = _graph(repo=tmp_path)
    kg._find_entity_file = lambda _eid: str(path)

    rel = await kg.add_frontmatter_relationships(
        "person-alice", {"has_projects": ["project-atlas"]}, evidence=EVIDENCE
    )
    assert rel == "people/alice.md"
    meta = _frontmatter(path)
    assert meta["has_projects"] == ["project-atlas"]
    assert meta[ASSERTIONS_KEY] == [
        {
            "type": "has_projects",
            "target": "project-atlas",
            "source_id": "doc:meetings/meeting-a.md",
            "occurred_at": "2026-06-02T14:00:00+00:00",
            "time_source": "explicit",
            "recorded_at": "2026-09-27T12:00:00+00:00",
        }
    ]
    assert path.read_text(encoding="utf-8").endswith("# Alice\n")


@pytest.mark.asyncio
async def test_restating_a_relationship_from_new_evidence_adds_an_assertion(tmp_path):
    path = _entity_file(tmp_path)
    kg = _graph(repo=tmp_path)
    kg._find_entity_file = lambda _eid: str(path)
    await kg.add_frontmatter_relationships(
        "person-alice", {"has_projects": ["project-atlas"]}, evidence=EVIDENCE
    )
    later = {**EVIDENCE, "source_id": "doc:meetings/meeting-b.md", "occurred_at": AUGUST}
    changed = await kg.add_frontmatter_relationships(
        "person-alice", {"has_projects": ["project-atlas"]}, evidence=later
    )
    assert changed == "people/alice.md"  # the file changed, so it is re-ingested
    meta = _frontmatter(path)
    assert meta["has_projects"] == ["project-atlas"]
    assert [a["source_id"] for a in meta[ASSERTIONS_KEY]] == [
        "doc:meetings/meeting-a.md",
        "doc:meetings/meeting-b.md",
    ]


@pytest.mark.asyncio
async def test_reingesting_the_same_source_changes_nothing(tmp_path):
    path = _entity_file(tmp_path)
    kg = _graph(repo=tmp_path)
    kg._find_entity_file = lambda _eid: str(path)
    args = ("person-alice", {"has_projects": ["project-atlas"]})
    await kg.add_frontmatter_relationships(*args, evidence=EVIDENCE)
    before = path.read_text(encoding="utf-8")
    assert await kg.add_frontmatter_relationships(*args, evidence=EVIDENCE) is None
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_relationship_without_evidence_writes_no_assertion(tmp_path):
    path = _entity_file(tmp_path)
    kg = _graph(repo=tmp_path)
    kg._find_entity_file = lambda _eid: str(path)
    await kg.add_frontmatter_relationships("person-alice", {"has_projects": ["project-atlas"]})
    meta = _frontmatter(path)
    assert meta["has_projects"] == ["project-atlas"]
    assert ASSERTIONS_KEY not in meta


@pytest.mark.asyncio
async def test_removing_a_relationship_removes_its_assertions(tmp_path):
    path = _entity_file(
        tmp_path,
        has_projects=["project-atlas", "project-borealis"],
        **{
            ASSERTIONS_KEY: [
                {"type": "has_projects", "target": "project-atlas", "source_id": "doc:a"},
                {"type": "has_projects", "target": "project-atlas", "source_id": "doc:b"},
                {"type": "has_projects", "target": "project-borealis", "source_id": "doc:a"},
            ]
        },
    )
    kg = _graph(repo=tmp_path)
    kg._find_entity_file = lambda _eid: str(path)
    await kg._remove_relationship_from_file("person-alice", "project-atlas", "has_projects")
    meta = _frontmatter(path)
    assert meta["has_projects"] == ["project-borealis"]
    assert [a["target"] for a in meta[ASSERTIONS_KEY]] == ["project-borealis"]


@pytest.mark.asyncio
async def test_written_files_rebuild_to_the_same_edges(tmp_path):
    """Live write-through then rebuild: the file round trip loses nothing."""
    path = _entity_file(tmp_path)
    kg = _graph(repo=tmp_path)
    kg._find_entity_file = lambda _eid: str(path)
    await kg.add_frontmatter_relationships(
        "person-alice", {"has_projects": ["project-atlas"]}, evidence=EVIDENCE
    )
    edges = await _ingest(_frontmatter(path))
    assert len(edges) == 1
    props = edges[0]["properties"]
    assert props["source_id"] == "doc:meetings/meeting-a.md"
    assert props["occurred_at"] == JUNE
    assert props["recorded_at"] == datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    assert props["time_source"] == "explicit"


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signal_node_times_are_typed_and_utc():
    client = _Client()
    sig = Signal(
        id="s1",
        type="decision",
        content="Use Postgres",
        source_meeting_id="a",
        source_timestamp="2026-06-02T10:00:00-04:00",
        created_at="2026-09-27T22:58:30+00:00",
    )
    await SignalGraphWriter(client).write_meeting_signals(
        MeetingSignals(meeting_id="m1", bot_id="a", signals=[sig])
    )
    params = next(p for q, p in client.writes if "MERGE (s:Signal" in q)
    assert params["valid_from"] == JUNE
    assert params["occurred_at"] == JUNE
    assert params["recorded_at"] == datetime(2026, 9, 27, 22, 58, 30, tzinfo=UTC)
    assert params["valid_to"] is None
    # The file mirror keeps the string it was written with.
    assert params["source_timestamp"] == "2026-06-02T10:00:00-04:00"


@pytest.mark.asyncio
async def test_superseding_closes_the_window_with_a_typed_time():
    client = _Client()
    await SignalGraphWriter(client).update_signal_properties(
        "s1", valid_to="2026-08-11T05:00:00-04:00"
    )
    assert client.writes[0][1]["props"]["valid_to"] == AUGUST


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_indexes_event_time():
    statements = "\n".join(generate_schema_from_domain(_make_domain_config()))
    assert "FOR (d:Document) ON (d.occurred_at)" in statements
    assert "FOR (s:Signal) ON (s.valid_from)" in statements
    assert "FOR (s:Signal) ON (s.occurred_at)" in statements
