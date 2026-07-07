"""
Test suite for Issue #595: MVP - Make metadata extraction fully domain-aware

This test suite follows TDD principles - all tests should initially FAIL (red phase)
until the implementation is complete. The tests guide the creation of:

1. DomainPromptBuilder - Service to generate domain-aware prompts
2. Modified analyze_metadata() - Use DomainPromptBuilder with active domain
3. Entity ID format validation - Ensure entity_type-normalized-name format

Test Categories:
- Unit tests (with mocks) - Test prompt building logic
- Integration tests (without mocks) - Test with real domain YAML files
- Error handling - Test proper errors when no domain configured
"""

import pytest
import re
from unittest.mock import Mock, patch, AsyncMock
from fastapi import HTTPException

from app.services.metadata import analyze_metadata
from app.core.domain_config.domain_config_service import get_domain_config_service
from app.model_schemas.domain_config import (
    DomainConfiguration,
    DomainEntity,
    DomainAttribute,
    DomainRelationship,
)

# Import the NEW service that needs to be created
# This import will FAIL initially - that's expected in TDD red phase
try:
    from app.services.domain_prompt_builder import DomainPromptBuilder
except ImportError:
    # Expected to fail initially - implementation doesn't exist yet
    DomainPromptBuilder = None


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def mock_consulting_domain():
    """Mock consulting_firm domain for unit tests."""
    return DomainConfiguration(
        id="consulting_firm",
        name="Consulting Firm",
        version="1.0.0",
        entities={
            "account": DomainEntity(
                name="account",
                description="Client organizations and companies",
                plural="accounts",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                    DomainAttribute(
                        name="industry",
                        type="enum",
                        required=False,
                        enum=["tech", "finance", "healthcare", "retail", "other"],
                    ),
                    DomainAttribute(name="revenue", type="number", required=False, unit="USD"),
                ],
                relationships=[
                    DomainRelationship(
                        type="has_projects",
                        target="project",
                        cardinality="one-to-many",
                    )
                ],
            ),
            "project": DomainEntity(
                name="project",
                description="Client projects and engagements",
                plural="projects",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                    DomainAttribute(name="budget", type="number", required=False, unit="USD"),
                    DomainAttribute(
                        name="status",
                        type="enum",
                        required=False,
                        enum=["planning", "active", "completed", "on_hold"],
                    ),
                ],
                relationships=[],
            ),
            "person": DomainEntity(
                name="person",
                description="Consultants and client contacts",
                plural="people",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                    DomainAttribute(name="email", type="string", required=False),
                    DomainAttribute(name="role", type="string", required=False),
                ],
                relationships=[],
            ),
            "team": DomainEntity(
                name="team",
                description="Project teams and departments",
                plural="teams",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                    DomainAttribute(name="department", type="string", required=False),
                ],
                relationships=[],
            ),
        },
    )


@pytest.fixture
def mock_personal_crm_domain():
    """Mock personal_crm domain for unit tests."""
    return DomainConfiguration(
        id="personal_crm",
        name="Personal CRM",
        version="1.0.0",
        entities={
            "contact": DomainEntity(
                name="contact",
                description="Personal and professional contacts",
                plural="contacts",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                    DomainAttribute(name="email", type="string", required=False),
                    DomainAttribute(
                        name="relationship_strength",
                        type="enum",
                        required=False,
                        enum=["weak", "moderate", "strong"],
                    ),
                ],
                relationships=[],
            ),
            "company": DomainEntity(
                name="company",
                description="Organizations and companies",
                plural="companies",
                attributes=[
                    DomainAttribute(name="name", type="string", required=True),
                    DomainAttribute(name="industry", type="string", required=False),
                ],
                relationships=[],
            ),
            "activity": DomainEntity(
                name="activity",
                description="Contact activities and interactions",
                plural="activities",
                attributes=[
                    DomainAttribute(
                        name="type",
                        type="enum",
                        required=False,
                        enum=["meeting", "call", "email", "event"],
                    ),
                    DomainAttribute(name="date", type="datetime", required=True),
                ],
                relationships=[],
            ),
        },
    )


# ============================================================================
# UNIT TESTS - DomainPromptBuilder (with mocks)
# ============================================================================


