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
from dataclasses import asdict, dataclass
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
_HORIZONS = ((0.3, 90), (0.6, 180), (1.01, 365))
_UNJUDGED_HORIZON_DAYS = 180

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
    "openbrain-import": PER_ITEM,
    "document": PER_ITEM,
    "other": PER_ITEM,
}
UNKNOWN_SOURCE_DEFAULT = PER_ITEM

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


def drop_rule(content: str, source: str | None) -> str | None:
    """Reason to drop outright, or None. Never fires for record-default sources."""
    if source_default(source) == "record":
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

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def library_stale_after(durable: float | None, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if durable is None:
        days = _UNJUDGED_HORIZON_DAYS
    else:
        days = next(d for bound, d in _HORIZONS if durable < bound)
    return (now + timedelta(days=days)).isoformat()



def library_claim_fields(
    signal: Any, *, attributed_to: str | None, as_of: str | None, stale_after: str
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
    client: Any = None,
    now: datetime | None = None,
) -> LaneDecision:
    """Decide lane or drop for one item. Never raises."""
    default = source_default(source)
    try:
        rule = drop_rule(content, source)
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
