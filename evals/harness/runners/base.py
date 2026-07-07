"""Runner protocol shared by all eval tasks.

A runner takes one fixture and produces a TaskResult. In live mode it calls
the production prompt path via a ClaudeClient; in offline mode it scores the
fixture's recorded replay blob instead (skipping when none exists or the
prompt has changed since recording).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class TaskResult:
    fixture_id: str
    skipped: bool = False
    skip_reason: str = ""
    raw_output: str = ""
    scores: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)

    @classmethod
    def skip(cls, fixture_id: str, reason: str) -> "TaskResult":
        return cls(fixture_id=fixture_id, skipped=True, skip_reason=reason)


class TaskRunner(Protocol):
    name: str
    prompt_name: str  # bare prompt file name under app/prompts/ ("" if N/A)

    async def run(self, fixture: dict, client: Any, offline: bool) -> TaskResult: ...


def response_text(response: Any) -> str | None:
    """Extract text from a Claude API response (Message object or dict)."""
    if hasattr(response, "content") and response.content:
        first = response.content[0]
        if hasattr(first, "text"):
            return first.text
    if isinstance(response, dict) and response.get("content"):
        first = response["content"][0]
        if isinstance(first, dict):
            return first.get("text")
    return None


def replay_or_none(
    fixture: dict, task: str, current_sha: str | None
) -> tuple[str | None, str]:
    """Return (replay_text, reason). reason is set when replay is unusable."""
    from ..loader import get_replay

    blob = get_replay(fixture, task)
    if blob is None:
        return None, "no recorded replay for this task"
    recorded_sha = blob.get("prompt_sha256")
    if recorded_sha and current_sha and recorded_sha != current_sha:
        return None, "prompt changed since replay was recorded"
    recorded = blob.get("llm_response")
    if not isinstance(recorded, str):
        return None, "replay blob missing llm_response text"
    return recorded, ""
