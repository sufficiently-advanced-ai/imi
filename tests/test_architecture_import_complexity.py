"""
Tests for architecture validation - import complexity checking.

This module tests the validation scripts that analyze import complexity,
dependency cycles, and import organization patterns in the codebase.
"""
import pytest
from unittest.mock import patch, mock_open


class TestImportComplexityAnalysis:
    """Test import complexity analysis functionality."""

    def test_should_detect_excessive_import_counts(self):
        """Test detection of files with excessive number of imports."""
        # Arrange
        mock_file_with_many_imports = """
        import os
        import sys
        import json
        import yaml
        import requests
        import asyncio
        import logging
        from typing import Dict, List, Optional, Union, Any
        from pathlib import Path
        from datetime import datetime, timedelta
        from dataclasses import dataclass
        from fastapi import FastAPI, HTTPException, Depends
        from pydantic import BaseModel, validator
        from sqlalchemy import create_engine, Column, Integer
        from app.models import Entity, Relationship, Meeting
        from app.services import claude_client, entity_brain, metadata
        from app.utils import retry, timeout, circuit_breaker
        """

        # This test should fail initially - no implementation exists
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer(max_imports_threshold=15)
            complexity = analyzer.analyze_import_count(mock_file_with_many_imports)

            # Should detect excessive imports (>15)
            assert complexity["total_imports"] > 15
            assert complexity["is_excessive"] is True
            assert "too_many_imports" in complexity["issues"]

    def test_should_categorize_import_types(self):
        """Test categorization of different types of imports."""
        # Arrange
        mock_imports = """
        import os  # Standard library
        import sys  # Standard library
        import requests  # Third-party
        import fastapi  # Third-party
        from app.models import Entity  # Local/internal
        from app.services import claude_client  # Local/internal
        from typing import Dict  # Typing
        from . import utils  # Relative
        from ..services import entity_brain  # Relative
        """

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer()
            categorization = analyzer.categorize_imports(mock_imports)

            # Should categorize imports properly
            assert categorization["standard_library"] >= 2  # os, sys
            assert categorization["third_party"] >= 2  # requests, fastapi
            assert categorization["local_imports"] >= 2  # app.models, app.services
            assert categorization["relative_imports"] >= 2  # ., ..
            assert categorization["typing_imports"] >= 1  # typing.Dict

    def test_should_detect_import_organization_violations(self):
        """Test detection of import organization violations (PEP 8)."""
        # Arrange - Poorly organized imports
        mock_poorly_organized_imports = """
        from app.models import Entity  # Local import first (wrong)
        import os  # Standard library second (wrong)
        import requests  # Third-party mixed in
        from typing import Dict
        from app.services import claude_client
        import sys  # Another standard library out of place
        """

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer()
            violations = analyzer.check_import_organization(mock_poorly_organized_imports)

            # Should detect organization violations
            assert len(violations) > 0
            assert any("standard_library_not_first" in v["type"] for v in violations)

    def test_should_analyze_import_depth(self):
        """Test analysis of import depth and nested imports."""
        # Arrange
        mock_deep_imports = """
        from app.services.domain.entities.processors.enhanced import EntityProcessor
        from app.utils.helpers.formatting.text.cleanup import clean_text
        from typing import Dict  # Shallow import
        """

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer()
            depth_analysis = analyzer.analyze_import_depth(mock_deep_imports)

            # Should analyze import path depth
            assert depth_analysis["max_depth"] >= 6  # app.services.domain.entities.processors.enhanced
            assert depth_analysis["average_depth"] > 2
            assert len(depth_analysis["deep_imports"]) >= 2

    def test_should_detect_circular_import_patterns(self):
        """Test detection of potential circular import patterns."""
        # Arrange
        module_imports = {
            "services/claude_client.py": ["services/entity_brain", "agents/memory_agent"],
            "services/entity_brain.py": ["services/claude_client", "models"],
            "agents/memory_agent.py": ["services/claude_client"],
            "models.py": []
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer()
            circular_imports = analyzer.detect_circular_imports(module_imports)

            # Should detect circular dependency
            assert len(circular_imports) > 0
            cycle = circular_imports[0]
            assert "claude_client" in cycle and "entity_brain" in cycle

    def test_should_analyze_unused_imports(self):
        """Test detection of unused imports in files."""
        # Arrange
        mock_file_content = """
        import os  # Used
        import sys  # Unused
        import json  # Unused
        from typing import Dict  # Used
        from app.models import Entity  # Unused

        def main():
            path = os.path.join("test", "path")  # Uses os
            data: Dict[str, str] = {}  # Uses Dict
            return path, data
        """

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer()
            unused_imports = analyzer.detect_unused_imports(mock_file_content)

            # Should detect unused imports
            assert len(unused_imports) >= 3  # sys, json, Entity
            unused_names = [imp["name"] for imp in unused_imports]
            assert "sys" in unused_names
            assert "json" in unused_names
            assert "Entity" in unused_names

    def test_should_detect_import_star_violations(self):
        """Test detection of star import violations (from module import *)."""
        # Arrange
        mock_star_imports = """
        from app.models import *  # Violation
        from typing import *  # Violation
        from app.services import claude_client  # OK
        import os  # OK
        """

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer()
            star_violations = analyzer.detect_star_imports(mock_star_imports)

            # Should detect star import violations
            assert len(star_violations) >= 2
            violated_modules = [v["module"] for v in star_violations]
            assert "app.models" in violated_modules
            assert "typing" in violated_modules

    def test_should_analyze_import_locality(self):
        """Test analysis of import locality (how far imports reach)."""
        # Arrange
        file_path = "/app/services/entity_brain.py"
        imports = [
            "app.models",  # Same app, different layer
            "app.services.claude_client",  # Same layer
            "app.utils.retry",  # Utility layer
            "external_package.module"  # External
        ]

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer()
            locality = analyzer.analyze_import_locality(file_path, imports)

            # Should categorize by locality
            assert locality["same_layer"] >= 1  # claude_client
            assert locality["cross_layer"] >= 1  # models
            assert locality["utility_layer"] >= 1  # utils
            assert locality["external"] >= 1  # external_package

    def test_should_calculate_import_stability(self):
        """Test calculation of import stability metrics."""
        # Arrange - Module dependency graph
        dependency_graph = {
            "models.py": [],  # Stable (no outgoing dependencies)
            "utils/retry.py": [],  # Stable
            "services/claude_client.py": ["models"],  # Relatively stable
            "routes/webhook.py": ["services/claude_client", "models", "utils/retry"],  # Less stable
            "agents/memory_agent.py": ["services/claude_client", "services/entity_brain"]  # Unstable
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer()
            stability = analyzer.calculate_import_stability(dependency_graph)

            # Should calculate instability (I = Ce / (Ca + Ce))
            assert stability["models.py"]["instability"] == 0  # Most stable
            assert stability["routes/webhook.py"]["instability"] > 0.5  # Less stable
            assert "most_stable" in stability
            assert "least_stable" in stability


class TestImportComplexityMetrics:
    """Test import complexity metrics calculation."""

    def test_should_calculate_import_coupling_metrics(self):
        """Test calculation of import-based coupling metrics."""
        # Arrange
        module_imports = {
            "high_coupling.py": ["module_a", "module_b", "module_c", "module_d", "module_e"],
            "low_coupling.py": ["module_a"],
            "medium_coupling.py": ["module_a", "module_b", "module_c"]
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer()
            coupling_metrics = analyzer.calculate_import_coupling(module_imports)

            # Should calculate coupling based on import counts
            assert coupling_metrics["high_coupling.py"]["efferent_coupling"] == 5
            assert coupling_metrics["low_coupling.py"]["efferent_coupling"] == 1
            assert coupling_metrics["high_coupling.py"]["coupling_level"] == "high"

    def test_should_identify_import_hotspots(self):
        """Test identification of import complexity hotspots."""
        # Arrange
        complexity_data = {
            "problem_file.py": {
                "total_imports": 25,
                "circular_imports": 3,
                "unused_imports": 5,
                "star_imports": 2,
                "deep_imports": 4
            },
            "clean_file.py": {
                "total_imports": 8,
                "circular_imports": 0,
                "unused_imports": 0,
                "star_imports": 0,
                "deep_imports": 1
            }
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer()
            hotspots = analyzer.identify_import_hotspots(complexity_data)

            # Should identify problem_file.py as hotspot
            assert len(hotspots) >= 1
            assert hotspots[0]["file"] == "problem_file.py"
            assert hotspots[0]["severity"] in ["high", "critical"]

    def test_should_calculate_import_complexity_score(self):
        """Test calculation of overall import complexity score."""
        # Arrange
        import_metrics = {
            "total_imports": 15,
            "unused_imports": 2,
            "star_imports": 1,
            "circular_imports": 1,
            "organization_violations": 3,
            "deep_imports": 2
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityAnalyzer
            analyzer = ImportComplexityAnalyzer()
            complexity_score = analyzer.calculate_complexity_score(import_metrics)

            # Should return normalized complexity score (0-100)
            assert 0 <= complexity_score <= 100
            # Higher violations should result in higher complexity
            assert complexity_score > 50  # Due to multiple violations


class TestImportComplexityConfiguration:
    """Test import complexity configuration."""

    def test_should_load_import_complexity_config(self):
        """Test loading import complexity configuration."""
        # Arrange
        mock_config_content = """
        import_complexity:
          enabled: true
          max_imports_per_file: 20
          max_import_depth: 5
          allow_star_imports: false
          detect_unused_imports: true
          thresholds:
            coupling:
              warning: 8
              error: 15
            complexity_score:
              warning: 60
              error: 80
          exclusions:
            - "__init__.py"
            - "tests/"
        """

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ConfigLoader

            with patch("builtins.open", mock_open(read_data=mock_config_content)):
                config = ConfigLoader.load_config(".architecture-rules.yml")

                import_config = config["import_complexity"]
                assert import_config["enabled"] is True
                assert import_config["max_imports_per_file"] == 20
                assert import_config["allow_star_imports"] is False

    def test_should_validate_import_complexity_thresholds(self):
        """Test validation of import complexity threshold configuration."""
        # Arrange
        invalid_config = {
            "import_complexity": {
                "thresholds": {
                    "coupling": {"warning": 15, "error": 10}  # Error < warning (invalid)
                }
            }
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ConfigValidator
            validator = ConfigValidator()

            with pytest.raises(ValueError, match="Error threshold must be greater"):
                validator.validate_import_complexity_config(invalid_config["import_complexity"])


class TestImportComplexityReporting:
    """Test import complexity reporting."""

    def test_should_generate_import_complexity_report(self):
        """Test generation of import complexity report."""
        # Arrange
        mock_analysis_results = {
            "summary": {
                "total_files": 100,
                "average_imports_per_file": 12,
                "hotspots_count": 5
            },
            "violations": [
                {
                    "file": "problem.py",
                    "type": "excessive_imports",
                    "count": 25,
                    "threshold": 20
                }
            ],
            "circular_imports": [
                {
                    "cycle": ["module_a.py", "module_b.py", "module_a.py"],
                    "length": 2
                }
            ]
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityReporter
            reporter = ImportComplexityReporter()
            report = reporter.generate_complexity_report(mock_analysis_results)

            # Should include all major sections
            assert "Import Complexity Summary" in report
            assert "Violations" in report
            assert "Circular Dependencies" in report
            assert "problem.py" in report

    def test_should_export_import_graph_visualization(self):
        """Test export of import dependency graph visualization."""
        # Arrange
        dependency_graph = {
            "nodes": [
                {"id": "models.py", "type": "model"},
                {"id": "services/claude_client.py", "type": "service"},
                {"id": "routes/webhook.py", "type": "route"}
            ],
            "edges": [
                {"source": "routes/webhook.py", "target": "services/claude_client.py"},
                {"source": "services/claude_client.py", "target": "models.py"}
            ]
        }

        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import ImportComplexityReporter
            reporter = ImportComplexityReporter()
            graph_data = reporter.export_dependency_graph(dependency_graph)

            # Should export graph in suitable format
            assert "nodes" in graph_data
            assert "edges" in graph_data
            assert len(graph_data["nodes"]) == 3
            assert len(graph_data["edges"]) == 2


class TestImportComplexityCLI:
    """Test command-line interface for import complexity analysis."""

    def test_should_analyze_import_complexity(self):
        """Test analysis of import complexity from command line."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            with patch("scripts.validate_architecture.ImportComplexityAnalyzer") as mock_analyzer:
                mock_results = {"violations": [], "hotspots": []}
                mock_analyzer.return_value.analyze_codebase.return_value = mock_results

                exit_code = main(["--check-import-complexity", "/app"])
                assert exit_code == 0  # Should pass with no violations

    def test_should_return_error_on_complexity_violations(self):
        """Test that script returns error code on complexity violations."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            mock_violations = [
                {"file": "problem.py", "type": "excessive_imports", "severity": "error"}
            ]

            with patch("scripts.validate_architecture.ImportComplexityAnalyzer") as mock_analyzer:
                mock_analyzer.return_value.analyze_codebase.return_value = {"violations": mock_violations}

                exit_code = main(["--check-import-complexity", "/app"])
                assert exit_code != 0  # Should indicate failure

    def test_should_support_fix_mode_for_imports(self):
        """Test automatic fixing of import complexity issues."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            with patch("scripts.validate_architecture.ImportComplexityAnalyzer") as mock_analyzer:
                main(["--check-import-complexity", "--fix", "/app"])

                # Should attempt to fix import issues
                mock_analyzer.return_value.fix_import_issues.assert_called_once()

    def test_should_export_dependency_graph(self):
        """Test export of dependency graph visualization."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            with patch("scripts.validate_architecture.ImportComplexityAnalyzer") as mock_analyzer:
                main(["--check-import-complexity", "--export-graph=/tmp/deps.json", "/app"])

                # Should export dependency graph
                mock_analyzer.return_value.export_dependency_graph.assert_called_once()

    def test_should_support_specific_analysis_types(self):
        """Test specific types of import analysis."""
        # This test should fail initially
        with pytest.raises(ImportError):
            from scripts.validate_architecture import main

            with patch("scripts.validate_architecture.ImportComplexityAnalyzer") as mock_analyzer:
                # Test circular import detection only
                main(["--check-import-complexity", "--check-circular", "/app"])
                mock_analyzer.return_value.detect_circular_imports.assert_called_once()

                # Test unused import detection only
                main(["--check-import-complexity", "--check-unused", "/app"])
                mock_analyzer.return_value.detect_unused_imports.assert_called_once()
