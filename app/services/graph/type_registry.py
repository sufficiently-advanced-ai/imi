"""Type Registry Service — Issue #877

Neo4j-backed store for graduated typing. Tracks which entity types,
relationship types, and attribute keys have been *seen* in the graph, and
whether each is canonical (declared in domain YAML), provisional
(user-created), aliased, or deprecated.

Storage: `(:_TypeRegistry {name, kind, status, domain_id, ...})` meta-nodes.
Unique constraint on (name, kind, domain_id).

The registry is a summary — the hard truth is the instance data itself.
It can be rebuilt from the graph at any time via `rebuild_from_instances`.
"""

import json
import logging
from typing import Any

from app.core.domain_config.active_domain import get_domain_config
from app.model_schemas.domain_config import DomainConfiguration
from app.model_schemas.type_registry import TypeEntry, TypeKind, TypeStatus
from app.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

_CONSTRAINT_CYPHER = """
CREATE CONSTRAINT type_registry_unique IF NOT EXISTS
FOR (t:_TypeRegistry)
REQUIRE (t.name, t.kind, t.domain_id) IS UNIQUE
"""

_UPSERT_CYPHER = """
MERGE (t:_TypeRegistry {name: $name, kind: $kind, domain_id: $domain_id})
ON CREATE SET
    t.status = $status,
    t.created_at = $created_at,
    t.created_by = $created_by,
    t.usage_count = $usage_count,
    t.aliased_to = $aliased_to,
    t.context = $context
ON MATCH SET
    t.usage_count = coalesce(t.usage_count, 0) + $usage_increment
RETURN t
"""

_SET_STATUS_CYPHER = """
MATCH (t:_TypeRegistry {name: $name, kind: $kind, domain_id: $domain_id})
SET t.status = $status,
    t.aliased_to = $aliased_to
RETURN t
"""

_LIST_CYPHER = """
MATCH (t:_TypeRegistry)
WHERE ($domain_id IS NULL OR t.domain_id = $domain_id)
  AND ($kind IS NULL OR t.kind = $kind)
  AND ($status IS NULL OR t.status = $status)
RETURN t
ORDER BY t.usage_count DESC, t.name ASC
"""

_GET_CYPHER = """
MATCH (t:_TypeRegistry {name: $name, kind: $kind, domain_id: $domain_id})
RETURN t
"""

_DELETE_CYPHER = """
MATCH (t:_TypeRegistry {name: $name, kind: $kind, domain_id: $domain_id})
DELETE t
RETURN count(*) AS deleted
"""


def _canonical_relationship_types(
    domain: DomainConfiguration,
) -> set[str]:
    types: set[str] = set()
    for entity in domain.entities.values():
        for rel in entity.relationships:
            types.add(rel.type)
            if rel.inverse_name:
                types.add(rel.inverse_name)
    return types


def _canonical_entity_types(domain: DomainConfiguration) -> set[str]:
    return set(domain.entities.keys())


def _canonical_attribute_keys(
    domain: DomainConfiguration, entity_type: str | None = None
) -> set[str]:
    keys: set[str] = set()
    for ename, entity in domain.entities.items():
        if entity_type is not None and ename != entity_type:
            continue
        for attr in entity.attributes:
            keys.add(attr.name)
    return keys


def is_canonical(
    name: str,
    kind: TypeKind,
    domain: DomainConfiguration | None = None,
    *,
    entity_type: str | None = None,
) -> bool:
    """Check whether a type name is declared in the domain YAML.

    For attributes, `entity_type` narrows the check to that entity's
    attribute list. If omitted, any entity declaring the attribute
    counts as canonical.
    """
    domain = domain or get_domain_config()
    if kind == TypeKind.ENTITY:
        return name in _canonical_entity_types(domain)
    if kind == TypeKind.RELATIONSHIP:
        return name in _canonical_relationship_types(domain)
    if kind == TypeKind.ATTRIBUTE:
        return name in _canonical_attribute_keys(domain, entity_type)
    return False


class RegistryBackendError(Exception):
    """Raised when the registry backend (Neo4j) fails on a CRUD call.

    Distinguishes a storage/availability failure from a normal "not found"
    result, so route handlers can return 503 instead of an empty/404 that
    would look like missing data.

    `record_usage`/`_upsert` intentionally do NOT raise this — those run
    from the graph-mutation hot path and must remain best-effort.
    """


