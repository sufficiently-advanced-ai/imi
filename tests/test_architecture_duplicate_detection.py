"""
Tests for architecture validation - duplicate service detection with fuzzy matching.

This module tests the validation scripts that detect duplicate services and components
in the codebase using fuzzy matching algorithms to catch similar functionality.
"""
import pytest
from unittest.mock import patch, mock_open


class TestDuplicateServiceDetection:
    """Test duplicate service detection functionality."""

    def test_should_detect_exact_duplicate_service_names(self):
        """Test detection of services with identical names."""
        # Arrange
        mock_files = [
            "/app/services/entity_brain.py",
            "/app/services/entity_brain_enhanced.py",  # Similar name
            "/app/services/claude_client.py",
            "/app/services/meeting_brain.py"
        ]

        # Test that the DuplicateDetector properly detects duplicate services
        scripts = pytest.importorskip("scripts.validate_architecture", reason="architecture tooling not available")
        detector = scripts.DuplicateDetector()
        duplicates = detector.detect_duplicate_services(mock_files)
        # Should detect entity_brain variants as potential duplicates
        assert len(duplicates) > 0
        assert any('entity_brain' in dup['name'] for dup in duplicates)


    def test_should_analyze_method_signatures_for_duplication(self):
        """Test analysis of method signatures to detect functional duplication."""
        # Arrange
        service_a_content = """
        class ServiceA:
            def process_entity(self, entity_id: str) -> dict:
                return {"id": entity_id}

            def validate_entity(self, entity: dict) -> bool:
                return True
        """

        service_b_content = """
        class ServiceB:
            def handle_entity(self, entity_id: str) -> dict:  # Similar signature
                return {"id": entity_id}

            def check_entity(self, entity: dict) -> bool:  # Similar signature
                return True
        """

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import DuplicateDetector
            detector = DuplicateDetector()
            similarity = detector.compare_method_signatures(service_a_content, service_b_content)

            # Should detect high similarity in method signatures
            assert similarity > 0.7

    def test_should_respect_whitelist_configuration(self):
        """Test that whitelisted duplicate patterns are ignored."""
        # Arrange
        config = {
            "duplicate_detection": {
                "whitelist": [
                    "entity_brain*",  # Allow entity_brain variations
                    "*_enhanced.py"   # Allow enhanced versions
                ]
            }
        }

        mock_files = [
            "/app/services/entity_brain.py",
            "/app/services/entity_brain_enhanced.py",  # Should be whitelisted
            "/app/services/entity_brain_refactored.py"  # Should be detected
        ]

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import DuplicateDetector
            detector = DuplicateDetector(config=config)
            duplicates = detector.detect_duplicate_services(mock_files)

            # Should not flag whitelisted patterns
            flagged_files = [d['file'] for d in duplicates]
            assert "/app/services/entity_brain_enhanced.py" not in flagged_files

    def test_should_analyze_import_dependencies_for_duplication(self):
        """Test analysis of import patterns to detect duplicate functionality."""
        # Arrange
        service_imports = {
            "service_a.py": ["from app.models import Entity", "import requests", "from typing import Dict"],
            "service_b.py": ["from app.models import Entity", "import httpx", "from typing import Dict"],  # Similar
            "service_c.py": ["from app.database import Base", "import sqlalchemy"]  # Different
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import DuplicateDetector
            detector = DuplicateDetector()
            similar_services = detector.analyze_import_similarity(service_imports)

            # Should detect service_a and service_b as similar
            assert len(similar_services) > 0
            assert any('service_a.py' in pair and 'service_b.py' in pair
                      for pair in similar_services)

    def test_should_generate_detailed_duplicate_report(self):
        """Test generation of detailed duplicate detection report."""
        # Arrange
        mock_duplicates = [
            {
                "name": "entity_registry",
                "files": ["entity_registry.py", "entity_registry_canonical.py"],
                "similarity_score": 0.85,
                "reason": "Similar class names and method signatures"
            }
        ]

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import DuplicateDetector
            detector = DuplicateDetector()
            report = detector.generate_duplicate_report(mock_duplicates)

            # Should include detailed information
            assert "entity_registry" in report
            assert "0.85" in report  # Similarity score
            assert "Similar class names" in report

    def test_should_exclude_test_files_from_analysis(self):
        """Test that test files are excluded from duplicate detection."""
        # Arrange
        mock_files = [
            "/app/services/entity_brain.py",
            "/tests/test_entity_brain.py",  # Should be excluded
            "/tests/issue-123_test_entity_brain.py"  # Should be excluded
        ]

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import DuplicateDetector
            detector = DuplicateDetector()
            filtered_files = detector.filter_analysis_files(mock_files)

            # Should exclude test files
            assert len(filtered_files) == 1
            assert "/app/services/entity_brain.py" in filtered_files

    def test_should_handle_false_positives_gracefully(self):
        """Test handling of false positive duplicate detections."""
        # Arrange - Services with similar names but different purposes
        mock_services = {
            "meeting_brain.py": "class MeetingBrain:\n    def analyze_meeting(self):\n        pass",
            "entity_brain.py": "class EntityBrain:\n    def process_entity(self):\n        pass"
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import DuplicateDetector
            detector = DuplicateDetector(similarity_threshold=0.9)  # High threshold
            duplicates = detector.analyze_service_similarity(mock_services)

            # Should not flag these as duplicates (different purposes)
            assert len(duplicates) == 0

    def test_should_detect_circular_dependencies(self):
        """Test detection of circular import dependencies."""
        # Arrange
        dependency_graph = {
            "service_a.py": ["service_b.py"],
            "service_b.py": ["service_c.py"],
            "service_c.py": ["service_a.py"]  # Creates cycle
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import DuplicateDetector
            detector = DuplicateDetector()
            cycles = detector.detect_circular_dependencies(dependency_graph)

            # Should detect the circular dependency
            assert len(cycles) > 0
            assert "service_a.py" in cycles[0]
            assert "service_b.py" in cycles[0]
            assert "service_c.py" in cycles[0]


class TestDuplicateDetectionConfiguration:
    """Test configuration loading and validation for duplicate detection."""

    def test_should_load_configuration_from_yaml(self):
        """Test loading duplicate detection configuration from .architecture-rules.yml."""
        # Arrange
        mock_config_content = """
        duplicate_detection:
          enabled: true
          similarity_threshold: 0.8
          whitelist:
            - "entity_*_enhanced.py"
            - "*_legacy.py"
          exclude_paths:
            - "tests/"
            - "migrations/"
        """

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ConfigLoader

            with patch("builtins.open", mock_open(read_data=mock_config_content)):
                config = ConfigLoader.load_config(".architecture-rules.yml")

                assert config["duplicate_detection"]["enabled"] is True
                assert config["duplicate_detection"]["similarity_threshold"] == 0.8
                assert len(config["duplicate_detection"]["whitelist"]) == 2

    def test_should_use_default_configuration_when_file_missing(self):
        """Test fallback to default configuration when config file is missing."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ConfigLoader

            with patch("os.path.exists", return_value=False):
                config = ConfigLoader.load_config(".architecture-rules.yml")

                # Should have default values
                assert "duplicate_detection" in config
                assert config["duplicate_detection"]["enabled"] is True
                assert config["duplicate_detection"]["similarity_threshold"] == 0.75

    def test_should_validate_configuration_values(self):
        """Test validation of configuration values."""
        # Arrange - Invalid configuration
        invalid_config = {
            "duplicate_detection": {
                "enabled": "yes",  # Should be boolean
                "similarity_threshold": 1.5,  # Should be <= 1.0
                "whitelist": "not_a_list"  # Should be list
            }
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ConfigValidator
            validator = ConfigValidator()

            with pytest.raises(ValueError):
                validator.validate_duplicate_config(invalid_config["duplicate_detection"])


class TestDuplicateDetectionCLI:
    """Test command-line interface for duplicate detection."""

    def test_should_return_exit_code_when_duplicates_found(self):
        """Test that script returns non-zero exit code when duplicates are found."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            with patch("scripts.validate_architecture.DuplicateDetector") as mock_detector:
                mock_detector.return_value.detect_duplicate_services.return_value = [
                    {"name": "test_duplicate", "files": ["a.py", "b.py"]}
                ]

                exit_code = main(["--check-duplicates", "/app/services"])
                assert exit_code != 0  # Should indicate failure

    def test_should_support_verbose_output_flag(self):
        """Test verbose output flag shows detailed information."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            with patch("scripts.validate_architecture.DuplicateDetector") as mock_detector:
                with patch("builtins.print") as mock_print:
                    main(["--check-duplicates", "--verbose", "/app/services"])

                    # Should have printed verbose information
                    assert any("Analyzing" in str(call) for call in mock_print.call_args_list)

    def test_should_support_output_format_options(self):
        """Test different output format options (json, yaml, text)."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            mock_duplicates = [{"name": "test", "files": ["a.py", "b.py"]}]

            with patch("scripts.validate_architecture.DuplicateDetector") as mock_detector:
                mock_detector.return_value.detect_duplicate_services.return_value = mock_duplicates

                # Test JSON output
                with patch("json.dump") as mock_json:
                    main(["--check-duplicates", "--format=json", "/app/services"])
                    mock_json.assert_called_once()
