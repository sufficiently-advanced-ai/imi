"""Tenant context middleware.

Resolves the tenant for each request and sets ``current_tenant_id`` for the
duration of the request, so downstream tenant-scoped accessors resolve to the
right container.

Phase 4.1 (core): the wired resolver is ``DefaultSingleTenantResolver``, so this
always sets ``"default"`` — effectively a no-op over the ContextVar default, and
therefore a no-behavior-change. It is installed now so that hosted's
a hosted org resolver (Phase 4.5) drops into the correct slot — and so the
public-endpoint resource-derived resolution and the 403-on-unprovisioned-org
rejection (also Phase 4.5) have a home.

Ordering: this middleware must run *after* ``AuthenticationMiddleware`` inbound
(it will later read ``request.state.user`` / ``org_id``). Because Starlette runs
middleware in reverse registration order, ``configure_tenancy`` must add it
*before* the auth middleware is added. See ``wiring.configure_tenancy``.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.middleware.request_context import current_tenant_id

logger = logging.getLogger(__name__)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Sets ``current_tenant_id`` per request from the configured resolver."""

    def __init__(self, app, resolver) -> None:
        # One-time guard: a misconfigured resolver (e.g. a downstream deployment
        # passing the wrong object to configure_tenancy) should fail loudly at
        # wiring time, not per request. Zero runtime cost.
        if resolver is None or not callable(getattr(resolver, "resolve", None)):
            raise TypeError(
                "TenantContextMiddleware requires a resolver implementing "
                f"async resolve(request); got {type(resolver).__name__}"
            )
        super().__init__(app)
        self._resolver = resolver

    async def dispatch(self, request: Request, call_next):
        # Resolvers signal "no provisioned tenant for this request" with
        # TenantResolutionError -> 403. Core's DefaultSingleTenantResolver never
        # raises, so this branch is inert in single-tenant mode (no behavior
        # change); it activates with the hosted resolvers in Phase 4.5.
        from app.core.tenancy.hosted_resolver import TenantResolutionError

        try:
            tenant_id = await self._resolver.resolve(request)
        except TenantResolutionError as exc:
            status_code = getattr(exc, "status_code", 403)
            # Record resolution denials for observability/security audit — these
            # can indicate misconfiguration or tenant-probing attempts.
            logger.warning(
                "Tenant resolution denied (%s) for %s %s: %s",
                status_code,
                request.method,
                request.url.path,
                exc,
            )
            return JSONResponse(status_code=status_code, content={"detail": str(exc)})
        token = current_tenant_id.set(tenant_id)
        request.state.tenant_id = tenant_id
        try:
            return await call_next(request)
        finally:
            # Reset so the ContextVar doesn't leak across requests handled on
            # the same task/thread.
            current_tenant_id.reset(token)
