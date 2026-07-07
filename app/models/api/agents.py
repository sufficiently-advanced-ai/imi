"""
Agent, workflow, and objectives models.

This module contains models for agent objectives, tool execution,
workflow execution, and pattern analysis.
"""

from datetime import UTC
from typing import TYPE_CHECKING, Any, Optional

from ..base import Annotated, BaseModel, Field, datetime
from ..types import (
    ObjectiveStatus,
)

if TYPE_CHECKING:
    from ..agent.objectives import ObjectiveBoundaries, ObjectiveKPI


class ObjectiveExecution(BaseModel):
    """Track execution of an objective with detailed metrics"""

    execution_id: str = Field(..., description="Unique identifier for this execution")
    objective_id: str = Field(..., description="ID of the objective being executed")
    start_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    end_time: datetime | None = Field(default=None)
    status: ObjectiveStatus = Field(default=ObjectiveStatus.PENDING)
    tool_executions: list[str] = Field(
        default_factory=list, description="IDs of tool executions performed"
    )
    kpi_measurements: list[dict[str, Any]] = Field(
        default_factory=list, description="Historical KPI measurements"
    )
    error_log: list[str] = Field(
        default_factory=list, description="Errors encountered during execution"
    )
    performance_metrics: dict[str, float | int] = Field(
        default_factory=dict, description="Performance data"
    )
    final_score: float | None = Field(
        default=None, description="Final weighted achievement score"
    )




class ObjectiveTemplate(BaseModel):
    """Reusable template for creating objectives"""

    template_id: str = Field(..., description="Unique identifier for the template")
    name: str = Field(..., description="Template name")
    description: str = Field(..., description="Template description")
    default_kpis: list["ObjectiveKPI"] = Field(
        ..., description="Default KPIs for this template"
    )
    default_boundaries: Optional["ObjectiveBoundaries"] = None
    default_tool_chain: list[dict[str, Any]] = Field(default_factory=list)
    category: str = Field(default="general", description="Template category")
    tags: list[str] = Field(
        default_factory=list, description="Template tags for organization"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))




class ObjectivePerformanceReport(BaseModel):
    """Performance report for objective execution analysis"""

    objective_id: str
    execution_count: int
    success_rate: float
    average_completion_time_seconds: float
    average_final_score: float
    kpi_achievement_rates: dict[str, float]
    common_failure_reasons: list[str]
    trend_analysis: dict[str, Any]
    recommendations: list[str]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))




class ToolExecution(BaseModel):
    """Model for tracking individual tool execution"""

    id: str = Field(..., description="Unique identifier for this tool execution")
    tool: str = Field(..., description="Name of the tool being executed")
    status: str = Field(default="pending", description="Execution status")
    start_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    end_time: datetime | None = Field(default=None)
    execution_time: float | None = Field(
        default=None, description="Execution time in seconds"
    )
    result: dict[str, Any] | None = Field(
        default=None, description="Tool execution result"
    )
    error: str | None = Field(default=None, description="Error message if failed")
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    context: dict[str, Any] = Field(
        default_factory=dict, description="Execution context"
    )




class WorkflowExecution(BaseModel):
    """Model for tracking workflow execution"""

    id: str = Field(..., description="Unique identifier for this workflow execution")
    workflow: str = Field(..., description="Name of the workflow being executed")
    status: str = Field(default="pending", description="Workflow execution status")
    start_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    end_time: datetime | None = Field(default=None)
    total_execution_time: float | None = Field(
        default=None, description="Total execution time in seconds"
    )
    total_steps: int = Field(default=0, description="Total number of steps in workflow")
    completed_steps: int = Field(default=0, description="Number of completed steps")
    tool_executions: list[str] = Field(
        default_factory=list, description="Tool execution IDs"
    )
    errors: list[str] = Field(default_factory=list, description="Errors encountered")
    result: dict[str, Any] | None = Field(
        default=None, description="Final workflow result"
    )




class ChatMessageRequest(BaseModel):
    """Request model for sending chat messages."""

    message: Annotated[
        str, Field(min_length=1, max_length=500, description="Chat message content")
    ]
    recipient: str = Field(default="everyone", description="Message recipient")




class PatternAnalysis(BaseModel):
    """Model for pattern detection analysis results"""

    patterns_detected: list[dict[str, Any]]
    domain_id: str
    analysis_timestamp: datetime
