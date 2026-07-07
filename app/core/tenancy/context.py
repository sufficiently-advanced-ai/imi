"""Tenant context accessors.

``current_tenant_id`` is declared in ``app/core/middleware/request_context.py``
alongside the other request-scoped ContextVars (reusing the already-defined
request-context machinery, per the multi-tenancy spec). It is re-exported here
so tenancy code has a single import home.

``current_tenant()`` resolves the per-tenant service container for whatever
tenant is current. Outside a request the ContextVar default (``"default"``)
applies, so scripts, background tasks, and tests transparently use the single
default container.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.middleware.request_context import (
    DEFAULT_TENANT_ID,
    current_tenant_id,
)

if TYPE_CHECKING:
    from app.core.tenancy.container import TenantServiceContainer

__all__ = [
    "DEFAULT_TENANT_ID",
    "current_tenant_id",
    "get_current_tenant_id",
    "current_tenant",
    "bind_current_tenant",
]


def get_current_tenant_id() -> str:
    """Return the current tenant id (``"default"`` when unset)."""
    return current_tenant_id.get()


def bind_current_tenant(fn):
    """Bind the CURRENT tenant to an async callable for deferred execution.

    ``TenantContextMiddleware`` resets ``current_tenant_id`` in its ``finally``
    before FastAPI ``BackgroundTasks`` run, so tenant-scoped work inside a
    background task would silently resolve the default tenant. Wrap the task
    at enqueue time::

        background_tasks.add_task(bind_current_tenant(process), bot_id)

    The wrapper re-sets the captured tenant around the invocation and restores
    the previous value afterwards.
    """
    import functools

    captured = current_tenant_id.get()

    @functools.wraps(fn)
    async def _bound(*args, **kwargs):
        token = current_tenant_id.set(captured)
        try:
            return await fn(*args, **kwargs)
        finally:
            current_tenant_id.reset(token)

    return _bound


def current_tenant() -> TenantServiceContainer:
    """Return the service container for the current tenant.

    Lazily builds the container on first access. In single-tenant mode this is
    always the one ``"default"`` container, whose resources are the existing
    process-global singletons.
    """
    # Imported lazily to avoid an import cycle (factory -> backends -> services
    # that may import this module).
    from app.core.tenancy.factory import get_container_factory

    return get_container_factory().get(get_current_tenant_id())
