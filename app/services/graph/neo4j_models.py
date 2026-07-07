"""
Neo4j Models — Domain-Driven Type Mapping and Property Serialization

Reads DomainEntity.attributes to determine what properties to set/validate
on Neo4j nodes, rather than maintaining a hardcoded mapping.
"""

import logging
from datetime import date, datetime
from typing import Any

from app.model_schemas.domain_config import DomainEntity

logger = logging.getLogger(__name__)

# Mapping from domain attribute types to Neo4j-compatible Python types
_TYPE_COERCIONS = {
    "string": str,
    "number": float,
    "date": str,        # Store as ISO string; Neo4j date() can be used in Cypher
    "datetime": str,    # Store as ISO string
    "boolean": bool,
    "enum": str,        # Enums stored as strings, validated at app level
}


def coerce_property_value(value: Any, attr_type: str) -> Any:
    """Coerce a metadata value to the appropriate Neo4j-storable type.

    Args:
        value: Raw value from frontmatter/metadata
        attr_type: Domain attribute type (string, number, date, etc.)

    Returns:
        Coerced value suitable for Neo4j property storage
    """
    if value is None:
        return None

    coerce_fn = _TYPE_COERCIONS.get(attr_type, str)

    try:
        if attr_type in ("date", "datetime"):
            if isinstance(value, (date, datetime)):
                return value.isoformat()
            return str(value)
        if attr_type == "number":
            if isinstance(value, str):
                # Handle currency/unit strings like "$1000000"
                cleaned = value.replace("$", "").replace(",", "").strip()
                return float(cleaned) if cleaned else None
            return float(value)
        if attr_type == "boolean":
            if isinstance(value, str):
                return value.lower() in ("true", "yes", "1")
            return bool(value)
        return coerce_fn(value)
    except (ValueError, TypeError) as e:
        logger.debug(f"Could not coerce {value!r} to {attr_type}: {e}")
        return str(value) if value else None


def build_node_properties(
    metadata: dict[str, Any],
    entity_def: DomainEntity,
    entity_id: str,
) -> dict[str, Any]:
    """Build Neo4j node properties from metadata using domain entity definition.

    Args:
        metadata: Raw frontmatter/metadata dict from the markdown file
        entity_def: DomainEntity definition with attribute specs
        entity_id: The entity's unique ID

    Returns:
        Dictionary of properties ready for Neo4j MERGE/SET
    """
    properties: dict[str, Any] = {
        "id": entity_id,
        "name": metadata.get("name", ""),
    }

    # Add canonical_name for search
    name = metadata.get("name", "")
    properties["canonical_name"] = name.lower().strip() if name else ""

    # Set properties from domain-defined attributes
    for attr in entity_def.attributes:
        raw_value = metadata.get(attr.name)
        if raw_value is None:
            if attr.required:
                logger.warning(
                    "Missing required attribute '%s' for entity %s",
                    attr.name, entity_id,
                )
            continue
        coerced = coerce_property_value(raw_value, attr.type)
        if coerced is not None:
            properties[attr.name] = coerced

    # Preserve extra metadata that isn't in the domain schema
    # (e.g., title, company, department from person profiles)
    extra_keys = {"title", "role", "department", "company", "email", "contact",
                  "status", "description", "type"}
    for key in extra_keys:
        if key not in properties and key in metadata:
            val = metadata[key]
            if isinstance(val, (str, int, float, bool)):
                properties[key] = val

    # Aliases from frontmatter feed the EntityResolver's alias index so
    # known surface-form variants resolve to this node instead of minting
    # duplicates.
    aliases = metadata.get("aliases")
    if isinstance(aliases, str):
        aliases = [aliases]
    if isinstance(aliases, list):
        cleaned = [a.strip() for a in aliases if isinstance(a, str) and a.strip()]
        if cleaned:
            properties["aliases"] = cleaned

    return properties


def extract_relationship_targets(
    metadata: dict[str, Any],
    rel_type: str,
) -> list[str]:
    """Extract relationship target IDs from metadata.

    Handles both single values and lists. The relationship type name in the
    domain config is also used as the metadata key.

    Args:
        metadata: Raw frontmatter/metadata dict
        rel_type: Relationship type name (e.g., "has_projects", "managed_by")

    Returns:
        List of target entity ID strings
    """
    value = metadata.get(rel_type)
    if value is None:
        return []

    if isinstance(value, list):
        return [str(v).strip() for v in value if v]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def serialize_metadata_for_neo4j(metadata: dict[str, Any]) -> dict[str, Any]:
    """Serialize a metadata dict so all values are Neo4j-compatible.

    Neo4j properties must be primitives or homogeneous lists of primitives.
    Nested dicts and mixed-type lists are serialized to JSON strings.
    """
    import json

    result = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            result[key] = value
        elif isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
        elif isinstance(value, list):
            # Neo4j supports homogeneous lists of primitives
            if all(isinstance(v, str) for v in value):
                result[key] = value
            elif all(isinstance(v, (int, float)) for v in value):
                result[key] = value
            else:
                result[key] = json.dumps(value)
        elif isinstance(value, dict):
            result[key] = json.dumps(value)
        elif isinstance(value, set):
            result[key] = list(value)
        else:
            result[key] = str(value)
    return result
