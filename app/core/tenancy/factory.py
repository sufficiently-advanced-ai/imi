"""Tenant container factory.

Maps ``tenant_id -> TenantServiceContainer``, building lazily and caching. A
process-global factory is exposed via ``get_container_factory()``.

If no backends have been configured (e.g. a script or test imports a tenant-
scoped accessor without booting the app), the factory **auto-installs the
single-tenant default backends**. This is what lets non-request code resolve to
the one default container transparently — the basis of "tests stay green".

``configure_tenancy`` (or hosted) installs a different ``TenantBackends`` bundle
via ``set_container_factory`` / ``install_backends``.

NOTE: LRU eviction of idle containers is a Phase 4.7 (scale-hardening) concern;
this factory pins containers for the process lifetime. In single-tenant mode
that is at most one container, so it is a non-issue here.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable

from app.core.tenancy.backends.base import TenantBackends
from app.core.tenancy.container import TenantServiceContainer

logger = logging.getLogger(__name__)


class TenantContainerFactory:
    """Builds and caches per-tenant service containers.

    Phase 4.7: optionally bounded by ``max_containers`` with LRU eviction of idle
    tenants, so a busy multi-tenant deployment doesn't pin every tenant's
    in-memory graph/caches forever. ``max_containers=None`` (the default) means
    unbounded — which is correct for single-tenant (at most one container), so
    core behavior is unchanged.
    """

    def __init__(
        self,
        backends: TenantBackends | None = None,
        *,
        max_containers: int | None = None,
        on_evict: Callable[[str, TenantServiceContainer], None] | None = None,
    ) -> None:
        if max_containers is not None and max_containers < 1:
            raise ValueError("max_containers must be >= 1 or None (unbounded)")
        self._backends = backends
        self._max = max_containers
        self._on_evict = on_evict
        # OrderedDict as an LRU: most-recently-used at the end.
        self._containers: OrderedDict[str, TenantServiceContainer] = OrderedDict()

    def install_backends(self, backends: TenantBackends) -> None:
        """Install/replace the backend bundle and drop cached containers."""
        self._backends = backends
        self._clear_cached_containers()

    def _clear_cached_containers(self) -> None:
        """Drop all cached containers, running on_evict for each.

        Used instead of a bare dict.clear() so resource cleanup (the on_evict
        hook) also runs on full-cache clears (install_backends/reset), not only
        on LRU eviction — otherwise a hosted on_evict that closes connections
        would leak them on a clear.
        """
        while self._containers:
            tenant_id, container = self._containers.popitem(last=False)
            if self._on_evict is not None:
                try:
                    self._on_evict(tenant_id, container)
                except Exception:
                    logger.exception("on_evict hook failed for tenant %s", tenant_id)

    @property
    def backends(self) -> TenantBackends:
        if self._backends is None:
            # Lazy single-tenant default — see module docstring.
            from app.core.tenancy.backends.default import build_default_backends

            self._backends = build_default_backends()
        return self._backends

    def get(self, tenant_id: str) -> TenantServiceContainer:
        # Guard against a None/empty/whitespace id reaching the cache or backend
        # (would create a meaningless container under a junk key).
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError(f"Invalid tenant_id for container lookup: {tenant_id!r}")
        tenant_id = tenant_id.strip()

        container = self._containers.get(tenant_id)
        if container is not None:
            # Mark most-recently-used.
            self._containers.move_to_end(tenant_id)
            return container

        descriptor = self.backends.registry.get(tenant_id)
        container = TenantServiceContainer(tenant_id, descriptor, self.backends)
        self._containers[tenant_id] = container  # inserted at end (MRU)
        self._evict_if_needed()
        return container

    def _evict_if_needed(self) -> None:
        if self._max is None:
            return
        while len(self._containers) > self._max:
            evicted_id, evicted = self._containers.popitem(last=False)  # LRU end
            logger.info("Evicting idle tenant container: %s", evicted_id)
            if self._on_evict is not None:
                try:
                    self._on_evict(evicted_id, evicted)
                except Exception:  # eviction cleanup must not break the request
                    logger.exception("on_evict hook failed for tenant %s", evicted_id)

    def cached_tenant_ids(self) -> list[str]:
        """Return currently-cached tenant ids, LRU-first (for observability/tests)."""
        return list(self._containers.keys())

    def reset(self) -> None:
        """Drop all cached containers (next access rebuilds)."""
        self._clear_cached_containers()


_factory: TenantContainerFactory | None = None


def get_container_factory() -> TenantContainerFactory:
    """Return the process-global tenant container factory (creating it lazily)."""
    global _factory
    if _factory is None:
        _factory = TenantContainerFactory()
    return _factory


def set_container_factory(factory: TenantContainerFactory) -> None:
    """Replace the process-global factory (used by hosted / tests)."""
    global _factory
    _factory = factory
