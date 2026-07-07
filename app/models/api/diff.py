"""
Models for api.diff
"""

from ..base import BaseModel, Field, datetime


class DiffExplanationCache(BaseModel):
    """Model for caching diff explanations"""

    file_path: str
    current_commit: str
    previous_commit: str
    explanation: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

