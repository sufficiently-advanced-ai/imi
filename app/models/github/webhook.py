"""
Models for github.webhook
"""


from ..base import BaseModel, Field, HttpUrl


class GitHubRepository(BaseModel):
    """GitHub repository information from webhook payload"""

    id: int
    name: str
    full_name: str
    private: bool
    html_url: HttpUrl
    description: str | None
    fork: bool
    created_at: int
    updated_at: str
    pushed_at: int
    default_branch: str




class GitHubCommit(BaseModel):
    """GitHub commit information from webhook payload"""

    id: str
    message: str
    timestamp: str  # GitHub sends ISO-8601 format string
    author: dict[str, str]
    committer: dict[str, str]
    tree_id: str
    url: HttpUrl
    distinct: bool
    added: list[str] = Field(default_factory=list)  # Keep default_factory
    removed: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "properties": {
                "added": {"alias": ["added", "added_files"]},
                "modified": {"alias": ["modified", "modified_files"]},
                "removed": {"alias": ["removed", "removed_files"]},
            }
        }

