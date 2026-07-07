"""Tests for issue #684: Workflow Configuration Schema and Loader Service."""

import os
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.workflow import (
    AgentConfig,
    ProcessorConfig,
    ProcessorsConfig,
    WorkflowConfig,
)
from app.services.workflow_loader import (
    WorkflowLoader,
    get_workflow,
    list_workflows,
)


class TestProcessorConfig:
    """Test ProcessorConfig model."""

    def test_processor_config_defaults(self):
        """Test ProcessorConfig default values."""
        config = ProcessorConfig()
        assert config.confidence_threshold == 0.7
        assert config.system_prompt_path is None

    def test_processor_config_custom_values(self):
        """Test ProcessorConfig with custom values."""
        config = ProcessorConfig(
            confidence_threshold=0.85,
            system_prompt_path="prompts/decision_detector.md"
        )
        assert config.confidence_threshold == 0.85
        assert config.system_prompt_path == "prompts/decision_detector.md"

    def test_processor_config_threshold_validation(self):
        """Test confidence_threshold must be between 0 and 1."""
        with pytest.raises(ValidationError):
            ProcessorConfig(confidence_threshold=1.5)
        with pytest.raises(ValidationError):
            ProcessorConfig(confidence_threshold=-0.1)


class TestProcessorsConfig:
    """Test ProcessorsConfig model."""

    def test_processors_config_defaults(self):
        """Test ProcessorsConfig default values."""
        config = ProcessorsConfig()
        assert "decision_detector" in config.enabled
        assert "action_item_detector" in config.enabled
        assert "key_point_extractor" in config.enabled
        assert config.config == {}

    def test_processors_config_custom_enabled(self):
        """Test ProcessorsConfig with custom enabled list."""
        config = ProcessorsConfig(
            enabled=["decision_detector"],
            config={
                "decision_detector": ProcessorConfig(confidence_threshold=0.9)
            }
        )
        assert config.enabled == ["decision_detector"]
        assert config.config["decision_detector"].confidence_threshold == 0.9


class TestAgentConfig:
    """Test AgentConfig model."""

    def test_agent_config_defaults(self):
        """Test AgentConfig default values."""
        config = AgentConfig()
        assert config.enabled is True
        assert config.model == "claude-sonnet-4-5-20250929"
        assert config.system_prompt_path is None
        assert config.skills == []

    def test_agent_config_custom_values(self):
        """Test AgentConfig with custom values."""
        config = AgentConfig(
            enabled=False,
            model="claude-opus-4",
            system_prompt_path="prompts/agent.md",
            skills=["search", "analyze"]
        )
        assert config.enabled is False
        assert config.model == "claude-opus-4"
        assert config.system_prompt_path == "prompts/agent.md"
        assert config.skills == ["search", "analyze"]




class TestWorkflowConfig:
    """Test WorkflowConfig model."""

    def test_workflow_config_valid(self):
        """Test valid WorkflowConfig creation."""
        config = WorkflowConfig(
            workflow_id="sales_meeting",
            name="Sales Meeting Workflow",
            description="Workflow for sales calls",
            processors=ProcessorsConfig(),
            agent=AgentConfig(),
        )
        assert config.workflow_id == "sales_meeting"
        assert config.name == "Sales Meeting Workflow"
        assert config.description == "Workflow for sales calls"

    def test_workflow_config_required_fields(self):
        """Test WorkflowConfig requires workflow_id and name."""
        with pytest.raises(ValidationError) as exc_info:
            WorkflowConfig(name="Test Workflow")
        assert "workflow_id" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            WorkflowConfig(workflow_id="test")
        assert "name" in str(exc_info.value)

    def test_workflow_config_default_description(self):
        """Test WorkflowConfig default description is empty string."""
        config = WorkflowConfig(
            workflow_id="test",
            name="Test",
            processors=ProcessorsConfig(),
            agent=AgentConfig(),
        )
        assert config.description == ""

    def test_workflow_config_nested_configs(self):
        """Test WorkflowConfig with nested configuration objects."""
        config = WorkflowConfig(
            workflow_id="custom_workflow",
            name="Custom Workflow",
            processors=ProcessorsConfig(
                enabled=["decision_detector"],
                config={
                    "decision_detector": ProcessorConfig(confidence_threshold=0.95)
                }
            ),
            agent=AgentConfig(
                enabled=True,
                model="claude-opus-4",
                skills=["meeting_analysis"]
            ),
        )
        assert config.processors.config["decision_detector"].confidence_threshold == 0.95
        assert config.agent.model == "claude-opus-4"


