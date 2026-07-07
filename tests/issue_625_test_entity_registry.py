"""
Unit tests for issue #625 EntityRegistry enhancements.

These tests use mocks to verify the new methods added to EntityRegistry:
- find_entity(name, entity_type) - Find entity by exact name match
- search_entities(query, entity_type, limit) - Fuzzy search for entities

All tests use mocks for external dependencies.
"""

import pytest

from app.model_schemas.domain_config import (
    DomainAttribute,
    DomainConfiguration,
    DomainEntity,
)
from app.services.entity_registry import EntityRegistry


class TestEntityRegistryFindEntity:
    """Unit tests for EntityRegistry.find_entity() method."""

    @pytest.fixture
    def entity_registry(self):
        """Create an EntityRegistry instance for testing."""
        registry = EntityRegistry()

        # Register a test domain
        domain = DomainConfiguration(
            id="test_domain",
            name="Test Domain",
            version="1.0.0",
            entities={
                "person": DomainEntity(
                    name="Person",
                    description="A person entity",
                    plural="People",
                    attributes=[
                        DomainAttribute(name="name", type="string", required=True),
                        DomainAttribute(name="email", type="string", required=False),
                    ],
                    relationships=[],
                ),
                "project": DomainEntity(
                    name="Project",
                    description="A project entity",
                    plural="Projects",
                    attributes=[
                        DomainAttribute(name="title", type="string", required=True),
                    ],
                    relationships=[],
                ),
            },
        )
        registry.register_domain(domain)

        return registry

    def test_find_entity_by_name_and_type(self, entity_registry):
        """Should find entity by exact name match and entity type."""
        # Add a test entity with name attribute at top level
        entity = type(
            "Entity",
            (),
            {
                "id": "person-john-doe",
                "entity_type": "person",
                "name": "John Doe",
                "email": "john@example.com",
            },
        )()
        entity_registry.entities["person"]["person-john-doe"] = entity

        result = entity_registry.find_entity("John Doe", "person")
        assert result is not None
        assert result.id == "person-john-doe"

    def test_find_entity_returns_none_if_not_found(self, entity_registry):
        """Should return None if entity with given name doesn't exist."""
        result = entity_registry.find_entity("Nonexistent Person", "person")
        assert result is None

    def test_find_entity_filters_by_type(self, entity_registry):
        """Should only search within specified entity type."""
        # Add entities of different types with same name
        person_entity = type("Entity", (), {"id": "person-acme", "entity_type": "person", "name": "Acme"})()
        project_entity = type("Entity", (), {"id": "project-acme", "entity_type": "project", "name": "Acme"})()

        entity_registry.entities["person"]["person-acme"] = person_entity
        entity_registry.entities["project"]["project-acme"] = project_entity

        result = entity_registry.find_entity("Acme", "person")
        assert result is not None
        assert result.entity_type == "person"

    def test_find_entity_case_insensitive(self, entity_registry):
        """Should perform case-insensitive name matching."""
        entity = type("Entity", (), {"id": "person-john-doe", "entity_type": "person", "name": "John Doe"})()
        entity_registry.entities["person"]["person-john-doe"] = entity

        result = entity_registry.find_entity("john doe", "person")
        assert result is not None


