"""Meetings API — browse the meeting documents in the corpus.

Reads meetings/meeting-*.md (the source of truth) and the matching
signals/meeting-*.json. Meetings are ordered by when they happened
(frontmatter start_time, ADR-004), never by when they were ingested.

GET /api/meetings/history/list   - paged list, newest first
GET /api/meetings/history/stats  - corpus totals
GET /api/meetings/{bot_id}/content - one meeting: summary, transcript, signals
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.observation import Observation, build_observation_header
from app.services.signal_store import SignalStore
from app.utils.event_time import to_utc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

MEETINGS_DIR = Path("/app/repo/meetings")
SIGNALS_DIR = Path("/app/repo/signals")
SIGNAL_TYPES = ("decision", "action_item", "key_point", "insight")
_BOT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class SignalCounts(BaseModel):
    decision: int = 0
    action_item: int = 0
    key_point: int = 0
    insight: int = 0


class EntityCounts(BaseModel):
    """Shape MeetingViewer reads: people/projects/accounts from the document's
    entities, action_items/decisions from its visible signals."""

    people: int = 0
    projects: int = 0
    accounts: int = 0
    action_items: int = 0
    decisions: int = 0


class MeetingListItem(BaseModel):
    id: str
    bot_id: str
    title: str
    start_time: str | None = None
    time_source: str | None = None
    participants: list[str] = Field(default_factory=list)
    attendee_count: int = 0
    purpose: str | None = None
    key_points: list[str] = Field(default_factory=list)
    summarized: bool = False
    has_transcript: bool = False
    lane: str = "record"
    signal_counts: SignalCounts = Field(default_factory=SignalCounts)


class MeetingListResponse(BaseModel):
    items: list[MeetingListItem]
    total: int
    next_cursor: str | None = None
    page_size: int


class MeetingStats(BaseModel):
    total_meetings: int = 0
    meetings_with_transcripts: int = 0
    meetings_summarized: int = 0
    total_signals: int = 0
    first_meeting: str | None = None
    last_meeting: str | None = None


class MeetingSignal(BaseModel):
    id: str
    type: str
    content: str
    owner: str | None = None
    status: str | None = None
    position: int = 0


class MeetingContent(BaseModel):
    bot_id: str
    meeting_id: str
    title: str | None = None
    body: str = ""  # the summary; "" when the meeting has none
    purpose: str | None = None
    key_points: list[str] = Field(default_factory=list)
    summarized: bool = False
    transcript: str | None = None
    updated_at: str
    duration: float | None = None  # seconds (MeetingViewer's unit)
    participants: list[str] = Field(default_factory=list)
    platform: str | None = None
    start_time: str | None = None
    time_source: str | None = None
    entities_mentioned: dict[str, list[str]] = Field(default_factory=dict)
    entity_counts: EntityCounts = Field(default_factory=EntityCounts)
    signals: list[MeetingSignal] = Field(default_factory=list)
    is_finalized: bool = True
    status: str = "completed"


# --- reading -----------------------------------------------------------------

# path -> (mtime_ns, Observation). Meeting files change rarely; re-parsing
# every file on every list request is the cost this avoids.
_cache: dict[Path, tuple[int, Observation]] = {}


def _load(path: Path) -> Observation | None:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        obs = Observation.from_markdown(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        logger.warning("[MEETINGS] skipping unparseable %s: %s", path.name, e)
        return None
    _cache[path] = (mtime, obs)
    return obs


def _all_meetings() -> list[Observation]:
    if not MEETINGS_DIR.is_dir():
        return []
    meetings = [m for m in (_load(p) for p in sorted(MEETINGS_DIR.glob("meeting-*.md"))) if m]
    meetings.sort(key=_event_time, reverse=True)
    return meetings


def _event_time(obs: Observation) -> datetime:
    return to_utc(obs.occurred_at or obs.observed_at) or datetime.min.replace(tzinfo=UTC)


def _visible_signals(bot_id: str) -> list[Any]:
    """The meeting's signals minus those shown under another (signal dedup)."""
    ms = SignalStore(SIGNALS_DIR).load(bot_id)
    if not ms:
        return []
    from app.services.signal_dedup import shown_under

    # Pointers can reach signals in other meetings; a pointer this file
    # cannot resolve hides nothing (shown_under's rule).
    table = {s.id: (s.metadata or {}).get("duplicate_of") for s in ms.signals}
    lookup = lambda sid: (sid in table, table.get(sid))  # noqa: E731
    return [s for s in ms.signals if shown_under(s.id, lookup) is None]


def _signal_counts(signals: list[Any]) -> SignalCounts:
    counts = SignalCounts()
    for s in signals:
        if s.type in SIGNAL_TYPES:
            setattr(counts, s.type, getattr(counts, s.type) + 1)
    return counts


