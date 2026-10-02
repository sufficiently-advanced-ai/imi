"""Lane admission (ADR-003) — record, library, or drop, decided once at intake.

Every entry point (captures today; the ingest ``ADMIT`` phase next) calls
``admit()`` before anything is built. Decision order:

1. **Source default** — ``SOURCE_DEFAULTS`` (overridable in
   ``config/lanes.yaml``): meeting bots and own notes → record; RSS, web,
   YouTube → library; mail and mixed imports → per item.
2. **Deterministic drop rules** — automated mail senders (DMARC reports,
   statements). Never applied to record-default sources.
3. **The decision model** (Jev, operation ``lane_admission``) — one
   ``decide()`` per item: a lane Choice (memory / library / junk) plus a
   durability Noul. Same contract as ``entity_admission``: never raises;
   ``off`` skips, ``shadow`` logs verdicts without acting, ``on`` acts.

Asymmetric bars: dropping needs P(junk) >= ``DROP_MIN_PROBABILITY`` and is
never applied to record-default sources (a thought typed by hand or a meeting
bot's transcript is never discarded by a model). A per-item source is filed
as record only when P(memory) >= ``RECORD_MIN_PROBABILITY``.

With no model available, per-item sources fall back to ``record`` — the
pre-lanes behaviour — so an outage degrades to the old system, never hides
first-party content.

Intake channel (ADR-007): agent-mediated intake (MCP) names the connector
the content came from as ``source`` — but the source is the client's claim.
The transport stamps ``channel`` (``"mcp"``); on that channel a record-default
source is judged per item unless ``config/lanes.yaml`` lists it under
``mcp_trusted_sources``. ``channel`` never comes from tool arguments or a
request body. With no model configured the per-item fallback above still
applies, so an instance without a judge behaves as before.

Library items get ``stale_after`` (ADR-003 §5), scaled by the durability
judgment.

The questions were calibrated on the full production corpus (2026-09-26,
``scripts/classify_memories.py``, which imports them from here).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

LANE_OPERATION = "lane_admission"
DROP_MIN_PROBABILITY = 0.80  # P(junk) needed to drop a non-record-default item
RECORD_MIN_PROBABILITY = 0.50  # P(memory) needed to file a per-item source as record
PER_ITEM = "per_item"
STATE_CHARS = 2500  # the head is enough to judge a lane; the model degrades on long noisy state

# Library decay horizons by durability (ADR-003 §5).
_HORIZONS = ((0.3, 90), (0.6, 180), (1.01, 365))  # defaults; lanes.yaml library.decay

SOURCE_DEFAULTS: dict[str, str] = {
    # we were party to it
    "manual": "record",
    "fireflies": "record",
    "otter": "record",
    "fathom": "record",
    "grain": "record",
    "zoom": "record",
    "plaud": "record",
    "local_recording": "record",
    "littlebird": "record",
    "slack": "record",
    "gcal": "record",
    "lcars": "record",
    "ai-circle-inbox": "record",
    "ai-circle-brief": "record",
    "nightly-memory-retro": "record",
    # watched sources
    "web": "library",
    "youtube": "library",
    "rss": "library",
    # mixed: judged per item
    "mail": PER_ITEM,
    "email": PER_ITEM,
    "gmail": PER_ITEM,
    "gdrive": PER_ITEM,
    "openbrain-import": PER_ITEM,
    "document": PER_ITEM,
    "other": PER_ITEM,
}
UNKNOWN_SOURCE_DEFAULT = PER_ITEM
UNKNOWN_SOURCE = "unknown"  # stored when an MCP caller names no source (ADR-007 §3)

# Intake channels (ADR-007). Stamped by the transport, never client-supplied.
MCP_CHANNEL = "mcp"

_AUTOMATED_SENDERS = re.compile(
    r"dmarc|mimecastreport|no\.reply\.alerts@chase|noreply@\S*(statement|billing)",
    re.I,
)

DEFAULT_OWNER = "the owner of this knowledge base"


def _config_candidates() -> tuple[Path, ...]:
    # Resolved at load time, not import time, so LANES_CONFIG_PATH set after
    # import (tests, entrypoints) is honoured. An explicit path is exclusive.
    explicit = os.getenv("LANES_CONFIG_PATH")
    if explicit:
        return (Path(explicit),)
    return (Path("config/lanes.yaml"), Path("/app/config/lanes.yaml"))


def _load_config() -> dict[str, Any]:
    for candidate in _config_candidates():
        if candidate.is_file():
            try:
                data = yaml.safe_load(candidate.read_text()) or {}
                if isinstance(data, dict):
                    return data
                logger.warning("[LANES] %s: top-level YAML must be a mapping; ignoring", candidate)
            except (OSError, yaml.YAMLError) as e:
                logger.warning("[LANES] Could not read %s: %s", candidate, e)
    return {}


_config: dict[str, Any] | None = None


def lanes_config() -> dict[str, Any]:
    global _config
    if _config is None:
        _config = _load_config()
    return _config


def reset_lanes_config() -> None:
    global _config
    _config = None


def source_default(source: str | None) -> str:
    sources = {**SOURCE_DEFAULTS, **(lanes_config().get("sources") or {})}
    value = sources.get((source or "").lower(), UNKNOWN_SOURCE_DEFAULT)
    return value if value in ("record", "library", PER_ITEM) else UNKNOWN_SOURCE_DEFAULT


def mcp_trusted_sources() -> frozenset[str]:
    """lanes.yaml ``mcp_trusted_sources``: record-default sources an MCP caller
    may assert without judgment (ADR-007 §2). Empty by default."""
    raw = lanes_config().get("mcp_trusted_sources") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        logger.warning("[LANES] mcp_trusted_sources must be a list; ignoring")
        return frozenset()
    return frozenset(str(s).strip().lower() for s in raw if str(s).strip())


def effective_default(source: str | None, channel: str | None = None) -> str:
    """The source default once the intake channel is known (ADR-007 §2).

    On the MCP channel the source is the agent's claim, so a record-default
    source is judged per item unless it is listed in ``mcp_trusted_sources``.
    Other channels (REST, connectors, migrations) keep the source default.
    """
    default = source_default(source)
    if (
        channel == MCP_CHANNEL
        and default == "record"
        and (source or "").lower() not in mcp_trusted_sources()
    ):
        return PER_ITEM
    return default


def owner_name() -> str:
    """lanes.yaml ``owner``, else the KB_OWNER_NAME setting, else a neutral phrase."""
    configured = lanes_config().get("owner")
    if configured:
        return str(configured)
    try:
        from app.config import settings

        kb_owner = (getattr(settings, "KB_OWNER_NAME", None) or "").strip()
    except Exception:
        kb_owner = ""
    return kb_owner or DEFAULT_OWNER


# ---- questions (calibrated 2026-09-26; see module docstring) ----------------


def lane_criteria(owner: str) -> dict[str, str]:
    return {
        "memory": (
            f"Personal memory: about {owner}'s own work, life, projects, clients, relationships, "
            "decisions, plans, commitments, lessons, or conversations and communities they took "
            "part in (their meetings, their peer groups, their own notes, ideas and "
            "retrospectives). Includes meeting summaries and mail written to them personally by "
            "people they work with."
        ),
        "library": (
            "Library/reference: third-party published content they read, watched or subscribed "
            "to — articles, blog posts, newsletters, videos and their transcripts, news, product "
            "announcements, papers, documentation. About the outside world, not about their own "
            "life. A blog post or essay written in the first person by someone else is library: "
            "'I' there is the author, not them."
        ),
        "junk": (
            "No durable value: automated or transactional notices (statements, bills, receipts, "
            "alerts, verification codes, shipping, account or security notifications, marketing "
            "promos), system test fixtures and probes, error / login / paywall / suspended pages, "
            "or content that is empty or mostly navigation and boilerplate."
        ),
    }


def build_lane_questions(owner: str | None = None) -> dict[str, Any]:
    from app.services.inference.decisions import Choice, Noul

    owner = owner or owner_name()
    return {
        "lane": Choice(
            instructions=(
                f"This is one record from {owner}'s personal knowledge store. Which kind of "
                "record is it? Judge by what the content is, not by its tags."
            ),
            criteria=lane_criteria(owner),
        ),
        "durable": Noul(
            instructions=(
                f"Would {owner} plausibly want this recalled when working on something related "
                "six or more months from now? Answer no for ephemeral, generic, promotional or "
                "trivial items."
            ),
        ),
    }


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def clean_for_state(text: str, limit: int = STATE_CHARS) -> str:
    """Strip image/link markup and collapse whitespace so the head carries text."""
    text = _MD_IMAGE.sub("", text or "")
    text = _MD_LINK.sub(r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()[:limit]


def build_lane_state(content: str, source: str | None, source_id: str | None = None,
                     summary: str | None = None) -> dict[str, Any]:
    state = {
        "source": source,
        "source_id": source_id,
        "summary": summary,
        "content": clean_for_state(content),
    }
    return {k: v for k, v in state.items() if v}


# ---- rules ------------------------------------------------------------------


def mail_sender(content: str) -> str:
    m = re.search(r"^From: (.+)$", content or "", re.M)
    return m.group(1).strip() if m else ""


def drop_rule(content: str, source: str | None, channel: str | None = None) -> str | None:
    """Reason to drop outright, or None. Never fires for record-default sources
    (as admitted on ``channel`` — see ``effective_default``)."""
    if effective_default(source, channel) == "record":
        return None
    sender = mail_sender(content)
    extra = lanes_config().get("drop_senders") or []
    if sender and (
        _AUTOMATED_SENDERS.search(sender)
        or any(str(s).lower() in sender.lower() for s in extra)
    ):
        return f"automated mail ({sender[:80]})"
    return None


# ---- decision ---------------------------------------------------------------


@dataclass(frozen=True)
class LaneDecision:
    lane: str  # "record" | "library" (meaningless when drop)
    drop: bool = False
    reason: str = ""
    source_default: str = UNKNOWN_SOURCE_DEFAULT
    mode: str = "off"
    p_memory: float | None = None
    p_library: float | None = None
    p_junk: float | None = None
    durable: float | None = None
    stale_after: str | None = None
    channel: str | None = None  # intake channel (ADR-007), stamped by the transport

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---- per-deployment library policy (ADR-006) ---------------------------------
#
# ``library:`` and ``recall:`` in config/lanes.yaml. Every key is optional and
# the defaults reproduce ADR-003 exactly. A bad value is logged and replaced by
# its default — a config typo never breaks intake. What stays fixed regardless
# (ADR-006 §2): the lane is server-assigned, library produces claims only, is
# never instruction-grade, never feeds profiles as fact, and decay is never
# deletion.

LIBRARY_ENTITY_MODES = ("link_only", "allowlist")
# Entity types a claim's source (authors / publisher) may resolve to, in order,
# filtered to the active domain (domains name people and organizations
# differently: person/contact, organization/company).
DEFAULT_ATTRIBUTION_TYPES = ("person", "contact", "organization", "company")
DEFAULT_RECALL_LANES = ("record",)


@dataclass(frozen=True)
class LibraryPolicy:
    decay_enabled: bool = True
    horizons_days: tuple[int, int, int] = tuple(d for _, d in _HORIZONS)
    entity_mode: str = "link_only"
    create_types: tuple[str, ...] = ()  # as configured; validated per domain
    infer_relationships: bool = False
    attribution_types: tuple[str, ...] | None = None  # None: the defaults

    @property
    def allowlist(self) -> bool:
        return self.entity_mode == "allowlist"


def _warn(message: str, *args: Any) -> None:
    logger.warning("[LANES] lanes.yaml: " + message + "; using the default", *args)


def _bool(section: dict, key: str, default: bool, where: str) -> bool:
    value = section.get(key, default)
    if isinstance(value, bool):
        return value
    _warn("%s.%s must be true or false (got %r)", where, key, value)
    return default


def _type_list(value: Any, where: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        _warn("%s must be a list of entity type names (got %r)", where, value)
        return None
    return tuple(dict.fromkeys(v.strip() for v in value))


def _parse_library_policy(config: dict[str, Any]) -> LibraryPolicy:
    default = LibraryPolicy()
    library = config.get("library")
    if library is None:
        return default
    if not isinstance(library, dict):
        _warn("library must be a mapping (got %r)", library)
        return default

    decay = library.get("decay") or {}
    if not isinstance(decay, dict):
        _warn("library.decay must be a mapping (got %r)", decay)
        decay = {}
    decay_enabled = _bool(decay, "enabled", default.decay_enabled, "library.decay")
    horizons = decay.get("horizons_days", list(default.horizons_days))
    if (
        isinstance(horizons, list)
        and len(horizons) == len(_HORIZONS)
        and all(isinstance(d, int) and not isinstance(d, bool) and d > 0 for d in horizons)
        and list(horizons) == sorted(horizons)
    ):
        horizons_days = tuple(horizons)
    else:
        _warn(
            "library.decay.horizons_days must be %d ascending positive day counts "
            "(low / medium / high durability), got %r",
            len(_HORIZONS), horizons,
        )
        horizons_days = default.horizons_days

    entities = library.get("entities") or {}
    if not isinstance(entities, dict):
        _warn("library.entities must be a mapping (got %r)", entities)
        entities = {}
    mode = entities.get("mode", default.entity_mode)
    if mode not in LIBRARY_ENTITY_MODES:
        _warn("library.entities.mode must be one of %s (got %r)", LIBRARY_ENTITY_MODES, mode)
        mode = default.entity_mode
    create_types = _type_list(entities.get("create_types"), "library.entities.create_types") or ()
    if create_types and mode != "allowlist":
        logger.info("[LANES] library.entities.create_types is ignored unless mode is allowlist")

    return LibraryPolicy(
        decay_enabled=decay_enabled,
        horizons_days=horizons_days,
        entity_mode=mode,
        create_types=create_types,
        infer_relationships=_bool(
            library, "infer_relationships", default.infer_relationships, "library"
        ),
        attribution_types=_type_list(library.get("attribution_types"), "library.attribution_types"),
    )


# (config dict it was parsed from, policy). Holding the dict — not its id() —
# means a reloaded config can never be mistaken for the cached one.
_policy_cache: tuple[dict[str, Any], LibraryPolicy] | None = None


def library_policy() -> LibraryPolicy:
    """The validated ``library:`` policy, parsed once per loaded config."""
    global _policy_cache
    config = lanes_config()
    if _policy_cache is None or _policy_cache[0] is not config:
        try:
            policy = _parse_library_policy(config)
        except Exception as e:  # never break intake over config
            logger.warning("[LANES] library policy unreadable, using defaults: %s", e)
            policy = LibraryPolicy()
        _policy_cache = (config, policy)
    return _policy_cache[1]


def _match_domain_types(
    wanted: tuple[str, ...], domain_types: set[str] | None, where: str, *, warn: bool
) -> tuple[str, ...]:
    """``wanted`` mapped case-insensitively onto the active domain's types.

    Unknown types are skipped (with a warning when they were configured).
    Without a readable domain nothing can be validated, so nothing matches."""
    if not wanted or not domain_types:
        return ()
    by_lower = {t.lower(): t for t in domain_types}
    out = []
    for t in wanted:
        match = by_lower.get(t.lower())
        if match:
            out.append(match)
        elif warn:
            logger.warning(
                "[LANES] lanes.yaml: %s names %r, which is not an entity type of the active "
                "domain; ignoring it", where, t,
            )
    return tuple(dict.fromkeys(out))


def library_create_types(domain_types: set[str] | None) -> frozenset[str]:
    """Entity types library content may create (ADR-006 §4). Empty unless
    ``library.entities.mode`` is ``allowlist``; creation still goes through
    the resolver and entity admission — this lifts the ban, not the gate."""
    policy = library_policy()
    if not policy.allowlist:
        return frozenset()
    return frozenset(
        _match_domain_types(
            policy.create_types, domain_types, "library.entities.create_types", warn=True
        )
    )


def library_attribution_types(domain_types: set[str] | None) -> tuple[str, ...]:
    """Entity types a claim's source resolves to (ADR-006 §5), in order."""
    configured = library_policy().attribution_types
    if configured is None:
        return _match_domain_types(
            DEFAULT_ATTRIBUTION_TYPES, domain_types, "attribution", warn=False
        )
    return _match_domain_types(configured, domain_types, "library.attribution_types", warn=True)