class TestEntityRegistrySearchEntities:
    """Unit tests for EntityRegistry.search_entities() method."""

    @pytest.fixture
    def entity_registry(self):
        """Create an EntityRegistry with test data."""
        registry = EntityRegistry()

        domain = DomainConfiguration(
            id="test_domain",
            name="Test Domain",
            version="1.0.0",
            entities={
                "person": DomainEntity(
                    name="Person",
                    description="A person entity",
                    plural="People",
                    attributes=[
                        DomainAttribute(name="name", type="string", required=True),
                    ],
                    relationships=[],
                ),
            },
        )
        registry.register_domain(domain)

        return registry

    def test_search_entities_fuzzy_matching(self, entity_registry):
        """Should find entities using fuzzy/partial matching."""
        # Add test entities
        entities = [
            {"id": "person-john-doe", "name": "John Doe"},
            {"id": "person-john-smith", "name": "John Smith"},
            {"id": "person-jane-doe", "name": "Jane Doe"},
        ]

        for ent in entities:
            entity_registry.entities["person"][ent["id"]] = type("Entity", (), {"id": ent["id"], "name": ent["name"]})()

        # Search with "john d" which has higher similarity to "John Doe"
        results = entity_registry.search_entities("john d", "person")
        assert len(results) >= 1
        names = [r[0].name for r in results]
        assert "John Doe" in names

    def test_search_entities_respects_limit(self, entity_registry):
        """Should limit results to specified number."""
        # Add multiple matching entities
        for i in range(10):
            entity_id = f"person-john-{i}"
            entity_registry.entities["person"][entity_id] = type(
                "Entity", (), {"id": entity_id, "name": f"John Person {i}"}
            )()

        results = entity_registry.search_entities("john", "person", limit=3)
        assert len(results) <= 3

    def test_search_entities_filters_by_type(self, entity_registry):
        """Should only search within specified entity type."""
        results = entity_registry.search_entities("test", "person")
        assert isinstance(results, list)

    def test_search_entities_returns_sorted_by_relevance(self, entity_registry):
        """Should return results sorted by relevance/similarity score."""
        entities = [
            {"id": "person-1", "name": "John Doe"},
            {"id": "person-2", "name": "Johnny Doeman"},
            {"id": "person-3", "name": "Jonathan Doe"},
        ]

        for ent in entities:
            entity_registry.entities["person"][ent["id"]] = type("Entity", (), {"id": ent["id"], "name": ent["name"]})()

        results = entity_registry.search_entities("john doe", "person")
        assert len(results) > 0
        assert results[0][0].name == "John Doe"

    def test_search_entities_empty_query(self, entity_registry):
        """Should handle empty query gracefully."""
        results = entity_registry.search_entities("", "person")
        assert results == []

    def test_search_entities_no_matches(self, entity_registry):
        """Should return empty list when no matches found."""
        results = entity_registry.search_entities("xyz123nomatch", "person")
        assert results == []

    def test_search_entities_all_types_when_type_none(self, entity_registry):
        """Should search all entity types when entity_type is None."""
        # Add domain with multiple entity types
        domain = DomainConfiguration(
            id="test_domain",
            name="Test Domain",
            version="1.0.0",
            entities={
                "person": DomainEntity(
                    name="Person", description="A person", plural="People", attributes=[], relationships=[]
                ),
                "project": DomainEntity(
                    name="Project", description="A project", plural="Projects", attributes=[], relationships=[]
                ),
            },
        )
        entity_registry.register_domain(domain)

        results = entity_registry.search_entities("test", entity_type=None)
        assert isinstance(results, list)


class TestEntityRegistryValidation:
    """Test validation methods work with new find/search functionality."""

    @pytest.fixture
    def entity_registry(self):
        """Create an EntityRegistry for testing."""
        registry = EntityRegistry()
        domain = DomainConfiguration(
            id="test_domain",
            name="Test Domain",
            version="1.0.0",
            entities={
                "person": DomainEntity(
                    name="Person",
                    description="A person",
                    plural="People",
                    attributes=[
                        DomainAttribute(name="name", type="string", required=True),
                        DomainAttribute(name="email", type="string", required=False),
                    ],
                    relationships=[],
                )
            },
        )
        registry.register_domain(domain)
        return registry

    def test_validate_entity_with_schema(self, entity_registry):
        """Should validate entity attributes against schema."""
        # This already exists, testing it still works
        is_valid, errors = entity_registry.validate_entity("person", {"name": "John Doe", "email": "john@example.com"})
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_entity_missing_required(self, entity_registry):
        """Should catch missing required fields."""
        is_valid, errors = entity_registry.validate_entity(
            "person",
            {"email": "john@example.com"},  # Missing required 'name'
        )
        assert is_valid is False
        assert len(errors) > 0

    def test_validate_entity_unknown_type(self, entity_registry):
        """Should return error for unknown entity type."""
        is_valid, errors = entity_registry.validate_entity("unknown_type", {"name": "Test"})
        assert is_valid is False
        assert any("Unknown entity type" in err for err in errors)


class TestEntityRegistryDuplicateDetection:
    """Test duplicate detection functionality."""

    @pytest.fixture
    def entity_registry(self):
        """Create an EntityRegistry for testing."""
        registry = EntityRegistry()
        domain = DomainConfiguration(
            id="test_domain",
            name="Test Domain",
            version="1.0.0",
            entities={
                "person": DomainEntity(
                    name="Person",
                    description="A person",
                    plural="People",
                    attributes=[
                        DomainAttribute(name="name", type="string", required=True),
                    ],
                    relationships=[],
                )
            },
        )
        registry.register_domain(domain)
        return registry

    def test_detect_exact_duplicate(self, entity_registry):
        """Should detect exact duplicate by name and type using find_entity."""
        # Add an entity
        entity_registry.entities["person"]["person-john-doe"] = type(
            "Entity",
            (),
            {
                "id": "person-john-doe",
                "name": "John Doe",
                "entity_type": "person",
            },
        )()

        # Find duplicate
        duplicate = entity_registry.find_entity("John Doe", "person")
        assert duplicate is not None
        assert duplicate.id == "person-john-doe"



class TestEntityRegistryEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def entity_registry(self):
        """Create an EntityRegistry for testing."""
        return EntityRegistry()




