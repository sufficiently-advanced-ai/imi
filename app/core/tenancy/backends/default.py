"""Single-tenant default backend implementations.

Each is a thin adapter over today's process-global singletons, so the one
``"default"`` tenant reproduces current behavior exactly. Hosted replaces these
(Postgres+RLS, scoped Neo4j, per-tenant corpus/FAISS) in later phases.

Heavy imports are done lazily inside methods to avoid import cycles (these
adapters reach back into services that may, transitively, import tenancy).
"""

from __future__ import annotations

import os
from typing import Any

from app.core.middleware.request_context import DEFAULT_TENANT_ID
from app.core.tenancy.backends.base import TenantBackends, TenantDescriptor
from app.core.tenancy.resolver import DefaultSingleTenantResolver


class DefaultTenantRegistry:
    """One in-memory tenant row built from ``settings`` + ``ACTIVE_DOMAIN``."""

    def __init__(self) -> None:
        self._descriptor: TenantDescriptor | None = None

    def _build(self) -> TenantDescriptor:
        from app.config import get_settings

        settings = get_settings()
        return TenantDescriptor(
            tenant_id=DEFAULT_TENANT_ID,
            git_repo_url=getattr(settings, "GIT_REPO_URL", None),
            git_token=getattr(settings, "GITHUB_TOKEN", None),
            domain_id=os.environ.get("ACTIVE_DOMAIN"),
            graph_scope=None,  # single Neo4j, no scoping in core
            status="active",
        )

    def get(self, tenant_id: str) -> TenantDescriptor:
        if tenant_id != DEFAULT_TENANT_ID:
            raise KeyError(
                f"Unknown tenant '{tenant_id}'. Core ships single-tenant; only "
                f"'{DEFAULT_TENANT_ID}' exists. Multi-tenant registries are hosted (Phase 4.4)."
            )
        if self._descriptor is None:
            self._descriptor = self._build()
        return self._descriptor

    def list(self) -> list[TenantDescriptor]:
        return [self.get(DEFAULT_TENANT_ID)]


class DefaultRelationalBackend:
    """Returns the existing SQLite session factory from ``app.database``.

    Reads the module global at call time so it reflects whatever startup
    initialization (``create_database_session``) produced — identity preserved.
    """

    def session_factory_for(self, tenant_id: str) -> Any:
        from app import database

        return database._session_factory


class DefaultGraphBackend:
    """Returns the existing legacy knowledge graph singleton.

    Delegates to the factory's internal resolver, which retains the original
    lazy Neo4j/in-memory-fallback logic and module-global caching, so the graph
    instance is identical to today's.
    """

    def graph_for(self, tenant_id: str) -> Any:
        from app.services.graph.factory import _resolve_default_knowledge_graph

        return _resolve_default_knowledge_graph()


class DefaultCorpusBackend:
    """Owns one ``GitOperations`` per tenant (single instance for 'default').

    ``GitOperations.__init__`` only computes the repo path (no I/O), so lazy
    construction here is side-effect free and matches the previous module-global
    instance behavior. The single-tenant instance uses the same default
    ``<repo>/`` working dir as before.
    """

    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}

    def git_ops_for(self, tenant_id: str, descriptor: TenantDescriptor) -> Any:
        inst = self._instances.get(tenant_id)
        if inst is None:
            from app.git_ops import GitOperations

            inst = GitOperations()
            self._instances[tenant_id] = inst
        return inst


class DefaultVectorBackend:
    """Returns the FAISS index directory (today's hardcoded location)."""

    def index_dir_for(self, tenant_id: str) -> str:
        from app.services import semantica_init

        return semantica_init.FAISS_INDEX_DIR


def build_default_backends() -> TenantBackends:
    """Assemble the single-tenant default backend bundle."""
    return TenantBackends(
        registry=DefaultTenantRegistry(),
        relational=DefaultRelationalBackend(),
        graph=DefaultGraphBackend(),
        corpus=DefaultCorpusBackend(),
        vector=DefaultVectorBackend(),
        resolver=DefaultSingleTenantResolver(),
    )
