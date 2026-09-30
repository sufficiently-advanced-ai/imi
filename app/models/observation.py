"""Observation — the source-agnostic unit the core ingest path operates on.

Replaces MeetingState on the core path (open-core strategy, decision O3).
A meeting is one *producer* of observations (see MeetingState.to_observation()).

Serialization note: to_markdown()/from_markdown() intentionally keep the legacy
meeting frontmatter keys (meeting_id, bot_id, updated_at, start_time) so the
on-disk document format — and every existing knowledge repo — stays unchanged.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.lane import validate_lane
from app.utils.event_time import TIME_SOURCE_UNRECORDED, validate_time_source


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


def _header_lines(title: str, participants: list[str]) -> list[str]:
    parts = [f"# {title}", ""]
    if participants:
        parts.append("## Participants")
        parts.append("")
        for p in participants:
            parts.append(f"- {p}")
        parts.append("")
    return parts


def build_observation_header(title: str, participants: list[str]) -> str:
    """Title + participants block that opens every observation body."""
    return "\n".join(_header_lines(title, participants))


def build_observation_body(title: str, content: str, participants: list[str]) -> str:
    """The markdown body signals are promoted from: header + the raw content
    under ## Discussion. A summarized document does not store this body — it
    is rebuilt from the transcript on parse (see from_markdown)."""
    return "\n".join([*_header_lines(title, participants), "## Discussion", "", content])


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
    # ADR-004: when imi ingested this (never a proxy for occurred_at) and
    # how occurred_at was obtained. Server-assigned.
    recorded_at: datetime | None = None
    time_source: str = TIME_SOURCE_UNRECORDED
    participants: list[str] = Field(default_factory=list)
    # Resolved graph ids of every entity this observation was linked to at
    # ingest time. Authoritative for rebuilding MENTIONED_IN from the file:
    # names in entities_mentioned/participants are surface forms, and
    # re-slugging them on rebuild would not reproduce ingest-time resolution
    # ("Dan" -> person-dan-kauppi).
    entity_ids: list[str] = Field(default_factory=list)
    # ADR-003: record (we were party to it) or library (third-party content).
    # Assigned by the ingest ADMIT phase and persisted in the frontmatter so a
    # rebuild from files applies the same gates as live ingest. A library
    # observation has no participants; its authors are kept separately and
    # never become person nodes.
    lane: str = "record"
    authors: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    # Synthesized meeting summary (app/services/meeting_synthesis.py). When
    # set, the document body is the summary instead of the Discussion copy of
    # the transcript; summary_prompt records which prompt version wrote it and
    # marks the body as a summary on parse. Never extraction input.
    summary: str | None = None
    purpose: str | None = None
    summary_prompt: str | None = None
    status: str = "completed"
    is_finalized: bool = True
    update_count: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("time_source", mode="before")
    @classmethod
    def _validate_time_source(cls, value: str) -> str:
        return validate_time_source(value)

    @field_validator("lane", mode="before")
    @classmethod
    def _validate_lane(cls, value: str) -> str:
        # Frontmatter is hand-editable; "Library" must not read as not-library.
        if isinstance(value, str):
            value = value.strip().lower()
        return validate_lane(value)

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
        # Only written when known: files from before ADR-004 stay
        # byte-identical (absent time_source reads as unrecorded).
        if self.recorded_at:
            frontmatter.append(f"recorded_at: {self.recorded_at.isoformat()}")
        if self.time_source != TIME_SOURCE_UNRECORDED:
            frontmatter.append(f"time_source: {self.time_source}")
        # Only written for library: record files stay byte-identical to the
        # pre-lanes format (absent lane reads as record).
        if self.lane != "record":
            frontmatter.append(f"lane: {self.lane}")
        if self.authors:
            frontmatter.append("authors:")
            for a in self.authors:
                frontmatter.append(f"  - {_yaml_escape(a)}")

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

        if self.entity_ids:
            frontmatter.append("entity_ids:")
            for eid in self.entity_ids:
                frontmatter.append(f"  - {_yaml_escape(eid)}")

        if self.key_points:
            frontmatter.append("key_points:")
            for kp in self.key_points:
                frontmatter.append(f"  - {_yaml_escape(kp)}")

        # A summary without the transcript it summarizes could not be parsed
        # back into extraction input, so it is only written alongside one.
        summarized = bool(self.summary and self.raw_content)
        if summarized:
            if self.purpose:
                frontmatter.append(f"purpose: {_yaml_escape(self.purpose)}")
            frontmatter.append(f"summary_prompt: {_yaml_escape(self.summary_prompt or 'unknown')}")

        frontmatter.append("---")

        if summarized:
            header = build_observation_header(self.title or "", self.participants)
            body = header.rstrip() + "\n\n" + self.summary.strip()
        else:
            body = self.content
        output = "\n".join(frontmatter) + "\n\n" + body
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

        # A summarized document stores the summary as its body; the extraction
        # body is rebuilt from the transcript exactly as BUILD_MEETING built it.
        summary = None
        summary_prompt = frontmatter.get("summary_prompt")
        participants = frontmatter.get("participants") or []
        if summary_prompt and raw_content:
            title = str(frontmatter.get("title") or "")
            header = build_observation_header(title, participants).strip()
            summary = content[len(header):].strip() if content.startswith(header) else content
            content = build_observation_body(title, raw_content, participants)

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
            recorded_at=_parse_dt(frontmatter.get("recorded_at")),
            time_source=frontmatter.get("time_source") or TIME_SOURCE_UNRECORDED,
            participants=participants,
            entity_ids=frontmatter.get("entity_ids") or [],
            lane=frontmatter.get("lane") or "record",
            authors=frontmatter.get("authors") or [],
            key_points=frontmatter.get("key_points") or [],
            summary=summary,
            purpose=frontmatter.get("purpose") if summary else None,
            summary_prompt=summary_prompt if summary else None,
            status=frontmatter.get("status", "completed"),
            is_finalized=frontmatter.get("is_finalized", False),
            update_count=frontmatter.get("update_count", 0),
        )
