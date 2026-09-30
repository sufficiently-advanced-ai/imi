"""Event time on evidence (ADR-004) — pure helpers, no I/O.

Evidence (documents, signals, relationship assertions) carries two times:

* ``occurred_at`` — when it happened. Point-in-time queries filter on this.
* ``recorded_at`` — when imi ingested it. Never a proxy for event time:
  content is routinely backfilled.

``time_source`` records how ``occurred_at`` was obtained, so an undated
backfill is distinguishable from a real timestamp.

Files keep ISO-8601 strings; the graph stores native ``DATETIME`` values
normalised to UTC. Conversion happens here, at the graph write.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

TIME_SOURCE_EXPLICIT = "explicit"  # caller supplied request.timestamp
TIME_SOURCE_CONTENT = "content_header"  # recovered from the content itself
TIME_SOURCE_INFERRED = "inferred"  # derived from other evidence
TIME_SOURCE_FALLBACK = "fallback_now"  # no date found; ingest time was used
TIME_SOURCE_UNRECORDED = "unrecorded"  # written before ADR-004; source not kept

TIME_SOURCES = frozenset(
    {
        TIME_SOURCE_EXPLICIT,
        TIME_SOURCE_CONTENT,
        TIME_SOURCE_INFERRED,
        TIME_SOURCE_FALLBACK,
        TIME_SOURCE_UNRECORDED,
    }
)

# Frontmatter key holding per-assertion relationship evidence. The typed keys
# (``works_on_projects: [project-x]``) stay as they are — they are the current
# relationship list every existing reader parses.
ASSERTIONS_KEY = "relationship_assertions"


def to_utc(value: Any) -> datetime | None:
    """Parse a file/API time value into an aware UTC datetime.

    Accepts datetimes, dates and ISO-8601 strings (``Z`` or an offset). A
    value without a timezone is taken as UTC. Returns None when unparseable.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    elif hasattr(value, "to_native"):  # neo4j.time.DateTime / Date
        return to_utc(value.to_native())
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_iso(value: Any) -> str | None:
    """UTC ISO-8601 string for a time value, or None."""
    dt = to_utc(value)
    return dt.isoformat() if dt else None


def normalize_temporal(value: Any) -> Any:
    """Replace Neo4j temporal values with ISO-8601 strings, recursively.

    Applied at the driver boundary so readers keep receiving strings after
    the stored type became ``DATETIME``.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {k: normalize_temporal(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [normalize_temporal(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_native") and hasattr(value, "iso_format"):
        native = value.to_native()
        return native.isoformat() if hasattr(native, "isoformat") else value.iso_format()
    return value


def validate_time_source(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in TIME_SOURCES else TIME_SOURCE_UNRECORDED


def is_observation_document(metadata: dict[str, Any]) -> bool:
    """True for documents produced by ingest (meetings, conversations, library
    documents). Entity profiles and hand-written notes are not evidence."""
    return bool(metadata.get("meeting_id") or metadata.get("bot_id"))


def document_event_time(metadata: dict[str, Any]) -> dict[str, Any]:
    """Event-time properties for a Document node, read from its frontmatter.

    ``start_time`` is the file key for ``occurred_at`` (kept so existing
    knowledge repos stay valid). ``updated_at`` on an observation document is
    the observation time and is used when ``start_time`` is absent. Returns
    an empty dict for documents that carry no event time.
    """
    occurred = to_utc(metadata.get("occurred_at")) or to_utc(metadata.get("start_time"))
    if occurred is None and is_observation_document(metadata):
        occurred = to_utc(metadata.get("updated_at"))
    if occurred is None:
        occurred = to_utc(metadata.get("date"))
    if occurred is None:
        return {}
    props: dict[str, Any] = {
        "occurred_at": occurred,
        "time_source": validate_time_source(metadata.get("time_source")),
    }
    recorded = to_utc(metadata.get("recorded_at"))
    if recorded is not None:
        props["recorded_at"] = recorded
    return props


def signal_event_time(signal: Any) -> datetime | None:
    """When a signal became true: ``valid_from``, else ``source_timestamp``."""
    return to_utc(getattr(signal, "valid_from", None)) or to_utc(
        getattr(signal, "source_timestamp", None)
    )


def make_assertion(
    rel_type: str,
    target: str,
    *,
    source_id: str,
    occurred_at: Any,
    time_source: str,
    recorded_at: Any = None,
) -> dict[str, Any]:
    """One frontmatter assertion entry (ISO strings, stable key order)."""
    entry: dict[str, Any] = {
        "type": rel_type,
        "target": target,
        "source_id": source_id,
    }
    iso = to_iso(occurred_at)
    if iso:
        entry["occurred_at"] = iso
    entry["time_source"] = validate_time_source(time_source)
    rec = to_iso(recorded_at)
    if rec:
        entry["recorded_at"] = rec
    return entry


def read_assertions(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Well-formed assertion entries from an entity file's frontmatter."""
    raw = metadata.get(ASSERTIONS_KEY)
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rel_type = str(item.get("type") or "").strip().lower()
        target = str(item.get("target") or "").strip()
        source_id = str(item.get("source_id") or "").strip()
        if not rel_type or not target or not source_id:
            continue
        out.append({**item, "type": rel_type, "target": target, "source_id": source_id})
    return out


