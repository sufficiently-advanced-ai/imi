"""Tenant resolution.

A ``TenantResolver`` maps an incoming request to a tenant id. Core ships
``DefaultSingleTenantResolver`` (always ``"default"``); hosted supplies
an org-based resolver (org_id -> tenant_id via the registry) in Phase 4.5.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from starlette.requests import Request

from app.core.middleware.request_context import DEFAULT_TENANT_ID

__all__ = ["TenantResolver", "DefaultSingleTenantResolver"]


@runtime_checkable
class TenantResolver(Protocol):
    """Resolves the tenant id for a request.

    Implementations MUST derive the tenant from trusted state only (the
    authenticated session, or a resource id for public endpoints) — never from
    a client-supplied header/body. That rule is the primary defense against
    tenant spoofing; it is enforced by hosted resolvers in Phase 4.5.
    """

    async def resolve(self, request: Request) -> str:  # pragma: no cover - protocol
        ...


class DefaultSingleTenantResolver:
    """Single-tenant resolver: every request is the one ``"default"`` tenant."""

    async def resolve(self, request: Request) -> str:
        return DEFAULT_TENANT_ID
