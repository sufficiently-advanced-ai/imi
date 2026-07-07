"""
System utilities and health check models.

This module contains models for system health, reinitialization,
pagination, and mode settings.
"""

from typing import Any

from ..base import BaseModel, ConfigDict, Field, field_validator


class HealthResponse(BaseModel):
    status: str
    git_status: str




class ReinitializeResponse(BaseModel):
    """Response model for repository reinitialization endpoint"""

    status: str
    message: str
    repository: str




class PaginationInfo(BaseModel):
    """Pagination information for responses"""
    page: int = Field(default=1, ge=1, description="Current page number")
    page_size: int = Field(default=50, ge=1, le=200, description="Items per page")
    total: int = Field(default=0, ge=0, description="Total number of items")
    total_pages: int = Field(default=0, ge=0, description="Total number of pages")




class ModeSettings(BaseModel):
    """Mode-specific settings configuration"""

    model_config = ConfigDict(extra="allow")

    intelligence: dict[str, Any] = Field(
        default_factory=lambda: {
            "summary_window": 300,
            "show_speakers": True,
            "highlight_decisions": False,
        }
    )
    state: dict[str, Any] = Field(
        default_factory=lambda: {
            "show_entities": True,
            "group_by": "type",
            "show_relationships": False,
        }
    )
    tasks: dict[str, Any] = Field(
        default_factory=lambda: {
            "show_completed": False,
            "group_by": "person",
            "priority_threshold": "medium",
        }
    )
    agenda: dict[str, Any] = Field(
        default_factory=lambda: {"show_timing": True, "highlight_current": True}
    )

    @field_validator("tasks")
    def validate_task_settings(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Validate task-specific settings"""
        if (gb := v.get("group_by")) and gb not in ("person", "project"):
            raise ValueError("invalid tasks.group_by; expected 'person' or 'project'")
        return v