class TypeRegistryService:
    """CRUD and usage-tracking for the type registry.

    Read/write CRUD methods (`get`, `list`, `set_status`, `delete`) raise
    `RegistryBackendError` when the underlying Neo4j call fails so callers
    can distinguish backend outage from "not found". `record_usage` /
    `_upsert` remain best-effort (log + return None) because they are
    invoked from graph-write paths that should not fail because of a
    summary-side problem.
    """

    def __init__(self, neo4j_client: Neo4jClient):
        self._client = neo4j_client

    async def ensure_constraints(self) -> None:
        try:
            await self._client.execute_write(_CONSTRAINT_CYPHER)
        except Exception as e:
            logger.warning(f"[TYPE_REGISTRY] constraint create failed: {e}")

    async def record_usage(
        self,
        name: str,
        kind: TypeKind,
        domain_id: str,
        *,
        created_by: str = "system",
        context: dict[str, Any] | None = None,
        entity_type: str | None = None,
    ) -> TypeEntry | None:
        """Record that a type was used (on create/update).

        Status is derived automatically: canonical if declared in YAML,
        otherwise provisional.
        """
        if not name:
            return None

        canonical = is_canonical(name, kind, entity_type=entity_type)
        status = TypeStatus.CANONICAL if canonical else TypeStatus.PROVISIONAL

        entry = TypeEntry(
            name=name,
            kind=kind,
            status=status,
            domain_id=domain_id,
            created_by=("domain_yaml" if canonical else created_by),
            usage_count=1,
            context=context or {},
        )
        return await self._upsert(entry, usage_increment=1)

    async def _upsert(
        self, entry: TypeEntry, *, usage_increment: int = 1
    ) -> TypeEntry | None:
        try:
            rows = await self._client.execute_write(
                _UPSERT_CYPHER,
                {
                    "name": entry.name,
                    "kind": entry.kind.value,
                    "domain_id": entry.domain_id,
                    "status": entry.status.value,
                    "created_at": entry.created_at,
                    "created_by": entry.created_by,
                    "usage_count": entry.usage_count,
                    "aliased_to": entry.aliased_to,
                    # Neo4j properties cannot hold maps — store as JSON text
                    # (decoded back to a dict in _row_to_entry).
                    "context": json.dumps(entry.context or {}),
                    "usage_increment": usage_increment,
                },
            )
        except Exception as e:
            logger.warning(f"[TYPE_REGISTRY] upsert failed for {entry.name}: {e}")
            return None
        return _row_to_entry(rows[0] if rows else None)

    async def get(
        self, name: str, kind: TypeKind, domain_id: str
    ) -> TypeEntry | None:
        try:
            rows = await self._client.execute_read(
                _GET_CYPHER,
                {"name": name, "kind": kind.value, "domain_id": domain_id},
            )
        except Exception as e:
            logger.warning(f"[TYPE_REGISTRY] get failed for {name}: {e}")
            raise RegistryBackendError(f"get '{name}' failed: {e}") from e
        return _row_to_entry(rows[0] if rows else None)

    async def list(
        self,
        domain_id: str | None = None,
        kind: TypeKind | None = None,
        status: TypeStatus | None = None,
    ) -> list[TypeEntry]:
        try:
            rows = await self._client.execute_read(
                _LIST_CYPHER,
                {
                    "domain_id": domain_id,
                    "kind": kind.value if kind else None,
                    "status": status.value if status else None,
                },
            )
        except Exception as e:
            logger.warning(f"[TYPE_REGISTRY] list failed: {e}")
            raise RegistryBackendError(f"list failed: {e}") from e
        return [e for e in (_row_to_entry(r) for r in rows) if e is not None]

    async def set_status(
        self,
        name: str,
        kind: TypeKind,
        domain_id: str,
        status: TypeStatus,
        *,
        aliased_to: str | None = None,
    ) -> TypeEntry | None:
        try:
            rows = await self._client.execute_write(
                _SET_STATUS_CYPHER,
                {
                    "name": name,
                    "kind": kind.value,
                    "domain_id": domain_id,
                    "status": status.value,
                    "aliased_to": aliased_to,
                },
            )
        except Exception as e:
            logger.warning(f"[TYPE_REGISTRY] set_status failed for {name}: {e}")
            raise RegistryBackendError(
                f"set_status '{name}' failed: {e}"
            ) from e
        return _row_to_entry(rows[0] if rows else None)

    async def delete(
        self, name: str, kind: TypeKind, domain_id: str
    ) -> bool:
        try:
            rows = await self._client.execute_write(
                _DELETE_CYPHER,
                {"name": name, "kind": kind.value, "domain_id": domain_id},
            )
        except Exception as e:
            logger.warning(f"[TYPE_REGISTRY] delete failed for {name}: {e}")
            raise RegistryBackendError(f"delete '{name}' failed: {e}") from e
        return bool(rows and rows[0].get("deleted", 0) > 0)


def _decode_context(raw: Any) -> dict[str, Any]:
    """Decode the JSON-text context property back to a dict.

    Tolerates legacy values: None/missing and (hypothetical) dict values
    pass through; malformed JSON degrades to {} rather than dropping the row.
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _row_to_entry(row: Any) -> TypeEntry | None:
    if not row:
        return None
    node = row.get("t") if isinstance(row, dict) else row
    if node is None:
        return None
    props = dict(node)
    try:
        return TypeEntry(
            name=props["name"],
            kind=TypeKind(props["kind"]),
            status=TypeStatus(props["status"]),
            domain_id=props["domain_id"],
            created_at=props.get("created_at", ""),
            created_by=props.get("created_by", "system"),
            usage_count=int(props.get("usage_count", 0) or 0),
            aliased_to=props.get("aliased_to"),
            context=_decode_context(props.get("context")),
        )
    except Exception as e:
        logger.warning(f"[TYPE_REGISTRY] deserialize failed: {e} for {props!r}")
        return None


_registry_singleton: TypeRegistryService | None = None


def get_type_registry_service() -> TypeRegistryService | None:
    """Return the shared registry service, or None if Neo4j is unavailable."""
    global _registry_singleton
    from app.neo4j_client import get_neo4j_client

    client = get_neo4j_client()
    if client is None:
        return None
    if _registry_singleton is None or _registry_singleton._client is not client:
        _registry_singleton = TypeRegistryService(client)
    return _registry_singleton
