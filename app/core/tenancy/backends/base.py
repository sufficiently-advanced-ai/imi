"""Backend interfaces for tenant-scoped stateful resources.

Each stateful resource a tenant owns is reached through one of these Protocols,
so the implementation can differ per edition:

  * core  -> single-tenant impls (SQLite, single Neo4j, single git dir, local
    FAISS, one-row in-memory registry) -- see ``default.py``.
  * hosted -> shared-store impls (Postgres+RLS, tenant_id-scoped Neo4j,
    per-tenant corpus/FAISS) -- Phases 4.2-4.5, not in this repo.

``TenantServiceContainer`` is assembled *from* these backends, lazily, per
tenant id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.core.tenancy.resolver import TenantResolver

__all__ = [
    "TenantDescriptor",
    "RelationalBackend",
    "GraphBackend",
    "CorpusBackend",
    "VectorBackend",
    "TenantRegistry",
    "TenantBackends",
]


@dataclass
class TenantDescriptor:
    """Per-tenant configuration resolved from the registry.

    In single-tenant mode this is one row built from ``settings`` (the existing
    ``GIT_REPO_URL`` / ``GITHUB_TOKEN`` / ``ACTIVE_DOMAIN``). In hosted it comes
    from the ``tenants`` table (Phase 4.4). Fields beyond ``tenant_id`` are
    optional so the single-tenant descriptor can omit what it doesn't need.
    """

    tenant_id: str
    git_repo_url: str | None = None
    git_token: str | None = None
    domain_id: str | None = None
    graph_scope: str | None = None
    status: str = "active"
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RelationalBackend(Protocol):
    """Provides the SQLAlchemy engine / session factory for a tenant."""

    def session_factory_for(self, tenant_id: str) -> Any:
        """Return an ``async_sessionmaker`` bound to the tenant's database."""
        ...


@runtime_checkable
class GraphBackend(Protocol):
    """Provides the knowledge graph for a tenant."""

    def graph_for(self, tenant_id: str) -> Any:
        """Return the (legacy) knowledge graph object for the tenant."""
        ...


@runtime_checkable
class CorpusBackend(Protocol):
    """Provides the git corpus operations object for a tenant."""

    def git_ops_for(self, tenant_id: str, descriptor: TenantDescriptor) -> Any:
        """Return a ``GitOperations`` bound to the tenant's working dir/remote."""
        ...


@runtime_checkable
class VectorBackend(Protocol):
    """Provides the vector index location/handle for a tenant."""

    def index_dir_for(self, tenant_id: str) -> str:
        """Return the FAISS index directory for the tenant."""
        ...


@runtime_checkable
class TenantRegistry(Protocol):
    """Looks up / lists tenant descriptors."""

    def get(self, tenant_id: str) -> TenantDescriptor:
        """Return the descriptor for ``tenant_id`` (raise if unknown)."""
        ...

    def list(self) -> list[TenantDescriptor]:
        """Return all known tenant descriptors."""
        ...


@dataclass
class TenantBackends:
    """Bundle of the backends + resolver used to assemble containers.

    ``configure_tenancy`` installs one of these into the container factory.
    Hosted swaps individual fields (e.g. a Postgres ``RelationalBackend``) while
    reusing the rest.
    """

    registry: TenantRegistry
    relational: RelationalBackend
    graph: GraphBackend
    corpus: CorpusBackend
    vector: VectorBackend
    resolver: TenantResolver
