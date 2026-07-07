"""
Semantica Config Adapter — Maps domain YAML configuration to Semantica entity schemas.

Bridges the existing DomainConfiguration (from config/domains/*.yaml) to
Semantica's extraction, graph, and search APIs.
"""

import logging
from typing import Any

from app.model_schemas.domain_config import (
    DomainConfiguration,
    DomainEntity,
)

logger = logging.getLogger(__name__)


# ── Type mappings ──

# Map domain YAML attribute types → Python types for Semantica property coercion
ATTR_TYPE_MAP = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "date": str,
    "datetime": str,
    "enum": str,
    "list": list,
    "array": list,
}


def entity_type_to_label(entity_type: str) -> str:
    """Convert entity type slug to Neo4j label. e.g. 'focus_area' → 'FocusArea'."""
    return "".join(word.capitalize() for word in entity_type.split("_"))


def relationship_type_to_neo4j(rel_type: str) -> str:
    """Convert relationship type to Neo4j format. e.g. 'has_projects' → 'HAS_PROJECTS'."""
    return rel_type.upper()


def get_entity_types(domain: DomainConfiguration | None) -> list[str]:
    """Return list of entity type names from domain config."""
    if not domain or not domain.entities:
        return []
    return list(domain.entities.keys())


def get_entity_schema(domain: DomainConfiguration | None, entity_type: str) -> DomainEntity | None:
    """Get entity schema definition for a given type."""
    if not domain or not domain.entities:
        return None
    return domain.entities.get(entity_type)


def get_relationship_types(domain: DomainConfiguration | None) -> list[str]:
    """Return all unique relationship types across all entities."""
    if not domain or not domain.entities:
        return []
    rel_types = set()
    for entity in domain.entities.values():
        if entity.relationships:
            for rel in entity.relationships:
                rel_types.add(rel.type)
    return sorted(rel_types)


def build_neo4j_schema_description(domain: DomainConfiguration | None) -> str:
    """Build a human-readable Neo4j schema description for LLM prompts."""
    if not domain or not domain.entities:
        return "(schema unavailable)"

    lines = []
    for name, entity in domain.entities.items():
        label = entity_type_to_label(name)
        attrs = [a.name for a in entity.attributes] if entity.attributes else ["name"]
        rels = []
        if entity.relationships:
            for r in entity.relationships:
                rel_neo4j = relationship_type_to_neo4j(r.type)
                target_label = entity_type_to_label(r.target)
                rels.append(f"{rel_neo4j} -> :{target_label}")
        rel_str = f" | {' | '.join(rels)}" if rels else ""
        lines.append(f":{label} ({', '.join(attrs)}){rel_str}")
    return "\n".join(lines)


def entity_id_to_slug(name: str) -> str:
    """Convert entity name to ID slug. e.g. 'Jane Doe' → 'jane-doe'."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def make_entity_id(entity_type: str, name: str) -> str:
    """Create entity ID in the standard format. e.g. 'person-jane-doe'."""
    return f"{entity_type}-{entity_id_to_slug(name)}"


def get_plural_directory(domain: DomainConfiguration | None, entity_type: str) -> str:
    """Get the plural directory name for an entity type (for file storage)."""
    if domain and domain.entities and entity_type in domain.entities:
        entity = domain.entities[entity_type]
        if hasattr(entity, "plural") and entity.plural:
            return entity.plural
    # Default pluralization
    if entity_type.endswith("y"):
        return entity_type[:-1] + "ies"
    if entity_type.endswith("s"):
        return entity_type + "es"
    return entity_type + "s"


def coerce_property_value(value: Any, attr_type: str) -> Any:
    """Coerce a property value to the expected type based on domain schema."""
    if value is None:
        return None
    target_type = ATTR_TYPE_MAP.get(attr_type, str)
    try:
        if target_type is bool:
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        if target_type is list:
            if isinstance(value, str):
                return [v.strip() for v in value.split(",") if v.strip()]
            if isinstance(value, list):
                return value
            return [value]
        return target_type(value)
    except (ValueError, TypeError):
        return value


def domain_entity_to_neo4j_properties(
    metadata: dict[str, Any],
    entity_def: DomainEntity | None,
    entity_id: str,
) -> dict[str, Any]:
    """Convert entity metadata to Neo4j-compatible properties, coercing types."""
    props: dict[str, Any] = {"id": entity_id}

    if not entity_def:
        # No schema — pass through as-is
        props.update({k: v for k, v in metadata.items() if v is not None})
        return props

    attr_types = {}
    if entity_def.attributes:
        attr_types = {a.name: a.type for a in entity_def.attributes}

    for key, value in metadata.items():
        if value is None:
            continue
        attr_type = attr_types.get(key, "string")
        coerced = coerce_property_value(value, attr_type)
        if coerced is not None:
            # Neo4j can't store nested dicts/lists of dicts — serialize them
            if isinstance(coerced, dict):
                import json
                props[key] = json.dumps(coerced)
            elif isinstance(coerced, list) and coerced and isinstance(coerced[0], dict):
                import json
                props[key] = json.dumps(coerced)
            else:
                props[key] = coerced

    return props
