"""Tenancy wiring entrypoint.

``configure_tenancy(app)`` installs the tenant backend bundle into the process
container factory and registers ``TenantContextMiddleware``. It mirrors the
existing ``configure_request_context(app)`` helper so it can be called inline
from ``app/main.py`` today, and absorbed unchanged by a future Phase 1c
``create_app()`` factory.

Core calls it with no arguments → single-tenant defaults. Hosted calls it with
its own ``resolver`` and/or ``backends`` (Postgres+RLS, scoped Neo4j,
per-tenant corpus/FAISS).
"""

from __future__ import annotations

import logging

from app.core.tenancy.backends.base import TenantBackends
from app.core.tenancy.resolver import TenantResolver

logger = logging.getLogger(__name__)


def configure_tenancy(
    app,
    *,
    resolver: TenantResolver | None = None,
    backends: TenantBackends | None = None,
):
    """Install tenant backends + middleware on ``app``.

    Args:
        app: the FastAPI/Starlette application.
        resolver: override the tenant resolver (default: single-tenant). If both
            ``resolver`` and ``backends`` are given, ``resolver`` wins.
        backends: override the full backend bundle (default: single-tenant
            adapters over today's globals).
    """
    from app.core.tenancy.backends.default import build_default_backends
    from app.core.tenancy.factory import get_container_factory
    from app.core.tenancy.middleware import TenantContextMiddleware

    backends = backends or build_default_backends()
    if resolver is not None:
        backends.resolver = resolver

    get_container_factory().install_backends(backends)

    # Registered before AuthenticationMiddleware in main.py so it runs *after*
    # auth inbound (Starlette = reverse registration order). Startup failures
    # here are intentionally NOT caught — a broken tenancy wiring must prevent
    # the app from booting rather than start in an inconsistent state.
    app.add_middleware(TenantContextMiddleware, resolver=backends.resolver)
    logger.info(
        "Tenancy configured: resolver=%s, registry=%s",
        type(backends.resolver).__name__,
        type(backends.registry).__name__,
    )
    return app
