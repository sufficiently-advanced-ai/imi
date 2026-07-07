"""Phase 4.3 tests — Neo4j tenant_id scoping (central chokepoint).

Verifies the TenantGraphScope enforcement helper and the scoped GraphBackend
without a live Neo4j. The scope is the single point every read/write routes
through, so testing it proves the isolation contract.
"""

import pytest

from app.core.tenancy.backends.neo4j_scoped import (
    TENANT_PROPERTY,
    ScopedGraphView,
    ScopedNeo4jGraphBackend,
    TenantGraphScope,
)


def test_scope_requires_tenant_id():
    with pytest.raises(ValueError):
        TenantGraphScope("")


def test_node_props_stamps_tenant_id():
    scope = TenantGraphScope("acme")
    props = scope.node_props({"id": "sig-1", "text": "hi"})
    assert props["id"] == "sig-1"
    assert props[TENANT_PROPERTY] == "acme"


def test_node_props_handles_none():
    assert TenantGraphScope("acme").node_props()[TENANT_PROPERTY] == "acme"


def test_where_scopes_each_variable():
    scope = TenantGraphScope("acme")
    assert scope.where("s") == "s.tenant_id = $tenant_id"
    assert scope.where("s", "e") == "s.tenant_id = $tenant_id AND e.tenant_id = $tenant_id"


def test_where_requires_variables():
    with pytest.raises(ValueError):
        TenantGraphScope("acme").where()


def test_params_merges_tenant_id():
    scope = TenantGraphScope("acme")
    p = scope.params({"id": "x"})
    assert p == {"id": "x", "tenant_id": "acme"}


def test_guard_write_params_rejects_cross_tenant():
    scope = TenantGraphScope("acme")
    scope.guard_write_params({"tenant_id": "acme"})  # ok
    with pytest.raises(ValueError):
        scope.guard_write_params({"tenant_id": "other"})
    with pytest.raises(ValueError):
        scope.guard_write_params({})  # missing = fail closed


def test_backend_returns_scoped_view_per_tenant():
    sentinel_graph = object()
    backend = ScopedNeo4jGraphBackend(lambda: sentinel_graph)
    view_a = backend.graph_for("acme")
    view_b = backend.graph_for("other")
    assert isinstance(view_a, ScopedGraphView)
    assert view_a.scope.tenant_id == "acme"
    assert view_b.scope.tenant_id == "other"


def test_scoped_view_falls_through_to_graph():
    class FakeGraph:
        def __init__(self):
            self.nodes = {"n1": 1}

        def query(self):
            return "ok"

    view = ScopedGraphView(FakeGraph(), TenantGraphScope("acme"))
    # Attribute + method access falls through to the wrapped graph.
    assert view.nodes == {"n1": 1}
    assert view.query() == "ok"
    assert view.scope.tenant_id == "acme"
