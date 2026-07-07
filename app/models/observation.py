"""Observation — the source-agnostic unit the core ingest path operates on.

Replaces MeetingState on the core path (open-core strategy, decision O3).
A meeting is one *producer* of observations (see MeetingState.to_observation()).

Serialization note: to_markdown()/from_markdown() intentionally keep the legacy
meeting frontmatter keys (meeting_id, bot_id, updated_at, start_time) so the
on-disk document format — and every existing knowledge repo — stays unchanged.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


def _yaml_escape(value: str) -> str:
    """Escape a string value for YAML if it contains special characters."""
    if not value:
        return '""'
    needs_quoting = any(c in value for c in ':{}[]&*#?|-<>=!%@`"\'\n\r\t,')
    needs_quoting = needs_quoting or value.lower() in ("true", "false", "null", "yes", "no")
    needs_quoting = needs_quoting or value.startswith(" ") or value.endswith(" ")
    if needs_quoting:
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )
        return f'"{escaped}"'
    return value


def _parse_dt(value):
    """Parse a datetime value from ISO format string or passthrough datetime objects.

    Returns None if the value cannot be parsed.
    """
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class Observation(BaseModel):
    """A finalized piece of observed content ready for signal extraction."""

    observation_id: str
    external_id: str  # stable producer-side id; keys signal + document filenames
    observed_at: datetime
    content: str  # structured markdown body signals are extracted from
    entities_mentioned: dict[str, list[str]]  # entity type -> names

    source: str = "ingest"  # producer tag: ingest | meeting | capture | ...
    raw_content: str | None = None  # original full text (e.g. transcript)
    title: str | None = None
    occurred_at: datetime | None = None
    participants: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    status: str = "completed"
    is_finalized: bool = True
    update_count: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_markdown(self) -> str:
        """Serialize to the legacy meeting-document format (see module note)."""
        frontmatter = [
            "---",
            f"meeting_id: {self.observation_id}",
            f"bot_id: {self.external_id}",
            f"updated_at: {self.observed_at.isoformat()}",
            f"update_count: {self.update_count}",
            f"is_finalized: {str(self.is_finalized).lower()}",
            f"status: {self.status}",
        ]
        if self.title:
            frontmatter.append(f"title: {_yaml_escape(self.title)}")
        if self.occurred_at:
            frontmatter.append(f"start_time: {self.occurred_at.isoformat()}")

        frontmatter.append("entities_mentioned:")
        for entity_type, names in self.entities_mentioned.items():
            if names:
                frontmatter.append(f"  {entity_type}:")
                for name in names:
                    frontmatter.append(f"    - {_yaml_escape(name)}")

        if self.participants:
            frontmatter.append("participants:")
            for p in self.participants:
                frontmatter.append(f"  - {_yaml_escape(p)}")

        if self.key_points:
            frontmatter.append("key_points:")
            for kp in self.key_points:
                frontmatter.append(f"  - {_yaml_escape(kp)}")

        frontmatter.append("---")

        output = "\n".join(frontmatter) + "\n\n" + self.content
        if self.raw_content:
            output += "\n\n## Full Transcript\n\n" + self.raw_content
        return output

    @classmethod
    def from_markdown(cls, document: str) -> "Observation":
        """Parse an observation document (legacy meeting frontmatter keys)."""
        import yaml

        parts = document.split("---", 2)
        if len(parts) < 3:
            raise ValueError("Invalid markdown format - missing frontmatter")

        frontmatter = yaml.safe_load(parts[1])
        raw = parts[2].strip()

        raw_content = None
        if "\n## Full Transcript\n" in raw:
            body_parts = raw.split("\n## Full Transcript\n", 1)
            content = body_parts[0].strip()
            raw_content = body_parts[1].strip() if len(body_parts) > 1 else None
        else:
            content = raw

        observed_at = _parse_dt(frontmatter["updated_at"])
        if observed_at is None:
            raise ValueError(
                "Observation.from_markdown: 'updated_at' is missing or unparseable "
                f"(got {frontmatter.get('updated_at')!r})"
            )

        return cls(
            observation_id=frontmatter["meeting_id"],
            external_id=frontmatter.get("bot_id", "unknown"),
            observed_at=observed_at,
            content=content,
            entities_mentioned=frontmatter.get("entities_mentioned") or {},
            raw_content=raw_content,
            title=frontmatter.get("title"),
            occurred_at=_parse_dt(frontmatter.get("start_time")),
            participants=frontmatter.get("participants") or [],
            key_points=frontmatter.get("key_points") or [],
            status=frontmatter.get("status", "completed"),
            is_finalized=frontmatter.get("is_finalized", False),
            update_count=frontmatter.get("update_count", 0),
        )
