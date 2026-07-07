"""
Service-specific models for display generation
"""

from typing import Any

from pydantic import BaseModel, Field


class DisplaySection(BaseModel):
    """Individual section of display content"""

    type: str  # header, text, bullets, highlight, person
    title: str = ""
    content: str = ""
    items: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DisplayContent(BaseModel):
    """Complete display content for bot video"""

    mode: str
    sections: list[DisplaySection]
    viewport: dict[str, int] = Field(
        default_factory=lambda: {"width": 1280, "height": 720}
    )
    refresh_interval: int = 5
    timestamp: str | None = None