class TestDomainPromptBuilderUnit:
    """Unit tests for DomainPromptBuilder with mocked dependencies."""

    def test_domain_prompt_builder_exists(self):
        """Test that DomainPromptBuilder class exists."""
        # This will FAIL initially - implementation doesn't exist yet
        assert DomainPromptBuilder is not None, "DomainPromptBuilder class must be created"

    def test_domain_prompt_builder_instantiation(self):
        """Test that DomainPromptBuilder can be instantiated."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        builder = DomainPromptBuilder()
        assert builder is not None

    def test_build_extraction_prompt_method_exists(self):
        """Test that build_extraction_prompt method exists."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        builder = DomainPromptBuilder()
        assert hasattr(
            builder, "build_extraction_prompt"
        ), "DomainPromptBuilder must have build_extraction_prompt method"

    def test_prompt_includes_consulting_firm_entities(self, mock_consulting_domain):
        """Test that prompt includes all consulting_firm entity types."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        builder = DomainPromptBuilder()
        content = "Test document about Acme Corp project with John Smith leading the team."
        prompt = builder.build_extraction_prompt(content, mock_consulting_domain)

        # Verify prompt includes all entity types
        assert "account" in prompt.lower(), "Prompt must include 'account' entity type"
        assert "project" in prompt.lower(), "Prompt must include 'project' entity type"
        assert "person" in prompt.lower(), "Prompt must include 'person' entity type"
        assert "team" in prompt.lower(), "Prompt must include 'team' entity type"

    def test_prompt_includes_personal_crm_entities(self, mock_personal_crm_domain):
        """Test that prompt includes all personal_crm entity types."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        builder = DomainPromptBuilder()
        content = "Met with Sarah at TechCorp for coffee."
        prompt = builder.build_extraction_prompt(content, mock_personal_crm_domain)

        # Verify prompt includes all entity types
        assert "contact" in prompt.lower(), "Prompt must include 'contact' entity type"
        assert "company" in prompt.lower(), "Prompt must include 'company' entity type"
        assert "activity" in prompt.lower(), "Prompt must include 'activity' entity type"

    def test_prompt_includes_entity_attributes(self, mock_consulting_domain):
        """Test that prompt includes entity attributes in extraction rules."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        builder = DomainPromptBuilder()
        content = "Test content"
        prompt = builder.build_extraction_prompt(content, mock_consulting_domain)

        # Verify attributes are mentioned for entities
        # Account has name, industry, revenue
        assert (
            "name" in prompt.lower()
        ), "Prompt must include attribute names like 'name'"

        # Check for enum attributes
        assert (
            "industry" in prompt.lower() or "status" in prompt.lower()
        ), "Prompt must include enum attributes"

    def test_prompt_specifies_entity_id_format(self, mock_consulting_domain):
        """Test that prompt specifies entity_type-normalized-name format for entity IDs."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        builder = DomainPromptBuilder()
        content = "Test content"
        prompt = builder.build_extraction_prompt(content, mock_consulting_domain)

        # Verify prompt instructs to use entity_type-normalized-name format
        # Should contain instructions about ID format
        id_format_mentioned = (
            "entity_type-" in prompt.lower()
            or "entity-type-" in prompt.lower()
            or ("entity" in prompt.lower() and "normalized" in prompt.lower())
        )
        assert (
            id_format_mentioned
        ), "Prompt must specify entity_type-normalized-name ID format"

    def test_prompt_includes_document_content(self, mock_consulting_domain):
        """Test that prompt includes the actual document content."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        builder = DomainPromptBuilder()
        content = "This is unique test content for validation XYZ123."
        prompt = builder.build_extraction_prompt(content, mock_consulting_domain)

        assert content in prompt, "Prompt must include the original document content"

    def test_prompt_different_for_different_domains(
        self, mock_consulting_domain, mock_personal_crm_domain
    ):
        """Test that prompts differ based on domain configuration."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        builder = DomainPromptBuilder()
        content = "Same content for both"

        prompt1 = builder.build_extraction_prompt(content, mock_consulting_domain)
        prompt2 = builder.build_extraction_prompt(content, mock_personal_crm_domain)

        # Prompts should be different due to different entity types
        assert prompt1 != prompt2, "Prompts should differ for different domains"

        # Consulting should have "accounts" (plural), personal_crm should have "contacts" (plural)
        # Check for plural entity forms in the output format section
        assert "accounts:" in prompt1.lower() and "accounts:" not in prompt2.lower()
        assert "contacts:" not in prompt1.lower() and "contacts:" in prompt2.lower()


