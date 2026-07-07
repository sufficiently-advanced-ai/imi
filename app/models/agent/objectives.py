"""
Models for agent.objectives
"""

from typing import Any

from ..api.core import ObjectiveStatus
from ..base import BaseModel, Field, datetime


class ObjectiveKPI(BaseModel):
    """Key Performance Indicator for agent objectives"""

    name: str = Field(..., description="Name of the KPI metric")
    target_value: float | int | str = Field(
        ..., description="Target value to achieve"
    )
    operator: str = Field(
        ..., description="Comparison operator: '>', '<', '>=', '<=', '=='"
    )
    weight: float = Field(default=1.0, description="Relative importance of this KPI")
    current_value: float | int | str | None = Field(
        default=None, description="Current measured value"
    )
    achieved: bool = Field(
        default=False, description="Whether this KPI has been achieved"
    )




class ObjectiveBoundaries(BaseModel):
    """Safety and operational boundaries for objective execution"""

    confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum confidence required for actions",
    )
    max_retries: int = Field(
        default=3, ge=0, description="Maximum retry attempts for failed operations"
    )
    timeout_seconds: int = Field(
        default=300, gt=0, description="Maximum execution time in seconds"
    )
    require_human_review: list[str] = Field(
        default_factory=list, description="Scenarios requiring human approval"
    )
    max_tool_executions: int = Field(
        default=50, gt=0, description="Maximum number of tool executions per objective"
    )
    quality_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0, description="Minimum quality score threshold"
    )




class AgentObjective(BaseModel):
    """High-level objective that agents work toward achieving"""

    id: str = Field(..., description="Unique identifier for the objective")
    name: str = Field(..., description="Human-readable name for the objective")
    description: str = Field(
        ..., description="Detailed description of what this objective achieves"
    )
    kpis: list[ObjectiveKPI] = Field(
        ..., description="Key performance indicators to track success"
    )
    boundaries: ObjectiveBoundaries = Field(
        default_factory=ObjectiveBoundaries,
        description="Safety and operational constraints",
    )
    tool_chain: list[dict[str, Any]] = Field(
        default_factory=list, description="Preferred tool execution sequence"
    )
    status: ObjectiveStatus = Field(
        default=ObjectiveStatus.PENDING, description="Current execution status"
    )
    priority: int = Field(
        default=1, ge=1, le=5, description="Priority level (1=highest, 5=lowest)"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = Field(
        default=None, description="When execution began"
    )
    completed_at: datetime | None = Field(
        default=None, description="When execution finished"
    )
    execution_context: dict[str, Any] = Field(
        default_factory=dict, description="Context data for execution"
    )

    def calculate_progress(self) -> float:
        """Calculate overall progress as percentage of achieved KPIs"""
        if not self.kpis:
            return 0.0
        achieved_count = sum(1 for kpi in self.kpis if kpi.achieved)
        return (achieved_count / len(self.kpis)) * 100.0

    def calculate_weighted_score(self) -> float:
        """Calculate weighted score based on KPI achievement and weights"""
        if not self.kpis:
            return 0.0
        total_weight = sum(kpi.weight for kpi in self.kpis)
        achieved_weight = sum(kpi.weight for kpi in self.kpis if kpi.achieved)
        return (achieved_weight / total_weight) * 100.0 if total_weight > 0 else 0.0

