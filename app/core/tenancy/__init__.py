"""Tenant-context core primitive (Phase 4.1).

This package owns the tenancy *abstraction*. It ships single-tenant by default:
every request resolves to the one ``"default"`` tenant, and the default backend
implementations are thin adapters over today's process-global singletons, so
behavior is unchanged.

Hosted deployments plug in real backends + an org resolver in later phases
(4.2-4.7) without the core knowing about them.

Public surface:
    current_tenant_id   -- ContextVar[str], set per-request (default "default")
    get_current_tenant_id() -> str
    current_tenant()    -> TenantServiceContainer for the current tenant
    TenantResolver, DefaultSingleTenantResolver
    TenantServiceContainer
    TenantBackends + the five backend Protocols
    configure_tenancy(app, ...) -- wiring entrypoint
"""

from app.core.tenancy.context import (
    DEFAULT_TENANT_ID,
    current_tenant,
    current_tenant_id,
    get_current_tenant_id,
)
from app.core.tenancy.resolver import DefaultSingleTenantResolver, TenantResolver

__all__ = [
    "DEFAULT_TENANT_ID",
    "current_tenant",
    "current_tenant_id",
    "get_current_tenant_id",
    "TenantResolver",
    "DefaultSingleTenantResolver",
]
