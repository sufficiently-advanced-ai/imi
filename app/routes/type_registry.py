"""Type Registry API routes — Issue #877

Admin surface for the graduated-typing registry. Lists types the system has
seen (canonical, provisional, aliased, deprecated) and lets admins change
their status.

Authentication: these endpoints are protected by `AuthenticationMiddleware`
(registered globally in `app/main.py`), which enforces session auth
on every non-public path. There is no admin-role concept in the codebase
yet, so any authenticated user can promote/alias/deprecate types. That is
acceptable for the single-tenant MVP deployment (one container per KB,
trusted operators). Gate with an admin role check when roles land —
see tracking note in app/services/auth if/when that lands.

MVP semantics: status flips happen in the registry only. Instance-level
migration of `_type_status` and bulk relationship-type rewrites are
intentionally deferred — the frontend uses the registry snapshot + instance
tags at render time.

Promote returns a suggested YAML snippet so an admin can commit the change
to `config/domains/*.yaml` via the normal PR flow.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.domain_config.active_domain import get_domain_config
from app.model_schemas.type_registry import TypeEntry, TypeKind, TypeStatus
from app.services.graph.type_registry import (
    RegistryBackendError,
    get_type_registry_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/type-registry", tags=["type-registry"])


def _backend_error(err: RegistryBackendError) -> HTTPException:
    """Translate a registry-backend failure into a 503 so callers see a
    storage outage instead of an empty list / 404 / 500.
    """
    return HTTPException(
        status_code=503,
        detail=f"Type registry backend unavailable: {err}",
    )


class AliasRequest(BaseModel):
    target: str = Field(..., description="Canonical name to alias this type to")


class PromoteResponse(BaseModel):
    entry: TypeEntry
    yaml_snippet: str = Field(
        ..., description="Suggested YAML diff to commit to the domain file"
    )


def _require_service():
    service = get_type_registry_service()
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Type registry unavailable (Neo4j not initialized)",
        )
    return service


def _current_domain_id() -> str:
    return get_domain_config().id


@router.get("", response_model=list[TypeEntry])
async def list_types(
    kind: TypeKind | None = Query(None),
    status: TypeStatus | None = Query(None),
    domain_id: str | None = Query(
        None, description="Defaults to the active domain"
    ),
) -> list[TypeEntry]:
    service = _require_service()
    try:
        return await service.list(
            domain_id=domain_id or _current_domain_id(),
            kind=kind,
            status=status,
        )
    except RegistryBackendError as e:
        raise _backend_error(e) from e


@router.get("/{kind}/{name}", response_model=TypeEntry)
async def get_type_entry(kind: TypeKind, name: str) -> TypeEntry:
    service = _require_service()
    try:
        entry = await service.get(name, kind, _current_domain_id())
    except RegistryBackendError as e:
        raise _backend_error(e) from e
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Type '{name}' not found")
    return entry


@router.post("/{kind}/{name}/promote", response_model=PromoteResponse)
async def promote_type(kind: TypeKind, name: str) -> PromoteResponse:
    """Mark a provisional type as canonical in the registry and return a
    suggested YAML snippet for the admin to commit to the domain file.

    Does NOT modify the YAML or migrate existing instance `_type_status`.
    """
    service = _require_service()
    domain_id = _current_domain_id()

    try:
        existing = await service.get(name, kind, domain_id)
    except RegistryBackendError as e:
        raise _backend_error(e) from e
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Type '{name}' not found")
    if existing.status == TypeStatus.CANONICAL:
        raise HTTPException(
            status_code=400, detail=f"Type '{name}' is already canonical"
        )

    try:
        updated = await service.set_status(
            name, kind, domain_id, TypeStatus.CANONICAL, aliased_to=None
        )
    except RegistryBackendError as e:
        raise _backend_error(e) from e
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update registry")

    return PromoteResponse(entry=updated, yaml_snippet=_yaml_snippet(updated))


@router.post("/{kind}/{name}/alias", response_model=TypeEntry)
async def alias_type(
    kind: TypeKind, name: str, body: AliasRequest
) -> TypeEntry:
    """Alias a provisional type to a canonical name. The frontend can fold
    aliased edges into the target at render/query time.

    The target must already be canonical — aliasing to a provisional or
    unknown type would just create a second layer of indirection.
    """
    service = _require_service()
    domain_id = _current_domain_id()

    if body.target == name:
        raise HTTPException(
            status_code=400, detail="Cannot alias a type to itself"
        )

    try:
        existing = await service.get(name, kind, domain_id)
    except RegistryBackendError as e:
        raise _backend_error(e) from e
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Type '{name}' not found")
    if existing.status == TypeStatus.CANONICAL:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot alias canonical type '{name}'. Canonical types are "
                "declared in domain YAML and must be edited there."
            ),
        )

    try:
        target_entry = await service.get(body.target, kind, domain_id)
    except RegistryBackendError as e:
        raise _backend_error(e) from e
    target_is_canonical = (
        target_entry is not None and target_entry.status == TypeStatus.CANONICAL
    )
    if not target_is_canonical:
        # Accept a target that's canonical-by-YAML even if it has no registry
        # row yet (e.g. freshly-added to the domain config and never used).
        from app.services.graph.type_registry import is_canonical as _is_canon

        if not _is_canon(body.target, kind):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Alias target '{body.target}' must be a canonical {kind.value} "
                    "type (declared in domain YAML or promoted in the registry)."
                ),
            )

    try:
        updated = await service.set_status(
            name, kind, domain_id, TypeStatus.ALIASED, aliased_to=body.target
        )
    except RegistryBackendError as e:
        raise _backend_error(e) from e
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update registry")
    return updated


@router.post("/{kind}/{name}/deprecate", response_model=TypeEntry)
async def deprecate_type(kind: TypeKind, name: str) -> TypeEntry:
    service = _require_service()
    domain_id = _current_domain_id()
    try:
        existing = await service.get(name, kind, domain_id)
    except RegistryBackendError as e:
        raise _backend_error(e) from e
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Type '{name}' not found")
    if existing.status == TypeStatus.CANONICAL:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot deprecate canonical type '{name}'. Remove it from "
                "the domain YAML instead."
            ),
        )

    try:
        updated = await service.set_status(
            name, kind, domain_id, TypeStatus.DEPRECATED, aliased_to=None
        )
    except RegistryBackendError as e:
        raise _backend_error(e) from e
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update registry")
    return updated


@router.delete("/{kind}/{name}")
async def delete_type(kind: TypeKind, name: str) -> dict[str, Any]:
    """Remove an entry from the registry. Refuses to delete canonical types
    — those should be deprecated or reworked in the domain YAML instead.
    """
    service = _require_service()
    domain_id = _current_domain_id()
    try:
        existing = await service.get(name, kind, domain_id)
    except RegistryBackendError as e:
        raise _backend_error(e) from e
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Type '{name}' not found")
    if existing.status == TypeStatus.CANONICAL:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot delete a canonical type. Remove it from the domain "
                "YAML or deprecate it instead."
            ),
        )

    try:
        ok = await service.delete(name, kind, domain_id)
    except RegistryBackendError as e:
        raise _backend_error(e) from e
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete entry")
    return {"deleted": True, "name": name, "kind": kind.value}


def _yaml_snippet(entry: TypeEntry) -> str:
    """Return a suggested YAML snippet for promoting a type to canonical.

    This is advisory — an admin pastes it into `config/domains/{domain}.yaml`
    under the appropriate entity and commits via PR. Keeps the YAML as the
    auditable source of truth for canonical types.
    """
    if entry.kind == TypeKind.RELATIONSHIP:
        source = entry.context.get("source_type", "<source_entity>")
        target = entry.context.get("target_type", "<target_entity>")
        return (
            f"# Add to entities.{source}.relationships in the domain YAML:\n"
            f"- type: {entry.name}\n"
            f"  target: {target}\n"
            f"  cardinality: many-to-many  # adjust as needed\n"
        )
    if entry.kind == TypeKind.ATTRIBUTE:
        entity_type = entry.context.get("entity_type", "<entity_type>")
        return (
            f"# Add to entities.{entity_type}.attributes in the domain YAML:\n"
            f"- name: {entry.name}\n"
            f"  type: string  # adjust as needed\n"
            f"  required: false\n"
        )
    # entity (rare — entity types shouldn't flex, but keep the code path)
    return (
        f"# Add a new entity under `entities` in the domain YAML:\n"
        f"{entry.name}:\n"
        f"  description: <describe this entity>\n"
        f"  plural: {entry.name}s\n"
    )
