"""Tests for issue #834: Wire up inverse_name in DomainRelationship → Neo4j.

Covers:
- Domain YAML inverse_name population (all three configs load without errors)
- DomainConfiguration validation of inverse_name symmetry
- Neo4j inverse edge creation in _ingest_file
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock
from pydantic import ValidationError

import yaml

from app.model_schemas.domain_config import (
    DomainAttribute,
    DomainConfiguration,
    DomainEntity,
    DomainRelationship,
)
from app.services.graph.neo4j_graph import Neo4jKnowledgeGraph


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────


def _make_bidirectional_domain() -> DomainConfiguration:
    """Domain config with inverse_name pairs for testing."""
    return DomainConfiguration(
        id="test_domain",
        name="Test Domain",
        entities={
            "account": DomainEntity(
                name="account",
                description="A client account",
                plural="accounts",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                ],
                relationships=[
                    DomainRelationship(
                        type="has_projects",
                        target="project",
                        cardinality="one-to-many",
                        inverse_name="belongs_to_account",
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
                relationships=[
                    DomainRelationship(
                        type="belongs_to_account",
                        target="account",
                        cardinality="many-to-one",
                        inverse_name="has_projects",
                    ),
                ],
            ),
        },
    )


@pytest.fixture
def mock_neo4j():
    client = AsyncMock()
    client.execute_read = AsyncMock(return_value=[])
    client.execute_write = AsyncMock(return_value=[])
    client.execute_many = AsyncMock(
        return_value={"succeeded": 0, "failed": 0, "errors": []}
    )
    return client


@pytest.fixture
def bidirectional_graph(mock_neo4j, tmp_path):
    domain = _make_bidirectional_domain()
    kg = Neo4jKnowledgeGraph(neo4j_client=mock_neo4j, domain_config=domain)
    mock_git = MagicMock()
    mock_git.repo_path = str(tmp_path / "test-repo")
    kg._git_ops = mock_git
    return kg


# ──────────────────────────────────────────────────────────────
# Domain YAML Loading Tests
# ──────────────────────────────────────────────────────────────


class TestDomainYAMLInverseNames:
    """Verify all three domain YAMLs load successfully with inverse_name."""

    YAML_DIR = os.path.join(os.path.dirname(__file__), "..", "config", "domains")




# ──────────────────────────────────────────────────────────────
# DomainConfiguration Validation Tests
# ──────────────────────────────────────────────────────────────


class TestInverseNameValidation:
    """Test DomainConfiguration.validate_inverse_names."""

    def test_valid_symmetric_pair(self):
        """Symmetric inverse_name pair should validate successfully."""
        config = _make_bidirectional_domain()
        assert config.id == "test_domain"

    def test_inverse_name_not_found_on_target(self):
        """inverse_name referencing non-existent relationship should fail."""
        with pytest.raises(ValidationError, match="not found on target entity"):
            DomainConfiguration(
                id="bad_domain",
                name="Bad",
                entities={
                    "a": DomainEntity(
                        name="a",
                        description="Entity A",
                        plural="as",
                        relationships=[
                            DomainRelationship(
                                type="links_to",
                                target="b",
                                cardinality="one-to-many",
                                inverse_name="nonexistent_rel",
                            ),
                        ],
                    ),
                    "b": DomainEntity(
                        name="b",
                        description="Entity B",
                        plural="bs",
                        relationships=[],
                    ),
                },
            )

    def test_inverse_points_to_wrong_entity(self):
        """inverse relationship that targets a different entity should fail."""
        with pytest.raises(ValidationError, match="targets 'c', expected 'a'"):
            DomainConfiguration(
                id="bad_domain",
                name="Bad",
                entities={
                    "a": DomainEntity(
                        name="a",
                        description="Entity A",
                        plural="as",
                        relationships=[
                            DomainRelationship(
                                type="links_to",
                                target="b",
                                cardinality="one-to-many",
                                inverse_name="wrong_target",
                            ),
                        ],
                    ),
                    "b": DomainEntity(
                        name="b",
                        description="Entity B",
                        plural="bs",
                        relationships=[
                            DomainRelationship(
                                type="wrong_target",
                                target="c",
                                cardinality="many-to-one",
                            ),
                        ],
                    ),
                    "c": DomainEntity(
                        name="c",
                        description="Entity C",
                        plural="cs",
                        relationships=[],
                    ),
                },
            )

    def test_missing_target_entity_with_inverse(self):
        """inverse_name on a rel whose target entity doesn't exist should fail."""
        with pytest.raises(ValidationError, match="target entity 'nonexistent' not found"):
            DomainConfiguration(
                id="bad_domain",
                name="Bad",
                entities={
                    "a": DomainEntity(
                        name="a",
                        description="Entity A",
                        plural="as",
                        relationships=[
                            DomainRelationship(
                                type="links_to",
                                target="nonexistent",
                                cardinality="one-to-many",
                                inverse_name="linked_from",
                            ),
                        ],
                    ),
                },
            )

    def test_asymmetric_inverse_name(self):
        """Mismatched inverse_name pair should fail validation."""
        with pytest.raises(ValidationError, match="asymmetric inverse"):
            DomainConfiguration(
                id="bad_domain",
                name="Bad",
                entities={
                    "a": DomainEntity(
                        name="a",
                        description="Entity A",
                        plural="as",
                        relationships=[
                            DomainRelationship(
                                type="links_to",
                                target="b",
                                cardinality="one-to-many",
                                inverse_name="linked_from",
                            ),
                        ],
                    ),
                    "b": DomainEntity(
                        name="b",
                        description="Entity B",
                        plural="bs",
                        relationships=[
                            DomainRelationship(
                                type="linked_from",
                                target="a",
                                cardinality="many-to-one",
                                inverse_name="wrong_name",
                            ),
                        ],
                    ),
                },
            )

    def test_self_referential_inverse(self):
        """Self-referencing relationship (e.g., collaborates_with) should validate."""
        config = DomainConfiguration(
            id="self_ref",
            name="Self Ref",
            entities={
                "person": DomainEntity(
                    name="person",
                    description="A person",
                    plural="people",
                    relationships=[
                        DomainRelationship(
                            type="collaborates_with",
                            target="person",
                            cardinality="many-to-many",
                            inverse_name="collaborates_with",
                        ),
                    ],
                ),
            },
        )
        assert "person" in config.entities

    def test_no_inverse_name_is_fine(self):
        """Relationships without inverse_name should still validate."""
        config = DomainConfiguration(
            id="no_inverse",
            name="No Inverse",
            entities={
                "a": DomainEntity(
                    name="a",
                    description="A",
                    plural="as",
                    relationships=[
                        DomainRelationship(
                            type="links_to",
                            target="b",
                            cardinality="one-to-many",
                        ),
                    ],
                ),
                "b": DomainEntity(
                    name="b",
                    description="B",
                    plural="bs",
                    relationships=[],
                ),
            },
        )
        assert config.id == "no_inverse"

    def test_one_sided_inverse_is_fine(self):
        """If only one side declares inverse_name and it's valid, validation passes."""
        config = DomainConfiguration(
            id="one_sided",
            name="One Sided",
            entities={
                "a": DomainEntity(
                    name="a",
                    description="A",
                    plural="as",
                    relationships=[
                        DomainRelationship(
                            type="links_to",
                            target="b",
                            cardinality="one-to-many",
                            inverse_name="linked_from",
                        ),
                    ],
                ),
                "b": DomainEntity(
                    name="b",
                    description="B",
                    plural="bs",
                    relationships=[
                        DomainRelationship(
                            type="linked_from",
                            target="a",
                            cardinality="many-to-one",
                            # No inverse_name — that's OK
                        ),
                    ],
                ),
            },
        )
        assert config.id == "one_sided"


