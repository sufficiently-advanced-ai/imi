"""Pluggable per-tenant backends.

``base`` defines the five backend Protocols + the ``TenantDescriptor`` and
``TenantBackends`` bundle. ``default`` provides the single-tenant
implementations (thin adapters over today's globals) that core ships.
"""

from app.core.tenancy.backends.base import (
    CorpusBackend,
    GraphBackend,
    RelationalBackend,
    TenantBackends,
    TenantDescriptor,
    TenantRegistry,
    VectorBackend,
)

__all__ = [
    "RelationalBackend",
    "GraphBackend",
    "CorpusBackend",
    "VectorBackend",
    "TenantRegistry",
    "TenantDescriptor",
    "TenantBackends",
]
