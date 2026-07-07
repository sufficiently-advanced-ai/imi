"""
Workflow configuration models for issue #684.

Provides Pydantic models for workflow configuration schema including:
- ProcessorConfig: Configuration for individual processors
- ProcessorsConfig: Collection of enabled processors and their configs
- AgentConfig: Agent settings including model and skills
- WorkflowConfig: Complete workflow configuration
"""


from pydantic import BaseModel, Field


class ProcessorConfig(BaseModel):
    """Configuration for an individual processor."""

    confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for processor decisions (0.0-1.0)"
    )
    system_prompt_path: str | None = Field(
        default=None,
        description="Path to custom system prompt file"
    )


class ProcessorsConfig(BaseModel):
    """Configuration for workflow processors."""

    enabled: list[str] = Field(
        default_factory=lambda: ["decision_detector", "action_item_detector", "key_point_extractor"],
        description="List of enabled processor names"
    )
    config: dict[str, ProcessorConfig] = Field(
        default_factory=dict,
        description="Per-processor configuration overrides"
    )


class AgentConfig(BaseModel):
    """Configuration for the workflow agent."""

    enabled: bool = Field(
        default=True,
        description="Whether the agent is enabled"
    )
    model: str = Field(
        default="claude-sonnet-4-5-20250929",
        description="Model identifier to use for agent"
    )
    system_prompt_path: str | None = Field(
        default=None,
        description="Path to custom agent system prompt"
    )
    skills: list[str] = Field(
        default_factory=list,
        description="List of enabled skills for the agent"
    )


class WorkflowConfig(BaseModel):
    """Complete workflow configuration."""

    workflow_id: str = Field(
        ...,
        description="Unique identifier for the workflow"
    )
    name: str = Field(
        ...,
        description="Human-readable name for the workflow"
    )
    description: str = Field(
        default="",
        description="Description of the workflow purpose"
    )
    processors: ProcessorsConfig = Field(
        default_factory=ProcessorsConfig,
        description="Processor configuration"
    )
    agent: AgentConfig = Field(
        default_factory=AgentConfig,
        description="Agent configuration"
    )
