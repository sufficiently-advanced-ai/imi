"""wipe_tenant_graph — tenant-scoped Neo4j deletion for KB rebuilds.

Covers: batching loop, guards (empty tenant, default-in-multi-tenant),
signals_only label scoping, and stats reporting. Uses a fake Neo4j client;
no live database required.
"""

import pytest

from app.services.graph.tenant_graph_wipe import (
    TenantWipeError,
    wipe_tenant_graph,
)


class FakeNeo4jClient:
    """Simulates batched DETACH DELETE over a node population."""

    def __init__(self, node_count: int):
        self.node_count = node_count
        self.write_queries: list[tuple[str, dict]] = []
        self.read_queries: list[tuple[str, dict]] = []

    async def execute_read(self, query: str, params: dict | None = None):
        self.read_queries.append((query, params or {}))
        return [{"nodes": self.node_count}]

    async def execute_write(self, query: str, params: dict | None = None):
        self.write_queries.append((query, params or {}))
        batch = (params or {}).get("batch", 1000)
        deleted = min(batch, self.node_count)
        self.node_count -= deleted
        return [{"deleted": deleted}]


@pytest.mark.asyncio
async def test_wipe_deletes_in_batches():
    client = FakeNeo4jClient(node_count=2500)
    stats = await wipe_tenant_graph(client, "tenant-a", batch_size=1000)

    assert stats["nodes_before"] == 2500
    assert stats["nodes_deleted"] == 2500
    assert stats["nodes_remaining_for_tenant"] == 0
    # 2500 nodes at batch 1000 → 3 write calls (1000, 1000, 500)
    assert len(client.write_queries) == 3
    for _query, params in client.write_queries:
        assert params["tenant_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_wipe_empty_graph_single_pass():
    client = FakeNeo4jClient(node_count=0)
    stats = await wipe_tenant_graph(client, "tenant-a")
    assert stats["nodes_deleted"] == 0
    assert len(client.write_queries) == 1


@pytest.mark.asyncio
async def test_signals_only_scopes_label():
    client = FakeNeo4jClient(node_count=10)
    stats = await wipe_tenant_graph(client, "tenant-a", signals_only=True)
    assert stats["signals_only"] is True
    assert all(":Signal" in q for q, _ in client.write_queries)
    assert all(":Signal" in q for q, _ in client.read_queries)


@pytest.mark.asyncio
async def test_full_wipe_has_no_label_filter():
    client = FakeNeo4jClient(node_count=10)
    await wipe_tenant_graph(client, "tenant-a", signals_only=False)
    assert all(":Signal" not in q for q, _ in client.write_queries)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_tenant", ["", "   ", None])
async def test_empty_tenant_refused(bad_tenant):
    client = FakeNeo4jClient(node_count=10)
    with pytest.raises(TenantWipeError):
        await wipe_tenant_graph(client, bad_tenant)
    assert client.write_queries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_batch", [0, -1, 0.5, "1000", None])
async def test_non_positive_batch_size_refused(bad_batch):
    """batch_size <= 0 would never terminate the deletion loop."""
    client = FakeNeo4jClient(node_count=10)
    with pytest.raises(TenantWipeError, match="batch_size"):
        await wipe_tenant_graph(client, "tenant-a", batch_size=bad_batch)
    assert client.write_queries == []


@pytest.mark.asyncio
async def test_default_tenant_refused_in_multi_tenant_mode(monkeypatch):
    import app.services.graph.factory as factory

    monkeypatch.setattr(factory, "is_multi_tenant_graph_backend", lambda: True)
    client = FakeNeo4jClient(node_count=10)
    with pytest.raises(TenantWipeError, match="default"):
        await wipe_tenant_graph(client, "default")
    assert client.write_queries == []


@pytest.mark.asyncio
async def test_default_tenant_allowed_in_single_tenant_mode(monkeypatch):
    import app.services.graph.factory as factory

    monkeypatch.setattr(factory, "is_multi_tenant_graph_backend", lambda: False)
    client = FakeNeo4jClient(node_count=5)
    stats = await wipe_tenant_graph(client, "default")
    assert stats["nodes_deleted"] == 5


@pytest.mark.asyncio
async def test_single_tenant_wipe_includes_unscoped_legacy_nodes(monkeypatch):
    """Pre-#953 graphs have nodes without tenant_id; in single-tenant mode
    they belong to the only tenant and must be matched by the wipe."""
    import app.services.graph.factory as factory

    monkeypatch.setattr(factory, "is_multi_tenant_graph_backend", lambda: False)
    client = FakeNeo4jClient(node_count=5)
    await wipe_tenant_graph(client, "default")
    assert all("tenant_id IS NULL" in q for q, _ in client.write_queries)


@pytest.mark.asyncio
async def test_multi_tenant_wipe_never_touches_unscoped_nodes(monkeypatch):
    import app.services.graph.factory as factory

    monkeypatch.setattr(factory, "is_multi_tenant_graph_backend", lambda: True)
    client = FakeNeo4jClient(node_count=5)
    await wipe_tenant_graph(client, "org-acme")
    assert all("IS NULL" not in q for q, _ in client.write_queries)


@pytest.mark.asyncio
async def test_include_unscoped_refused_in_multi_tenant_mode(monkeypatch):
    import app.services.graph.factory as factory

    monkeypatch.setattr(factory, "is_multi_tenant_graph_backend", lambda: True)
    client = FakeNeo4jClient(node_count=5)
    with pytest.raises(TenantWipeError, match="include_unscoped"):
        await wipe_tenant_graph(client, "org-acme", include_unscoped=True)
    assert client.write_queries == []