def _summary_body(obs: Observation) -> str:
    """What to show as the meeting's summary. A synthesized summary, else an
    older document's own body (a live-recorder summary) — but never the
    ## Discussion copy of the transcript an unsummarized ingest carries."""
    if obs.summary:
        return obs.summary.strip()
    if "## Discussion" in obs.content:
        return ""
    header = build_observation_header(obs.title or "", obs.participants).strip()
    body = obs.content.strip()
    return body[len(header):].strip() if body.startswith(header) else body


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _to_item(obs: Observation) -> MeetingListItem:
    return MeetingListItem(
        id=obs.observation_id,
        bot_id=obs.external_id,
        title=obs.title or obs.external_id,
        start_time=_iso(obs.occurred_at),
        time_source=obs.time_source,
        participants=obs.participants,
        attendee_count=len(obs.participants),
        purpose=obs.purpose,
        key_points=obs.key_points,
        summarized=bool(obs.summary),
        has_transcript=bool(obs.raw_content),
        lane=obs.lane,
        signal_counts=_signal_counts(_visible_signals(obs.external_id)),
    )


def _parse_day(value: str | None, name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{name} must be YYYY-MM-DD") from None


def _matches(obs: Observation, q: str) -> bool:
    haystack = " ".join(
        [obs.title or "", obs.purpose or "", *obs.participants, *obs.key_points,
         *(n for names in obs.entities_mentioned.values() for n in names if isinstance(n, str))]
    ).lower()
    return all(term in haystack for term in q.lower().split())


# --- routes ------------------------------------------------------------------


@router.get("/history/list", response_model=MeetingListResponse)
async def list_meetings(
    page_size: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, description="Opaque cursor from next_cursor"),
    start_date: str | None = Query(None, description="YYYY-MM-DD, inclusive (event time)"),
    end_date: str | None = Query(None, description="YYYY-MM-DD, inclusive (event time)"),
    q: str | None = Query(None, description="Words matched against title, purpose, people, entities"),
    has_transcript: bool | None = Query(None),
):
    start, end = _parse_day(start_date, "start_date"), _parse_day(end_date, "end_date")
    meetings = []
    for obs in _all_meetings():
        day = _event_time(obs).date()
        if (start and day < start) or (end and day > end):
            continue
        if has_transcript is not None and bool(obs.raw_content) != has_transcript:
            continue
        if q and not _matches(obs, q):
            continue
        meetings.append(obs)

    try:
        offset = max(0, int(cursor)) if cursor else 0
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid cursor") from None
    page = meetings[offset : offset + page_size]
    nxt = offset + page_size
    return MeetingListResponse(
        items=[_to_item(m) for m in page],
        total=len(meetings),
        next_cursor=str(nxt) if nxt < len(meetings) else None,
        page_size=page_size,
    )


@router.get("/history/stats", response_model=MeetingStats)
async def meeting_stats():
    meetings = _all_meetings()
    if not meetings:
        return MeetingStats()
    return MeetingStats(
        total_meetings=len(meetings),
        meetings_with_transcripts=sum(1 for m in meetings if m.raw_content),
        meetings_summarized=sum(1 for m in meetings if m.summary),
        total_signals=sum(len(_visible_signals(m.external_id)) for m in meetings),
        first_meeting=_iso(meetings[-1].occurred_at),
        last_meeting=_iso(meetings[0].occurred_at),
    )


@router.get("/{bot_id}/content", response_model=MeetingContent)
async def get_meeting_content(bot_id: str):
    if not _BOT_ID.match(bot_id):
        raise HTTPException(status_code=400, detail="invalid bot_id")
    obs = _load(MEETINGS_DIR / f"meeting-{bot_id}.md")
    if obs is None:
        raise HTTPException(status_code=404, detail=f"meeting {bot_id} not found")

    signals = sorted(_visible_signals(bot_id), key=lambda s: s.position)
    em = obs.entities_mentioned or {}

    def n(*keys: str) -> int:
        return len({x for k in keys for x in (em.get(k) or []) if isinstance(x, str)})

    return MeetingContent(
        bot_id=obs.external_id,
        meeting_id=obs.observation_id,
        title=obs.title,
        body=_summary_body(obs),
        purpose=obs.purpose,
        key_points=obs.key_points,
        summarized=bool(obs.summary),
        transcript=obs.raw_content,
        updated_at=obs.observed_at.isoformat(),
        participants=obs.participants,
        start_time=_iso(obs.occurred_at),
        time_source=obs.time_source,
        entities_mentioned={k: v for k, v in em.items() if isinstance(v, list)},
        entity_counts=EntityCounts(
            people=n("person", "people"),
            projects=n("project", "projects"),
            accounts=n("account", "accounts"),
            action_items=sum(1 for s in signals if s.type == "action_item"),
            decisions=sum(1 for s in signals if s.type == "decision"),
        ),
        signals=[
            MeetingSignal(
                id=s.id, type=s.type, content=s.content,
                owner=s.owner.name if s.owner else None, status=s.status, position=s.position,
            )
            for s in signals
        ],
        is_finalized=obs.is_finalized,
        status=obs.status,
    )
