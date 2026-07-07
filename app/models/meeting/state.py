"""
Models for meeting.state
"""

from typing import Any

from ..base import BaseModel, Field, datetime


class MeetingState(BaseModel):
    """Complete state of a meeting at a point in time"""

    meeting_id: str
    bot_id: str
    updated_at: datetime
    entities_mentioned: dict[str, list[str]]  # type -> names
    body: str  # Markdown content
    update_count: int = 0
    is_finalized: bool = False  # True when meeting has ended
    speaker_mappings: dict[str, dict[str, Any]] = Field(default_factory=dict)  # speaker -> entity mapping info

    # Additional fields for entity enrichment
    transcript: str | None = None
    start_time: datetime | None = None
    participants: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    title: str | None = None
    status: str = "in_progress"
    speakers: list[str] | None = None
    duration: float | None = None

    def _yaml_escape(self, value: str) -> str:
        """Escape a string value for YAML if it contains special characters."""
        if not value:
            return '""'
        # Check if value needs quoting (contains special chars or looks like a number/bool)
        needs_quoting = any(c in value for c in ':{}[]&*#?|-<>=!%@`"\'\n\r\t,')
        needs_quoting = needs_quoting or value.lower() in ('true', 'false', 'null', 'yes', 'no')
        needs_quoting = needs_quoting or value.startswith(' ') or value.endswith(' ')
        if needs_quoting:
            # Use double quotes and escape internal quotes and control characters
            escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')
            return f'"{escaped}"'
        return value

    def to_markdown(self) -> str:
        """Convert to markdown with frontmatter"""
        frontmatter = [
            "---",
            f"meeting_id: {self.meeting_id}",
            f"bot_id: {self.bot_id}",
            f"updated_at: {self.updated_at.isoformat()}",
            f"update_count: {self.update_count}",
            f"is_finalized: {str(self.is_finalized).lower()}",
            f"status: {self.status}",
        ]

        # Add title if present
        if self.title:
            frontmatter.append(f"title: {self._yaml_escape(self.title)}")

        # Add start_time if present
        if self.start_time:
            frontmatter.append(f"start_time: {self.start_time.isoformat()}")

        # Add duration if present
        if self.duration is not None:
            frontmatter.append(f"duration: {self.duration}")

        # Add entities_mentioned (proper YAML nested list format)
        frontmatter.append("entities_mentioned:")
        for entity_type, names in self.entities_mentioned.items():
            if names:
                frontmatter.append(f"  {entity_type}:")
                for name in names:
                    frontmatter.append(f"    - {self._yaml_escape(name)}")

        # Add participants if present
        if self.participants:
            frontmatter.append("participants:")
            for p in self.participants:
                frontmatter.append(f"  - {self._yaml_escape(p)}")

        # Add key_points if present
        if self.key_points:
            frontmatter.append("key_points:")
            for kp in self.key_points:
                frontmatter.append(f"  - {self._yaml_escape(kp)}")

        # Add speakers if present
        if self.speakers:
            frontmatter.append("speakers:")
            for s in self.speakers:
                frontmatter.append(f"  - {self._yaml_escape(s)}")

        # Add speaker mappings if present
        if self.speaker_mappings:
            frontmatter.append("speaker_mappings:")
            for speaker, mapping in self.speaker_mappings.items():
                frontmatter.append(f"  {self._yaml_escape(speaker)}:")
                for key, value in mapping.items():
                    if isinstance(value, str):
                        value = self._yaml_escape(value)
                    frontmatter.append(f"    {self._yaml_escape(key)}: {value}")

        frontmatter.append("---")

        # Build output with body
        output = "\n".join(frontmatter) + "\n\n" + self.body

        # Add full transcript section if present
        if self.transcript:
            output += "\n\n## Full Transcript\n\n" + self.transcript

        return output

    @classmethod
    def _parse_datetime(cls, value) -> datetime | None:
        """Parse a datetime value from frontmatter."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except (ValueError, TypeError):
                return None
        return None

    @classmethod
    def from_markdown(cls, content: str) -> "MeetingState":
        """Parse from markdown with frontmatter"""
        import yaml

        # Split frontmatter and body
        parts = content.split("---", 2)
        if len(parts) < 3:
            raise ValueError("Invalid markdown format - missing frontmatter")

        frontmatter = yaml.safe_load(parts[1])
        if not isinstance(frontmatter, dict):
            raise ValueError("Invalid markdown format - frontmatter is not a mapping")
        raw_content = parts[2].strip()

        # Split body and transcript section if present
        transcript = None
        if "\n## Full Transcript\n" in raw_content:
            body_parts = raw_content.split("\n## Full Transcript\n", 1)
            body = body_parts[0].strip()
            transcript = body_parts[1].strip() if len(body_parts) > 1 else None
        else:
            body = raw_content

        # Handle updated_at which might be a string or datetime
        updated_at = cls._parse_datetime(frontmatter.get("updated_at"))
        if updated_at is None:
            raise ValueError(
                "Invalid markdown format - missing or unparseable 'updated_at': "
                f"{frontmatter.get('updated_at')!r}"
            )

        # Handle entities_mentioned which might be None from YAML
        entities = frontmatter.get("entities_mentioned", {})
        if entities is None:
            entities = {}

        # Handle participants list (might be None)
        participants = frontmatter.get("participants", [])
        if participants is None:
            participants = []

        # Handle key_points list (might be None)
        key_points = frontmatter.get("key_points", [])
        if key_points is None:
            key_points = []

        # Handle speakers list (might be None)
        speakers = frontmatter.get("speakers")
        if speakers is not None and not isinstance(speakers, list):
            speakers = None

        return cls(
            meeting_id=frontmatter["meeting_id"],
            bot_id=frontmatter.get(
                "bot_id", "unknown"
            ),  # Provide default for backward compatibility
            updated_at=updated_at,
            entities_mentioned=entities,
            body=body,
            update_count=frontmatter.get("update_count", 0),
            is_finalized=frontmatter.get("is_finalized", False),
            speaker_mappings=frontmatter.get("speaker_mappings", {}),
            # Additional fields now persisted
            title=frontmatter.get("title"),
            start_time=cls._parse_datetime(frontmatter.get("start_time")),
            duration=frontmatter.get("duration"),
            status=frontmatter.get("status", "in_progress"),
            participants=participants,
            key_points=key_points,
            speakers=speakers,
            transcript=transcript,
        )

    def to_observation(self):
        """Adapt this meeting state into the core's source-agnostic Observation.

        Meeting files are one *producer* of observations; the core
        ingest/signal path no longer accepts MeetingState directly.
        """
        from app.models.observation import Observation

        return Observation(
            observation_id=self.meeting_id,
            external_id=self.bot_id,
            source="meeting",
            observed_at=self.updated_at,
            content=self.body,
            raw_content=self.transcript,
            title=self.title,
            occurred_at=self.start_time,
            participants=self.participants or [],
            entities_mentioned=self.entities_mentioned or {},
            key_points=self.key_points or [],
            status=self.status,
            is_finalized=self.is_finalized,
            update_count=self.update_count,
        )
