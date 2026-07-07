"""
Tests for architecture validation - module boundary validation with import rules.

This module tests the validation scripts that enforce architectural boundaries
and import rules to maintain clean separation between layers.
"""
import pytest
from unittest.mock import patch, mock_open


class TestModuleBoundaryValidation:
    """Test module boundary validation functionality."""


    def test_should_allow_valid_architectural_imports(self):
        """Test that valid architectural imports are not flagged."""
        # Arrange - Valid imports within boundaries
        mock_file_content = """
        from app.models import Entity
        from app.services.claude_client import ClaudeClient
        from app.config import settings
        from typing import Dict, List
        import json
        """

        boundary_rules = {
            "routes": {
                "can_import_from": ["services", "models", "config"],
                "cannot_import_from": ["agents"]
            }
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import BoundaryValidator
            validator = BoundaryValidator(boundary_rules)
            violations = validator.validate_file_imports("/app/routes/test.py", mock_file_content)

            # Should not detect any violations
            assert len(violations) == 0

    def test_should_validate_service_layer_boundaries(self):
        """Test validation of service layer import boundaries."""
        # Arrange - Services should not import from routes
        mock_service_content = """
        from app.services.claude_client import ClaudeClient
        from app.models import Entity
        from app.routes.webhook import WebhookHandler  # Forbidden: service -> route
        """

        boundary_rules = {
            "services": {
                "can_import_from": ["models", "config", "utils"],
                "cannot_import_from": ["routes", "agents"]
            }
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import BoundaryValidator
            validator = BoundaryValidator(boundary_rules)
            violations = validator.validate_file_imports("/app/services/test.py", mock_service_content)

            # Should detect the forbidden import
            assert len(violations) > 0
            assert any("routes.webhook" in v["import"] for v in violations)

    def test_should_validate_agent_layer_boundaries(self):
        """Test validation of agent layer import boundaries."""
        # Arrange - Agents should be able to import services but not routes
        mock_agent_content = """
        from app.services.claude_client import ClaudeClient  # Allowed
        from app.domain.entities.services import EntityService  # Allowed
        from app.routes.command import CommandHandler  # Forbidden: agent -> route
        from app.agents.base import AgentBase  # Allowed: same layer
        """

        boundary_rules = {
            "agents": {
                "can_import_from": ["services", "models", "config", "agents"],
                "cannot_import_from": ["routes"]
            }
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import BoundaryValidator
            validator = BoundaryValidator(boundary_rules)
            violations = validator.validate_file_imports("/app/agents/test.py", mock_agent_content)

            # Should detect the forbidden import
            assert len(violations) > 0
            assert any("routes.command" in v["import"] for v in violations)

    def test_should_parse_complex_import_statements(self):
        """Test parsing of complex import statements."""
        # Arrange
        complex_imports = """
        from app.services.claude_client import ClaudeClient, ModelConfig
        from app.models import (
            Entity,
            Relationship,
            Meeting
        )
        import app.routes.webhook as webhook_module
        from app.agents import memory_agent, chat_agent
        """

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportParser
            parser = ImportParser()
            imports = parser.extract_imports(complex_imports)

            # Should extract all import modules
            import_modules = [imp["module"] for imp in imports]
            assert "app.services.claude_client" in import_modules
            assert "app.models" in import_modules
            assert "app.routes.webhook" in import_modules
            assert "app.agents" in import_modules

    def test_should_detect_circular_boundary_violations(self):
        """Test detection of circular dependencies between layers."""
        # Arrange
        dependency_graph = {
            "/app/services/claude_client.py": ["/app/agents/memory_agent.py"],
            "/app/agents/memory_agent.py": ["/app/services/claude_client.py"]
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import BoundaryValidator
            validator = BoundaryValidator({})
            cycles = validator.detect_cross_layer_cycles(dependency_graph)

            # Should detect circular dependency
            assert len(cycles) > 0
            cycle = cycles[0]
            assert "services/claude_client.py" in cycle
            assert "agents/memory_agent.py" in cycle

    def test_should_support_exception_rules(self):
        """Test support for exception rules in boundary validation."""
        # Arrange
        mock_file_content = """
        from app.agents.memory_agent import MemoryAgent  # Normally forbidden
        """

        boundary_rules = {
            "routes": {
                "can_import_from": ["services"],
                "cannot_import_from": ["agents"],
                "exceptions": [
                    {"file": "/app/routes/command.py", "allow": ["agents.memory_agent"]}
                ]
            }
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import BoundaryValidator
            validator = BoundaryValidator(boundary_rules)
            violations = validator.validate_file_imports("/app/routes/command.py", mock_file_content)

            # Should not flag the exception
            assert len(violations) == 0

    def test_should_validate_relative_imports(self):
        """Test validation of relative imports within modules."""
        # Arrange
        mock_file_content = """
        from .base import AgentBase  # Allowed: same module
        from ..services.claude_client import ClaudeClient  # May be forbidden
        from ...routes.webhook import WebhookHandler  # Forbidden: crossing boundaries
        """

        boundary_rules = {
            "agents": {
                "can_import_from": ["services"],
                "cannot_import_from": ["routes"]
            }
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import BoundaryValidator
            validator = BoundaryValidator(boundary_rules)
            violations = validator.validate_file_imports("/app/agents/test.py", mock_file_content)

            # Should detect the forbidden relative import
            assert len(violations) > 0
            assert any("routes.webhook" in str(v) for v in violations)

    def test_should_generate_boundary_violation_report(self):
        """Test generation of boundary violation report."""
        # Arrange
        mock_violations = [
            {
                "file": "/app/routes/test.py",
                "import": "app.agents.memory_agent",
                "rule": "routes cannot import from agents",
                "severity": "error"
            }
        ]

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import BoundaryValidator
            validator = BoundaryValidator({})
            report = validator.generate_violation_report(mock_violations)

            # Should include violation details
            assert "/app/routes/test.py" in report
            assert "agents.memory_agent" in report
            assert "error" in report.lower()


class TestBoundaryConfigurationValidation:
    """Test boundary configuration validation."""

    def test_should_load_boundary_rules_from_config(self):
        """Test loading boundary rules from configuration file."""
        # Arrange
        mock_config_content = """
        boundary_validation:
          enabled: true
          rules:
            routes:
              can_import_from:
                - services
                - models
                - config
              cannot_import_from:
                - agents
            services:
              can_import_from:
                - models
                - config
              cannot_import_from:
                - routes
                - agents
            agents:
              can_import_from:
                - services
                - models
              cannot_import_from:
                - routes
        """

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ConfigLoader

            with patch("builtins.open", mock_open(read_data=mock_config_content)):
                config = ConfigLoader.load_config(".architecture-rules.yml")

                boundary_config = config["boundary_validation"]
                assert boundary_config["enabled"] is True
                assert "routes" in boundary_config["rules"]
                assert "services" in boundary_config["rules"]["routes"]["can_import_from"]

    def test_should_validate_boundary_rule_consistency(self):
        """Test validation of boundary rule consistency."""
        # Arrange - Inconsistent rules (A can import B, B can import A)
        inconsistent_rules = {
            "services": {
                "can_import_from": ["agents"]
            },
            "agents": {
                "can_import_from": ["services"]
            }
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import BoundaryValidator
            validator = BoundaryValidator({})

            with pytest.raises(ValueError, match="Circular dependency detected"):
                validator.validate_rule_consistency(inconsistent_rules)

    def test_should_support_layered_architecture_patterns(self):
        """Test support for common layered architecture patterns."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ArchitecturePatterns

            # Test MVC pattern
            mvc_rules = ArchitecturePatterns.get_mvc_rules()
            assert "controllers" in mvc_rules
            assert "models" in mvc_rules
            assert "views" in mvc_rules

            # Test Clean Architecture pattern
            clean_rules = ArchitecturePatterns.get_clean_architecture_rules()
            assert "entities" in clean_rules
            assert "use_cases" in clean_rules
            assert "interfaces" in clean_rules


class TestBoundaryValidationCLI:
    """Test command-line interface for boundary validation."""

    def test_should_validate_entire_codebase(self):
        """Test validation of entire codebase for boundary violations."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            with patch("scripts.validate_architecture.BoundaryValidator") as mock_validator:
                mock_validator.return_value.validate_codebase.return_value = []

                exit_code = main(["--check-boundaries", "/app"])
                assert exit_code == 0  # Should pass with no violations

    def test_should_return_error_code_on_violations(self):
        """Test that script returns error code when boundary violations found."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            mock_violations = [
                {"file": "test.py", "import": "forbidden.module", "rule": "test rule"}
            ]

            with patch("scripts.validate_architecture.BoundaryValidator") as mock_validator:
                mock_validator.return_value.validate_codebase.return_value = mock_violations

                exit_code = main(["--check-boundaries", "/app"])
                assert exit_code != 0  # Should indicate failure

    def test_should_support_specific_layer_validation(self):
        """Test validation of specific architectural layers."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            with patch("scripts.validate_architecture.BoundaryValidator") as mock_validator:
                main(["--check-boundaries", "--layer=routes", "/app"])

                # Should have called validation for specific layer
                mock_validator.return_value.validate_layer.assert_called_with("routes")

    def test_should_support_fix_mode(self):
        """Test automatic fixing of boundary violations where possible."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            with patch("scripts.validate_architecture.BoundaryValidator") as mock_validator:
                main(["--check-boundaries", "--fix", "/app"])

                # Should attempt to fix violations
                mock_validator.return_value.fix_violations.assert_called_once()


class TestArchitecturalPatternDetection:
    """Test detection and validation of architectural patterns."""

    def test_should_detect_current_architecture_pattern(self):
        """Test detection of current architectural pattern in codebase."""
        # Arrange - Mock codebase structure
        mock_structure = {
            "/app/routes/": ["webhook.py", "command.py", "digest.py"],
            "/app/services/": ["claude_client.py", "entity_brain.py"],
            "/app/agents/": ["memory_agent.py", "chat.py"],
            "/app/models/": ["models.py"]
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import PatternDetector
            detector = PatternDetector()
            pattern = detector.detect_architecture_pattern(mock_structure)

            # Should detect layered architecture or similar pattern
            assert pattern in ["layered", "mvc", "clean_architecture", "hexagonal"]

    def test_should_validate_pattern_compliance(self):
        """Test validation of compliance with detected architectural pattern."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import PatternValidator
            validator = PatternValidator("layered")

            mock_violations = [
                {"layer": "presentation", "imports_from": "data", "violation": "skip_layer"}
            ]

            compliance_score = validator.validate_pattern_compliance(mock_violations)
            assert 0 <= compliance_score <= 1  # Should return valid score

    def test_should_suggest_architectural_improvements(self):
        """Test generation of architectural improvement suggestions."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ArchitecturalAdvisor
            advisor = ArchitecturalAdvisor()

            mock_violations = [
                {"file": "route.py", "import": "agent.py", "rule": "layer_violation"}
            ]

            suggestions = advisor.generate_improvement_suggestions(mock_violations)

            # Should provide actionable suggestions
            assert len(suggestions) > 0
            assert any("refactor" in suggestion.lower() for suggestion in suggestions)
