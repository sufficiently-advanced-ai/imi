"""Tests for Neo4j Schema Generator.

Source: app/services/graph/neo4j_schema.py

Tests cover:
- Pure label/relationship conversion functions
- Schema generation from domain config
- Async schema initialization with mocked Neo4j client
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.model_schemas.domain_config import (
    DomainAttribute,
    DomainConfiguration,
    DomainEntity,
    DomainRelationship,
)
from app.services.graph.neo4j_schema import (
    entity_type_to_label,
    generate_schema_from_domain,
    initialize_schema_from_domain,
    relationship_type_to_neo4j,
)


# ──────────────────────────────────────────────────────────────
# Pure functions
# ──────────────────────────────────────────────────────────────


class TestEntityTypeToLabel:
    def test_simple(self):
        assert entity_type_to_label("person") == "Person"

    def test_compound(self):
        # title() capitalizes each word after underscore, then replace removes _
        # "interview_session".title() = "Interview_Session" → "InterviewSession"
        assert entity_type_to_label("interview_session") == "InterviewSession"

    def test_single_word(self):
        assert entity_type_to_label("account") == "Account"

    def test_triple_compound(self):
        assert entity_type_to_label("long_entity_name") == "LongEntityName"


class TestRelationshipTypeToNeo4j:
    def test_simple(self):
        assert relationship_type_to_neo4j("has_projects") == "HAS_PROJECTS"

    def test_managed_by(self):
        assert relationship_type_to_neo4j("managed_by") == "MANAGED_BY"

    def test_single_word(self):
        assert relationship_type_to_neo4j("owns") == "OWNS"


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────


def _make_domain_config(
    entities: dict[str, DomainEntity] | None = None,
) -> DomainConfiguration:
    """Build a realistic DomainConfiguration for testing."""
    if entities is None:
        entities = {
            "person": DomainEntity(
                name="person",
                description="A person",
                plural="people",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                    DomainAttribute(name="role", type="string", required=False),
                ],
                relationships=[
                    DomainRelationship(
                        type="has_projects",
                        target="project",
                        cardinality="one-to-many",
                    ),
                ],
            ),
            "project": DomainEntity(
                name="project",
                description="A project",
                plural="projects",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                ],
                relationships=[],
            ),
        }

    return DomainConfiguration(
        id="test_domain",
        name="Test Domain",
        entities=entities,
    )


# ──────────────────────────────────────────────────────────────
# generate_schema_from_domain
# ──────────────────────────────────────────────────────────────


class TestGenerateSchemaFromDomain:
    def test_base_constraints_always_present(self):
        """Entity ID uniqueness and entity name index are always generated."""
        domain = _make_domain_config(entities={})
        statements = generate_schema_from_domain(domain)

        # Base constraints + fulltext + document constraint = 4
        assert any("entity_id" in s for s in statements)
        assert any("entity_name" in s for s in statements)
        assert any("document_path" in s for s in statements)

    def test_per_entity_uniqueness_constraint(self):
        domain = _make_domain_config()
        statements = generate_schema_from_domain(domain)

        person_constraints = [s for s in statements if "person_id" in s and "Person" in s]
        assert len(person_constraints) == 1
        assert "REQUIRE n.id IS UNIQUE" in person_constraints[0]

    def test_multiple_entities_generate_constraints(self):
        domain = _make_domain_config()
        statements = generate_schema_from_domain(domain)

        # Both person and project should get uniqueness constraints
        assert any("person_id" in s for s in statements)
        assert any("project_id" in s for s in statements)

    def test_required_attribute_indexes(self):
        """Required attributes get property indexes."""
        domain = _make_domain_config()
        statements = generate_schema_from_domain(domain)

        # 'name' is required on person → should generate index
        name_indexes = [s for s in statements if "person_name" in s and "INDEX" in s]
        assert len(name_indexes) == 1

    def test_fulltext_index_included(self):
        domain = _make_domain_config()
        statements = generate_schema_from_domain(domain)

        fulltext = [s for s in statements if "FULLTEXT INDEX" in s and "entity_search" in s]
        assert len(fulltext) == 1
        assert "e.`name`" in fulltext[0]
        assert "e.`canonical_name`" in fulltext[0]

    def test_document_constraint_included(self):
        domain = _make_domain_config()
        statements = generate_schema_from_domain(domain)

        doc_constraints = [s for s in statements if "document_path" in s]
        assert len(doc_constraints) == 1
        assert "Document" in doc_constraints[0]




# ──────────────────────────────────────────────────────────────
# initialize_schema_from_domain
# ──────────────────────────────────────────────────────────────


class TestInitializeSchemaFromDomain:

    @pytest.mark.asyncio
    async def test_logs_warning_on_failures(self, caplog):
        """When execute_many reports failures, should log a warning."""
        mock_client = AsyncMock()
        mock_client.execute_many = AsyncMock(
            return_value={
                "succeeded": 6,
                "failed": 2,
                "errors": ["constraint error 1", "constraint error 2"],
            }
        )
        domain = _make_domain_config()

        import logging

        with (
            patch(
                "app.neo4j_client.get_neo4j_client",
                return_value=mock_client,
            ),
            caplog.at_level(logging.WARNING),
        ):
            await initialize_schema_from_domain(domain_config=domain)

        assert "2 failed" in caplog.text


    @pytest.mark.asyncio
    async def test_raises_on_execute_exception(self):
        """If execute_many raises, the exception should propagate."""
        mock_client = AsyncMock()
        mock_client.execute_many = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )
        domain = _make_domain_config()

        with (
            patch(
                "app.neo4j_client.get_neo4j_client",
                return_value=mock_client,
            ),
            pytest.raises(RuntimeError, match="connection lost"),
        ):
            await initialize_schema_from_domain(domain_config=domain)
