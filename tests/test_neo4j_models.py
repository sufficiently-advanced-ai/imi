"""Tests for Neo4j Models — Pure function unit tests.

Source: app/services/graph/neo4j_models.py

These are pure data transformation functions, so no mocks are needed.
"""

import json
import logging
from datetime import date, datetime

from app.model_schemas.domain_config import (
    DomainAttribute,
    DomainEntity,
    DomainRelationship,
)
from app.services.graph.neo4j_models import (
    build_node_properties,
    coerce_property_value,
    extract_relationship_targets,
    serialize_metadata_for_neo4j,
)


# ──────────────────────────────────────────────────────────────
# coerce_property_value
# ──────────────────────────────────────────────────────────────


class TestCoercePropertyValue:
    def test_string_value(self):
        assert coerce_property_value("hello", "string") == "hello"

    def test_number_from_float_string(self):
        assert coerce_property_value("42.5", "number") == 42.5

    def test_number_from_int(self):
        assert coerce_property_value(10, "number") == 10.0

    def test_number_from_currency_string(self):
        """Currency prefix like '$1,000,000' should be cleaned and coerced."""
        assert coerce_property_value("$1,000,000", "number") == 1000000.0

    def test_number_invalid_returns_string(self):
        """Non-numeric string falls through to str() fallback."""
        result = coerce_property_value("not-a-num", "number")
        assert result == "not-a-num"

    def test_boolean_true_string(self):
        assert coerce_property_value("true", "boolean") is True

    def test_boolean_yes_string(self):
        assert coerce_property_value("yes", "boolean") is True

    def test_boolean_false_string(self):
        assert coerce_property_value("false", "boolean") is False

    def test_boolean_from_bool(self):
        assert coerce_property_value(True, "boolean") is True

    def test_date_from_datetime(self):
        dt = datetime(2026, 1, 15, 10, 30, 0)
        result = coerce_property_value(dt, "date")
        assert result == "2026-01-15T10:30:00"

    def test_date_from_date(self):
        d = date(2026, 1, 15)
        result = coerce_property_value(d, "date")
        assert result == "2026-01-15"

    def test_date_from_string(self):
        result = coerce_property_value("2026-01-15", "date")
        assert result == "2026-01-15"

    def test_enum_value(self):
        assert coerce_property_value("tech", "enum") == "tech"

    def test_none_returns_none(self):
        assert coerce_property_value(None, "string") is None
        assert coerce_property_value(None, "number") is None
        assert coerce_property_value(None, "boolean") is None

    def test_unknown_type_defaults_to_str(self):
        assert coerce_property_value(42, "unknown_type") == "42"


# ──────────────────────────────────────────────────────────────
# build_node_properties
# ──────────────────────────────────────────────────────────────


def _make_entity_def(
    attributes: list[DomainAttribute] | None = None,
    relationships: list[DomainRelationship] | None = None,
) -> DomainEntity:
    """Helper to create a DomainEntity for testing."""
    return DomainEntity(
        name="person",
        description="A person entity",
        plural="people",
        attributes=attributes or [],
        relationships=relationships or [],
    )


class TestBuildNodeProperties:
    def test_basic_properties(self):
        entity_def = _make_entity_def()
        metadata = {"name": "Tom Smith"}
        props = build_node_properties(metadata, entity_def, "person-tom-smith")

        assert props["id"] == "person-tom-smith"
        assert props["name"] == "Tom Smith"
        assert props["canonical_name"] == "tom smith"

    def test_domain_attributes_coerced(self):
        entity_def = _make_entity_def(
            attributes=[
                DomainAttribute(name="name", type="string", required=True),
                DomainAttribute(name="age", type="number", required=False),
            ]
        )
        metadata = {"name": "Alice", "age": "30"}
        props = build_node_properties(metadata, entity_def, "person-alice")

        assert props["name"] == "Alice"
        assert props["age"] == 30.0

    def test_missing_required_attribute_logs_warning(self, caplog):
        entity_def = _make_entity_def(
            attributes=[
                DomainAttribute(name="name", type="string", required=True),
                DomainAttribute(name="email", type="string", required=True),
            ]
        )
        metadata = {"name": "Bob"}  # missing required "email"
        with caplog.at_level(logging.WARNING):
            props = build_node_properties(metadata, entity_def, "person-bob")

        assert "email" not in props
        assert "Missing required attribute 'email'" in caplog.text

    def test_extra_metadata_preserved(self):
        """Extra keys like title, company are preserved in output."""
        entity_def = _make_entity_def()
        metadata = {
            "name": "Charlie",
            "title": "VP of Engineering",
            "company": "Acme Corp",
            "department": "Engineering",
        }
        props = build_node_properties(metadata, entity_def, "person-charlie")

        assert props["title"] == "VP of Engineering"
        assert props["company"] == "Acme Corp"
        assert props["department"] == "Engineering"

    def test_extra_metadata_skips_non_primitives(self):
        """Non-primitive extra metadata values are skipped."""
        entity_def = _make_entity_def()
        metadata = {
            "name": "Dave",
            "title": ["multiple", "titles"],  # not a primitive
        }
        props = build_node_properties(metadata, entity_def, "person-dave")
        assert "title" not in props

    def test_empty_name(self):
        entity_def = _make_entity_def()
        metadata = {}
        props = build_node_properties(metadata, entity_def, "person-unknown")

        assert props["name"] == ""
        assert props["canonical_name"] == ""


