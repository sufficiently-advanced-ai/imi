"""
Models for github.events
"""

from typing import Any

from ..base import BaseModel, HttpUrl
from .webhook import GitHubCommit, GitHubRepository


class GitHubPushEvent(BaseModel):
    """GitHub push event webhook payload"""

    ref: str
    before: str
    after: str
    repository: GitHubRepository
    pusher: dict[str, str]
    sender: dict[str, Any]
    commits: list[GitHubCommit]
    head_commit: GitHubCommit
    created: bool = False
    deleted: bool = False
    forced: bool = False
    base_ref: str | None = None
    compare: HttpUrl

    class Config:
        extra = "allow"  # Allow extra fields in payload

