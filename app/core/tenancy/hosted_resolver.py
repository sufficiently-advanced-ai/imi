"""Hosted tenant resolvers (Phase 4.5).

Implements spec §4: the tenant is derived from the **session only** for
authenticated requests (never a client-supplied header/body — the primary
defense against tenant spoofing), and from a **resource id** for public
endpoints (e.g. a webhook secret).

  * ``ResourceTenantResolver`` — public path: pluggable strategies resolve by a
    request-derived resource id (e.g. webhook secret).
  * ``CompositeTenantResolver`` — tries authenticated resolution, then public.

Core's ``DefaultSingleTenantResolver`` is unaffected; these are installed in
multi-tenant deployments via ``configure_tenancy(resolver=...)``, which also
supply their own session-based authenticated resolver.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from starlette.requests import Request

logger = logging.getLogger(__name__)


class TenantResolutionError(Exception):
    """Raised when a request cannot be attributed to a provisioned tenant.

    The middleware translates this to HTTP 403 (see TenantContextMiddleware).
    """

    def __init__(self, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


# A public-endpoint strategy: (request) -> tenant_id or None.
ResourceStrategy = Callable[[Request], Awaitable[str | None]]


class ResourceTenantResolver:
    """Public-endpoint resolution by request-derived resource id.

    Each strategy inspects the request (webhook secret/signature, ...) and
    returns a tenant_id or None. The first match wins.
    """

    def __init__(self, strategies: list[ResourceStrategy]) -> None:
        self._strategies = strategies

    async def resolve(self, request: Request) -> str:
        for strategy in self._strategies:
            tenant_id = await strategy(request)
            if tenant_id:
                return tenant_id
        raise TenantResolutionError(
            f"Could not resolve tenant for public resource {request.url.path}"
        )


class CompositeTenantResolver:
    """Try authenticated resolution; fall back to public-resource resolution.

    Used so one resolver handles both session-bearing requests and public
    endpoints (webhooks) listed in PUBLIC_ENDPOINTS.
    """

    def __init__(self, authenticated, public) -> None:
        self._authenticated = authenticated
        self._public = public

    async def resolve(self, request: Request) -> str:
        try:
            return await self._authenticated.resolve(request)
        except TenantResolutionError:
            return await self._public.resolve(request)
