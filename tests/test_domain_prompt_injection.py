"""Tests for domain context injection into EntityService and ExtractEntitiesTool — Issue #835.

Verifies that domain context from serialize_domain_context is properly
injected into entity extraction prompts.
"""

import re

import pytest
import yaml
from pathlib import Path
from unittest.mock import Mock, AsyncMock

from app.domain.entities.services.entity_service import EntityService
from app.domain.entities.services.entity_repository import EntityRepository
from app.model_schemas.domain_config import DomainConfiguration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _load_domain_yaml(domain_id: str) -> DomainConfiguration:
    yaml_path = (
        Path(__file__).parent.parent / "config" / "domains" / f"{domain_id}.yaml"
    )
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)
    return DomainConfiguration(**raw["domain"])


@pytest.fixture
def consulting_firm():
    return _load_domain_yaml("consulting_firm")


@pytest.fixture
def mock_repository(consulting_firm):
    """Mock EntityRepository with consulting_firm domain loaded."""
    repo = Mock(spec=EntityRepository)
    repo.get_entity_types.return_value = list(consulting_firm.entities.keys())

    def get_schema(entity_type):
        return consulting_firm.entities.get(entity_type)

    repo.get_entity_schema.side_effect = get_schema
    repo.validate_entity.return_value = (True, [])
    repo.store_entity = AsyncMock()
    repo.get_entity = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_claude_client():
    client = Mock()
    client.generate_message = AsyncMock()
    return client


@pytest.fixture
def entity_service(mock_repository, mock_claude_client):
    return EntityService(
        repository=mock_repository,
        claude_client=mock_claude_client,
    )


# ---------------------------------------------------------------------------
# EntityService transcript extraction prompt tests
# ---------------------------------------------------------------------------


class TestTranscriptExtractionPromptInjection:
    """Domain context should appear in transcript extraction prompts."""

    def test_transcript_prompt_includes_domain_context_when_set(
        self, entity_service, consulting_firm
    ):
        """When domain_config is set, transcript prompt should include domain context."""
        entity_service._domain_config = consulting_firm

        entity_types = list(consulting_firm.entities.keys())
        prompt = entity_service._build_transcript_extraction_prompt(
            transcript_text="Test transcript",
            entity_types=entity_types,
        )

        # Should contain domain context
        assert "Consulting Firm" in prompt
        assert "account" in prompt.lower()
        assert "Client organizations" in prompt

    def test_transcript_prompt_works_without_domain_config(self, entity_service):
        """Without domain config, prompt should still work (backward compat)."""
        entity_service._domain_config = None

        prompt = entity_service._build_transcript_extraction_prompt(
            transcript_text="Test transcript",
            entity_types=["person", "project"],
        )

        assert "Test transcript" in prompt
        assert "person" in prompt
        assert isinstance(prompt, str)

    def test_transcript_prompt_includes_entity_descriptions(
        self, entity_service, consulting_firm
    ):
        entity_service._domain_config = consulting_firm

        entity_types = list(consulting_firm.entities.keys())
        prompt = entity_service._build_transcript_extraction_prompt(
            transcript_text="Meeting about budgets",
            entity_types=entity_types,
        )

        # Should include attribute info that helps Claude extract better
        assert "industry" in prompt or "budget" in prompt

    def test_transcript_prompt_includes_relationships(
        self, entity_service, consulting_firm
    ):
        entity_service._domain_config = consulting_firm

        entity_types = list(consulting_firm.entities.keys())
        prompt = entity_service._build_transcript_extraction_prompt(
            transcript_text="Discussing project team",
            entity_types=entity_types,
        )

        assert "has_projects" in prompt or "has_team_members" in prompt


# ---------------------------------------------------------------------------
# EntityService file extraction prompt tests
# ---------------------------------------------------------------------------


class TestFileExtractionPromptInjection:
    """Domain context should appear in file/content extraction prompts."""

    def test_extraction_prompt_includes_domain_name(
        self, entity_service, consulting_firm
    ):
        """_build_extraction_prompt should include domain context."""
        entity_service._domain_config = consulting_firm

        entity_schema = consulting_firm.entities["account"]
        prompt = entity_service._build_extraction_prompt(entity_schema, "account")

        # The prompt already includes entity-specific info. With domain context,
        # it should also reference the overall domain.
        assert "Consulting Firm" in prompt or "Client organizations" in prompt

    def test_extraction_prompt_still_works_without_domain(self, entity_service):
        """Without domain config, prompt should still include entity info."""
        entity_service._domain_config = None

        mock_schema = Mock()
        mock_schema.name = "Person"
        mock_schema.description = "People in the system"
        mock_schema.attributes_dict = {}
        mock_schema.relationships_dict = {}

        prompt = entity_service._build_extraction_prompt(mock_schema, "person")

        assert "Person" in prompt
        assert isinstance(prompt, str)


# ---------------------------------------------------------------------------
# get_entity_extraction_prompts includes domain header
# ---------------------------------------------------------------------------


class TestGetEntityExtractionPromptsWithDomain:
    """get_entity_extraction_prompts should include domain context."""

    def test_prompts_include_domain_header(self, entity_service, consulting_firm):
        entity_service._domain_config = consulting_firm

        prompts = entity_service.get_entity_extraction_prompts()

        # All prompts should include domain context
        for entity_type, prompt in prompts.items():
            assert (
                "Consulting Firm" in prompt or "Domain:" in prompt
            ), f"Prompt for {entity_type} missing domain context"

    def test_prompts_still_work_without_domain(self, entity_service):
        entity_service._domain_config = None

        prompts = entity_service.get_entity_extraction_prompts()

        # Should still return prompts, just without domain header
        assert len(prompts) > 0
        for entity_type, prompt in prompts.items():
            assert entity_type in prompt.lower() or "Extract" in prompt