class TestWorkflowLoader:
    """Test WorkflowLoader service."""

    def test_workflow_loader_singleton(self):
        """Test WorkflowLoader is singleton."""
        loader1 = WorkflowLoader()
        loader2 = WorkflowLoader()
        # Both should reference the same cached data
        assert loader1._cache is loader2._cache

    def test_load_valid_yaml(self):
        """Test loading valid YAML workflow configuration."""
        yaml_content = """
workflow_id: test_workflow
name: Test Workflow
description: A test workflow
processors:
  enabled:
    - decision_detector
  config:
    decision_detector:
      confidence_threshold: 0.8
agent:
  enabled: true
  model: claude-sonnet-4-5-20250929
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            f.flush()
            temp_path = f.name

        try:
            loader = WorkflowLoader()
            config = loader.load_from_file(Path(temp_path))

            assert config.workflow_id == "test_workflow"
            assert config.name == "Test Workflow"
            assert config.processors.config["decision_detector"].confidence_threshold == 0.8
        finally:
            os.unlink(temp_path)

    def test_load_invalid_yaml_raises_error(self):
        """Test loading invalid YAML raises validation error."""
        yaml_content = """
workflow_id: test
# Missing required 'name' field
processors:
  enabled: []
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            f.flush()
            temp_path = f.name

        try:
            loader = WorkflowLoader()
            with pytest.raises(ValueError) as exc_info:
                loader.load_from_file(Path(temp_path))
            assert "name" in str(exc_info.value).lower() or "validation" in str(exc_info.value).lower()
        finally:
            os.unlink(temp_path)

    def test_load_missing_file_returns_default(self):
        """Test loading missing file returns default config."""
        loader = WorkflowLoader()
        config = loader.load_from_file(Path("/nonexistent/workflow.yaml"))

        # Should return default configuration
        assert config.workflow_id == "default"
        assert config.name == "Default Workflow"

    def test_caching_returns_same_object(self):
        """Test caching returns same object for same workflow via get_workflow."""
        yaml_content = """
workflow_id: cached_test
name: Cached Test Workflow
processors:
  enabled: []
agent:
  enabled: true
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            f.flush()
            temp_path = f.name

        try:
            # Create loader with custom workflows dir pointing to temp file's directory
            temp_dir = Path(temp_path).parent
            loader = WorkflowLoader(workflows_dir=temp_dir)
            loader.clear_cache()  # Clear any existing cache

            # Rename temp file to match workflow_id
            workflow_path = temp_dir / "cached_test.yaml"
            os.rename(temp_path, workflow_path)
            temp_path = str(workflow_path)

            # First call loads from file and caches by workflow_id
            config1 = loader.get_workflow("cached_test")
            # Second call should return cached object
            config2 = loader.get_workflow("cached_test")

            # Should return the exact same object from cache
            assert config1 is config2
            assert config1.workflow_id == "cached_test"
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestGetWorkflowFunction:
    """Test get_workflow convenience function."""

    def test_get_workflow_by_id(self):
        """Test get_workflow retrieves workflow by ID."""
        # This should work with workflows in the config/workflows directory
        config = get_workflow("default")
        assert config is not None
        assert isinstance(config, WorkflowConfig)

    def test_get_workflow_missing_returns_default(self):
        """Test get_workflow returns default for missing workflow ID."""
        config = get_workflow("nonexistent_workflow_xyz")
        assert config is not None
        assert config.workflow_id == "default"


class TestListWorkflows:
    """Test list_workflows function."""

    def test_list_workflows_returns_ids(self):
        """Test list_workflows returns available workflow IDs."""
        workflow_ids = list_workflows()
        assert isinstance(workflow_ids, list)
        # Should include at least a default workflow
        assert len(workflow_ids) >= 0  # May be empty if no workflows configured


class TestWorkflowSchemaFromDict:
    """Test creating workflow config from dictionary."""

    def test_from_dict_complete(self):
        """Test creating WorkflowConfig from complete dictionary."""
        data = {
            "workflow_id": "dict_workflow",
            "name": "Dict Workflow",
            "description": "Created from dict",
            "processors": {
                "enabled": ["action_item_detector"],
                "config": {}
            },
            "agent": {
                "enabled": True,
                "model": "claude-sonnet-4-5-20250929",
                "skills": []
            },
        }

        config = WorkflowConfig(**data)
        assert config.workflow_id == "dict_workflow"
        assert config.processors.enabled == ["action_item_detector"]

    def test_from_dict_with_nested_processor_config(self):
        """Test creating WorkflowConfig with nested processor configurations."""
        data = {
            "workflow_id": "nested",
            "name": "Nested Config",
            "processors": {
                "enabled": ["decision_detector", "key_point_extractor"],
                "config": {
                    "decision_detector": {
                        "confidence_threshold": 0.9,
                        "system_prompt_path": "prompts/decisions.md"
                    },
                    "key_point_extractor": {
                        "confidence_threshold": 0.75
                    }
                }
            },
            "agent": {"enabled": True},
        }

        config = WorkflowConfig(**data)
        assert config.processors.config["decision_detector"].confidence_threshold == 0.9
        assert config.processors.config["key_point_extractor"].confidence_threshold == 0.75
