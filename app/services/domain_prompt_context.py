"""Domain prompt context serializer — Issue #835.

Converts a DomainConfiguration into a concise, LLM-friendly text block
that can be injected into any Claude prompt to give it ontological awareness
of the active domain's entity types, attributes, and relationships.

This is intentionally a lightweight serializer — not a full prompt builder.
It produces a context block (~500-1500 chars) meant to be prepended to
extraction prompts so Claude knows what entities to look for and how they
relate to each other.
"""


from app.model_schemas.domain_config import DomainConfiguration, DomainEntity


def serialize_domain_context(domain: DomainConfiguration | None) -> str:
    """Serialize a DomainConfiguration into concise LLM-friendly text.

    Args:
        domain: The domain configuration to serialize, or None.

    Returns:
        A text block describing the domain's entity types, key attributes,
        and relationships. Empty string if domain is None or has no entities.
    """
    if domain is None or not domain.entities:
        return ""

    lines = [f"Domain: {domain.name}"]
    lines.append("Entity types:")

    for entity_type, entity in domain.entities.items():
        lines.append(_serialize_entity(entity_type, entity))

    return "\n".join(lines)


def _serialize_entity(entity_type: str, entity: DomainEntity) -> str:
    """Serialize a single entity type into a compact description."""
    parts = [f"- {entity_type}: {entity.description}"]

    # Key attributes (show required first, then notable optional ones)
    attr_parts = []
    for attr in entity.attributes:
        if attr.name == "name":
            continue  # Skip 'name' — it's universal
        desc = attr.name
        if attr.required:
            desc += " (required)"
        if attr.enum:
            desc += f" [{', '.join(attr.enum)}]"
        attr_parts.append(desc)

    if attr_parts:
        parts.append(f"  Key attributes: {', '.join(attr_parts)}")

    # Relationships
    rel_parts = []
    for rel in entity.relationships:
        rel_parts.append(f"{rel.type} -> {rel.target}")

    if rel_parts:
        parts.append(f"  Relationships: {', '.join(rel_parts)}")

    return "\n".join(parts)
