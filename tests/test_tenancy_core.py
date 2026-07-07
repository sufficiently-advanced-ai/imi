"""Phase 4.1 tenancy core tests.

Covers the no-behavior-change guarantees of the tenant-context primitive:
  * the ContextVar defaults to "default" outside a request (scripts, tasks,
    tests resolve to the one default container);
  * tenant-scoped accessors return today's singletons (identity preserved);
  * the module-global proxies forward transparently;
  * the unknown-tenant path is rejected in single-tenant mode;
  * TenantContextMiddleware sets the ContextVar and propagates it to the route
    handler (settles the BaseHTTPMiddleware ContextVar-propagation question for
    this Starlette version).
"""

import asyncio

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.tenancy import (
    DEFAULT_TENANT_ID,
    current_tenant,
    get_current_tenant_id,
)
from app.core.tenancy.factory import get_container_factory
from app.core.tenancy.middleware import TenantContextMiddleware
from app.core.tenancy.proxy import _ContainerProxy
from app.core.tenancy.resolver import DefaultSingleTenantResolver


def test_default_tenant_id_outside_request():
    assert get_current_tenant_id() == DEFAULT_TENANT_ID


def test_current_tenant_returns_default_container():
    container = current_tenant()
    assert container.tenant_id == DEFAULT_TENANT_ID


def test_container_factory_caches_container():
    f = get_container_factory()
    assert f.get(DEFAULT_TENANT_ID) is f.get(DEFAULT_TENANT_ID)


def test_unknown_tenant_rejected_in_single_tenant_mode():
    with pytest.raises(KeyError):
        get_container_factory().get("some-other-tenant")


def test_git_ops_proxy_resolves_and_is_stable():
    from app.git_ops import git_ops

    # Proxy forwards attribute access to the container's GitOperations.
    assert git_ops.repo_path.endswith("/repo")
    # Same underlying instance each resolution (backend memoizes per tenant).
    assert current_tenant().git_ops is current_tenant().git_ops


def test_file_cache_proxy_transparency():
    from app.services.file_cache import FileCache, FolderCache, file_cache, folder_cache

    assert isinstance(file_cache, _ContainerProxy)
    assert isinstance(folder_cache, _ContainerProxy)
    # Underlying real instances of the right type, stable across access.
    assert isinstance(current_tenant().file_cache, FileCache)
    assert isinstance(current_tenant().folder_cache, FolderCache)
    assert current_tenant().file_cache is current_tenant().file_cache


def test_get_entity_registry_preserves_singleton_identity():
    from app.services.entity_registry import EntityRegistry, get_entity_registry

    # __new__ singleton retained: accessor, direct construction, and container
    # all yield the same instance (production code constructs EntityRegistry()
    # directly in several places and relies on this).
    assert get_entity_registry() is EntityRegistry()
    assert current_tenant().entity_registry is EntityRegistry()


def test_get_knowledge_graph_delegates_to_container():
    from app.services.graph.factory import get_knowledge_graph

    assert get_knowledge_graph() is current_tenant().graph


def test_get_domain_config_delegates_to_container():
    from app.core.domain_config import get_domain_config

    assert get_domain_config() is current_tenant().domain_config


def test_proxy_forwards_calls():
    class Target:
        def __init__(self):
            self.value = 7

        def echo(self, x):
            return x

    target = Target()
    proxy = _ContainerProxy(lambda _c: target, "t")
    # Patch resolution: temporarily point current_tenant at our target via a
    # selector that ignores the container.
    assert proxy.value == 7
    assert proxy.echo("hi") == "hi"
    proxy.value = 9
    assert target.value == 9


def test_middleware_sets_and_propagates_tenant_id():
    """TenantContextMiddleware must set current_tenant_id for the handler.

    This also verifies the ContextVar set inside a BaseHTTPMiddleware reaches
    the route handler in this Starlette version (the classic propagation
    gotcha). In single-tenant mode the value is always "default".
    """

    async def homepage(request):
        return JSONResponse(
            {
                "ctx_tenant": get_current_tenant_id(),
                "state_tenant": getattr(request.state, "tenant_id", None),
            }
        )

    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(TenantContextMiddleware, resolver=DefaultSingleTenantResolver())

    async def _call():
        # Drive the ASGI app directly (this environment's TestClient wrapper is
        # incompatible with the installed httpx — same issue that breaks several
        # pre-existing test modules). ASGITransport exercises the real request
        # path, so this is a genuine end-to-end ContextVar-propagation check.
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.get("/")

    resp = asyncio.run(_call())
    assert resp.status_code == 200
    body = resp.json()
    assert body["state_tenant"] == DEFAULT_TENANT_ID
    assert body["ctx_tenant"] == DEFAULT_TENANT_ID


def test_default_resolver_returns_default():
    resolver = DefaultSingleTenantResolver()
    assert asyncio.run(resolver.resolve(None)) == DEFAULT_TENANT_ID
