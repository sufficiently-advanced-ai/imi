"""When a capture's content was published (ADR-004 event time for captures).

By rule, no model. ``source_date`` is the capture's event time; this decides
it at intake and records how it was obtained in ``time_source``:

1. A full date in a web URL path (``/2026/08/19/``, ``/2026-08-31/``) is the
   publisher's own stamp, so it wins for a web page. The intake date for a
   web page is often when it was saved, not published: a 2015 article saved
   in 2026 must not rank as new. When the two agree (within
   ``_AGREE_DAYS``), the intake date is kept for its time of day.
2. The date the intake supplied (feed item date, mail ``Date`` header as the
   forwarder read it) — ``explicit``.
3. A date in the content head: a mail ``Date:`` header, the YouTube
   ``**Date:**`` header, a dated line near the top of an article —
   ``content_header``.
4. None found: no ``source_date``; ``time_source`` is ``fallback_now`` and
   readers fall back to ``created_at`` (ingest time).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.utils.event_time import (
    TIME_SOURCE_CONTENT,
    TIME_SOURCE_EXPLICIT,
    TIME_SOURCE_FALLBACK,
    TIME_SOURCE_INFERRED,
    to_utc,
)

_AGREE_DAYS = 3
_HEAD_LINES = 25  # a dated byline sits near the top, after any scraped nav
# ``/2026/08/19/`` ends at a path or extension boundary (``/2026/09/25-years``
# is a month plus a slug); ``/2026-08-31-x`` is a dated slug.
_URL_DATE = re.compile(
    r"/(19\d\d|20\d\d)(?:/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])(?=[/.]|$)"
    r"|-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?=[/\-_.]|$))"
)
_MAIL_DATE = re.compile(r"^Date:\s*(.+?)\s*$", re.M)
_YOUTUBE_DATE = re.compile(r"\*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2})")
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_LINE_DATE = re.compile(
    r"(?<![\w/])(?:(\d{4})-(\d{2})-(\d{2})"
    r"|(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4}))",
    re.I,
)


def _date(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


def _url_date(source_id: str | None) -> datetime | None:
    if not source_id or "://" not in source_id:
        return None
    m = _URL_DATE.search(source_id.split("?", 1)[0])
    if not m:
        return None
    month, day = (m.group(2), m.group(3)) if m.group(2) else (m.group(4), m.group(5))
    return _date(int(m.group(1)), int(month), int(day))


def _head_date(content: str) -> datetime | None:
    head = "\n".join((content or "").splitlines()[:_HEAD_LINES])
    if m := _MAIL_DATE.search(head):
        try:
            return to_utc(parsedate_to_datetime(m.group(1)))
        except (TypeError, ValueError, IndexError):
            pass
    if m := _YOUTUBE_DATE.search(head):
        return to_utc(m.group(1))
    if m := _LINE_DATE.search(head):
        if m.group(1):
            return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        month = _MONTHS.index(m.group(4)[:3].lower()) + 1
        return _date(int(m.group(6)), month, int(m.group(5)))
    return None


def published_date(
    content: str,
    source: str | None,
    source_id: str | None,
    given: str | None = None,
    *,
    now: datetime | None = None,
) -> tuple[str | None, str]:
    """(``source_date`` ISO string or None, ``time_source``) for a capture."""
    now = now or datetime.now(UTC)
    given_dt = to_utc(given)
    url_dt = _url_date(source_id) if (source or "").lower() not in ("mail", "email", "gmail") else None
    if url_dt is not None and url_dt <= now:
        if given_dt is not None and abs((given_dt - url_dt).days) <= _AGREE_DAYS:
            return given, TIME_SOURCE_EXPLICIT
        return url_dt.isoformat(), TIME_SOURCE_INFERRED
    if given_dt is not None:
        return given, TIME_SOURCE_EXPLICIT
    head_dt = _head_date(content)
    if head_dt is not None and head_dt <= now:
        return head_dt.isoformat(), TIME_SOURCE_CONTENT
    return None, TIME_SOURCE_FALLBACK
