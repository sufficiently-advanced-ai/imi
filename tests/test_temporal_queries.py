"""
Tests for Temporal Knowledge Graph — Issue #864.

Covers:
- SemanticaKnowledge temporal methods (get_state_at, get_active_relationships, get_provenance)
- TemporalQueryService higher-order queries (what_changed, what_changed_between,
  graph_as_of, find_contradictions, temporal_blast_radius)
"""

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

NOW = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
PAST = NOW - timedelta(days=30)
PAST2 = NOW - timedelta(days=15)


# ---------------------------------------------------------------------------
# Mock heavy imports before importing our modules.
# These packages only exist inside Docker — mock them for local test runs.
# Only stub modules that are genuinely unavailable: stubbing an importable
# module poisons sys.modules for every test that runs AFTER this file in the
# same session (real numpy consumers crash on the MagicMock).
# ---------------------------------------------------------------------------

_MOCK_MODULES = [
    "numpy", "fastembed", "faiss",
    "semantica", "semantica.graph_store", "semantica.vector_store",
    "semantica.embeddings", "semantica.context", "semantica.semantic_extract",
    "semantica.deduplication", "semantica.kg", "semantica.search",
]
for _mod in _MOCK_MODULES:
    if _mod not in sys.modules and importlib.util.find_spec(_mod.split(".")[0]) is None:
        sys.modules[_mod] = MagicMock()


# ===========================================================================
# Helpers
# ===========================================================================


def _build_real_sk(mock_graph_store):
    """Build a real SemanticaKnowledge with mocked dependencies."""
    from app.services.semantica_knowledge import SemanticaKnowledge

    return SemanticaKnowledge(
        graph_store=mock_graph_store,
        vector_store=MagicMock(),
        embedding_generator=MagicMock(),
        context_graph=MagicMock(),
        ner_extractor=MagicMock(),
        duplicate_detector=MagicMock(),
    )


def _mock_graph_store(query_results=None):
    """Create a mock graph_store with execute_query returning given results."""
    gs = MagicMock()
    gs.execute_query = MagicMock(return_value=query_results or [])
    return gs


# ===========================================================================
# Phase 1 — SemanticaKnowledge temporal methods
# ===========================================================================


class TestGetStateAt:
    """get_state_at should return entity properties at a specific timestamp."""

    @pytest.mark.asyncio
    async def test_returns_entity_state_at_past_time(self):
        gs = _mock_graph_store([
            {
                "id": "entity-1",
                "name": "Acme Corp",
                "entity_type": "Organization",
                "props": {"sector": "Technology"},
                "valid_from": "2026-01-01T00:00:00Z",
                "valid_to": None,
            }
        ])

        sk = _build_real_sk(gs)
        result = await sk.get_state_at("entity-1", PAST)

        assert result is not None
        assert result["name"] == "Acme Corp"
        assert result["type"] == "Organization"

    @pytest.mark.asyncio
    async def test_returns_none_when_entity_not_found(self):
        gs = _mock_graph_store([])
        sk = _build_real_sk(gs)
        result = await sk.get_state_at("nonexistent", PAST)
        assert result is None

    @pytest.mark.asyncio
    async def test_accepts_entity_name_lookup(self):
        """Should match by name when entity_id is a name, not an ID."""
        gs = _mock_graph_store([
            {
                "id": "entity-1",
                "name": "Acme Corp",
                "entity_type": "Organization",
                "props": {},
                "valid_from": "2026-01-01T00:00:00Z",
                "valid_to": None,
            }
        ])

        sk = _build_real_sk(gs)
        result = await sk.get_state_at("Acme Corp", PAST)

        assert result is not None
        assert result["id"] == "entity-1"
        # Unified Cypher matches by both id and name
        cypher_used = gs.execute_query.call_args[0][0]
        assert "toLower(n.name)" in cypher_used

    @pytest.mark.asyncio
    async def test_single_word_name_resolves(self):
        """Single-word names like 'Acme' should also resolve via name match."""
        gs = _mock_graph_store([
            {
                "id": "entity-1",
                "name": "Acme",
                "entity_type": "Organization",
                "props": {},
                "valid_from": "2026-01-01T00:00:00Z",
                "valid_to": None,
            }
        ])

        sk = _build_real_sk(gs)
        result = await sk.get_state_at("Acme", PAST)

        assert result is not None
        assert result["id"] == "entity-1"