# ──────────────────────────────────────────────────────────────
# Neo4j Inverse Edge Creation Tests
# ──────────────────────────────────────────────────────────────


class TestNeo4jInverseEdges:
    """Test that _ingest_file creates inverse edges when inverse_name is set."""

    @pytest.mark.asyncio
    async def test_ingest_creates_inverse_edge(self, bidirectional_graph, mock_neo4j):
        """When account has_projects→project, should also create BELONGS_TO_ACCOUNT edge."""
        metadata = {
            "id": "account-acme",
            "type": "account",
            "name": "Acme Corp",
            "has_projects": ["project-alpha"],
        }

        await bidirectional_graph._ingest_file("entities/account/acme.md", metadata)

        write_calls = mock_neo4j.execute_write.call_args_list

        # Find forward relationship calls
        forward_calls = [
            c for c in write_calls
            if "HAS_PROJECTS" in str(c) and "BELONGS_TO_ACCOUNT" not in str(c)
        ]
        assert len(forward_calls) >= 1, "Should create forward HAS_PROJECTS edge"

        # Find inverse relationship calls
        inverse_calls = [
            c for c in write_calls
            if "BELONGS_TO_ACCOUNT" in str(c)
        ]
        assert len(inverse_calls) >= 1, "Should create inverse BELONGS_TO_ACCOUNT edge"

    @pytest.mark.asyncio
    async def test_inverse_edge_has_correct_direction(self, bidirectional_graph, mock_neo4j):
        """Inverse edge should go from target→source (reversed direction)."""
        metadata = {
            "id": "account-acme",
            "type": "account",
            "name": "Acme Corp",
            "has_projects": ["project-alpha"],
        }

        await bidirectional_graph._ingest_file("entities/account/acme.md", metadata)

        write_calls = mock_neo4j.execute_write.call_args_list

        # Find the inverse BELONGS_TO_ACCOUNT call
        inverse_calls = [
            c for c in write_calls
            if "BELONGS_TO_ACCOUNT" in str(c)
        ]
        assert len(inverse_calls) >= 1

        # execute_write is called with (query, params) as positional args
        # so params dict is at call.args[1] or call[0][1]
        call_params = inverse_calls[0][0][1]
        assert call_params.get("source", "").startswith("project-"), (
            f"Inverse source should be the project, got: {call_params}"
        )
        assert call_params.get("target") == "account-acme", (
            f"Inverse target should be account-acme, got: {call_params}"
        )

    @pytest.mark.asyncio
    async def test_inverse_edge_tagged_as_inverse(self, bidirectional_graph, mock_neo4j):
        """Inverse edges should have source='inverse' in properties."""
        metadata = {
            "id": "account-acme",
            "type": "account",
            "name": "Acme Corp",
            "has_projects": ["project-alpha"],
        }

        await bidirectional_graph._ingest_file("entities/account/acme.md", metadata)

        write_calls = mock_neo4j.execute_write.call_args_list

        inverse_calls = [
            c for c in write_calls
            if "BELONGS_TO_ACCOUNT" in str(c)
        ]
        assert len(inverse_calls) >= 1

        call_params = inverse_calls[0][0][1]
        props = call_params.get("props", {})
        assert props.get("source") == "inverse", (
            f"Inverse edge should have source='inverse', got: {props}"
        )

    @pytest.mark.asyncio
    async def test_no_inverse_when_not_defined(self, mock_neo4j, tmp_path):
        """Relationships without inverse_name should not create inverse edges."""
        domain = DomainConfiguration(
            id="test_no_inv",
            name="Test No Inverse",
            entities={
                "person": DomainEntity(
                    name="person",
                    description="A person",
                    plural="people",
                    attributes=[
                        DomainAttribute(name="name", type="string", required=True),
                    ],
                    relationships=[
                        DomainRelationship(
                            type="works_on",
                            target="project",
                            cardinality="many-to-many",
                            # No inverse_name
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
            },
        )
        kg = Neo4jKnowledgeGraph(neo4j_client=mock_neo4j, domain_config=domain)
        kg._git_ops = MagicMock()
        kg._git_ops.repo_path = str(tmp_path / "test-repo")

        metadata = {
            "id": "person-tom",
            "type": "person",
            "name": "Tom",
            "works_on": ["project-alpha"],
        }

        await kg._ingest_file("entities/person/tom.md", metadata)

        write_calls = mock_neo4j.execute_write.call_args_list

        # Should have WORKS_ON but no inverse
        works_on_calls = [c for c in write_calls if "WORKS_ON" in str(c)]
        assert len(works_on_calls) >= 1

        # No other relationship type should exist
        all_rel_types_in_calls = []
        for c in write_calls:
            call_str = str(c)
            if "MERGE (a)-[r:" in call_str:
                all_rel_types_in_calls.append(call_str)

        # Filter out WORKS_ON — remaining should be empty (no inverse)
        non_works_on = [c for c in all_rel_types_in_calls if "WORKS_ON" not in c]
        assert len(non_works_on) == 0, (
            f"No inverse edges should be created, found: {non_works_on}"
        )

    @pytest.mark.asyncio
    async def test_multiple_targets_create_multiple_inverses(
        self, bidirectional_graph, mock_neo4j
    ):
        """Multiple relationship targets should each get an inverse edge."""
        metadata = {
            "id": "account-acme",
            "type": "account",
            "name": "Acme Corp",
            "has_projects": ["project-alpha", "project-beta", "project-gamma"],
        }

        await bidirectional_graph._ingest_file("entities/account/acme.md", metadata)

        write_calls = mock_neo4j.execute_write.call_args_list

        inverse_calls = [
            c for c in write_calls
            if "BELONGS_TO_ACCOUNT" in str(c)
        ]
        assert len(inverse_calls) == 3, (
            f"Should create 3 inverse edges, got {len(inverse_calls)}"
        )
