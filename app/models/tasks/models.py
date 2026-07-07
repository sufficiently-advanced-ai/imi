"""
Models for tasks.models
"""

from ..base import BaseModel, Field

# Import shared types from central module
from ..types import TaskStatus


class TaskResponse(BaseModel):
    """Response model for task status endpoint"""

    task_id: str
    status: TaskStatus
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None




class WebhookResponse(BaseModel):
    """Response model for webhook endpoint"""

    status: str
    processed_files: list[str] = Field(default_factory=list)
    metadata_updates: int = 0
    background_tasks: list[str] = Field(default_factory=list)

