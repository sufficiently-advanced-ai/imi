"""Tenant-scoped Neo4j wipe — the per-tenant counterpart of clear_all_data().

The hosted edition shares one Neo4j across tenants (tenant_id property on
every node/edge, see app/core/tenancy/backends/neo4j_scoped.py), so the
global ``clear_all_data()`` is forbidden there (startup guard in main.py).
A KB rebuild still needs to clear *one* tenant's derived graph state; this
module provides that primitive. Also usable for tenant offboarding.

Only nodes carrying this tenant's ``tenant_id`` property are touched —
DETACH DELETE removes their relationships regardless of edge direction.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1000


class TenantWipeError(ValueError):
    """Raised when a wipe request fails its safety guards."""


def tenant_match_clause(label: str, include_unscoped: bool) -> str:
    """Cypher MATCH+WHERE selecting one tenant's nodes.

    ``include_unscoped`` additionally matches nodes with NO tenant_id
    property — legacy graphs built before tenant stamping (issue #953) have
    these, and in a single-tenant deployment they belong to the default
    tenant by definition.
    """
    if include_unscoped:
        return (
            f"MATCH (n{label}) "
            "WHERE n.tenant_id = $tenant_id OR n.tenant_id IS NULL "
        )
    return f"MATCH (n{label} {{tenant_id: $tenant_id}}) "


async def wipe_tenant_graph(
    client: Any,
    tenant_id: str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    signals_only: bool = False,
    include_unscoped: bool | None = None,
) -> dict[str, Any]:
    """Delete every node (and attached relationship) for one tenant.

    Args:
        client: Neo4j client exposing ``execute_read`` / ``execute_write``.
        tenant_id: the tenant whose graph data is wiped. Must be non-empty;
            wiping the "default" tenant is refused while a multi-tenant
            backend is active (that would be the global-wipe foot-gun this
            module exists to prevent).
        batch_size: nodes deleted per transaction (memory-pressure guard,
            mirrors clear_all_data()).
        signals_only: only delete ``:Signal`` nodes, leaving entity nodes in
            place (lighter pass when entities are known-good).
        include_unscoped: also delete nodes lacking a tenant_id property
            (pre-#953 legacy data). Defaults to True in single-tenant mode —
            unscoped nodes belong to the only tenant there — and False when
            a multi-tenant backend is active (unscoped nodes are ambiguous;
            never delete another tenant's possible data).

    Returns:
        dict with nodes_before, nodes_deleted, nodes_remaining_for_tenant.
    """
    if not tenant_id or not str(tenant_id).strip():
        raise TenantWipeError("wipe_tenant_graph requires a non-empty tenant_id")
    if not isinstance(batch_size, int) or batch_size <= 0:
        raise TenantWipeError(
            f"wipe_tenant_graph requires batch_size > 0 (got {batch_size!r}) — "
            "a non-positive batch never terminates the deletion loop"
        )

    from app.services.graph.factory import is_multi_tenant_graph_backend

    multi_tenant = is_multi_tenant_graph_backend()
    if tenant_id == "default" and multi_tenant:
        raise TenantWipeError(
            "Refusing to wipe tenant 'default' while a multi-tenant graph "
            "backend is active — unscoped legacy nodes would make this a "
            "global wipe. Run with an explicit tenant id."
        )

    if include_unscoped is None:
        include_unscoped = not multi_tenant
    elif include_unscoped and multi_tenant:
        raise TenantWipeError(
            "include_unscoped=True is not allowed with a multi-tenant graph "
            "backend — unscoped nodes cannot be attributed to one tenant."
        )

    label = ":Signal" if signals_only else ""
    match = tenant_match_clause(label, include_unscoped)
    count_query = match + "RETURN count(n) AS nodes"
    delete_query = (
        match + "WITH n LIMIT $batch DETACH DELETE n RETURN count(*) AS deleted"
    )

    count_result = await client.execute_read(count_query, {"tenant_id": tenant_id})
    nodes_before = count_result[0]["nodes"] if count_result else 0

    total_deleted = 0
    while True:
        result = await client.execute_write(
            delete_query, {"tenant_id": tenant_id, "batch": batch_size}
        )
        deleted = result[0]["deleted"] if result else 0
        total_deleted += deleted
        if deleted < batch_size:
            break

    remaining_result = await client.execute_read(count_query, {"tenant_id": tenant_id})
    remaining = remaining_result[0]["nodes"] if remaining_result else 0

    stats = {
        "tenant_id": tenant_id,
        "signals_only": signals_only,
        "nodes_before": nodes_before,
        "nodes_deleted": total_deleted,
        "nodes_remaining_for_tenant": remaining,
    }
    logger.info(
        "wipe_tenant_graph: tenant=%s signals_only=%s deleted=%d (before=%d, remaining=%d)",
        tenant_id,
        signals_only,
        total_deleted,
        nodes_before,
        remaining,
    )
    return stats
