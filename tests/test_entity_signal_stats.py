"""Unit tests for Neo4jKnowledgeGraph.get_entity_signal_stats.

Signal-derived statistics are the ingest-path counterpart to the file-world
``entity_documents`` map: the ingest pipeline writes Signal->Entity edges but
no Document nodes, so document-based stats are always zero for ingested
entities. This method aggregates the signal edges in a single query.
"""

import pytest

from app.services.graph.neo4j_graph import Neo4jKnowledgeGraph


class _FakeNeo4j:
    """Minimal async stand-in for the neo4j client used by the graph."""

    def __init__(self, rows):
        self._rows = rows
        self.last_query = None
        self.last_params = None

    async def execute_read(self, query, params=None):
        self.last_query = query
        self.last_params = params or {}
        return self._rows


@pytest.mark.asyncio
async def test_get_entity_signal_stats_aggregates_counts():
    rows = [
        {
            "mention_count": 7,
            "document_count": 3,
            "recent_count": 2,
            "last_ts": "2026-06-18T10:00:00+00:00",
        }
    ]
    kg = Neo4jKnowledgeGraph.__new__(Neo4jKnowledgeGraph)
    kg.neo4j = _FakeNeo4j(rows)

    stats = await kg.get_entity_signal_stats(
        "person-jeff-jennings", recent_cutoff_iso="2026-05-20T00:00:00+00:00"
    )

    assert stats == {
        "mention_count": 7,
        "document_count": 3,
        "recent_count": 2,
        "last_ts": "2026-06-18T10:00:00+00:00",
    }
    assert kg.neo4j.last_params["entity_id"] == "person-jeff-jennings"
    assert kg.neo4j.last_params["cutoff"] == "2026-05-20T00:00:00+00:00"
    # Signals matched by both MENTIONS and ASSIGNED_TO must not double-count.
    assert "count(DISTINCT s)" in kg.neo4j.last_query


@pytest.mark.asyncio
async def test_get_entity_signal_stats_empty_is_zeroed():
    kg = Neo4jKnowledgeGraph.__new__(Neo4jKnowledgeGraph)
    kg.neo4j = _FakeNeo4j([])  # no signals
    stats = await kg.get_entity_signal_stats("person-nobody", recent_cutoff_iso="x")
    assert stats == {
        "mention_count": 0,
        "document_count": 0,
        "recent_count": 0,
        "last_ts": None,
    }


@pytest.mark.asyncio
async def test_get_entity_signal_stats_swallows_db_error():
    class _Boom:
        async def execute_read(self, *a, **k):
            raise RuntimeError("db down")

    kg = Neo4jKnowledgeGraph.__new__(Neo4jKnowledgeGraph)
    kg.neo4j = _Boom()
    stats = await kg.get_entity_signal_stats("person-x", recent_cutoff_iso="x")
    assert stats == {
        "mention_count": 0,
        "document_count": 0,
        "recent_count": 0,
        "last_ts": None,
    }
