"""Phase 4.7 tests — bounded LRU eviction of idle tenant containers."""

import pytest

from app.core.tenancy.backends.base import TenantBackends, TenantDescriptor
from app.core.tenancy.factory import TenantContainerFactory


class _FakeRegistry:
    def get(self, tenant_id):
        return TenantDescriptor(tenant_id=tenant_id, status="active")

    def list(self):
        return []


def _backends():
    # Only the registry is touched during container build; others are unused
    # because container properties are lazy.
    return TenantBackends(
        registry=_FakeRegistry(),
        relational=object(),
        graph=object(),
        corpus=object(),
        vector=object(),
        resolver=object(),
    )


def test_unbounded_by_default_never_evicts():
    f = TenantContainerFactory(_backends())
    for tid in ("a", "b", "c", "d", "e"):
        f.get(tid)
    assert set(f.cached_tenant_ids()) == {"a", "b", "c", "d", "e"}


def test_invalid_max_rejected():
    with pytest.raises(ValueError):
        TenantContainerFactory(_backends(), max_containers=0)


def test_get_caches_same_container():
    f = TenantContainerFactory(_backends())
    assert f.get("a") is f.get("a")


def test_lru_eviction_drops_least_recently_used():
    f = TenantContainerFactory(_backends(), max_containers=2)
    f.get("a")
    f.get("b")
    f.get("c")  # exceeds cap -> evict LRU ("a")
    assert f.cached_tenant_ids() == ["b", "c"]


def test_access_refreshes_recency():
    f = TenantContainerFactory(_backends(), max_containers=2)
    f.get("a")
    f.get("b")
    f.get("a")  # touch "a" -> now MRU; "b" is LRU
    f.get("c")  # evict "b"
    assert set(f.cached_tenant_ids()) == {"a", "c"}


def test_on_evict_hook_called():
    evicted = []
    f = TenantContainerFactory(
        _backends(), max_containers=1, on_evict=lambda tid, c: evicted.append(tid)
    )
    f.get("a")
    f.get("b")  # evicts "a"
    assert evicted == ["a"]


def test_on_evict_failure_does_not_break_get():
    def boom(tid, c):
        raise RuntimeError("cleanup failed")

    f = TenantContainerFactory(_backends(), max_containers=1, on_evict=boom)
    f.get("a")
    # Eviction hook raises internally but get() must still succeed.
    container = f.get("b")
    assert container.tenant_id == "b"
    assert f.cached_tenant_ids() == ["b"]


def test_get_rejects_blank_tenant_id():
    f = TenantContainerFactory(_backends())
    for bad in (None, "", "   ", 123):
        with pytest.raises(ValueError):
            f.get(bad)


def test_get_strips_whitespace():
    f = TenantContainerFactory(_backends())
    c = f.get("  acme  ")
    assert c.tenant_id == "acme"
    assert f.cached_tenant_ids() == ["acme"]


def test_full_clear_runs_on_evict_hook():
    evicted = []
    f = TenantContainerFactory(_backends(), on_evict=lambda tid, c: evicted.append(tid))
    f.get("a")
    f.get("b")
    # reset()/install_backends() must run on_evict on every dropped container,
    # not silently clear (else a hosted cleanup hook leaks resources).
    f.reset()
    assert sorted(evicted) == ["a", "b"]
    assert f.cached_tenant_ids() == []


def test_install_backends_runs_on_evict_hook():
    evicted = []
    f = TenantContainerFactory(_backends(), on_evict=lambda tid, c: evicted.append(tid))
    f.get("a")
    f.install_backends(_backends())
    assert evicted == ["a"]