def assertions_for(
    metadata: dict[str, Any], rel_type: str, target: str
) -> list[dict[str, Any]]:
    """Assertions in ``metadata`` for one ``(type, target)`` pair."""
    rel_type = rel_type.strip().lower()
    return [
        a
        for a in read_assertions(metadata)
        if a["type"] == rel_type and a["target"] == target
    ]


def merge_assertion(
    metadata: dict[str, Any], assertion: dict[str, Any]
) -> bool:
    """Add ``assertion`` to ``metadata`` unless the same ``(type, target,
    source_id)`` is already recorded. Returns True when the file changed."""
    existing = metadata.get(ASSERTIONS_KEY)
    if not isinstance(existing, list):
        existing = []
    key = (assertion["type"], assertion["target"], assertion["source_id"])
    for item in existing:
        if isinstance(item, dict) and (
            str(item.get("type") or "").strip().lower(),
            str(item.get("target") or "").strip(),
            str(item.get("source_id") or "").strip(),
        ) == key:
            return False
    existing.append(assertion)
    metadata[ASSERTIONS_KEY] = existing
    return True


def drop_assertions(metadata: dict[str, Any], rel_type: str, target: str) -> bool:
    """Remove every assertion for ``(type, target)``. True when changed."""
    existing = metadata.get(ASSERTIONS_KEY)
    if not isinstance(existing, list):
        return False
    rel_type = rel_type.strip().lower()
    kept = [
        item
        for item in existing
        if not (
            isinstance(item, dict)
            and str(item.get("type") or "").strip().lower() == rel_type
            and str(item.get("target") or "").strip() == target
        )
    ]
    if len(kept) == len(existing):
        return False
    if kept:
        metadata[ASSERTIONS_KEY] = kept
    else:
        metadata.pop(ASSERTIONS_KEY, None)
    return True


def edge_event_props(assertion: dict[str, Any] | None) -> dict[str, Any]:
    """Graph properties for one relationship edge.

    An edge with no assertion (a bare id in the typed list) gets an empty
    ``source_id`` and no ``occurred_at``: it exists in the current graph and
    is left out of point-in-time queries.
    """
    if not assertion:
        return {"source_id": ""}
    props: dict[str, Any] = {
        "source_id": assertion["source_id"],
        "time_source": validate_time_source(assertion.get("time_source")),
    }
    occurred = to_utc(assertion.get("occurred_at"))
    if occurred is not None:
        props["occurred_at"] = occurred
    recorded = to_utc(assertion.get("recorded_at"))
    if recorded is not None:
        props["recorded_at"] = recorded
    return props