class TestGetActiveRelationships:
    """get_active_relationships should return relationships active at a time."""

    @pytest.mark.asyncio
    async def test_returns_active_relationships(self):
        gs = MagicMock()
        gs.execute_query = MagicMock(side_effect=[
            # Outgoing
            [
                {
                    "rel_type": "WORKS_FOR",
                    "target_id": "entity-2",
                    "target_name": "Acme Corp",
                    "target_type": "Organization",
                    "props": {"role": "CEO"},
                    "valid_from": "2026-01-01T00:00:00Z",
                    "valid_to": None,
                }
            ],
            # Incoming (empty)
            [],
        ])

        sk = _build_real_sk(gs)
        result = await sk.get_active_relationships("entity-1", PAST)

        assert len(result) == 1
        assert result[0]["relationship_type"] == "WORKS_FOR"
        assert result[0]["target_name"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_relationships(self):
        gs = MagicMock()
        gs.execute_query = MagicMock(side_effect=[[], []])

        sk = _build_real_sk(gs)
        result = await sk.get_active_relationships("entity-1", PAST)
        assert result == []

    @pytest.mark.asyncio
    async def test_includes_both_directions(self):
        """Should return both outgoing and incoming relationships."""
        gs = MagicMock()
        gs.execute_query = MagicMock(side_effect=[
            # Outgoing
            [{"rel_type": "WORKS_FOR", "target_id": "e2", "target_name": "Acme",
              "target_type": "Organization", "props": {},
              "valid_from": "2026-01-01T00:00:00Z", "valid_to": None}],
            # Incoming
            [{"rel_type": "MANAGES", "source_id": "e3", "source_name": "Bob",
              "source_type": "Person", "props": {},
              "valid_from": "2026-01-01T00:00:00Z", "valid_to": None}],
        ])

        sk = _build_real_sk(gs)
        result = await sk.get_active_relationships("entity-1", PAST)

        assert len(result) == 2


class TestGetProvenance:
    """get_provenance should return entity's provenance chain."""

    @pytest.mark.asyncio
    async def test_returns_provenance_data(self):
        gs = _mock_graph_store([
            {
                "source": "meeting-transcript-2026-01-15.md",
                "action": "MENTIONS",
                "timestamp": "2026-01-15T10:00:00Z",
                "actor": "webhook",
            },
            {
                "source": "meeting-transcript-2026-02-01.md",
                "action": "EXTRACTED_FROM",
                "timestamp": "2026-02-01T14:00:00Z",
                "actor": "webhook",
            },
        ])

        sk = _build_real_sk(gs)
        result = await sk.get_provenance("entity-1")

        assert "history" in result
        assert len(result["history"]) == 2
        assert result["history"][0]["action"] == "MENTIONS"

    @pytest.mark.asyncio
    async def test_returns_empty_provenance_for_unknown_entity(self):
        gs = _mock_graph_store([])
        sk = _build_real_sk(gs)
        result = await sk.get_provenance("nonexistent")
        assert "history" in result
        assert len(result["history"]) == 0


class TestErrorPropagation:
    """Errors from graph_store should propagate, not be swallowed."""

    @pytest.mark.asyncio
    async def test_get_state_at_propagates_errors(self):
        gs = MagicMock()
        gs.execute_query = MagicMock(side_effect=RuntimeError("Neo4j down"))

        sk = _build_real_sk(gs)
        with pytest.raises(RuntimeError, match="Neo4j down"):
            await sk.get_state_at("entity-1", PAST)

    @pytest.mark.asyncio
    async def test_get_active_relationships_propagates_errors(self):
        gs = MagicMock()
        gs.execute_query = MagicMock(side_effect=RuntimeError("Connection lost"))

        sk = _build_real_sk(gs)
        with pytest.raises(RuntimeError, match="Connection lost"):
            await sk.get_active_relationships("entity-1", PAST)

    @pytest.mark.asyncio
    async def test_get_provenance_propagates_errors(self):
        gs = MagicMock()
        gs.execute_query = MagicMock(side_effect=RuntimeError("Timeout"))

        sk = _build_real_sk(gs)
        with pytest.raises(RuntimeError, match="Timeout"):
            await sk.get_provenance("entity-1")


# ===========================================================================
# Phase 2 — TemporalQueryService higher-order queries
# ===========================================================================


class TestWhatChanged:
    """what_changed should diff entity state between a past time and now."""

    @pytest.mark.asyncio
    async def test_detects_attribute_changes(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(side_effect=[
            {"id": "e1", "name": "Acme", "type": "Organization",
             "attributes": {"sector": "Finance", "status": "Active"}},
            {"id": "e1", "name": "Acme", "type": "Organization",
             "attributes": {"sector": "Technology", "status": "Active"}},
        ])

        svc = TemporalQueryService(mock_sk)
        result = await svc.what_changed("e1", since=PAST)

        assert result["entity_id"] == "e1"
        changes = result["changes"]
        assert any(c["field"] == "attributes.sector" for c in changes)

    @pytest.mark.asyncio
    async def test_returns_no_changes_when_unchanged(self):
        from app.services.temporal_queries import TemporalQueryService

        state = {"id": "e1", "name": "Acme", "type": "Organization",
                 "attributes": {"sector": "Technology"}}
        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(return_value=state)

        svc = TemporalQueryService(mock_sk)
        result = await svc.what_changed("e1", since=PAST)

        assert result["changes"] == []

    @pytest.mark.asyncio
    async def test_handles_entity_not_found_at_since(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(side_effect=[
            None,
            {"id": "e1", "name": "Acme", "type": "Organization", "attributes": {}},
        ])

        svc = TemporalQueryService(mock_sk)
        result = await svc.what_changed("e1", since=PAST)

        assert result["entity_id"] == "e1"
        assert result["created_after_since"] is True


class TestWhatChangedBetween:
    """what_changed_between should diff between two arbitrary timestamps."""

    @pytest.mark.asyncio
    async def test_diffs_between_two_timestamps(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(side_effect=[
            {"id": "e1", "name": "Acme", "type": "Organization",
             "attributes": {"sector": "Finance"}},
            {"id": "e1", "name": "Acme Corp", "type": "Organization",
             "attributes": {"sector": "Technology"}},
        ])

        svc = TemporalQueryService(mock_sk)
        result = await svc.what_changed_between("e1", start=PAST, end=PAST2)

        assert result["entity_id"] == "e1"
        assert result["start"] == PAST.isoformat()
        assert result["end"] == PAST2.isoformat()
        changes = result["changes"]
        assert any(c["field"] == "name" for c in changes)
        assert any(c["field"] == "attributes.sector" for c in changes)

    @pytest.mark.asyncio
    async def test_handles_both_states_missing(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(return_value=None)

        svc = TemporalQueryService(mock_sk)
        result = await svc.what_changed_between("e1", start=PAST, end=PAST2)

        assert result["entity_id"] == "e1"
        assert result["error"] is not None


class TestGraphAsOf:
    """graph_as_of should reconstruct subgraph around entity at a time."""

    @pytest.mark.asyncio
    async def test_builds_subgraph_at_time(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(side_effect=[
            {"id": "e1", "name": "Alice", "type": "Person", "attributes": {}},
            {"id": "e2", "name": "Acme", "type": "Organization", "attributes": {}},
        ])
        mock_sk.get_active_relationships = AsyncMock(side_effect=[
            [{"relationship_type": "WORKS_FOR", "target_id": "e2",
              "target_name": "Acme", "properties": {}}],
            [],
        ])

        svc = TemporalQueryService(mock_sk)
        result = await svc.graph_as_of("e1", timestamp=PAST, depth=1)

        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        node_names = {n["name"] for n in result["nodes"]}
        assert "Alice" in node_names
        assert "Acme" in node_names

    @pytest.mark.asyncio
    async def test_handles_missing_entity(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(return_value=None)

        svc = TemporalQueryService(mock_sk)
        result = await svc.graph_as_of("nonexistent", timestamp=PAST)

        assert result["nodes"] == []
        assert result["edges"] == []

    @pytest.mark.asyncio
    async def test_respects_depth_zero(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(return_value={
            "id": "e1", "name": "Alice", "type": "Person", "attributes": {}
        })

        svc = TemporalQueryService(mock_sk)
        result = await svc.graph_as_of("e1", timestamp=PAST, depth=0)

        assert len(result["nodes"]) == 1
        assert len(result["edges"]) == 0

    @pytest.mark.asyncio
    async def test_deduplicates_edges(self):
        """Same relationship seen from both sides should appear only once."""
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(side_effect=[
            {"id": "e1", "name": "Alice", "type": "Person", "attributes": {}},
            {"id": "e2", "name": "Bob", "type": "Person", "attributes": {}},
        ])
        # When visiting e1: outgoing KNOWS -> e2
        # When visiting e2: incoming KNOWS from e1 (same relationship, other direction)
        mock_sk.get_active_relationships = AsyncMock(side_effect=[
            [{"relationship_type": "KNOWS", "target_id": "e2",
              "target_name": "Bob", "direction": "outgoing", "properties": {}}],
            [{"relationship_type": "KNOWS", "source_id": "e1",
              "source_name": "Alice", "target_id": "e2",
              "direction": "incoming", "properties": {}}],
        ])

        svc = TemporalQueryService(mock_sk)
        result = await svc.graph_as_of("e1", timestamp=PAST, depth=2)

        assert len(result["nodes"]) == 2
        # Edge should only appear once despite being seen from both sides
        assert len(result["edges"]) == 1


class TestFindContradictions:
    """find_contradictions should detect conflicting signals for an entity.

    S4-4: sources from semantic conflict layer only (keyword path removed).
    """

    @pytest.mark.asyncio
    async def test_detects_contradicting_signals_from_semantic_layer(self):
        """Signals with LLM-detected conflict metadata surface as contradictions."""
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.graph_store = MagicMock()
        mock_sk.graph_store.execute_query = MagicMock(return_value=[
            {
                "signal_id": "s1",
                "content": "Project Alpha is on track for Q1 delivery",
                "timestamp": "2026-01-15T10:00:00Z",
                "type": "status_update",
            },
        ])

        # s1 has a pending conflict candidate pointing at s2 (LLM-detected)
        mock_signal = MagicMock()
        mock_signal.id = "s1"
        mock_signal.source_timestamp = "2026-01-15T10:00:00Z"
        mock_signal.metadata = {
            "conflict_candidates": [{
                "other_signal_id": "s2",
                "other_content": "Project Alpha is delayed",
                "rationale": "Contradicts on-track status",
                "confidence": 0.91,
                "speakers": ["Alice"],
                "status": "pending",
                "proposed_at": "2026-02-20T10:00:00Z",
            }]
        }

        svc = TemporalQueryService(mock_sk)
        with patch("app.services.temporal_queries.signal_store") as mock_store:
            mock_store.find_signal_by_id.side_effect = lambda sid: (
                (mock_signal, MagicMock()) if sid == "s1" else None
            )
            result = await svc.find_contradictions("entity-alpha")

        assert "contradictions" in result
        assert len(result["contradictions"]) >= 1
        contradiction = result["contradictions"][0]
        assert "signal_a" in contradiction
        assert "signal_b" in contradiction
        assert "reason" in contradiction

    @pytest.mark.asyncio
    async def test_no_contradictions_when_single_signal(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.graph_store = MagicMock()
        mock_sk.graph_store.execute_query = MagicMock(return_value=[
            {
                "signal_id": "s1",
                "content": "Project Alpha is on track",
                "timestamp": "2026-01-15T10:00:00Z",
                "type": "status_update",
            },
        ])

        svc = TemporalQueryService(mock_sk)
        result = await svc.find_contradictions("entity-alpha")

        assert result["contradictions"] == []

    @pytest.mark.asyncio
    async def test_filters_by_date_range(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.graph_store = MagicMock()
        mock_sk.graph_store.execute_query = MagicMock(return_value=[])

        svc = TemporalQueryService(mock_sk)
        result = await svc.find_contradictions(
            "entity-alpha",
            date_from=PAST,
            date_to=NOW,
        )

        # Verify date filtering was applied in query params
        call_args = mock_sk.graph_store.execute_query.call_args
        assert call_args is not None
        assert result["contradictions"] == []

    @pytest.mark.asyncio
    async def test_uses_references_relationship_pattern(self):
        """Should query REFERENCES_* relationships, not :ABOUT."""
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.graph_store = MagicMock()
        mock_sk.graph_store.execute_query = MagicMock(return_value=[])

        svc = TemporalQueryService(mock_sk)
        await svc.find_contradictions("entity-1")

        cypher = mock_sk.graph_store.execute_query.call_args[0][0]
        assert "REFERENCES_" in cypher
        assert "ABOUT" not in cypher


class TestTemporalBlastRadius:
    """temporal_blast_radius should BFS traverse with time-scoped relationships."""

    @pytest.mark.asyncio
    async def test_traverses_to_max_depth(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(side_effect=[
            {"id": "e1", "name": "Alice", "type": "Person", "attributes": {}},
            {"id": "e2", "name": "Acme", "type": "Organization", "attributes": {}},
            {"id": "e3", "name": "Project X", "type": "Project", "attributes": {}},
        ])
        mock_sk.get_active_relationships = AsyncMock(side_effect=[
            [{"relationship_type": "WORKS_FOR", "target_id": "e2",
              "target_name": "Acme", "properties": {}}],
            [{"relationship_type": "OWNS", "target_id": "e3",
              "target_name": "Project X", "properties": {}}],
            [],
        ])

        svc = TemporalQueryService(mock_sk)
        result = await svc.temporal_blast_radius("e1", at_time=PAST, max_depth=3)

        assert len(result["nodes"]) == 3
        assert result["depth_map"]["e1"] == 0
        assert result["depth_map"]["e2"] == 1
        assert result["depth_map"]["e3"] == 2

    @pytest.mark.asyncio
    async def test_handles_cycles(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(side_effect=[
            {"id": "e1", "name": "Alice", "type": "Person", "attributes": {}},
            {"id": "e2", "name": "Bob", "type": "Person", "attributes": {}},
        ])
        mock_sk.get_active_relationships = AsyncMock(side_effect=[
            [{"relationship_type": "KNOWS", "target_id": "e2",
              "target_name": "Bob", "properties": {}}],
            [{"relationship_type": "KNOWS", "target_id": "e1",
              "target_name": "Alice", "properties": {}}],
        ])

        svc = TemporalQueryService(mock_sk)
        result = await svc.temporal_blast_radius("e1", at_time=PAST, max_depth=5)

        assert len(result["nodes"]) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_entity(self):
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(return_value=None)

        svc = TemporalQueryService(mock_sk)
        result = await svc.temporal_blast_radius("nonexistent", at_time=PAST)

        assert result["nodes"] == []
        assert result["edges"] == []
        assert result["depth_map"] == {}

    @pytest.mark.asyncio
    async def test_uses_resolved_id_not_raw_name(self):
        """BFS should use the resolved entity ID from get_state_at, not the raw input."""
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        # Name lookup resolves "Alice" to id "e1"
        mock_sk.get_state_at = AsyncMock(return_value={
            "id": "e1", "name": "Alice", "type": "Person", "attributes": {}
        })
        mock_sk.get_active_relationships = AsyncMock(return_value=[])

        svc = TemporalQueryService(mock_sk)
        result = await svc.temporal_blast_radius("Alice", at_time=PAST)

        # depth_map should use resolved ID "e1", not raw name "Alice"
        assert "e1" in result["depth_map"]
        assert result["depth_map"]["e1"] == 0
        # get_active_relationships should be called with resolved ID
        mock_sk.get_active_relationships.assert_called_once_with("e1", PAST)

    @pytest.mark.asyncio
    async def test_traverses_incoming_relationships(self):
        """BFS should follow incoming relationships using source_id."""
        from app.services.temporal_queries import TemporalQueryService

        mock_sk = MagicMock()
        mock_sk.get_state_at = AsyncMock(side_effect=[
            {"id": "e1", "name": "Alice", "type": "Person", "attributes": {}},
            {"id": "e3", "name": "Bob", "type": "Person", "attributes": {}},
        ])
        mock_sk.get_active_relationships = AsyncMock(side_effect=[
            # e1 has an incoming relationship from e3
            [{"relationship_type": "MANAGES", "source_id": "e3",
              "source_name": "Bob", "target_id": "e1",
              "direction": "incoming", "properties": {}}],
            [],
        ])

        svc = TemporalQueryService(mock_sk)
        result = await svc.temporal_blast_radius("e1", at_time=PAST, max_depth=2)

        assert len(result["nodes"]) == 2
        # Bob should be found via the incoming relationship
        node_names = {n["name"] for n in result["nodes"]}
        assert "Bob" in node_names
        # Edge should be source=e3 -> target=e1 (preserving direction)
        assert result["edges"][0]["source"] == "e3"
        assert result["edges"][0]["target"] == "e1"


# ===========================================================================
# Part A — Temporal validity set during graph build
# ===========================================================================


class TestAddEntityTemporalValidity:
    """add_entity should set valid_from on nodes from available metadata."""

    @pytest.mark.asyncio
    async def test_sets_valid_from_from_updated_at(self):
        """Should extract valid_from from updated_at in metadata."""
        gs = MagicMock()
        gs.execute_query = MagicMock(return_value={"success": True, "records": []})

        sk = _build_real_sk(gs)
        await sk.add_entity(
            entity_id="person-test",
            entity_type="person",
            name="Test Person",
            properties={"updated_at": "2026-02-15T10:00:00Z"},
        )

        # Extract the props dict passed to execute_query
        call_args = gs.execute_query.call_args
        props = call_args[1]["props"] if "props" in call_args[1] else call_args[0][1]["props"]
        assert "valid_from" in props
        assert props["valid_from"] == "2026-02-15T10:00:00Z"

    @pytest.mark.asyncio
    async def test_sets_valid_from_fallback_to_now(self):
        """Should fall back to current time if no date fields in metadata."""
        gs = MagicMock()
        gs.execute_query = MagicMock(return_value={"success": True, "records": []})

        sk = _build_real_sk(gs)
        before = datetime.now(UTC).isoformat()
        await sk.add_entity(
            entity_id="person-test",
            entity_type="person",
            name="Test Person",
            properties={"title": "Engineer"},
        )

        call_args = gs.execute_query.call_args
        props = call_args[1]["props"] if "props" in call_args[1] else call_args[0][1]["props"]
        assert "valid_from" in props
        # Should be approximately now
        assert props["valid_from"] >= before

    @pytest.mark.asyncio
    async def test_preserves_explicit_valid_from(self):
        """Should not overwrite an explicitly provided valid_from."""
        gs = MagicMock()
        gs.execute_query = MagicMock(return_value={"success": True, "records": []})

        sk = _build_real_sk(gs)
        await sk.add_entity(
            entity_id="person-test",
            entity_type="person",
            name="Test Person",
            properties={"valid_from": "2026-01-01T00:00:00Z", "updated_at": "2026-03-01T00:00:00Z"},
        )

        call_args = gs.execute_query.call_args
        props = call_args[1]["props"] if "props" in call_args[1] else call_args[0][1]["props"]
        assert props["valid_from"] == "2026-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_preserves_explicit_valid_to(self):
        """Should preserve valid_to for departed entities."""
        gs = MagicMock()
        gs.execute_query = MagicMock(return_value={"success": True, "records": []})

        sk = _build_real_sk(gs)
        await sk.add_entity(
            entity_id="person-departed",
            entity_type="person",
            name="Departed Person",
            properties={"valid_from": "2026-01-01T00:00:00Z", "valid_to": "2026-02-28T23:59:59Z"},
        )

        call_args = gs.execute_query.call_args
        props = call_args[1]["props"] if "props" in call_args[1] else call_args[0][1]["props"]
        assert props["valid_to"] == "2026-02-28T23:59:59Z"


class TestAddRelationshipTemporalValidity:
    """add_relationship should set valid_from on edges."""

    @pytest.mark.asyncio
    async def test_sets_valid_from_on_relationship(self):
        """Should set valid_from matching created_at."""
        gs = MagicMock()
        gs.execute_query = MagicMock(return_value={"success": True, "records": []})

        sk = _build_real_sk(gs)
        await sk.add_relationship(
            source_id="person-a",
            target_id="team-b",
            rel_type="member_of",
        )

        call_args = gs.execute_query.call_args
        props = call_args[1]["props"] if "props" in call_args[1] else call_args[0][1]["props"]
        assert "valid_from" in props
        assert "created_at" in props
        assert props["valid_from"] == props["created_at"]

    @pytest.mark.asyncio
    async def test_preserves_explicit_valid_from_on_relationship(self):
        """Should not overwrite explicitly provided valid_from."""
        gs = MagicMock()
        gs.execute_query = MagicMock(return_value={"success": True, "records": []})

        sk = _build_real_sk(gs)
        await sk.add_relationship(
            source_id="person-a",
            target_id="team-b",
            rel_type="member_of",
            properties={"valid_from": "2026-01-06T00:00:00Z"},
        )

        call_args = gs.execute_query.call_args
        props = call_args[1]["props"] if "props" in call_args[1] else call_args[0][1]["props"]
        assert props["valid_from"] == "2026-01-06T00:00:00Z"