# ──────────────────────────────────────────────────────────────
# extract_relationship_targets
# ──────────────────────────────────────────────────────────────


class TestExtractRelationshipTargets:
    def test_list_value(self):
        metadata = {"has_projects": ["alpha", "beta"]}
        result = extract_relationship_targets(metadata, "has_projects")
        assert result == ["alpha", "beta"]

    def test_string_value(self):
        metadata = {"has_projects": "alpha"}
        result = extract_relationship_targets(metadata, "has_projects")
        assert result == ["alpha"]

    def test_missing_key(self):
        result = extract_relationship_targets({}, "has_projects")
        assert result == []

    def test_none_value(self):
        metadata = {"has_projects": None}
        result = extract_relationship_targets(metadata, "has_projects")
        assert result == []

    def test_empty_string(self):
        metadata = {"has_projects": "  "}
        result = extract_relationship_targets(metadata, "has_projects")
        assert result == []

    def test_filters_empty_list_items(self):
        metadata = {"has_projects": ["alpha", "", None, "beta"]}
        result = extract_relationship_targets(metadata, "has_projects")
        assert result == ["alpha", "beta"]


# ──────────────────────────────────────────────────────────────
# serialize_metadata_for_neo4j
# ──────────────────────────────────────────────────────────────


class TestSerializeMetadataForNeo4j:
    def test_primitives_pass_through(self):
        metadata = {"name": "Alice", "age": 30, "active": True, "score": 0.95}
        result = serialize_metadata_for_neo4j(metadata)
        assert result == metadata

    def test_none_values_dropped(self):
        metadata = {"name": "Alice", "email": None}
        result = serialize_metadata_for_neo4j(metadata)
        assert "email" not in result
        assert result["name"] == "Alice"

    def test_datetime_to_isoformat(self):
        dt = datetime(2026, 1, 15, 10, 30)
        result = serialize_metadata_for_neo4j({"created": dt})
        assert result["created"] == "2026-01-15T10:30:00"

    def test_date_to_isoformat(self):
        d = date(2026, 1, 15)
        result = serialize_metadata_for_neo4j({"created": d})
        assert result["created"] == "2026-01-15"

    def test_homogeneous_string_list(self):
        metadata = {"tags": ["a", "b", "c"]}
        result = serialize_metadata_for_neo4j(metadata)
        assert result["tags"] == ["a", "b", "c"]

    def test_homogeneous_number_list(self):
        metadata = {"scores": [1, 2.5, 3]}
        result = serialize_metadata_for_neo4j(metadata)
        assert result["scores"] == [1, 2.5, 3]

    def test_mixed_list_serialized_to_json(self):
        metadata = {"mixed": ["a", 1, True]}
        result = serialize_metadata_for_neo4j(metadata)
        assert result["mixed"] == json.dumps(["a", 1, True])

    def test_dict_serialized_to_json(self):
        metadata = {"nested": {"key": "value"}}
        result = serialize_metadata_for_neo4j(metadata)
        assert result["nested"] == json.dumps({"key": "value"})

    def test_set_converted_to_list(self):
        metadata = {"items": {"a", "b"}}
        result = serialize_metadata_for_neo4j(metadata)
        assert set(result["items"]) == {"a", "b"}
        assert isinstance(result["items"], list)

    def test_unknown_type_converted_to_str(self):
        """Objects that aren't primitives/list/dict/set/datetime get str()."""

        class Custom:
            def __str__(self):
                return "custom-value"

        metadata = {"obj": Custom()}
        result = serialize_metadata_for_neo4j(metadata)
        assert result["obj"] == "custom-value"
