"""
Neo4j Schema Generator — Domain-Driven

Reads the active DomainConfiguration and generates Cypher statements for
constraints, indexes, and full-text search. The domain YAML is the single
source of truth: entity types become Neo4j labels, attributes become property
indexes, relationships become relationship types.

No hardcoded entity types. Ever.
"""

import logging

from app.model_schemas.domain_config import DomainConfiguration

logger = logging.getLogger(__name__)


def entity_type_to_label(entity_type: str) -> str:
    """Convert a domain entity type key to a Neo4j label.

    Examples:
        "person"            → "Person"
        "interview_session" → "InterviewSession"
        "account"           → "Account"
    """
    return entity_type.title().replace("_", "")


def relationship_type_to_neo4j(rel_type: str) -> str:
    """Convert a domain relationship type to a Neo4j relationship type.

    Examples:
        "has_projects"          → "HAS_PROJECTS"
        "managed_by"            → "MANAGED_BY"
        "completed_assessment"  → "COMPLETED_ASSESSMENT"
    """
    return rel_type.upper()


def generate_schema_from_domain(domain_config: DomainConfiguration) -> list[str]:
    """Generate Neo4j constraint and index statements from domain config.

    Args:
        domain_config: The active DomainConfiguration

    Returns:
        List of Cypher statements to execute
    """
    statements: list[str] = []

    # Base constraint — every entity node gets the :Entity label with unique id
    statements.append(
        "CREATE CONSTRAINT entity_id IF NOT EXISTS "
        "FOR (e:Entity) REQUIRE e.id IS UNIQUE"
    )

    # Base index on canonical_name for Entity
    statements.append(
        "CREATE INDEX entity_name IF NOT EXISTS "
        "FOR (e:Entity) ON (e.`name`)"
    )

    # Per-entity-type constraints and indexes from domain config
    all_labels = []
    for entity_id, entity_def in domain_config.entities.items():
        label = entity_type_to_label(entity_id)
        all_labels.append(label)

        # Uniqueness constraint on id for each label
        safe_name = entity_id.replace("-", "_")
        statements.append(
            f"CREATE CONSTRAINT {safe_name}_id IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        )

        # Property indexes for required or searchable attributes
        for attr in entity_def.attributes:
            if attr.required or attr.name == "name":
                idx_name = f"{safe_name}_{attr.name}"
                statements.append(
                    f"CREATE INDEX {idx_name} IF NOT EXISTS "
                    f"FOR (n:{label}) ON (n.`{attr.name}`)"
                )

    # Full-text search index across all entity names
    # Note: Neo4j requires at least one label for fulltext indexes.
    # We use the base Entity label which all nodes will have.
    statements.append(
        "CREATE FULLTEXT INDEX entity_search IF NOT EXISTS "
        "FOR (e:Entity) ON EACH [e.`name`, e.`canonical_name`]"
    )

    # Document node constraint
    statements.append(
        "CREATE CONSTRAINT document_path IF NOT EXISTS "
        "FOR (d:Document) REQUIRE d.path IS UNIQUE"
    )

    # --- Signal node schema (system-level, not a domain entity) ---
    statements.append(
        "CREATE CONSTRAINT signal_id IF NOT EXISTS "
        "FOR (s:Signal) REQUIRE s.id IS UNIQUE"
    )
    statements.append(
        "CREATE INDEX signal_type IF NOT EXISTS "
        "FOR (s:Signal) ON (s.signal_type)"
    )
    statements.append(
        "CREATE INDEX signal_meeting IF NOT EXISTS "
        "FOR (s:Signal) ON (s.source_meeting_id)"
    )
    statements.append(
        "CREATE INDEX signal_status IF NOT EXISTS "
        "FOR (s:Signal) ON (s.status)"
    )

    logger.info(
        f"Generated {len(statements)} schema statements for domain '{domain_config.id}' "
        f"({len(domain_config.entities)} entity types: {list(domain_config.entities.keys())})"
    )
    return statements


async def initialize_schema_from_domain(
    domain_config: DomainConfiguration | None = None,
) -> None:
    """Initialize Neo4j schema from the active domain configuration.

    Called during app startup. Generates and executes all constraint/index
    statements. Safe to call multiple times (uses IF NOT EXISTS).

    Args:
        domain_config: Optional explicit domain config. If not provided,
                       loads from the active DomainConfigService.
    """
    from app.neo4j_client import get_neo4j_client

    client = get_neo4j_client()

    if domain_config is None:
        from app.core.domain_config import get_domain_config
        domain_config = get_domain_config()

    if domain_config is None:
        logger.warning("No active domain config — skipping Neo4j schema initialization")
        return

    statements = generate_schema_from_domain(domain_config)
    try:
        results = await client.execute_many(statements)
        if results["failed"] > 0:
            logger.warning(
                f"Neo4j schema for '{domain_config.id}': "
                f"{results['succeeded']} succeeded, {results['failed']} failed — "
                f"{results['errors']}"
            )
        logger.info(f"Neo4j schema initialized for domain '{domain_config.id}'")
    except Exception:
        logger.exception(f"Failed to initialize Neo4j schema for domain '{domain_config.id}'")
        raise
