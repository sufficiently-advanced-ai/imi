"""
Signal Feed API - Reverse-chronological feed of persisted meeting signals.

Reads pre-extracted signal JSON files from repo/signals/ (written by the
signal promoter during meeting finalization). Supports filtering by type,
entity, and date range.

GET /api/signals/feed       - Returns signals grouped by day
GET /api/signals/{signal_id} - Returns a single signal by ID
"""

import logging
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.models.signal import MeetingSignals
from app.models.signal import Signal as PersistedSignal
from app.services.signal_store import SignalStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signals", tags=["signals"])


# --- API Response Models (backward-compatible with frontend) ---


class Signal(BaseModel):
    """API-facing signal with flat entity dict for frontend compatibility."""

    id: str = Field(..., description="Unique signal identifier")
    type: str = Field(
        ..., description="Signal type: decision, action_item, key_point, insight"
    )
    content: str = Field(..., description="Signal content text")
    source_meeting_id: str = Field(..., description="Source meeting bot_id")
    source_meeting_title: str | None = Field(
        None, description="Source meeting title"
    )
    source_timestamp: str = Field(..., description="Meeting updated_at timestamp")
    participants: list[str] = Field(
        default_factory=list, description="Meeting participants"
    )
    entities: dict[str, list[str]] = Field(
        default_factory=dict, description="Linked entities grouped by type"
    )
    confidence: float = Field(0.8, description="Extraction confidence")
    status: str | None = Field(
        None, description="For action items: open, in_progress, done"
    )
    owner: str | None = Field(
        None, description="For action items: assigned person"
    )
    position: int = Field(0, description="Document order position within source meeting")
    metadata: dict = Field(default_factory=dict, description="Additional metadata")


class DayGroup(BaseModel):
    """Group of signals for a single day."""

    date: str = Field(..., description="Date in YYYY-MM-DD format")
    label: str = Field(..., description="Human-readable day label")
    signals: list[Signal] = Field(default_factory=list)


class SignalFeedResponse(BaseModel):
    """Response for the signal feed endpoint."""

    days: list[DayGroup] = Field(default_factory=list)
    total_signals: int = Field(0)
    total_meetings: int = Field(0)


# --- Helpers ---


def _to_api_signal(persisted: PersistedSignal) -> Signal:
    """Convert a persisted Signal (with EntityRef list) to the API Signal (with flat dict).

    This maintains backward compatibility with the frontend which expects:
        entities: { "person": ["Sarah Chen"], "project": ["CRM"] }
        owner: "Sarah Chen"  (string, not object)
    """
    # Group EntityRef objects by type
    entities_dict: dict[str, list[str]] = defaultdict(list)
    for ref in persisted.entities:
        if ref.name not in entities_dict[ref.type]:
            entities_dict[ref.type].append(ref.name)

    return Signal(
        id=persisted.id,
        type=persisted.type,
        content=persisted.content,
        source_meeting_id=persisted.source_meeting_id,
        source_meeting_title=persisted.source_meeting_title,
        source_timestamp=persisted.source_timestamp,
        participants=persisted.participants,
        entities=dict(entities_dict),
        confidence=persisted.confidence,
        status=persisted.status,
        owner=persisted.owner.name if persisted.owner else None,
        position=persisted.position,
        metadata=persisted.metadata,
    )


def _format_day_label(date_str: str) -> str:
    """Create human-readable day label."""
    try:
        date = datetime.fromisoformat(date_str)
    except ValueError:
        return date_str
    today = datetime.now().date()
    delta = (today - date.date()).days

    if delta == 0:
        return "Today"
    elif delta == 1:
        return "Yesterday"
    elif delta < 7:
        return date.strftime("%A")  # Day name
    else:
        return date.strftime("%B %d, %Y")


def _matches_entity_filter(persisted: PersistedSignal, entity_id: str) -> bool:
    """Check if a signal references the given entity slug ID."""
    for ref in persisted.entities:
        if ref.id == entity_id:
            return True
    if persisted.owner and persisted.owner.id == entity_id:
        return True
    return False


def _matches_date_filter(
    persisted: PersistedSignal,
    date_from: str | None,
    date_to: str | None,
) -> bool:
    """Check if a signal falls within the date range (inclusive)."""
    try:
        ts = persisted.source_timestamp[:10]  # YYYY-MM-DD
        if len(ts) != 10:
            return True  # Include signals with malformed timestamps
    except (TypeError, IndexError):
        return True  # Include on error rather than silently exclude
    if date_from and ts < date_from:
        return False
    if date_to and ts > date_to:
        return False
    return True


# --- Endpoints ---


@router.get("/feed", response_model=SignalFeedResponse)
async def get_signal_feed(
    signal_type: str | None = Query(
        None, description="Filter by type: decision, action_item, key_point, insight"
    ),
    entity_id: str | None = Query(
        None, description="Filter by entity slug ID (e.g. person-sarah-chen)"
    ),
    date_from: str | None = Query(
        None, description="Filter start date (YYYY-MM-DD, inclusive)"
    ),
    date_to: str | None = Query(
        None, description="Filter end date (YYYY-MM-DD, inclusive)"
    ),
    limit: int = Query(100, ge=1, le=500, description="Max signals to return"),
):
    """
    Get reverse-chronological feed of signals extracted from meetings.

    Reads from persisted JSON files in repo/signals/. Signals are grouped
    by day and sorted newest-first.
    """
    store = SignalStore()
    all_meeting_signals: list[MeetingSignals] = store.load_all()

    if not all_meeting_signals:
        return SignalFeedResponse(days=[], total_signals=0, total_meetings=0)

    # Flatten and filter
    filtered: list[PersistedSignal] = []
    for ms in all_meeting_signals:
        for signal in ms.signals:
            if signal_type and signal.type != signal_type:
                continue
            if entity_id and not _matches_entity_filter(signal, entity_id):
                continue
            if not _matches_date_filter(signal, date_from, date_to):
                continue
            filtered.append(signal)

    # Sort by meeting timestamp descending, then document position ascending
    filtered.sort(key=lambda s: (s.source_meeting_id, s.position))
    filtered.sort(key=lambda s: s.source_timestamp, reverse=True)

    # Apply limit
    filtered = filtered[:limit]

    # Convert to API models and group by day
    day_groups: dict[str, DayGroup] = {}
    for persisted in filtered:
        api_signal = _to_api_signal(persisted)
        try:
            date_str = api_signal.source_timestamp[:10]
        except (IndexError, TypeError):
            continue

        if date_str not in day_groups:
            day_groups[date_str] = DayGroup(
                date=date_str,
                label=_format_day_label(api_signal.source_timestamp),
                signals=[],
            )
        day_groups[date_str].signals.append(api_signal)

    sorted_days = sorted(day_groups.values(), key=lambda d: d.date, reverse=True)

    return SignalFeedResponse(
        days=sorted_days,
        total_signals=len(filtered),
        total_meetings=len(all_meeting_signals),
    )


@router.get("/{signal_id}", response_model=Signal)
async def get_signal_by_id(signal_id: str):
    """Get a single signal by its ID. Scans all persisted signal files."""
    store = SignalStore()
    for ms in store.load_all():
        for signal in ms.signals:
            if signal.id == signal_id:
                return _to_api_signal(signal)

    raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
