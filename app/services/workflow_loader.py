"""
Workflow Loader Service for issue #684.

Provides functions to load and cache workflow configurations from YAML files.
"""

import logging
from pathlib import Path
from threading import RLock

import yaml

from app.models.workflow import (
    AgentConfig,
    ProcessorsConfig,
    WorkflowConfig,
)

logger = logging.getLogger(__name__)

# Default workflow configuration
DEFAULT_WORKFLOW = WorkflowConfig(
    workflow_id="default",
    name="Default Workflow",
    description="Default workflow configuration",
    processors=ProcessorsConfig(),
    agent=AgentConfig(),
)

# Shared cache for singleton pattern
_shared_cache: dict[str, WorkflowConfig] = {}
_cache_lock = RLock()


class WorkflowLoader:
    """Service for loading and caching workflow configurations."""

    # Class-level cache shared across instances for singleton behavior
    _cache = _shared_cache

    def __init__(self, workflows_dir: Path | None = None):
        """Initialize the workflow loader.

        Args:
            workflows_dir: Directory containing workflow YAML files.
                          Defaults to config/workflows.
        """
        self._workflows_dir = workflows_dir or Path("config/workflows")

    def load_from_file(self, file_path: Path) -> WorkflowConfig:
        """Load workflow configuration from a YAML file.

        Args:
            file_path: Path to the YAML configuration file.

        Returns:
            WorkflowConfig instance.

        Raises:
            ValueError: If the YAML is invalid or fails validation.
        """
        # If file doesn't exist, return default
        if not file_path.exists():
            logger.warning(f"Workflow file not found: {file_path}, using default")
            return DEFAULT_WORKFLOW

        try:
            content = file_path.read_text()
            data = yaml.safe_load(content)

            if data is None:
                raise ValueError("Empty YAML file")

            # Handle wrapped workflow config
            if "workflow" in data:
                data = data["workflow"]

            config = WorkflowConfig(**data)

            # Cache by workflow_id for consistent lookup
            with _cache_lock:
                self._cache[config.workflow_id] = config

            return config

        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML: {e}") from e
        except (TypeError, KeyError) as e:
            raise ValueError(f"Invalid configuration: {e}") from e

    def get_workflow(self, workflow_id: str) -> WorkflowConfig:
        """Get a workflow configuration by ID.

        Args:
            workflow_id: The workflow identifier.

        Returns:
            WorkflowConfig for the specified workflow, or default if not found.
        """
        # Check cache first
        with _cache_lock:
            if workflow_id in self._cache:
                return self._cache[workflow_id]

        # Try to load from file
        file_path = self._workflows_dir / f"{workflow_id}.yaml"
        if file_path.exists():
            return self.load_from_file(file_path)

        # Also try .yml extension
        file_path = self._workflows_dir / f"{workflow_id}.yml"
        if file_path.exists():
            return self.load_from_file(file_path)

        # Return default if not found
        logger.info(f"Workflow '{workflow_id}' not found, returning default")
        return DEFAULT_WORKFLOW

    def list_workflows(self) -> list[str]:
        """List available workflow IDs.

        Returns:
            List of workflow IDs found in the workflows directory.
        """
        if not self._workflows_dir.exists():
            return []

        workflow_ids = []
        for file_path in self._workflows_dir.glob("*.yaml"):
            workflow_ids.append(file_path.stem)
        for file_path in self._workflows_dir.glob("*.yml"):
            if file_path.stem not in workflow_ids:
                workflow_ids.append(file_path.stem)

        return sorted(workflow_ids)

    def clear_cache(self) -> None:
        """Clear the workflow configuration cache."""
        with _cache_lock:
            self._cache.clear()


# Singleton instance
_loader_instance: WorkflowLoader | None = None
_loader_lock = RLock()


def _get_loader() -> WorkflowLoader:
    """Get or create the singleton loader instance."""
    global _loader_instance
    with _loader_lock:
        if _loader_instance is None:
            _loader_instance = WorkflowLoader()
    return _loader_instance


def get_workflow(workflow_id: str | None) -> WorkflowConfig:
    """Get a workflow configuration by ID.

    Convenience function using the singleton loader.

    Args:
        workflow_id: The workflow identifier. If None, returns default workflow.

    Returns:
        WorkflowConfig for the specified workflow, or default if not found.
    """
    if workflow_id is None:
        return DEFAULT_WORKFLOW
    return _get_loader().get_workflow(workflow_id)


def list_workflows() -> list[str]:
    """List available workflow IDs.

    Convenience function using the singleton loader.

    Returns:
        List of workflow IDs found in the workflows directory.
    """
    return _get_loader().list_workflows()
