"""Container-resolving proxy for module-global singletons.

Several singletons are imported by value at ~60 call sites each, e.g.::

    from app.git_ops import git_ops
    git_ops.commit(...)

To make these tenant-scoped without touching every call site, the module-global
name is replaced with a ``_ContainerProxy`` whose attribute/call access is
forwarded to the corresponding attribute on the *current* tenant's container,
resolved at call time. In single-tenant mode that resolves to the one default
container, so behavior is unchanged.

Only ordinary attribute access and direct calls are forwarded. Dunder protocol
methods (``__iter__``, ``__len__`` ...) are intentionally *not* proxied because
Python resolves them on the type, not the instance; none of the proxied
singletons (``git_ops``, ``file_cache``, ``folder_cache``) are used through such
protocols.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class _ContainerProxy:
    """Transparent proxy to an attribute on the current tenant container."""

    __slots__ = ("_selector", "_label")

    def __init__(self, selector: Callable[[Any], Any], label: str = "") -> None:
        # Bypass __setattr__ (which would forward to the target).
        object.__setattr__(self, "_selector", selector)
        object.__setattr__(self, "_label", label)

    def _resolve(self) -> Any:
        from app.core.tenancy.context import current_tenant

        return object.__getattribute__(self, "_selector")(current_tenant())

    def __getattr__(self, name: str) -> Any:
        # Only called for names not found normally (i.e. not _selector/_label).
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._resolve(), name, value)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve()(*args, **kwargs)

    def __repr__(self) -> str:
        label = object.__getattribute__(self, "_label")
        return f"<_ContainerProxy {label!r} -> current_tenant()>"
