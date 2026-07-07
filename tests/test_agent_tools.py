"""
Tests for Agent Tool Arsenal functionality.
"""

import pytest
import sys
import os

# Add the parent directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import Mock

from app.services.agent_tools import ToolResult






@pytest.mark.asyncio
async def test_simplified_interfaces():
    """Test the simplified natural language interfaces for all three tools."""
    from app.services.tools.extract_entities import ExtractEntitiesTool
    from app.services.tools.generate_insights import GenerateInsightsTool

    mock_client = Mock()
    mock_git = Mock()
    mock_cache = Mock()

    # Test extract_entities simplification
    entities_tool = ExtractEntitiesTool(mock_client, mock_git, mock_cache)
    assert len(entities_tool.input_schema["properties"]) == 1
    assert "content" in entities_tool.input_schema["properties"]
    assert "natural language instructions" in entities_tool.input_schema["properties"]["content"]["description"]

    # Test generate_insights simplification
    insights_tool = GenerateInsightsTool(mock_client, mock_git, mock_cache)
    assert len(insights_tool.input_schema["properties"]) == 1
    assert "data" in insights_tool.input_schema["properties"]
    assert "natural language request" in insights_tool.input_schema["properties"]["data"]["description"]


@pytest.mark.asyncio 
async def test_natural_language_parsing():
    """Test natural language parsing functions in simplified tools."""
    from app.services.tools.extract_entities import ExtractEntitiesTool
    from app.services.tools.generate_insights import GenerateInsightsTool

    mock_client = Mock()
    mock_git = Mock()
    mock_cache = Mock()

    # Test entity tool parsing
    entities_tool = ExtractEntitiesTool(mock_client, mock_git, mock_cache)
    parsed = entities_tool._parse_natural_input("find only people in: John Smith works on project Alpha")
    assert parsed["entity_types"] == ["people"]
    assert "John Smith works on project Alpha" in parsed["content"]

    # Test insights tool parsing
    insights_tool = GenerateInsightsTool(mock_client, mock_git, mock_cache)
    parsed = insights_tool._parse_insight_request("analyze risks for next 2 weeks")
    assert parsed["insight_types"] == ["risks", "predictions"]
    assert parsed["prediction_horizon_days"] == 14





def test_tool_input_output_schemas():
    """Test that tools have proper input/output schemas."""
    from app.services.tools.extract_entities import ExtractEntitiesTool

    tool = ExtractEntitiesTool(None, None, None)

    # Check input schema
    input_schema = tool.input_schema
    assert input_schema["type"] == "object"
    assert "content" in input_schema["properties"]
    assert "content" in input_schema["required"]

    # Check output schema
    output_schema = tool.output_schema
    assert output_schema["type"] == "object"
    assert "entities" in output_schema["properties"]


def test_tool_result_structure():
    """Test the ToolResult data structure."""
    result = ToolResult(
        success=True,
        data={"test": "data"},
        execution_time_ms=100,
        metadata={"tool": "test"}
    )
    
    assert result.success is True
    assert result.data == {"test": "data"}
    assert result.execution_time_ms == 100
    assert result.metadata == {"tool": "test"}




    # Second tool should have received context from first tool






def test_enhanced_tool_execution_fields():
    """Test that ToolExecution has been enhanced with decision logging fields."""
    from app.services.agent_tools import ToolExecution
    from datetime import datetime
    
    execution = ToolExecution(
        tool_name="test_tool",
        execution_id="test-123",
        start_time=datetime.now(),
        reasoning="Test reasoning",
        confidence=0.8,
        context={"test": "context"},
        agent_name="test_agent",
        decision_type="execute_test"
    )
    
    # Verify all enhanced fields are present
    assert execution.reasoning == "Test reasoning"
    assert execution.confidence == 0.8
    assert execution.context == {"test": "context"}
    assert execution.agent_name == "test_agent"
    assert execution.decision_type == "execute_test"


if __name__ == "__main__":
    pytest.main([__file__])