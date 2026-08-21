"""Tests for the UNWIND batch writer and its integration with the full build."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.model_schemas.domain_config import (
    DomainAttribute,
    DomainConfiguration,
    DomainEntity,
    DomainRelationship,
)
from app.models import File
from app.services.graph.batch_writer import Neo4jBatchWriter
from app.services.graph.neo4j_graph import Neo4jKnowledgeGraph


def _domain() -> DomainConfiguration:
    return DomainConfiguration(
        id="test_domain",
        name="Test Domain",
        entities={
            "person": DomainEntity(
                name="person",
                description="A person",
                plural="people",
                attributes=[DomainAttribute(name="name", type="string", required=True)],
                relationships=[
                    DomainRelationship(type="has_projects", target="project", cardinality="one-to-many"),
                ],
            ),
            "project": DomainEntity(
                name="project",
                description="A project",
                plural="projects",
                attributes=[DomainAttribute(name="name", type="string", required=True)],
                relationships=[],
            ),
        },
    )


def _client():
    client = AsyncMock()
    client.execute_read = AsyncMock(return_value=[])
    client.execute_write = AsyncMock(return_value=[])
    return client


# ── writer unit tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flush_orders_statements_and_groups_by_label():
    client = _client()
    w = Neo4jBatchWriter(client, chunk_size=100)
    w.add_relationship("person-a", "project-x", "HAS_PROJECTS", {"source": "metadata"})
    w.add_mention("person-a", "doc:m.md", "m.md", "m.md")
    w.add_document("doc:m.md", {"id": "doc:m.md", "path": "m.md"})
    w.add_stub("project-x", "Project", {"id": "project-x", "name": "X", "canonical": "x", "etype": "project", "ts": "t"})
    w.add_node("person-a", "Person", {"name": "A"})
    w.add_node("person-a", "Person", {"role": "dev"})  # merged, not duplicated
    w.record_type_usage("entity", "person")
    w.record_type_usage("entity", "person")

    usage = AsyncMock()
    stats = await w.flush(record_type_usage=usage)

    queries = [c.args[0] for c in client.execute_write.await_args_list]
    assert all(q.startswith("UNWIND $rows AS row") for q in queries)
    def _kind(q: str) -> str:
        if "MENTIONED_IN" in q:
            return "mention"
        if "HAS_PROJECTS" in q:
            return "rel"
        if "MERGE (d:Document" in q:
            return "doc"
        if "ON CREATE" in q:
            return "stub"
        return "node"

    kinds = [_kind(q) for q in queries]
    assert kinds == ["node", "stub", "doc", "rel", "mention"]

    node_rows = client.execute_write.await_args_list[0].args[1]["rows"]
    assert node_rows == [{"id": "person-a", "props": {"name": "A", "role": "dev"}}]
    assert stats["nodes"] == 1 and stats["relationships"] == 1 and stats["statements"] == 5
    usage.assert_awaited_once_with("entity", "person")
    assert w.pending() == {"nodes": 0, "stubs": 0, "documents": 0, "relationships": 0, "mentions": 0, "type_usage": 0}


@pytest.mark.asyncio
async def test_flush_chunks_rows():
    client = _client()
    w = Neo4jBatchWriter(client, chunk_size=2)
    for i in range(5):
        w.add_node(f"person-{i}", "Person", {"name": str(i)})
    stats = await w.flush()
    assert stats["statements"] == 3
    sizes = [len(c.args[1]["rows"]) for c in client.execute_write.await_args_list]
    assert sizes == [2, 2, 1]


# ── integration with Neo4jKnowledgeGraph ────────────────────────────────────


def _graph(client):
    kg = Neo4jKnowledgeGraph(neo4j_client=client, domain_config=_domain())
    git = MagicMock()
    git.repo_path = "/tmp/test-repo"
    kg._git_ops = git
    return kg, git


PERSON_MD = "---\nid: person-alice\ntype: person\nname: Alice\nhas_projects:\n  - Apollo\n---\nbody\n"
MEETING_MD = "---\ntitle: Standup\npeople:\n  - Alice\n---\nnotes\n"


@pytest.mark.asyncio
async def test_full_build_uses_loaded_content_and_batches_writes():
    client = _client()
    kg, git = _graph(client)
    git.read_markdown_files = AsyncMock(return_value=[
        File(path="entities/person/alice.md", content=PERSON_MD),
        File(path="meetings/standup.md", content=MEETING_MD),
        File(path="README.md", content="# readme"),
    ])
    git.read_file = AsyncMock(side_effect=AssertionError("read_file must not be called during a full build"))
    kg._record_type_usage = AsyncMock()
    kg._reingest_signals = AsyncMock()
    kg.process_stubs = AsyncMock(return_value={})
    kg._sync_from_neo4j = AsyncMock()

    stats = await kg.build_graph(force_rebuild=True)

    git.read_file.assert_not_called()
    queries = [c.args[0] for c in client.execute_write.await_args_list]
    unwind = [q for q in queries if q.startswith("UNWIND")]
    assert unwind, "expected batched UNWIND statements"
    # No per-row MERGE statements for nodes/relationships slipped through
    assert not any(q.startswith("MERGE (n:Entity:") for q in queries)
    assert any("HAS_PROJECTS" in q for q in unwind)
    assert any("MENTIONED_IN" in q for q in unwind)
    assert kg._batch is None  # collector released after the build
    assert isinstance(stats, dict)
    # type usage recorded once per distinct type, not once per write
    kinds = sorted({c.args for c in kg._record_type_usage.await_args_list})
    assert ("entity", "person") in kinds and ("relationship", "HAS_PROJECTS") in kinds
    assert kg._record_type_usage.await_count == len(kinds)


@pytest.mark.asyncio
async def test_build_graph_accepts_reingest_signals_flag():
    client = _client()
    kg, git = _graph(client)
    git.read_markdown_files = AsyncMock(return_value=[])
    kg._reingest_signals = AsyncMock()
    kg.process_stubs = AsyncMock(return_value={})
    kg._sync_from_neo4j = AsyncMock()
    await kg.build_graph(force_rebuild=True, reingest_signals=False)
    kg._reingest_signals.assert_not_awaited()
    await kg.build_graph(force_rebuild=True)
    kg._reingest_signals.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_files_is_incremental_and_skips_readme():
    client = _client()
    kg, git = _graph(client)
    git.read_markdown_files = AsyncMock(return_value=[File(path="entities/person/alice.md", content=PERSON_MD)])
    kg._record_type_usage = AsyncMock()
    kg._sync_from_neo4j = AsyncMock()

    n = await kg.ingest_files(["entities/person/alice.md", "README.md", "notes.txt"])

    assert n == 1
    git.read_markdown_files.assert_awaited_once_with(["entities/person/alice.md"])
    queries = [c.args[0] for c in client.execute_write.await_args_list]
    assert any(q.startswith("UNWIND") for q in queries)
    assert any("CO_OCCURRENCE" in q for q in queries)
    kg._sync_from_neo4j.assert_not_awaited()  # caches never built in this process


@pytest.mark.asyncio
async def test_remove_files_deletes_documents_and_stubs_entities():
    client = _client()
    client.execute_write = AsyncMock(side_effect=[[{"n": 2}], [{"n": 1}]])
    kg, _ = _graph(client)
    kg.document_entities["meetings/a.md"] = {"person-x"}
    result = await kg.remove_files(["meetings/a.md", "entities/person/x.md"])
    assert result == {"documents_deleted": 2, "entities_stubbed": 1}
    assert "meetings/a.md" not in kg.document_entities
    first, second = client.execute_write.await_args_list
    assert "DETACH DELETE d" in first.args[0]
    assert first.args[1]["ids"] == ["doc:meetings/a.md", "doc:entities/person/x.md"]
    assert "stub = true" in second.args[0]


@pytest.mark.asyncio
async def test_write_helpers_hit_neo4j_directly_outside_a_batch():
    client = _client()
    kg, _ = _graph(client)
    kg._record_type_usage = AsyncMock()
    await kg._upsert_node("person-a", "person", "Person", {"name": "A"})
    assert client.execute_write.await_args.args[0].startswith("MERGE (n:Entity:Person")


@pytest.mark.asyncio
async def test_batch_is_task_scoped_not_instance_scoped():
    """A write from another task during a bulk ingest must hit Neo4j directly."""
    import asyncio

    client = _client()
    kg, git = _graph(client)
    kg._record_type_usage = AsyncMock()
    kg._sync_from_neo4j = AsyncMock()
    gate = asyncio.Event()

    async def slow_read(paths):
        gate.set()
        await asyncio.sleep(0.05)
        return [File(path="entities/person/alice.md", content=PERSON_MD)]

    git.read_markdown_files = slow_read
    ingest = asyncio.create_task(kg.ingest_files(["entities/person/alice.md"]))
    await gate.wait()
    # concurrent request-path write while the bulk ingest is in flight
    await kg._upsert_node("person-zed", "person", "Person", {"name": "Zed"})
    direct = [c.args[0] for c in client.execute_write.await_args_list]
    assert any(q.startswith("MERGE (n:Entity:Person") for q in direct), "request write must not be buffered"
    await ingest
    assert kg._batch is None
