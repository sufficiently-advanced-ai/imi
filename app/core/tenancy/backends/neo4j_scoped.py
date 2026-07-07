"""Hosted tenant-scoped Neo4j GraphBackend (Phase 4.3).

Spec §5.2: a single shared Neo4j instance, every node and relationship carrying
a ``tenant_id`` property, with isolation enforced **centrally** in the graph
write/query layer so no individual Cypher call hand-rolls the filter. This module
is that central chokepoint plus the ``GraphBackend`` implementation.

Why property-scoping (not database-per-tenant): consistency with the Postgres
"one shared store, partition by tenant_id" model, and Neo4j multi-database is
Enterprise-only (compose runs Community). Safety net: the git corpus is
authoritative and physically isolated, and the graph is a rebuildable cache — a
scoping bug cannot corrupt the source of truth. Database-per-tenant on Neo4j
Enterprise is the documented future upgrade.

Core keeps the unscoped single-Neo4j ``DefaultGraphBackend``; this runs only when
a deployment installs it via ``configure_tenancy(backends=...)``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

TENANT_PROPERTY = "tenant_id"


class TenantGraphScope:
    """The single enforcement point for tenant isolation in Cypher.

    Rather than letting each query hand-roll a ``tenant_id`` filter (easy to
    forget = a leak), all reads and writes route their property maps, WHERE
    predicates, and params through one of these. One instance per tenant.
    """

    def __init__(self, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("TenantGraphScope requires a non-empty tenant_id")
        self.tenant_id = tenant_id

    def node_props(self, props: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Stamp ``tenant_id`` onto a node/relationship property map for writes.

        Used in MERGE/CREATE property maps so every written node and edge is
        owned by this tenant.
        """
        merged = dict(props or {})
        merged[TENANT_PROPERTY] = self.tenant_id
        return merged

    def where(self, *variables: str) -> str:
        """Return a Cypher predicate scoping the given bound variables.

        e.g. ``scope.where("s", "e")`` ->
        ``s.tenant_id = $tenant_id AND e.tenant_id = $tenant_id``.
        Callers AND this into their WHERE clause.
        """
        if not variables:
            raise ValueError("where() needs at least one variable to scope")
        return " AND ".join(f"{v}.{TENANT_PROPERTY} = ${TENANT_PROPERTY}" for v in variables)

    def params(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Merge ``$tenant_id`` into a query parameter dict."""
        merged = dict(extra or {})
        merged[TENANT_PROPERTY] = self.tenant_id
        return merged

    def guard_write_params(self, params: Mapping[str, Any]) -> None:
        """Assert a write's params carry this tenant's id (fail closed).

        A defensive check the writer can call before executing a mutation, so a
        write that somehow lost its tenant scoping raises instead of silently
        landing unscoped.
        """
        actual = params.get(TENANT_PROPERTY)
        if actual != self.tenant_id:
            raise ValueError(
                f"Refusing cross-tenant graph write: params tenant_id={actual!r} "
                f"!= scope tenant_id={self.tenant_id!r}"
            )


class ScopedGraphView:
    """Thin wrapper pairing the shared legacy graph with a tenant scope.

    Returned by the backend as the per-tenant graph handle. Exposes the
    underlying graph plus the ``scope`` the write/query layer uses to enforce
    isolation. Attribute access falls through to the wrapped graph so existing
    callers keep working.
    """

    def __init__(self, graph: Any, scope: TenantGraphScope) -> None:
        object.__setattr__(self, "_graph", graph)
        object.__setattr__(self, "scope", scope)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_graph"), name)


class ScopedNeo4jGraphBackend:
    """GraphBackend that returns a tenant-scoped view of the shared graph."""

    def __init__(self, base_graph_provider) -> None:
        # base_graph_provider() -> the shared legacy graph (e.g. the existing
        # _resolve_default_knowledge_graph). Kept injectable for testing.
        self._provider = base_graph_provider
        self._scopes: dict[str, TenantGraphScope] = {}

    def graph_for(self, tenant_id: str) -> ScopedGraphView:
        scope = self._scopes.get(tenant_id)
        if scope is None:
            scope = TenantGraphScope(tenant_id)
            self._scopes[tenant_id] = scope
        return ScopedGraphView(self._provider(), scope)
