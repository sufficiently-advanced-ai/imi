"""Tests for Knowledge Graph Factory.

Source: app/services/graph/factory.py

Tests the singleton pattern, Neo4j vs fallback selection, and cache clearing.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.services.graph import factory


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the global singleton before and after each test."""
    original = factory._knowledge_graph
    factory._knowledge_graph = None
    yield
    factory._knowledge_graph = original


# ──────────────────────────────────────────────────────────────
# get_knowledge_graph
# ──────────────────────────────────────────────────────────────


class TestGetKnowledgeGraph:
    def test_creates_neo4j_instance(self):
        """When Neo4j is available, should create Neo4jKnowledgeGraph."""
        from app.services.graph.neo4j_graph import Neo4jKnowledgeGraph

        mock_client = MagicMock()
        mock_client._driver = MagicMock()  # driver is not None → Neo4j is initialized

        mock_domain_service = MagicMock()
        mock_domain_service.get_active_domain.return_value = MagicMock()

        with (
            patch("app.neo4j_client.get_neo4j_client", return_value=mock_client),
            patch(
                "app.core.dependencies.get_domain_config_service",
                return_value=mock_domain_service,
            ),
        ):
            result = factory.get_knowledge_graph()

        assert isinstance(result, Neo4jKnowledgeGraph)

    def test_returns_cached_instance(self):
        """Second call returns the same instance (singleton)."""
        mock_client = MagicMock()
        mock_client._driver = MagicMock()

        mock_domain_service = MagicMock()
        mock_domain_service.get_active_domain.return_value = MagicMock()

        with (
            patch("app.neo4j_client.get_neo4j_client", return_value=mock_client),
            patch(
                "app.core.dependencies.get_domain_config_service",
                return_value=mock_domain_service,
            ),
        ):
            first = factory.get_knowledge_graph()
            second = factory.get_knowledge_graph()

        assert first is second



# ──────────────────────────────────────────────────────────────
# clear_knowledge_graph_cache
# ──────────────────────────────────────────────────────────────


class TestClearKnowledgeGraphCache:
    def test_calls_invalidate_cache(self):
        """When graph exists, should call invalidate_cache."""
        mock_graph = MagicMock()
        factory._knowledge_graph = mock_graph

        factory.clear_knowledge_graph_cache()

        mock_graph.invalidate_cache.assert_called_once()

    def test_no_graph_doesnt_raise(self):
        """When no graph is initialized, should not raise."""
        factory._knowledge_graph = None
        factory.clear_knowledge_graph_cache()  # should not raise
