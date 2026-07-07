"""Per-tenant service container.

Holds (lazily) the stateful services for one tenant: git ops, knowledge graph,
file/folder caches, entity registry, resolved domain config, vector index, and
a DB session handle. Each property delegates to the appropriate backend the
first time it is touched.

In single-tenant mode there is exactly one container (``tenant_id == "default"``)
and every property resolves to today's global singleton, so behavior is
unchanged. The caches the container *owns* directly (``file_cache`` /
``folder_cache``) are created once per container, matching the previous single
module-global instance.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.tenancy.backends.base import TenantBackends, TenantDescriptor


class TenantServiceContainer:
    """Lazily-instantiated holder of one tenant's stateful services."""

    def __init__(self, tenant_id: str, descriptor: TenantDescriptor, backends: TenantBackends) -> None:
        self.tenant_id = tenant_id
        self.descriptor = descriptor
        self._backends = backends
        # Container-owned caches (pure in-memory, no backend needed).
        self._file_cache: Any = None
        self._folder_cache: Any = None
        self._signal_store: Any = None

    # --- corpus ---------------------------------------------------------
    @property
    def git_ops(self) -> Any:
        return self._backends.corpus.git_ops_for(self.tenant_id, self.descriptor)

    # --- graph ----------------------------------------------------------
    @property
    def graph(self) -> Any:
        return self._backends.graph.graph_for(self.tenant_id)

    # --- entity registry ------------------------------------------------
    @property
    def entity_registry(self) -> Any:
        # Single-tenant: the existing EntityRegistry process singleton (its
        # __new__ is intentionally retained in Phase 4.1 — see the entity
        # registry module). Per-tenant instances are a later-phase concern.
        from app.services.entity_registry import EntityRegistry

        return EntityRegistry()

    # --- caches ---------------------------------------------------------
    @property
    def file_cache(self) -> Any:
        if self._file_cache is None:
            from app.services.file_cache import FileCache

            self._file_cache = FileCache()
        return self._file_cache

    @property
    def folder_cache(self) -> Any:
        if self._folder_cache is None:
            from app.services.file_cache import FolderCache

            self._folder_cache = FolderCache()
        return self._folder_cache

    # --- signal store ---------------------------------------------------
    @property
    def signal_store(self) -> Any:
        # Single-tenant: one disk-backed SignalStore over the signals/ dir.
        # The module-global ``signal_store`` proxy (and ~20 call sites incl.
        # the decisions endpoint and DETECT_SUPERSESSION/CONFLICTS) resolve
        # through here; without this property they raise AttributeError. The
        # hosted edition overrides this via a per-tenant memory backend.
        if self._signal_store is None:
            from app.services.signal_store import SignalStore

            self._signal_store = SignalStore()
        return self._signal_store

    # --- domain config --------------------------------------------------
    @property
    def domain_config(self) -> Any:
        # Single-tenant: the module-global active domain loaded at import time.
        from app.core.domain_config.active_domain import _resolve_active_domain

        return _resolve_active_domain()

    # --- vector ---------------------------------------------------------
    @property
    def vector_index_dir(self) -> str:
        return self._backends.vector.index_dir_for(self.tenant_id)

    # --- relational -----------------------------------------------------
    def session_factory(self) -> Any:
        """Return the tenant's ``async_sessionmaker`` (or None if uninitialized)."""
        return self._backends.relational.session_factory_for(self.tenant_id)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        """Yield a DB session for this tenant (mirrors ``get_database_session``)."""
        factory = self.session_factory()
        if factory is None:
            raise RuntimeError("Database session factory is not initialized.")
        async with factory() as s:
            yield s
