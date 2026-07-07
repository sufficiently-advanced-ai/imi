"""Tests that EntityActivityTracker derives stats/activities from signals.

In an ingest-only deployment the graph has Signal->Entity edges but no
Document nodes, so the legacy ``entity_documents`` path returns all zeros.
The tracker must prefer signal-derived statistics when they exist.
"""

import pytest

from app.services.entity_activity_tracker import EntityActivityTracker


class _Node:
    def __init__(self, connections):
        self.connections = set(connections)
        self.name = "Jeff Jennings"
        self.metadata = {"name": "Jeff Jennings", "type": "person"}


class _FakeGraph:
    """Graph with signal stats but an empty entity_documents map (ingest world)."""

    def __init__(self):
        self.entity_documents = {}  # no Document nodes on ingest path
        self.nodes = {"person-jeff": _Node({"person-amy", "person-bob"})}

    async def get_entity_signal_stats(self, entity_id, recent_cutoff_iso):
        return {
            "mention_count": 9,
            "document_count": 4,
            "recent_count": 3,
            "last_ts": "2026-06-18T10:00:00+00:00",
        }

    async def find_signals_for_entity(self, entity_id, limit=20):
        return [
            {
                "id": "sig-1",
                "type": "decision",
                "content": "Approved budget for Q3",
                "source_meeting_id": "ingest-abc",
                "source_meeting_title": "Planning",
                "source_timestamp": "2026-06-18T10:00:00+00:00",
                "status": "",
                "owner": "",
                "confidence": 0.9,
                "mentions": [],
            }
        ]


@pytest.mark.asyncio
async def test_statistics_derived_from_signals_when_no_documents():
    tracker = EntityActivityTracker(_FakeGraph())
    stats = await tracker.get_entity_statistics("person-jeff")
    assert stats.total_mentions == 9
    assert stats.document_count == 4
    assert stats.recent_mentions == 3
    assert stats.activity_count == 9
    assert stats.relationship_count == 2  # still from node.connections
    assert stats.last_activity is not None


@pytest.mark.asyncio
async def test_activities_built_from_signals_when_no_documents():
    tracker = EntityActivityTracker(_FakeGraph())
    activities = await tracker.get_entity_activities("person-jeff")
    assert len(activities) == 1
    assert activities[0].activity_type == "decision"
    assert "budget" in activities[0].description.lower()


@pytest.mark.asyncio
async def test_falls_back_to_legacy_when_no_signals():
    """When the graph reports no signals, the legacy document path runs and
    (with an empty entity_documents map) returns zeros — not an exception."""

    class _NoSignalGraph(_FakeGraph):
        async def get_entity_signal_stats(self, entity_id, recent_cutoff_iso):
            return {
                "mention_count": 0,
                "document_count": 0,
                "recent_count": 0,
                "last_ts": None,
            }

    tracker = EntityActivityTracker(_NoSignalGraph())
    stats = await tracker.get_entity_statistics("person-jeff")
    assert stats.total_mentions == 0
    assert stats.relationship_count == 2  # connections still resolved
