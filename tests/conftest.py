"""Shared test fixtures and configuration."""
import pytest
import asyncio
import sys
import os
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime

# Add the parent directory to the Python path so we can import app modules
# This works both locally and in the container
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# ---------------------------------------------------------------------------
# Environment-gated test modules. The legacy never-collectable files that used
# to live in this list (tests of modules never synced into the community
# edition) were deleted 2026-07 — the suite is module-complete: every test
# here targets code that exists in this repo.
# ---------------------------------------------------------------------------
collect_ignore = [
    # requires the `docker` python package (not a project dependency)
    "issue-360_test_container_deployment.py",
]


# ---------------------------------------------------------------------------
# requires_neo4j marker — skip integration tests when no Neo4j is reachable.
# CI provides one via a service container (NEO4J_URI=bolt://localhost:7687);
# a bare `pytest` run skips these instead of failing on connection errors.
# ---------------------------------------------------------------------------
_neo4j_reachable_cache = None


def _neo4j_reachable() -> bool:
    global _neo4j_reachable_cache
    if _neo4j_reachable_cache is None:
        import socket
        from urllib.parse import urlparse

        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        parsed = urlparse(uri)
        host, port = parsed.hostname or "localhost", parsed.port or 7687
        try:
            with socket.create_connection((host, port), timeout=1):
                _neo4j_reachable_cache = True
        except OSError:
            _neo4j_reachable_cache = False
    return _neo4j_reachable_cache


def pytest_collection_modifyitems(config, items):
    if _neo4j_reachable():
        return
    skip_neo4j = pytest.mark.skip(reason="Neo4j not reachable (set NEO4J_URI or start a local instance)")
    for item in items:
        if "requires_neo4j" in item.keywords:
            item.add_marker(skip_neo4j)


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_git_ops():
    """Mock GitOperations for testing."""
    with patch('app.main.git_ops') as mock:
        mock.get_status = AsyncMock(return_value="connected")
        mock.read_markdown_files = AsyncMock(return_value=[
            {"path": "test.md", "content": "# Test Content"}
        ])
        mock.initialize = AsyncMock()
        yield mock


@pytest.fixture
def mock_anthropic():
    """Mock Anthropic client for testing."""
    with patch('anthropic.Anthropic') as mock:
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Test response")]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5
        mock.return_value.messages.create = AsyncMock(return_value=mock_response)
        yield mock


@pytest.fixture
def mock_claude_client():
    """Mock ClaudeClient for testing."""
    mock_client = AsyncMock()
    mock_client.query = AsyncMock(return_value="Test response")
    return mock_client


@pytest.fixture
def mock_file_cache() -> AsyncMock:
    """Mock FileCache for testing."""
    mock_cache = AsyncMock()
    mock_cache.get_file = AsyncMock(return_value=None)
    mock_cache.set_file = AsyncMock()
    return mock_cache


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables for testing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
    monkeypatch.setenv("GIT_REPO_URL", "https://github.com/test/repo.git")
    monkeypatch.setenv("GITHUB_TOKEN", "test-github-token")


@pytest.fixture(autouse=True)
def disable_rate_limiting(monkeypatch):
    """Neuter the app's rate-limiting middleware for tests.

    The middleware's token bucket is keyed per client host on the process-wide
    app instance, so rapid TestClient requests from earlier tests in a segment
    starve later ones (429 responses without the JSON body tests expect).
    """
    try:
        from app.core.middleware.rate_limiter import RateLimiter
    except ImportError:
        yield
        return
    monkeypatch.setattr(RateLimiter, "is_allowed", lambda self, request: (True, {}))
    yield
