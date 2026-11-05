"""Capture linking policy (ADR-011): ``library.linking`` in config/lanes.yaml.

Every key is optional. The defaults leave the LINK phase off, so a deployment
that never configures it sees no change. Like the rest of the library policy
(``lane_admission.library_policy``), a bad value is logged and replaced by its
default; a config typo never breaks intake or a link run.

    library:
      linking:
        mode: off              # off | shadow | on
        batch_size: 10         # captures per capture_linking call
        concurrency: 2         # capture_linking calls in flight per run
        min_confidence: 0.6    # model confidence needed to keep a link
        roles:                 # which domain type plays each role
          source: source       # deterministic (FROM_SOURCE)
          topic: topic         # seeded (ABOUT)
          storyline: storyline # seeded (ADVANCES)
          essay: essay         # seeded (EVIDENCE_FOR)
        corroboration:         # bar for creating a create_types entity
          min_captures: 3
          min_sources: 2
          window_days: 30
        record_sources: []     # record-lane capture sources linked to seeded
                               # items only (ADR-012), e.g. [changes]
        record_storyline_min_confidence:   # storyline bar for those items
                                           # (default: storyline_min_confidence)
        meetings:              # call transcripts -> storyline excerpts (ADR-013)
          mode: off            # off | shadow | on; effective mode is the lower
                               # of this and library.linking.mode
          storyline_min_confidence: 0.75
          max_excerpts_per_storyline: 3
          max_storylines_per_meeting: 5
          max_lines_per_part: 400
          overlap_lines: 40
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.services.lane_admission import _warn, lanes_config, library_create_types

logger = logging.getLogger(__name__)

LINK_MODES = ("off", "shadow", "on")
SOURCE_ROLE = "source"
SEEDED_ROLES = ("topic", "storyline", "essay")
DEFAULT_ROLES: dict[str, str | None] = {
    "source": "source",
    "topic": "topic",
    "storyline": "storyline",
    "essay": "essay",
}


@dataclass(frozen=True)
class Corroboration:
    min_captures: int = 3
    min_sources: int = 2
    window_days: int = 30


@dataclass(frozen=True)
class MeetingLinkingPolicy:
    """``library.linking.meetings`` (ADR-013 §6). Meetings stay record-lane
    content: these settings configure the LINK phase and never set a lane."""

    mode: str = "off"
    storyline_min_confidence: float = 0.75
    max_excerpts_per_storyline: int = 3
    max_storylines_per_meeting: int = 5
    max_lines_per_part: int = 400
    overlap_lines: int = 40


def effective_mode(*modes: str) -> str:
    """The lowest of the modes, ordered off < shadow < on."""
    return min(modes, key=LINK_MODES.index) if modes else "off"


@dataclass(frozen=True)
class LinkingPolicy:
    mode: str = "off"
    batch_size: int = 10
    concurrency: int = 2
    min_confidence: float = 0.6
    roles: dict[str, str | None] = field(default_factory=lambda: dict(DEFAULT_ROLES))
    corroboration: Corroboration = Corroboration()
    # Storylines take a higher bar than topics (a link is evidence for a thesis).
    storyline_min_confidence: float = 0.75
    max_advances: int = 2  # storylines per capture
    # Record-lane capture sources the LINK phase also links, to seeded items
    # only: no mentions, no entity creation, no corroboration (ADR-012).
    record_sources: tuple[str, ...] = ()
    # Storyline bar for those record items. The owner's own work log is
    # first-hand, so the model's confidence there is relevance only, and a
    # single work item rarely bears on a thesis as strongly as a news report.
    # Defaults to storyline_min_confidence.
    record_storyline_min_confidence: float | None = None
    meetings: MeetingLinkingPolicy = MeetingLinkingPolicy()

    @property
    def meeting_mode(self) -> str:
        """Effective meeting mode: the lower of library.linking.mode and
        library.linking.meetings.mode."""
        return effective_mode(self.mode, self.meetings.mode)

    def role_type(self, role: str, domain_types: set[str] | None) -> str | None:
        """The active domain's type for ``role`` (case-insensitive), or None
        when the role is unconfigured or the domain lacks the type."""
        wanted = self.roles.get(role)
        if not wanted or not domain_types:
            return None
        by_lower = {t.lower(): t for t in domain_types}
        return by_lower.get(wanted.lower())

    def create_types(self, domain_types: set[str] | None) -> frozenset[str]:
        """Corroborated tier: ``library.entities.create_types`` (ADR-006 §4),
        minus any type a deterministic or seeded role already owns: each type
        is created in exactly one way (ADR-011 §3)."""
        owned = {
            t for role in (SOURCE_ROLE, *SEEDED_ROLES)
            if (t := self.role_type(role, domain_types))
        }
        return frozenset(library_create_types(domain_types) - owned)


def _positive_int(section: dict, key: str, default: int, where: str) -> int:
    value = section.get(key, default)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    _warn("%s.%s must be a positive integer (got %r)", where, key, value)
    return default


def _mode(value: Any, default: str, where: str) -> str:
    # YAML reads a bare `off` / `on` as a boolean.
    if value is False:
        value = "off"
    elif value is True:
        value = "on"
    if value not in LINK_MODES:
        _warn("%s.mode must be one of %s (got %r)", where, LINK_MODES, value)
        return default
    return value


def _unit(section: dict, key: str, default: float, where: str) -> float:
    value = section.get(key, default)
    if isinstance(value, int | float) and not isinstance(value, bool) and 0.0 <= value <= 1.0:
        return float(value)
    _warn("%s.%s must be a number from 0 to 1 (got %r)", where, key, value)
    return default


def parse_meeting_policy(raw: Any) -> MeetingLinkingPolicy:
    default = MeetingLinkingPolicy()
    if raw is None:
        return default
    where = "library.linking.meetings"
    if not isinstance(raw, dict):
        _warn("%s must be a mapping (got %r)", where, raw)
        return default
    size = _positive_int(raw, "max_lines_per_part", default.max_lines_per_part, where)
    overlap = raw.get("overlap_lines", default.overlap_lines)
    if not (isinstance(overlap, int) and not isinstance(overlap, bool) and 0 <= overlap < size):
        _warn("%s.overlap_lines must be an integer from 0 to max_lines_per_part - 1 (got %r)",
              where, overlap)
        overlap = min(default.overlap_lines, size - 1)
    return MeetingLinkingPolicy(
        mode=_mode(raw.get("mode", default.mode), default.mode, where),
        storyline_min_confidence=_unit(raw, "storyline_min_confidence",
                                       default.storyline_min_confidence, where),
        max_excerpts_per_storyline=_positive_int(raw, "max_excerpts_per_storyline",
                                                 default.max_excerpts_per_storyline, where),
        max_storylines_per_meeting=_positive_int(raw, "max_storylines_per_meeting",
                                                 default.max_storylines_per_meeting, where),
        max_lines_per_part=size,
        overlap_lines=overlap,
    )


def parse_linking_policy(config: dict[str, Any]) -> LinkingPolicy:
    default = LinkingPolicy()
    library = config.get("library")
    linking = library.get("linking") if isinstance(library, dict) else None
    if linking is None:
        return default
    if not isinstance(linking, dict):
        _warn("library.linking must be a mapping (got %r)", linking)
        return default
    where = "library.linking"

    mode = linking.get("mode", default.mode)
    # YAML reads a bare `off` / `on` as a boolean.
    if mode is False:
        mode = "off"
    elif mode is True:
        mode = "on"
    if mode not in LINK_MODES:
        _warn("%s.mode must be one of %s (got %r)", where, LINK_MODES, mode)
        mode = default.mode

    confidence = linking.get("min_confidence", default.min_confidence)
    if not (
        isinstance(confidence, int | float)
        and not isinstance(confidence, bool)
        and 0.0 <= confidence <= 1.0
    ):
        _warn("%s.min_confidence must be a number from 0 to 1 (got %r)", where, confidence)
        confidence = default.min_confidence

    roles: dict[str, str | None] = dict(DEFAULT_ROLES)
    raw_roles = linking.get("roles")
    if raw_roles is not None:
        if isinstance(raw_roles, dict):
            for role, type_name in raw_roles.items():
                if role not in DEFAULT_ROLES:
                    _warn("%s.roles has unknown role %r", where, role)
                elif type_name is None or (isinstance(type_name, str) and type_name.strip()):
                    roles[role] = type_name.strip() if type_name else None
                else:
                    _warn("%s.roles.%s must be an entity type name (got %r)", where, role, type_name)
        else:
            _warn("%s.roles must be a mapping (got %r)", where, raw_roles)

    storyline_floor = linking.get("storyline_min_confidence", default.storyline_min_confidence)
    if not (
        isinstance(storyline_floor, int | float)
        and not isinstance(storyline_floor, bool)
        and 0.0 <= storyline_floor <= 1.0
    ):
        _warn("%s.storyline_min_confidence must be a number from 0 to 1 (got %r)", where, storyline_floor)
        storyline_floor = default.storyline_min_confidence

    raw_corr = linking.get("corroboration") or {}
    if not isinstance(raw_corr, dict):
        _warn("%s.corroboration must be a mapping (got %r)", where, raw_corr)
        raw_corr = {}
    corr_where = f"{where}.corroboration"
    corroboration = Corroboration(
        min_captures=_positive_int(raw_corr, "min_captures", Corroboration.min_captures, corr_where),
        min_sources=_positive_int(raw_corr, "min_sources", Corroboration.min_sources, corr_where),
        window_days=_positive_int(raw_corr, "window_days", Corroboration.window_days, corr_where),
    )

    record_floor = linking.get("record_storyline_min_confidence")
    if record_floor is not None and not (
        isinstance(record_floor, int | float)
        and not isinstance(record_floor, bool)
        and 0.0 <= record_floor <= 1.0
    ):
        _warn("%s.record_storyline_min_confidence must be a number from 0 to 1 (got %r)", where, record_floor)
        record_floor = None

    raw_record = linking.get("record_sources") or []
    if isinstance(raw_record, str):
        raw_record = [raw_record]
    record_sources: list[str] = []
    if isinstance(raw_record, list):
        for value in raw_record:
            if isinstance(value, str) and value.strip():
                record_sources.append(value.strip())
            else:
                _warn("%s.record_sources entries must be source names (got %r)", where, value)
    else:
        _warn("%s.record_sources must be a list of source names (got %r)", where, raw_record)

    return LinkingPolicy(
        mode=mode,
        batch_size=_positive_int(linking, "batch_size", default.batch_size, where),
        concurrency=_positive_int(linking, "concurrency", default.concurrency, where),
        min_confidence=float(confidence),
        roles=roles,
        corroboration=corroboration,
        storyline_min_confidence=float(storyline_floor),
        max_advances=_positive_int(linking, "max_advances", default.max_advances, where),
        record_sources=tuple(dict.fromkeys(record_sources)),
        record_storyline_min_confidence=None if record_floor is None else float(record_floor),
        meetings=parse_meeting_policy(linking.get("meetings")),
    )


_cache: tuple[dict[str, Any], LinkingPolicy] | None = None


def linking_policy() -> LinkingPolicy:
    """The validated ``library.linking`` policy, parsed once per loaded config."""
    global _cache
    config = lanes_config()
    if _cache is None or _cache[0] is not config:
        try:
            policy = parse_linking_policy(config)
        except Exception as e:  # never break a run over config
            logger.warning("[LINK] library.linking unreadable, using defaults: %s", e)
            policy = LinkingPolicy()
        _cache = (config, policy)
    return _cache[1]
