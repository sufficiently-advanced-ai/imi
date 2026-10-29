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
class LinkingPolicy:
    mode: str = "off"
    batch_size: int = 10
    concurrency: int = 2
    min_confidence: float = 0.6
    roles: dict[str, str | None] = field(default_factory=lambda: dict(DEFAULT_ROLES))
    corroboration: Corroboration = Corroboration()

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

    return LinkingPolicy(
        mode=mode,
        batch_size=_positive_int(linking, "batch_size", default.batch_size, where),
        concurrency=_positive_int(linking, "concurrency", default.concurrency, where),
        min_confidence=float(confidence),
        roles=roles,
        corroboration=corroboration,
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
