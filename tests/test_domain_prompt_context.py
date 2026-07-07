"""Tests for domain_prompt_context serializer — Issue #835.

Tests that DomainConfiguration objects are correctly serialized into
concise, LLM-friendly text for injection into Claude prompts.
"""

import pytest
import yaml
from pathlib import Path

from app.model_schemas.domain_config import (
    DomainAttribute,
    DomainConfiguration,
    DomainEntity,
)
from app.services.domain_prompt_context import serialize_domain_context


# ---------------------------------------------------------------------------
# Fixtures — build DomainConfigurations from actual YAML files
# ---------------------------------------------------------------------------

def _load_domain_yaml(domain_id: str) -> DomainConfiguration:
    """Load a real domain YAML and return a DomainConfiguration."""
    yaml_path = Path(__file__).parent.parent / "config" / "domains" / f"{domain_id}.yaml"
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)
    return DomainConfiguration(**raw["domain"])


@pytest.fixture
def consulting_firm():
    return _load_domain_yaml("consulting_firm")


@pytest.fixture
def executive_recruiting():
    return _load_domain_yaml("executive_recruiting")


@pytest.fixture
def personal_crm():
    return _load_domain_yaml("personal_crm")


@pytest.fixture
def minimal_domain():
    """A bare-minimum domain with one entity and no relationships."""
    return DomainConfiguration(
        id="minimal",
        name="Minimal Domain",
        entities={
            "widget": DomainEntity(
                name="widget",
                description="A simple widget",
                plural="widgets",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                ],
                relationships=[],
            ),
        },
    )


@pytest.fixture
def empty_domain():
    """A domain with no entities at all."""
    return DomainConfiguration(
        id="empty",
        name="Empty Domain",
        entities={},
    )


# ---------------------------------------------------------------------------
# Core serialization tests
# ---------------------------------------------------------------------------

class TestSerializeDomainContext:
    """Tests for the serialize_domain_context function."""

    def test_returns_string(self, consulting_firm):
        result = serialize_domain_context(consulting_firm)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_domain_name(self, consulting_firm):
        result = serialize_domain_context(consulting_firm)
        assert "Consulting Firm" in result

    def test_includes_all_entity_types_consulting(self, consulting_firm):
        result = serialize_domain_context(consulting_firm)
        assert "account" in result.lower()
        assert "project" in result.lower()
        assert "person" in result.lower()
        assert "team" in result.lower()

    def test_includes_entity_descriptions(self, consulting_firm):
        result = serialize_domain_context(consulting_firm)
        assert "Client organizations" in result
        assert "Client projects and engagements" in result
        assert "Consultants and client contacts" in result

    def test_includes_key_attributes(self, consulting_firm):
        result = serialize_domain_context(consulting_firm)
        # Should mention important attributes
        assert "industry" in result
        assert "budget" in result
        assert "status" in result

    def test_includes_enum_values(self, consulting_firm):
        """Enum values help Claude understand valid attribute options."""
        result = serialize_domain_context(consulting_firm)
        # Account.industry has enum values
        assert "tech" in result
        assert "finance" in result

    def test_includes_relationships(self, consulting_firm):
        result = serialize_domain_context(consulting_firm)
        assert "has_projects" in result
        assert "has_team_members" in result

    def test_relationship_targets_shown(self, consulting_firm):
        """Relationships should show their target entity type."""
        result = serialize_domain_context(consulting_firm)
        # has_projects -> project
        assert "project" in result.lower()


    def test_personal_crm_entities(self, personal_crm):
        result = serialize_domain_context(personal_crm)
        assert "Personal CRM" in result
        assert "contact" in result.lower()
        assert "company" in result.lower()

    def test_minimal_domain(self, minimal_domain):
        result = serialize_domain_context(minimal_domain)
        assert "Minimal Domain" in result
        assert "widget" in result.lower()
        assert "A simple widget" in result

    def test_empty_domain_returns_empty_or_minimal(self, empty_domain):
        """An empty domain should return an empty string or minimal header."""
        result = serialize_domain_context(empty_domain)
        # Should not crash, and should be short
        assert isinstance(result, str)

    def test_conciseness(self, consulting_firm):
        """Output should be concise enough to fit in a prompt without blowing up tokens."""
        result = serialize_domain_context(consulting_firm)
        # Consulting firm has 4 entities — output should be under ~2000 chars
        assert len(result) < 3000, f"Output too long ({len(result)} chars): {result[:200]}..."

    def test_no_yaml_or_json_format(self, consulting_firm):
        """Output should be plain text, not YAML or JSON."""
        result = serialize_domain_context(consulting_firm)
        assert "```" not in result
        assert "{" not in result or result.count("{") < 3  # Allow minimal braces



class TestSerializeDomainContextFormat:
    """Tests for the structure and format of serialized output."""

    def test_entity_types_are_separated(self, consulting_firm):
        """Each entity type should be clearly delineated."""
        result = serialize_domain_context(consulting_firm)
        lines = result.strip().split("\n")
        # Should have multiple lines (not all crammed together)
        assert len(lines) >= 4


    def test_none_domain_returns_empty(self):
        """Passing None should return empty string."""
        result = serialize_domain_context(None)
        assert result == ""
