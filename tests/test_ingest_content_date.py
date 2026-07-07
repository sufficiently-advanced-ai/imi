"""Ingest stamps the real content date, not ingest time.

Regression guard for the migration bug where the pipeline stamped
datetime.now() into every observation — and thus every signal's
source_timestamp/valid_from and the meeting start_time — instead of the
email's true Date:. See IngestOrchestrator._phase_build_observation and
_parse_content_timestamp.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.ingestion.models import ContentSource, IngestRequest
from app.services.orchestrators.ingest_orchestrator import (
    IngestOrchestrator,
    _parse_content_timestamp,
)


# --- pure helper -----------------------------------------------------------

def test_parse_iso_date_header():
    content = "From: a@b.com\nDate: 2026-03-31T16:21:13\nDirection: inbound\n\nbody"
    dt = _parse_content_timestamp(content)
    assert dt == datetime(2026, 3, 31, 16, 21, 13, tzinfo=UTC)


def test_parse_rfc2822_date_header():
    content = "From: a@b.com\nDate: Wed, 31 Mar 2026 16:21:13 -0400\n\nbody"
    dt = _parse_content_timestamp(content)
    # -0400 normalizes to 20:21:13 UTC
    assert dt == datetime(2026, 3, 31, 20, 21, 13, tzinfo=timezone.utc)


def test_first_date_header_wins_in_thread():
    content = (
        "Date: 2026-05-10T09:00:00\n\nReply...\n\n"
        "> Date: 2026-05-01T08:00:00\n> original"
    )
    assert _parse_content_timestamp(content) == datetime(2026, 5, 10, 9, 0, tzinfo=UTC)


def test_no_date_header_returns_none():
    assert _parse_content_timestamp("just some notes, no header") is None


def test_unparseable_date_skipped():
    # "Date: tomorrow" can't parse; the later valid one is used.
    content = "Date: tomorrow\nDate: 2026-02-20T14:07:53\n"
    assert _parse_content_timestamp(content) == datetime(2026, 2, 20, 14, 7, 53, tzinfo=UTC)


# --- integration: _phase_build_observation ---------------------------------

@pytest.fixture
def orchestrator():
    return IngestOrchestrator(
        classifier=Mock(),
        claude_client=None,
        graph=AsyncMock(),
        signal_writer=AsyncMock(),
        git_ops=Mock(),
    )


@pytest.mark.asyncio
async def test_build_observation_uses_content_date(orchestrator):
    req = IngestRequest(
        content="From: x@y.com\nDate: 2026-03-31T16:21:13\n\nInvoice attached.",
        source=ContentSource.EMAIL,
    )
    obs = await orchestrator._phase_build_observation(req, "ingest-abc123", "email_thread")
    expected = datetime(2026, 3, 31, 16, 21, 13, tzinfo=UTC)
    assert obs.observed_at == expected
    assert obs.occurred_at == expected


@pytest.mark.asyncio
async def test_explicit_timestamp_wins_over_content(orchestrator):
    explicit = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    req = IngestRequest(
        content="Date: 2026-03-31T16:21:13\n\nbody",
        timestamp=explicit,
    )
    obs = await orchestrator._phase_build_observation(req, "ingest-abc123", "email_thread")
    assert obs.observed_at == explicit


@pytest.mark.asyncio
async def test_falls_back_to_now_without_date(orchestrator):
    before = datetime.now(UTC)
    req = IngestRequest(content="notes with no date header at all")
    obs = await orchestrator._phase_build_observation(req, "ingest-abc123", "notes")
    after = datetime.now(UTC)
    assert before <= obs.observed_at <= after
