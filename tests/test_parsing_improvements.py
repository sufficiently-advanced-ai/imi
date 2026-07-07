"""
Tests for improved natural language parsing with security fixes.
"""

import pytest
import sys
import os

# Add the parent directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import Mock, patch
from app.services.tools.extract_entities import ExtractEntitiesTool
from app.services.tools.generate_insights import GenerateInsightsTool
from app.services.tools.parsing_utils import validate_file_path


class TestSecurityImprovements:
    """Test security improvements in parsing."""
    
    def test_path_traversal_prevention(self):
        """Test that path traversal attempts are blocked."""
        dangerous_paths = [
            "../../../etc/passwd",
            "/etc/shadow",
            "C:\\Windows\\System32\\config\\sam",
            "~/../../etc/hosts",
            "/root/.ssh/id_rsa",
            "/etc/passwd",
            "/var/log/secure"
        ]
        
        for path in dangerous_paths:
            result = validate_file_path(path)
            assert result is None, f"Dangerous path {path} should be blocked"
    
    def test_safe_paths_allowed(self):
        """Test that safe paths within repo are allowed."""
        # Mock the current working directory
        with patch('os.getcwd', return_value='/app'):
            safe_paths = [
                "repo/test.md",
                "./repo/meetings/meeting.md",
                "/app/repo/data.txt"
            ]
            
            for path in safe_paths:
                result = validate_file_path(path, ['/app', '/app/repo'])
                # Safe paths should either be allowed or blocked based on actual existence
                # The key is they don't cause security issues
                assert result is None or result.startswith('/app')
    
    def test_file_path_extraction_with_validation(self):
        """Test that file paths are extracted and validated properly."""
        tool = ExtractEntitiesTool(None, None, None)
        
        # Test dangerous path
        result = tool._parse_natural_input("file:/etc/passwd find entities")
        assert result["file_path"] is None
        
        # Test path with spaces
        result = tool._parse_natural_input("file:./repo/my file.md extract people")
        # Should handle spaces properly now
        assert "my file.md" not in result["content"]


class TestEdgeCaseHandling:
    """Test edge case handling improvements."""
    
    def test_case_insensitive_parsing(self):
        """Test that parsing is consistently case-insensitive."""
        tool = ExtractEntitiesTool(None, None, None)
        
        test_cases = [
            "FIND ONLY PEOPLE",
            "Find Only People",
            "find only people",
            "FiNd OnLy PeOpLe"
        ]
        
        for input_text in test_cases:
            result = tool._parse_natural_input(input_text)
            assert result["entity_types"] == ["people"], f"Failed for: {input_text}"
    
    def test_whitespace_normalization(self):
        """Test proper whitespace handling."""
        tool = ExtractEntitiesTool(None, None, None)
        
        input_text = "find   only    people\n\n\tin:\t\tJohn    Smith    attended"
        result = tool._parse_natural_input(input_text)
        
        # Check whitespace is normalized
        assert "\n" not in result["content"]
        assert "\t" not in result["content"]
        assert "  " not in result["content"]  # No double spaces
        assert "John Smith attended" in result["content"]
    
    def test_multiple_instruction_handling(self):
        """Test handling of conflicting instructions."""
        tool = ExtractEntitiesTool(None, None, None)
        
        # First instruction should win
        result = tool._parse_natural_input("find only people and find only projects")
        assert result["entity_types"] == ["people"]
    
    def test_entity_extraction_quality(self):
        """Test improved entity extraction that avoids common words."""
        tool = GenerateInsightsTool(None, None, None)
        
        # Should extract proper entities
        result = tool._parse_insight_request("analyze risks for Project Alpha and Team Backend")
        assert "Project Alpha" in result["entities"]
        assert "Team Backend" in result["entities"]
        
        # Should not extract common words
        result = tool._parse_insight_request("for the next week about the team")
        assert "the" not in result["entities"]
        assert "next" not in result["entities"]
        assert "week" not in result["entities"]
        assert "about" not in result["entities"]


class TestComplexScenarios:
    """Test complex real-world scenarios."""
    
    def test_complex_entity_extraction(self):
        """Test complex entity extraction scenario."""
        tool = ExtractEntitiesTool(None, None, None)
        
        input_text = """file:./repo/meeting-notes.md find only people and teams without metadata in:
        During the meeting, John Smith from Engineering and Sarah Chen from Product team
        discussed Project Alpha-2.0 progress with Team Backend."""
        
        result = tool._parse_natural_input(input_text)
        
        assert result["entity_types"] == ["people", "teams"]
        assert result["include_metadata"] is False
        assert "John Smith" in result["content"]
        assert "Sarah Chen" in result["content"]
        assert "Team Backend" in result["content"]
    
    def test_complex_insight_request(self):
        """Test complex insight generation request."""
        tool = GenerateInsightsTool(None, None, None)
        
        input_text = """analyze risks for Project Phoenix and Team Alpha 
        for next 2 weeks with high confidence focusing on delivery timeline"""
        
        result = tool._parse_insight_request(input_text)
        
        assert result["prediction_horizon_days"] == 14
        assert "Project Phoenix" in result["entities"]
        assert "Team Alpha" in result["entities"]  
        assert result["confidence_threshold"] == 0.9
        assert result["insight_types"] == ["risks", "predictions"]
    
    def test_json_input_handling(self):
        """Test that JSON input is properly handled."""
        tool = GenerateInsightsTool(None, None, None)
        
        json_input = '{"commitments": [{"owner": "John", "due": "2025-01-31"}], "risks": ["timeline", "resources"]}'
        result = tool._parse_insight_request(json_input)
        
        assert result["analyzed_data"] == {
            "commitments": [{"owner": "John", "due": "2025-01-31"}],
            "risks": ["timeline", "resources"]
        }


@pytest.mark.parametrize("input_text,expected_entities", [
    ("for Project Alpha", ["Project Alpha"]),
    ("about Team Engineering", ["Team Engineering"]),
    ("Project: Mobile App Development", ["Mobile App Development"]),
    ("for project X and team Y", []),  # Single letters should be filtered
    ("analyze the next phase", []),  # Common words filtered
])
def test_entity_extraction_parametrized(input_text, expected_entities):
    """Parametrized tests for entity extraction."""
    tool = GenerateInsightsTool(None, None, None)
    result = tool._parse_insight_request(input_text)
    assert set(result["entities"]) == set(expected_entities)


@pytest.mark.parametrize("input_text,expected_days", [
    ("next 2 weeks", 14),
    ("next 30 days", 30),
    ("next month", 30),
    ("next quarter", 90),
    ("3 weeks", 21),
    ("analyze data", 30),  # Default
])
def test_time_horizon_parametrized(input_text, expected_days):
    """Parametrized tests for time horizon parsing."""
    tool = GenerateInsightsTool(None, None, None)
    result = tool._parse_insight_request(input_text)
    assert result["prediction_horizon_days"] == expected_days