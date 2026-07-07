"""
Github models package.
"""

from .events import GitHubPushEvent
from .webhook import GitHubCommit, GitHubRepository

__all__ = [
    "GitHubRepository",
    "GitHubCommit",
    "GitHubPushEvent",
]
