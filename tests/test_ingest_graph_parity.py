"""Live ingest must leave the same graph footprint a rebuild from files would.

Covers: resolved entity ids recorded on the observation and round-tripped
through the meeting file, the file reader linking exactly those ids, the
persisted meeting being linked into the graph (Document + MENTIONED_IN via
ingest_files), and signals tied to their source meeting Document.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.observation import Observation
from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.graph.signal_graph_writer import SignalGraphWriter
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator


def _obs(**kw):
    base = dict(
        observation_id="m1",
        external_id="ingest-abc",
        observed_at=datetime(2026, 9, 21, 14, 0),
        content="body",
        entities_mentioned={"person": ["Dan"]},
        participants=["Dan Kauppi"],
    )
    base.update(kw)
    return Observation(**base)


def test_entity_ids_round_trip_through_meeting_markdown():
    obs = _obs(entity_ids=["account-foley", "person-dan-kauppi"])
    parsed = Observation.from_markdown(obs.to_markdown())
    assert parsed.entity_ids == ["account-foley", "person-dan-kauppi"]
    # Absent -> empty, legacy files unaffected
    assert Observation.from_markdown(_obs().to_markdown()).entity_ids == []


@pytest.mark.asyncio
async def test_file_reader_links_exactly_the_resolved_ids():
    """A meeting file with entity_ids links those ids and ignores the surface
    names, so a rebuild can't mint person-dan beside person-dan-kauppi."""
    from app.services.graph.neo4j_graph import Neo4jKnowledgeGraph
    from tests.test_neo4j_graph import _make_domain_config

    kg = Neo4jKnowledgeGraph(neo4j_client=AsyncMock(), domain_config=_make_domain_config())
    kg._ensure_entity_exists = AsyncMock()
    refs = await kg._extract_entity_references(
        "meetings/meeting-ingest-abc.md",
        {
            "entity_ids": ["person-dan-kauppi", "bogus-type-x", "person-scott-jennings"],
            "entities_mentioned": {"person": ["Dan"]},
            "participants": ["Dan"],
        },
    )
    assert refs == {"person-dan-kauppi", "person-scott-jennings"}
    created = {c.args[0] for c in kg._ensure_entity_exists.call_args_list}
    assert "person-dan" not in created


class _Graph:
    def __init__(self):
        self.nodes = {}
        self.ingested = []

    async def add_node(self, entity_type, name, entity_id=None, properties=None):
        self.nodes[entity_id] = name
        return True

    async def create_semantic_relationship(self, **kw):
        return True

    async def ingest_files(self, paths):
        self.ingested.extend(paths)
        return len(paths)


class _Writer:
    async def write_meeting_signals(self, ms):
        return len(ms.signals)


def _orch(graph, git=None):
    return IngestOrchestrator(
        classifier=None, claude_client=None, graph=graph,
        signal_writer=_Writer(), git_ops=git, tools={},
    )


@pytest.mark.asyncio
async def test_enrich_graph_records_resolved_ids_on_observation():
    graph = _Graph()
    obs = _obs(entities_mentioned={"person": ["Dan", "Dan Kauppi"]})
    ms = MeetingSignals(meeting_id="m1", bot_id="ingest-abc", signals=[
        Signal(id="s1", type="action_item", content="Dan to check the license",
               source_meeting_id="ingest-abc", source_timestamp="2026-09-21T14:00:00+00:00",
               entities=[EntityRef(id="person-dan", type="person", name="Dan")]),
    ])
    await _orch(graph)._phase_enrich_graph(ms, "Dan to check the license", obs)

    # 'Dan' resolved onto the participant; only the resolved id is recorded.
    assert obs.entity_ids == ["person-dan-kauppi"]
    assert ms.signals[0].entities[0].id == "person-dan-kauppi"


@pytest.mark.asyncio
async def test_persist_links_meeting_document_in_graph():
    graph = _Graph()
    git = MagicMock()
    git.commit_file = AsyncMock()
    await _orch(graph, git)._phase_persist(_obs(entity_ids=["person-dan-kauppi"]), None, "ingest-abc")
    assert graph.ingested == ["meetings/meeting-ingest-abc.md"]


@pytest.mark.asyncio
async def test_persist_links_document_even_when_commit_fails():
    graph = _Graph()
    git = MagicMock()
    git.commit_file = AsyncMock(side_effect=RuntimeError("git commit failed"))
    await _orch(graph, git)._phase_persist(_obs(), None, "ingest-abc")
    assert graph.ingested == ["meetings/meeting-ingest-abc.md"]


@pytest.mark.asyncio
async def test_signal_writer_links_signal_to_source_meeting_document():
    writes = []

    class _Client:
        async def execute_write(self, query, params):
            writes.append((query, params))
            return []

    ms = MeetingSignals(meeting_id="m1", bot_id="ingest-abc", signals=[
        Signal(id="s1", type="insight", content="General observation, no entities",
               source_meeting_id="ingest-abc", source_timestamp="2026-09-21T14:00:00+00:00"),
    ])
    await SignalGraphWriter(_Client()).write_meeting_signals(ms)

    doc_writes = [p for q, p in writes if "FROM_DOCUMENT" in q]
    assert doc_writes == [{
        "signal_id": "s1",
        "doc_id": "doc:meetings/meeting-ingest-abc.md",
        "path": "meetings/meeting-ingest-abc.md",
        "name": "meeting-ingest-abc.md",
    }]
