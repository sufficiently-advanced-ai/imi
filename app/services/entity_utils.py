"""
Entity utility functions for working with entity IDs and types.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Control characters (incl. newline, carriage return, tab) never appear in a
# real entity name. Their presence means transcript text leaked into the
# extracted name (e.g. "range-control\r\nelectrical-equipment-repair"), which
# then slugifies into a contaminated entity ID + stub file.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

# Upper bound on a sane entity name. Sentence-fragment junk ("future business
# is …") runs long; real names — even multi-word org names — stay well under.
_MAX_ENTITY_NAME_LEN = 100


def is_valid_entity_name(name: str) -> bool:
    """Return True if ``name`` looks like a real entity, False for the
    high-confidence extraction-junk classes seen in production.

    Deliberately conservative — it rejects only unambiguous garbage so it never
    drops a legitimate multi-word org or person name:

    - empty / whitespace-only
    - control characters / newlines (transcript text leaking into the name)
    - no alphabetic character at all (phone numbers like ``+1-571-583-8135``,
      pure-numeric or punctuation-only fragments)
    - absurdly long strings (sentence fragments, not names)

    This is the single shared predicate used at both the extraction gate and
    the ingest ``add_node`` gate so the two never diverge.
    """
    if not isinstance(name, str):
        return False
    if _CONTROL_CHARS_RE.search(name):
        return False
    stripped = name.strip()
    if not stripped:
        return False
    if len(stripped) > _MAX_ENTITY_NAME_LEN:
        return False
    if not any(c.isalpha() for c in stripped):
        return False
    return True


# Common entity type prefixes used throughout the system
VALID_ENTITY_TYPES = {
    "person",
    "project",
    "team",
    "company",
    "account",
    "meeting",
    "document",
    "topic",
    "tool",
    "technology",
}


def extract_entity_type_from_id(entity_id: str) -> str | None:
    """
    Extract entity type from entity ID.

    Entity IDs follow the pattern: {entity_type}-{normalized_name}
    Examples:
        - "person-john-smith" -> "person"
        - "project-crm-modernization" -> "project"
        - "team-engineering" -> "team"

    Args:
        entity_id: The entity ID string

    Returns:
        The entity type if found, None otherwise
    """
    if not entity_id or not isinstance(entity_id, str):
        return None

    # Handle empty strings
    if not entity_id.strip():
        return None

    # Split on first hyphen only
    if "-" in entity_id:
        parts = entity_id.split("-", 1)
        potential_type = parts[0].lower()

        # Check if it's a valid entity type
        if potential_type in VALID_ENTITY_TYPES:
            return potential_type

    return None


def is_entity_id(value: str) -> bool:
    """
    Check if a string is a properly formatted entity ID.

    Args:
        value: The string to check

    Returns:
        True if the string is a valid entity ID format
    """
    return extract_entity_type_from_id(value) is not None


def ensure_entity_id_format(entity_type: str, name: str) -> str:
    """
    Ensure a name is in proper entity ID format.

    Args:
        entity_type: The entity type (e.g., "person", "project")
        name: The name or existing entity ID

    Returns:
        Properly formatted entity ID
    """
    # Idempotent for any entity type (including domain-only types not in the
    # static VALID_ENTITY_TYPES set): if it already carries this type's prefix,
    # return as-is.
    if name.startswith(f"{entity_type}-"):
        return name

    # Otherwise, create the ID
    normalized_name = name.lower().replace(" ", "-").replace("_", "-")
    return f"{entity_type}-{normalized_name}"


def get_valid_entity_types() -> set[str]:
    """
    Get the set of valid entity types.

    Returns:
        Set of valid entity type strings
    """
    return VALID_ENTITY_TYPES.copy()


# Always-valid system entity types, independent of domain
SYSTEM_ENTITY_TYPES = {"meeting", "document", "topic"}


def get_active_entity_types() -> set[str]:
    """Valid entity types for the active domain = domain entity types ∪ system types.

    Falls back to the static VALID_ENTITY_TYPES if no domain is configured.
    """
    try:
        from app.core.domain_config.domain_config_service import get_domain_config_service
        domain = get_domain_config_service().get_active_domain()
        if domain and domain.entities:
            return set(domain.entities.keys()) | SYSTEM_ENTITY_TYPES
    except Exception as exc:
        logger.warning("Failed to resolve active domain entity types; falling back to static defaults", exc_info=exc)
    return VALID_ENTITY_TYPES.copy()