def recall_default_lanes() -> list[str]:
    """``recall.default_lanes`` — the lanes recall searches when the caller
    names none (ADR-006 §7). Default: record only (ADR-003)."""
    default = list(DEFAULT_RECALL_LANES)
    try:
        recall = lanes_config().get("recall")
        if recall is None:
            return default
        lanes = recall.get("default_lanes") if isinstance(recall, dict) else None
        if lanes is None and isinstance(recall, dict):
            return default
        from app.models.lane import LANES

        if (
            isinstance(lanes, list)
            and lanes
            and all(isinstance(lane, str) and lane in LANES for lane in lanes)
        ):
            return list(dict.fromkeys(lanes))
        _warn("recall.default_lanes must be a non-empty list of %s (got %r)", sorted(LANES), lanes)
    except Exception as e:  # never break recall over config
        logger.warning("[LANES] recall.default_lanes unreadable, using the default: %s", e)
    return default


def library_stale_after(durable: float | None, now: datetime | None = None) -> str | None:
    """When a library record decays out of recall (ADR-003 §5), or None when
    ``library.decay.enabled`` is false. Horizons by durability come from
    ``library.decay.horizons_days``; an unjudged record gets the middle one."""
    policy = library_policy()
    if not policy.decay_enabled:
        return None
    now = now or datetime.now(UTC)
    horizons = policy.horizons_days
    if durable is None:
        days = horizons[len(horizons) // 2]
    else:
        days = next(d for (bound, _), d in zip(_HORIZONS, horizons, strict=True) if durable < bound)
    return (now + timedelta(days=days)).isoformat()


def library_decay_enabled() -> bool:
    return library_policy().decay_enabled



def library_claim_fields(
    signal: Any, *, attributed_to: str | None, as_of: str | None, stale_after: str | None
) -> dict[str, Any]:
    """ADR-003 §3: the field values that make a library-lane signal a claim —
    attributed to its source and dated, never our decision or action item.

    Shared by live ingest and the migration backfill (scripts/stamp_lanes.py)
    so both produce the same record. Pure: returns the fields, sets nothing.
    """
    metadata = dict(signal.metadata or {})
    if signal.type != "claim":
        metadata.setdefault("extracted_type", signal.type)
    if attributed_to:
        metadata.setdefault("attributed_to", attributed_to)
    if as_of:
        metadata.setdefault("as_of", as_of)
    return {
        "type": "claim",
        "lane": "library",
        "stale_after": signal.stale_after or stale_after,
        "status": None,
        "owner": None,
        "due_date": None,
        "metadata": metadata,
    }


def apply_lane(
    default: str,
    p_memory: float,
    p_library: float,
    p_junk: float,
) -> tuple[str, bool, str]:
    """(lane, drop, reason) from the model's probabilities. Pure."""
    if default != "record" and p_junk >= DROP_MIN_PROBABILITY:
        return "library", True, f"model: junk (p={p_junk:.2f})"
    if default == PER_ITEM:
        if p_memory >= RECORD_MIN_PROBABILITY:
            return "record", False, f"model: memory (p={p_memory:.2f})"
        return "library", False, f"model: not memory (p_memory={p_memory:.2f})"
    return default, False, f"source default ({default})"


def _fallback_lane(default: str) -> str:
    return "record" if default == PER_ITEM else default


def _default_client():
    try:
        from app.services.inference.decisions import get_decision_client

        client = get_decision_client()
        return client if client.mode(LANE_OPERATION) != "off" else None
    except Exception as e:  # config errors must never break intake
        logger.warning("[LANES] Decision model unavailable: %s", e)
        return None


async def admit(
    content: str,
    source: str | None,
    *,
    source_id: str | None = None,
    summary: str | None = None,
    channel: str | None = None,
    client: Any = None,
    now: datetime | None = None,
) -> LaneDecision:
    """Decide lane or drop for one item. Never raises.

    ``channel`` is the intake transport (ADR-007) — set by the server-side
    handler, never taken from client input. It is recorded on the decision.
    """
    decision = await _admit(
        content, source, source_id=source_id, summary=summary,
        channel=channel, client=client, now=now,
    )
    return replace(decision, channel=channel) if channel else decision


async def _admit(
    content: str,
    source: str | None,
    *,
    source_id: str | None = None,
    summary: str | None = None,
    channel: str | None = None,
    client: Any = None,
    now: datetime | None = None,
) -> LaneDecision:
    default = effective_default(source, channel)
    try:
        rule = drop_rule(content, source, channel)
        if rule:
            return LaneDecision(lane="library", drop=True, reason=f"rule: {rule}", source_default=default)

        if client is None:
            client = _default_client()
        mode = client.mode(LANE_OPERATION) if client is not None else "off"
        fallback = _fallback_lane(default)
        if mode == "off":
            return _finish(fallback, False, f"source default ({default}); model off", default, mode, now=now)

        from app.services.inference.decisions import DecisionUnavailable

        try:
            result = await client.decide(
                build_lane_state(content, source, source_id, summary),
                build_lane_questions(),
                operation=LANE_OPERATION,
            )
            probs = result.choice("lane").probabilities
            durable = result.noul("durable")
        except (DecisionUnavailable, ValueError, KeyError, TypeError) as e:
            logger.warning("[LANES] Judgment failed for %s/%s, using default: %s", source, source_id, e)
            return _finish(fallback, False, f"source default ({default}); model failed", default, mode, now=now)

        p_mem, p_lib, p_junk = (float(probs.get(k, 0.0)) for k in ("memory", "library", "junk"))
        lane, drop, reason = apply_lane(default, p_mem, p_lib, p_junk)
        logger.info(
            "[LANES] %s %s/%s: memory=%.2f library=%.2f junk=%.2f durable=%.2f -> %s%s",
            mode, source, (source_id or "")[:60], p_mem, p_lib, p_junk, durable,
            "drop" if drop else lane, "" if mode == "on" else " (not applied)",
        )
        if mode != "on":
            lane, drop, reason = fallback, False, f"source default ({default}); shadow: {reason}"
        return _finish(lane, drop, reason, default, mode, p_mem, p_lib, p_junk, durable, now)
    except Exception as e:  # never break intake
        logger.warning("[LANES] Admission failed for %s/%s, using default: %s", source, source_id, e)
        return LaneDecision(lane=_fallback_lane(default), reason=f"admission error: {e}", source_default=default)


def _finish(lane, drop, reason, default, mode, p_mem=None, p_lib=None, p_junk=None, durable=None, now=None):
    return LaneDecision(
        lane=lane, drop=drop, reason=reason, source_default=default, mode=mode,
        p_memory=p_mem, p_library=p_lib, p_junk=p_junk, durable=durable,
        stale_after=library_stale_after(durable, now) if lane == "library" and not drop else None,
    )


# ---- drop audit -------------------------------------------------------------

_log_lock = threading.Lock()


class AdmissionLog:
    """Append-only JSONL of dropped items (ADR-003 §2): reviewable, idempotent.

    One file per month under ``memory/admission/``; a content hash already in
    this month's file is not appended again, so re-submitting a dropped item
    does not grow the log.
    """

    def __init__(self, repo_root: Path):
        self.dir = repo_root / "memory" / "admission"
        self.repo_root = repo_root

    def path(self, now: datetime | None = None) -> Path:
        return self.dir / f"{(now or datetime.now(UTC)).strftime('%Y-%m')}.jsonl"

    def relative_path(self, now: datetime | None = None) -> str:
        return str(self.path(now).relative_to(self.repo_root))

    def append(self, decision: LaneDecision, *, content: str, source: str | None,
               source_id: str | None, now: datetime | None = None) -> bool:
        """True when a row was written, False when the item was already logged."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        path = self.path(now)
        with _log_lock:
            if path.exists():
                with path.open() as f:
                    if any(content_hash in line for line in f):
                        return False
            path.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "at": (now or datetime.now(UTC)).isoformat(),
                "source": source,
                "source_id": source_id,
                "content_hash": content_hash,
                "snippet": clean_for_state(content, 200),
                **{k: v for k, v in decision.as_dict().items() if v is not None},
            }
            with path.open("a") as f:
                f.write(json.dumps(row) + "\n")
        return True