# ============================================================================
# UNIT TESTS - analyze_metadata with domain awareness (with mocks)
# ============================================================================


class TestAnalyzeMetadataDomainAware:
    """Unit tests for analyze_metadata with domain awareness."""

    @pytest.mark.asyncio
    async def test_analyze_metadata_fails_without_domain(self):
        """Test that analyze_metadata raises ValueError when no domain is configured."""
        # Mock domain service to return None (no active domain)
        with patch(
            "app.services.metadata.get_domain_config_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_active_domain.return_value = None
            mock_get_service.return_value = mock_service

            # Should raise ValueError
            with pytest.raises(ValueError, match="No domain configured"):
                await analyze_metadata("test/path.md")

    @pytest.mark.asyncio
    async def test_analyze_metadata_uses_domain_prompt_builder(
        self, mock_consulting_domain
    ):
        """Test that analyze_metadata uses DomainPromptBuilder with active domain."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        # Mock domain service to return active domain
        with patch(
            "app.services.metadata.get_domain_config_service"
        ) as mock_get_service, patch(
            "app.services.metadata.DomainPromptBuilder"
        ) as mock_builder_class, patch(
            "app.services.file_cache.file_cache"
        ) as mock_cache, patch(
            "app.services.metadata.get_claude_client"
        ) as mock_claude:

            # Setup mocks
            mock_service = Mock()
            mock_service.get_active_domain.return_value = mock_consulting_domain
            mock_get_service.return_value = mock_service

            mock_builder = Mock()
            mock_builder.build_extraction_prompt.return_value = "test prompt"
            mock_builder_class.return_value = mock_builder

            # Mock file cache
            from app.models import File

            mock_file = File(path="test/path.md", content="Test content")
            mock_cache.get_file = AsyncMock(return_value=mock_file)

            # Mock Claude client
            mock_client = Mock()
            mock_message = Mock()
            mock_message.content = [Mock(text="```yaml\ntitle: Test\n```")]
            mock_client.generate_message = AsyncMock(return_value=mock_message)
            mock_claude.return_value = mock_client

            # Call analyze_metadata
            try:
                await analyze_metadata("test/path.md")
            except HTTPException:
                pass  # May fail due to other reasons, we're just checking builder was called

            # Verify DomainPromptBuilder was instantiated and used
            mock_builder_class.assert_called_once()
            mock_builder.build_extraction_prompt.assert_called_once()

            # Verify it was called with content and domain
            call_args = mock_builder.build_extraction_prompt.call_args
            assert (
                mock_consulting_domain in call_args[0]
            ), "Should pass domain to prompt builder"

    @pytest.mark.asyncio
    async def test_analyze_metadata_gets_active_domain(self):
        """Test that analyze_metadata retrieves active domain from DomainConfigService."""
        with patch(
            "app.services.metadata.get_domain_config_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_active_domain.return_value = None
            mock_get_service.return_value = mock_service

            # Should attempt to get active domain
            try:
                await analyze_metadata("test/path.md")
            except ValueError:
                pass  # Expected to fail with no domain

            # Verify get_active_domain was called
            mock_service.get_active_domain.assert_called_once()


# ============================================================================
# INTEGRATION TESTS - Real domain files (NO mocks)
# ============================================================================


class TestDomainPromptBuilderIntegration:
    """Integration tests using real domain YAML files."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_load_real_consulting_firm_domain(self):
        """Integration test - load real consulting_firm.yaml and verify structure."""
        service = get_domain_config_service()
        domain = await service.load_domain("consulting_firm")

        assert domain is not None, "Should load consulting_firm.yaml"
        assert domain.id == "consulting_firm"
        assert domain.name == "Consulting Firm"

        # Verify expected entity types exist
        assert "account" in domain.entities
        assert "project" in domain.entities
        assert "person" in domain.entities
        assert "team" in domain.entities

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_load_real_personal_crm_domain(self):
        """Integration test - load real personal_crm.yaml and verify structure."""
        service = get_domain_config_service()
        domain = await service.load_domain("personal_crm")

        assert domain is not None, "Should load personal_crm.yaml"
        assert domain.id == "personal_crm"
        assert domain.name == "Personal CRM"

        # Verify expected entity types exist
        assert "contact" in domain.entities
        assert "company" in domain.entities
        assert "activity" in domain.entities

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_build_prompt_with_real_consulting_firm(self):
        """Integration test - build prompt with real consulting_firm.yaml."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        # Load real domain
        service = get_domain_config_service()
        domain = await service.load_domain("consulting_firm")
        assert domain is not None

        # Build prompt
        builder = DomainPromptBuilder()
        content = "Meeting notes with Acme Corp about Q4 project timeline."
        prompt = builder.build_extraction_prompt(content, domain)

        # Verify prompt contains entity types from real domain
        assert "account" in prompt.lower()
        assert "project" in prompt.lower()
        assert "person" in prompt.lower()
        assert "team" in prompt.lower()

        # Verify content is included
        assert content in prompt

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_build_prompt_with_real_personal_crm(self):
        """Integration test - build prompt with real personal_crm.yaml."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        # Load real domain
        service = get_domain_config_service()
        domain = await service.load_domain("personal_crm")
        assert domain is not None

        # Build prompt
        builder = DomainPromptBuilder()
        content = "Coffee meeting with Sarah Johnson at TechStart Inc."
        prompt = builder.build_extraction_prompt(content, domain)

        # Verify prompt contains entity types from real domain
        assert "contact" in prompt.lower()
        assert "company" in prompt.lower()
        assert "activity" in prompt.lower()

        # Verify content is included
        assert content in prompt


# ============================================================================
# ENTITY ID FORMAT VALIDATION TESTS
# ============================================================================


class TestEntityIdFormat:
    """Tests for entity ID format validation (entity_type-normalized-name)."""

    def test_entity_id_format_pattern(self):
        """Test entity ID format matches entity_type-normalized-name pattern."""
        # Define the expected pattern
        entity_id_pattern = re.compile(r"^[a-z_]+(-[a-z0-9-]+)+$")

        # Valid entity IDs
        valid_ids = [
            "account-acme-corp",
            "project-q4-migration",
            "person-john-smith",
            "team-engineering",
            "contact-sarah-johnson",
            "company-techstart-inc",
            "activity-coffee-meeting",
        ]

        for entity_id in valid_ids:
            assert entity_id_pattern.match(
                entity_id
            ), f"Valid entity ID should match pattern: {entity_id}"

        # Invalid entity IDs
        invalid_ids = [
            "AcmeCorp",  # No entity type prefix
            "account_acme_corp",  # Underscores instead of hyphens
            "account",  # No normalized name
            "Account-Acme-Corp",  # Capital letters
            "account-Acme-Corp",  # Capital in normalized name
        ]

        for entity_id in invalid_ids:
            assert not entity_id_pattern.match(
                entity_id
            ), f"Invalid entity ID should NOT match pattern: {entity_id}"

    def test_entity_id_normalization_rules(self):
        """Test that entity name normalization rules are correct."""
        # Expected normalizations
        test_cases = [
            ("Acme Corp", "acme-corp"),
            ("John Smith", "john-smith"),
            ("Q4 Migration Project", "q4-migration-project"),
            ("TechStart Inc.", "techstart-inc"),
            ("Sarah O'Connor", "sarah-oconnor"),  # Remove apostrophes
        ]

        for original, expected_normalized in test_cases:
            # This is the normalization logic that should be implemented
            normalized = original.lower().replace(" ", "-").replace(".", "").replace("'", "")

            assert (
                normalized == expected_normalized
            ), f"Normalization of '{original}' should be '{expected_normalized}', got '{normalized}'"

    @pytest.mark.integration
    def test_entity_id_with_entity_type_prefix(self):
        """Test that entity IDs include entity type as prefix."""
        # Examples of correctly formatted entity IDs
        test_cases = [
            ("account", "Acme Corp", "account-acme-corp"),
            ("project", "Q4 Migration", "project-q4-migration"),
            ("person", "John Smith", "person-john-smith"),
            ("contact", "Sarah Johnson", "contact-sarah-johnson"),
            ("company", "TechStart", "company-techstart"),
        ]

        for entity_type, entity_name, expected_id in test_cases:
            # Build entity ID using the expected format
            normalized_name = entity_name.lower().replace(" ", "-")
            entity_id = f"{entity_type}-{normalized_name}"

            assert (
                entity_id == expected_id
            ), f"Entity ID for {entity_type} '{entity_name}' should be '{expected_id}'"


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================


class TestErrorHandling:
    """Tests for error handling in domain-aware metadata extraction."""

    @pytest.mark.asyncio
    async def test_no_domain_configured_error_message(self):
        """Test that error message is clear when no domain is configured."""
        with patch(
            "app.services.metadata.get_domain_config_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_active_domain.return_value = None
            mock_get_service.return_value = mock_service

            with pytest.raises(ValueError) as exc_info:
                await analyze_metadata("test/path.md")

            # Verify error message is helpful
            error_message = str(exc_info.value)
            assert "domain" in error_message.lower()
            assert "configured" in error_message.lower() or "no" in error_message.lower()

    def test_domain_prompt_builder_handles_empty_entities(self):
        """Test that DomainPromptBuilder handles domain with no entities."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        # Create domain with no entities
        empty_domain = DomainConfiguration(
            id="empty_domain",
            name="Empty Domain",
            version="1.0.0",
            entities={},  # No entities
        )

        builder = DomainPromptBuilder()
        content = "Test content"

        # Should handle gracefully (either return generic prompt or raise clear error)
        try:
            prompt = builder.build_extraction_prompt(content, empty_domain)
            assert prompt is not None, "Should return some prompt even with empty entities"
        except ValueError as e:
            # Acceptable to raise error for empty entities
            assert "entities" in str(e).lower()

    @pytest.mark.asyncio
    async def test_domain_service_returns_none_handled(self):
        """Test that None return from domain service is handled properly."""
        with patch(
            "app.services.metadata.get_domain_config_service"
        ) as mock_get_service:
            mock_service = Mock()
            # Explicitly return None
            mock_service.get_active_domain.return_value = None
            mock_get_service.return_value = mock_service

            with pytest.raises(ValueError):
                await analyze_metadata("test/path.md")


# ============================================================================
# SUCCESS CRITERIA TESTS
# ============================================================================


class TestSuccessCriteria:
    """Tests verifying the success criteria from issue #595."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_success_criteria_prompt_builder_exists(self):
        """SUCCESS CRITERIA: DomainPromptBuilder service exists (~50 lines)."""
        assert (
            DomainPromptBuilder is not None
        ), "DomainPromptBuilder class must be implemented"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_success_criteria_works_with_both_domains(self):
        """SUCCESS CRITERIA: Works with both consulting_firm and personal_crm domains."""
        if DomainPromptBuilder is None:
            pytest.skip("DomainPromptBuilder not implemented yet")

        service = get_domain_config_service()

        # Test consulting_firm
        consulting_domain = await service.load_domain("consulting_firm")
        assert consulting_domain is not None

        builder = DomainPromptBuilder()
        prompt1 = builder.build_extraction_prompt("Test", consulting_domain)
        assert prompt1 is not None
        assert "account" in prompt1.lower()

        # Test personal_crm
        crm_domain = await service.load_domain("personal_crm")
        assert crm_domain is not None

        prompt2 = builder.build_extraction_prompt("Test", crm_domain)
        assert prompt2 is not None
        assert "contact" in prompt2.lower()

    @pytest.mark.asyncio
    async def test_success_criteria_error_when_no_domain(self):
        """SUCCESS CRITERIA: Proper error when no domain configured."""
        with patch(
            "app.services.metadata.get_domain_config_service"
        ) as mock_get_service:
            mock_service = Mock()
            mock_service.get_active_domain.return_value = None
            mock_get_service.return_value = mock_service

            # Must raise ValueError with clear message
            with pytest.raises(ValueError, match="No domain configured"):
                await analyze_metadata("test/path.md")

    def test_success_criteria_entity_id_format(self):
        """SUCCESS CRITERIA: Entity IDs match entity_type-normalized-name format."""
        # Pattern for valid entity IDs
        entity_id_pattern = re.compile(r"^[a-z_]+(-[a-z0-9-]+)+$")

        # Test valid formats
        valid_examples = [
            "account-acme-corp",
            "project-q4-migration",
            "person-john-smith",
            "contact-sarah-johnson",
        ]

        for entity_id in valid_examples:
            assert entity_id_pattern.match(entity_id), f"Valid ID rejected: {entity_id}"
