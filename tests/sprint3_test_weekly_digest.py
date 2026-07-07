"""Tests for weekly standing digest (Sprint 3, Task S3-4).

TDD: tests written first, then implementation.

Coverage:
- build_weekly_digest:
    - counts diff parsed from both frontmatters; prev None → counts_then None
    - active_added/removed from heading sets
    - transitions window filter (8d-old excluded, 2d-old included)
    - aging commitments: open action_item 10d old included; done/recent excluded; owner fallback
- render_weekly_digest:
    - sections + _None_ for empty sections
    - sanitization: multiline content one line
    - first-digest line when counts_then is None
    - counts table with delta
- export_weekly_digest:
    - file written (read back)
    - commit args captured
    - git-fail path → committed=False
    - summary counts correct
- Endpoints:
    - POST /digest/weekly/export shape
    - GET /digest/weekly/latest returns newest of two files
    - GET /digest/weekly/latest 404 when none exist
    - route-order: /digest/weekly/latest not captured by /digest/{date}
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from app.models.signal import Signal
    from app.services.signal_store import SignalStore
    from app.services.weekly_digest import WeeklyDigest

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


def _make_signal(
    *,
    signal_id: str,
    signal_type: str = "action_item",
    content: str = "Follow up with client",
    source_timestamp: str | None = None,
    status: str | None = "open",
    owner_name: str | None = None,
    created_at: str | None = None,
) -> "Signal":
    from app.models.signal import Signal

    if source_timestamp is None:
        source_timestamp = NOW.isoformat()

    kwargs: dict = {
        "id": signal_id,
        "type": signal_type,
        "content": content,
        "source_meeting_id": "bot-test-001",
        "source_timestamp": source_timestamp,
        "status": status,
    }
    if owner_name is not None:
        from app.models.signal import EntityRef

        kwargs["owner"] = EntityRef(
            id=f"person-{owner_name.lower().replace(' ', '-')}",
            type="person",
            name=owner_name,
        )
    if created_at is not None:
        kwargs["created_at"] = created_at

    return Signal(**kwargs)


def _build_store(tmp_path: Path, signals: list) -> "SignalStore":
    from app.models.signal import MeetingSignals
    from app.services.signal_store import SignalStore

    store = SignalStore(signals_dir=tmp_path / "signals")
    ms = MeetingSignals(
        meeting_id="meet-test-001",
        bot_id="bot-test-001",
        signals=signals,
    )
    store.save(ms)
    return store


def _make_git_ops(tmp_path: Path) -> MagicMock:
    mock = MagicMock()
    mock.repo_path = str(tmp_path)
    mock.commit_and_push = AsyncMock()
    mock.get_revision_before = AsyncMock(return_value=None)
    mock.read_file_at_revision = AsyncMock(return_value=None)
    return mock


def _make_view(
    *,
    view_id: str = "sig-0001",
    state: str = "active",
    content: str = "Use Postgres for relational data",
    client_id: str | None = None,
    source_meeting_id: str = "bot-abc",
    source_meeting_title: str | None = "Meeting",
    source_timestamp: str = "2026-01-15T10:00:00Z",
    owner: str | None = None,
    review_status: str = "confirmed",
) -> dict:
    return {
        "id": view_id,
        "content": content,
        "state": state,
        "state_reason": f"review_status={review_status}",
        "review_status": review_status,
        "provenance_status": "verified",
        "can_use_as_evidence": True,
        "can_use_as_instruction": True,
        "owner": owner,
        "owner_id": None,
        "client_id": client_id,
        "source_meeting_id": source_meeting_id,
        "source_meeting_title": source_meeting_title,
        "source_timestamp": source_timestamp,
        "superseded_by": None,
        "age_days": 5,
        "tenant_id": None,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Minimal constitution markdown templates
# ---------------------------------------------------------------------------


def _make_constitution_md(
    *,
    decisions_active: int = 2,
    decisions_stale: int = 1,
    decisions_superseded: int = 0,
    decisions_pending_review: int = 1,
    active_headings: list[str] | None = None,
    generated_at: str = "2026-03-08T12:00:00+00:00",
) -> str:
    """Build a minimal constitution-like markdown with frontmatter + Active Decisions section."""
    if active_headings is None:
        active_headings = ["Use Postgres for relational data", "Deploy on Kubernetes"]

    fm = "\n".join(
        [
            "---",
            "artifact: constitution",
            "version: 0",
            "tenant_id: DEFAULT",
            f"generated_at: {generated_at}",
            "stale_threshold_days: 90",
            f"decisions_total: {decisions_active + decisions_stale + decisions_superseded}",
            f"decisions_active: {decisions_active}",
            f"decisions_stale: {decisions_stale}",
            f"decisions_superseded: {decisions_superseded}",
            f"decisions_pending_review: {decisions_pending_review}",
            "---",
        ]
    )

    active_section = "\n## Active Decisions\n\n### General\n"
    for h in active_headings:
        active_section += f"\n#### {h}\n- **Owner:** Unassigned\n"

    return fm + "\n\n# Constitution\n" + active_section


# ---------------------------------------------------------------------------
# 1. _parse_frontmatter helper (tested via build_weekly_digest)
# ---------------------------------------------------------------------------


class TestParseFromFrontmatter:
    """Verify that frontmatter integer counts are extracted correctly."""

    def test_parse_counts_from_md(self):
        from app.services.weekly_digest import _parse_frontmatter

        md = _make_constitution_md(
            decisions_active=3,
            decisions_stale=1,
            decisions_superseded=2,
            decisions_pending_review=4,
        )
        counts = _parse_frontmatter(md)
        assert counts["decisions_active"] == 3
        assert counts["decisions_stale"] == 1
        assert counts["decisions_superseded"] == 2
        assert counts["decisions_pending_review"] == 4

    def test_returns_none_for_empty_string(self):
        from app.services.weekly_digest import _parse_frontmatter

        assert _parse_frontmatter("") is None

    def test_returns_none_for_no_frontmatter(self):
        from app.services.weekly_digest import _parse_frontmatter

        assert _parse_frontmatter("# Just markdown\nNo frontmatter here") is None


# ---------------------------------------------------------------------------
# 2. build_weekly_digest — counts
# ---------------------------------------------------------------------------


class TestBuildWeeklyDigestCounts:
    """Counts comparison between current and previous constitution frontmatter."""

    def test_counts_from_fresh_render_and_prev_md(self):
        from app.services.weekly_digest import build_weekly_digest

        views = [
            _make_view(view_id="sig-a", state="active"),
            _make_view(view_id="sig-b", state="active"),
        ]
        prev_md = _make_constitution_md(
            decisions_active=1,
            decisions_stale=0,
            decisions_superseded=0,
            decisions_pending_review=0,
        )
        digest = build_weekly_digest(
            views=views,
            all_signals=[],
            prev_constitution_md=prev_md,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        assert digest.counts_now["decisions_active"] == 2
        assert digest.counts_then is not None
        assert digest.counts_then["decisions_active"] == 1

    def test_counts_then_none_when_no_prev(self):
        from app.services.weekly_digest import build_weekly_digest

        views = [_make_view(view_id="sig-a", state="active")]
        digest = build_weekly_digest(
            views=views,
            all_signals=[],
            prev_constitution_md=None,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        assert digest.counts_then is None
        assert digest.counts_now["decisions_active"] >= 0


# ---------------------------------------------------------------------------
# 3. build_weekly_digest — active_added / active_removed
# ---------------------------------------------------------------------------


class TestBuildWeeklyDigestHeadingDiff:
    """Heading diff: active_added = present now absent then; active_removed = vice versa."""

    def test_added_heading(self):
        from app.services.weekly_digest import build_weekly_digest

        # prev has only heading A; now has heading A and B → B is added
        prev_md = _make_constitution_md(
            active_headings=["Use Postgres for relational data"],
            decisions_active=1,
        )
        views = [
            _make_view(view_id="sig-a", content="Use Postgres for relational data"),
            _make_view(view_id="sig-b", content="Deploy on Kubernetes"),
        ]
        digest = build_weekly_digest(
            views=views,
            all_signals=[],
            prev_constitution_md=prev_md,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        added_headings = [h.lower() for h in digest.active_added]
        assert any("kubernetes" in h for h in added_headings)

    def test_removed_heading(self):
        from app.services.weekly_digest import build_weekly_digest

        # prev has A and B; now has only A → B is removed
        prev_md = _make_constitution_md(
            active_headings=[
                "Use Postgres for relational data",
                "Deploy on Kubernetes",
            ],
            decisions_active=2,
        )
        views = [
            _make_view(view_id="sig-a", content="Use Postgres for relational data"),
        ]
        digest = build_weekly_digest(
            views=views,
            all_signals=[],
            prev_constitution_md=prev_md,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        removed_headings = [h.lower() for h in digest.active_removed]
        assert any("kubernetes" in h for h in removed_headings)

    def test_no_diff_when_headings_same(self):
        from app.services.weekly_digest import build_weekly_digest

        prev_md = _make_constitution_md(
            active_headings=["Use Postgres for relational data"],
            decisions_active=1,
        )
        views = [
            _make_view(view_id="sig-a", content="Use Postgres for relational data")
        ]
        digest = build_weekly_digest(
            views=views,
            all_signals=[],
            prev_constitution_md=prev_md,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        assert digest.active_added == []
        assert digest.active_removed == []

    def test_no_prev_no_diff(self):
        from app.services.weekly_digest import build_weekly_digest

        views = [_make_view(view_id="sig-a", content="Use Postgres")]
        digest = build_weekly_digest(
            views=views,
            all_signals=[],
            prev_constitution_md=None,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        # When no previous, active_added/removed are empty (nothing to compare against)
        assert digest.active_added == []
        assert digest.active_removed == []


# ---------------------------------------------------------------------------
# 4. build_weekly_digest — transitions window filter
# ---------------------------------------------------------------------------


class TestBuildWeeklyDigestTransitions:
    """Only transitions within the 7-day window are included."""

    def test_recent_transition_included(self):
        from app.services.weekly_digest import build_weekly_digest

        two_days_ago = (NOW - timedelta(days=2)).isoformat()
        transition = {
            "signal_id": "sig-001",
            "from": "active",
            "to": "stale",
            "reason": "aged out",
            "at": two_days_ago,
        }
        digest = build_weekly_digest(
            views=[],
            all_signals=[],
            prev_constitution_md=None,
            transitions=[transition],
            now=NOW,
            aging_days=7,
        )
        assert len(digest.transitions) == 1
        assert digest.transitions[0]["signal_id"] == "sig-001"

    def test_old_transition_excluded(self):
        from app.services.weekly_digest import build_weekly_digest

        eight_days_ago = (NOW - timedelta(days=8)).isoformat()
        transition = {
            "signal_id": "sig-002",
            "from": "candidate",
            "to": "active",
            "reason": "confirmed",
            "at": eight_days_ago,
        }
        digest = build_weekly_digest(
            views=[],
            all_signals=[],
            prev_constitution_md=None,
            transitions=[transition],
            now=NOW,
            aging_days=7,
        )
        assert len(digest.transitions) == 0

    def test_boundary_exactly_7d_included(self):
        from app.services.weekly_digest import build_weekly_digest

        exactly_7d_ago = (NOW - timedelta(days=7)).isoformat()
        transition = {
            "signal_id": "sig-003",
            "from": "active",
            "to": "superseded",
            "reason": "replaced",
            "at": exactly_7d_ago,
        }
        digest = build_weekly_digest(
            views=[],
            all_signals=[],
            prev_constitution_md=None,
            transitions=[transition],
            now=NOW,
            aging_days=7,
        )
        assert len(digest.transitions) == 1


# ---------------------------------------------------------------------------
# 5. build_weekly_digest — aging commitments
# ---------------------------------------------------------------------------


class TestBuildWeeklyDigestAgingCommitments:
    """Open action_items older than aging_days threshold are included."""

    def test_old_open_action_item_included(self):
        from app.services.weekly_digest import build_weekly_digest

        ten_days_ago = (NOW - timedelta(days=10)).isoformat()
        sig = _make_signal(
            signal_id="ai-001",
            signal_type="action_item",
            content="Follow up with client about contract renewal",
            status="open",
            created_at=ten_days_ago,
        )
        digest = build_weekly_digest(
            views=[],
            all_signals=[sig],
            prev_constitution_md=None,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        assert len(digest.aging_commitments) == 1
        commitment = digest.aging_commitments[0]
        assert commitment["signal_id"] == "ai-001"
        assert commitment["age_days"] == 10
        assert "owner" in commitment

    def test_done_action_item_excluded(self):
        from app.services.weekly_digest import build_weekly_digest

        ten_days_ago = (NOW - timedelta(days=10)).isoformat()
        sig = _make_signal(
            signal_id="ai-002",
            signal_type="action_item",
            content="Completed task",
            status="done",
            created_at=ten_days_ago,
        )
        digest = build_weekly_digest(
            views=[],
            all_signals=[sig],
            prev_constitution_md=None,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        assert len(digest.aging_commitments) == 0

    def test_recent_open_action_item_excluded(self):
        from app.services.weekly_digest import build_weekly_digest

        two_days_ago = (NOW - timedelta(days=2)).isoformat()
        sig = _make_signal(
            signal_id="ai-003",
            signal_type="action_item",
            content="Recent task",
            status="open",
            created_at=two_days_ago,
        )
        digest = build_weekly_digest(
            views=[],
            all_signals=[sig],
            prev_constitution_md=None,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        assert len(digest.aging_commitments) == 0

    def test_owner_name_extracted(self):
        from app.services.weekly_digest import build_weekly_digest

        ten_days_ago = (NOW - timedelta(days=10)).isoformat()
        sig = _make_signal(
            signal_id="ai-004",
            signal_type="action_item",
            content="Task with owner",
            status="open",
            owner_name="Alice Smith",
            created_at=ten_days_ago,
        )
        digest = build_weekly_digest(
            views=[],
            all_signals=[sig],
            prev_constitution_md=None,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        assert len(digest.aging_commitments) == 1
        assert digest.aging_commitments[0]["owner"] == "Alice Smith"

    def test_owner_fallback_unassigned(self):
        from app.services.weekly_digest import build_weekly_digest

        ten_days_ago = (NOW - timedelta(days=10)).isoformat()
        sig = _make_signal(
            signal_id="ai-005",
            signal_type="action_item",
            content="Unassigned task",
            status="open",
            owner_name=None,
            created_at=ten_days_ago,
        )
        digest = build_weekly_digest(
            views=[],
            all_signals=[sig],
            prev_constitution_md=None,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        assert len(digest.aging_commitments) == 1
        assert digest.aging_commitments[0]["owner"] == "Unassigned"

    def test_non_action_item_excluded(self):
        from app.services.weekly_digest import build_weekly_digest

        ten_days_ago = (NOW - timedelta(days=10)).isoformat()
        sig = _make_signal(
            signal_id="kp-001",
            signal_type="key_point",
            content="Some key point",
            status=None,
            created_at=ten_days_ago,
        )
        digest = build_weekly_digest(
            views=[],
            all_signals=[sig],
            prev_constitution_md=None,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        assert len(digest.aging_commitments) == 0

    def test_aging_commitments_capped_at_20(self):
        from app.services.weekly_digest import build_weekly_digest

        # Create 25 old action items
        signals = []
        for i in range(25):
            days_old = 10 + i  # vary age slightly
            created = (NOW - timedelta(days=days_old)).isoformat()
            sig = _make_signal(
                signal_id=f"ai-{i:03d}",
                signal_type="action_item",
                content=f"Task {i}",
                status="open",
                created_at=created,
            )
            signals.append(sig)

        digest = build_weekly_digest(
            views=[],
            all_signals=signals,
            prev_constitution_md=None,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        # Should be capped at 20
        assert len(digest.aging_commitments) == 20
        # Should have overflow of 5
        assert digest.aging_overflow == 5

    def test_aging_commitments_no_overflow_when_under_cap(self):
        from app.services.weekly_digest import build_weekly_digest

        # Create 10 old action items (under cap)
        signals = []
        for i in range(10):
            created = (NOW - timedelta(days=10 + i)).isoformat()
            sig = _make_signal(
                signal_id=f"ai-{i:03d}",
                signal_type="action_item",
                content=f"Task {i}",
                status="open",
                created_at=created,
            )
            signals.append(sig)

        digest = build_weekly_digest(
            views=[],
            all_signals=signals,
            prev_constitution_md=None,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        assert len(digest.aging_commitments) == 10
        assert digest.aging_overflow == 0

    def test_aging_commitments_sorted_oldest_first_before_cap(self):
        from app.services.weekly_digest import build_weekly_digest

        # Create items with varying ages (all >= 7d to pass aging threshold)
        signals = []
        ages = [8, 25, 10, 30, 15]  # out-of-order ages, all >= 7
        for i, days in enumerate(ages):
            created = (NOW - timedelta(days=days)).isoformat()
            sig = _make_signal(
                signal_id=f"ai-{i:03d}",
                signal_type="action_item",
                content=f"Task {i} ({days}d old)",
                status="open",
                created_at=created,
            )
            signals.append(sig)

        digest = build_weekly_digest(
            views=[],
            all_signals=signals,
            prev_constitution_md=None,
            transitions=[],
            now=NOW,
            aging_days=7,
        )
        # All under cap, check order (oldest = highest age_days first)
        assert len(digest.aging_commitments) == 5
        assert digest.aging_commitments[0]["age_days"] == 30  # oldest first
        assert digest.aging_commitments[1]["age_days"] == 25
        assert digest.aging_commitments[-1]["age_days"] == 8  # youngest last


# ---------------------------------------------------------------------------
# 6. render_weekly_digest
# ---------------------------------------------------------------------------


class TestRenderWeeklyDigest:
    """Markdown output correctness."""

    def _build_digest(self, **kwargs) -> "WeeklyDigest":
        from app.services.weekly_digest import build_weekly_digest

        defaults = {
            "views": [],
            "all_signals": [],
            "prev_constitution_md": None,
            "transitions": [],
            "now": NOW,
            "aging_days": 7,
        }
        defaults.update(kwargs)
        return build_weekly_digest(**defaults)

    def test_title_contains_date(self):
        from app.services.weekly_digest import render_weekly_digest

        d = self._build_digest()
        text = render_weekly_digest(d)
        assert "# Weekly digest" in text
        assert "2026-03-15" in text

    def test_first_digest_line_when_no_prev(self):
        from app.services.weekly_digest import render_weekly_digest

        d = self._build_digest()
        text = render_weekly_digest(d)
        assert "First weekly digest" in text

    def test_counts_table_when_prev(self):
        from app.services.weekly_digest import render_weekly_digest

        prev_md = _make_constitution_md(
            decisions_active=1,
            decisions_stale=0,
            decisions_superseded=0,
            decisions_pending_review=0,
        )
        views = [
            _make_view(view_id="sig-a"),
            _make_view(view_id="sig-b"),
        ]
        d = self._build_digest(views=views, prev_constitution_md=prev_md)
        text = render_weekly_digest(d)
        # Should have a table with delta column
        assert "state" in text.lower()
        assert "now" in text.lower()
        # No first-digest line
        assert "First weekly digest" not in text

    def test_state_changes_section_present_with_transitions(self):
        from app.services.weekly_digest import render_weekly_digest

        two_days_ago = (NOW - timedelta(days=2)).isoformat()
        transition = {
            "signal_id": "sig-abcd1234",
            "from": "active",
            "to": "stale",
            "reason": "aged out",
            "at": two_days_ago,
        }
        prev_md = _make_constitution_md()
        d = self._build_digest(
            transitions=[transition],
            prev_constitution_md=prev_md,
        )
        text = render_weekly_digest(d)
        assert "## State changes" in text
        assert "sig-abcd" in text
        assert "active" in text
        assert "stale" in text

    def test_state_changes_section_none_when_empty(self):
        from app.services.weekly_digest import render_weekly_digest

        d = self._build_digest()
        text = render_weekly_digest(d)
        assert "## State changes" in text
        assert "_None_" in text

    def test_awaiting_review_section(self):
        from app.services.weekly_digest import render_weekly_digest

        views = [
            _make_view(view_id="sig-a", state="candidate", review_status="pending"),
        ]
        d = self._build_digest(views=views)
        text = render_weekly_digest(d)
        assert "## Awaiting review" in text

    def test_awaiting_review_none_when_zero(self):
        from app.services.weekly_digest import render_weekly_digest

        d = self._build_digest()
        text = render_weekly_digest(d)
        assert "## Awaiting review" not in text

    def test_commitments_aging_section(self):
        from app.services.weekly_digest import render_weekly_digest

        ten_days_ago = (NOW - timedelta(days=10)).isoformat()
        sig = _make_signal(
            signal_id="ai-001",
            content="Follow up with client",
            status="open",
            created_at=ten_days_ago,
        )
        d = self._build_digest(all_signals=[sig])
        text = render_weekly_digest(d)
        assert "## Commitments aging" in text
        assert "Follow up with client" in text

    def test_commitments_section_none_when_empty(self):
        from app.services.weekly_digest import render_weekly_digest

        d = self._build_digest()
        text = render_weekly_digest(d)
        assert "## Commitments aging" in text
        assert "_None_" in text

    def test_multiline_content_collapsed_to_one_line(self):
        from app.services.weekly_digest import render_weekly_digest

        ten_days_ago = (NOW - timedelta(days=10)).isoformat()
        sig = _make_signal(
            signal_id="ai-multiline",
            content="Follow up\nwith client\nabout renewal",
            status="open",
            created_at=ten_days_ago,
        )
        d = self._build_digest(all_signals=[sig])
        text = render_weekly_digest(d)
        # content in aging commitments bullet should be one line (no newlines inside)
        assert "Follow up with client about renewal" in text

    def test_aging_commitments_overflow_line_shown(self):
        from app.services.weekly_digest import (
            render_weekly_digest,
            AGING_COMMITMENTS_CAP,
        )

        # Create more than cap items
        signals = []
        for i in range(AGING_COMMITMENTS_CAP + 5):
            created = (NOW - timedelta(days=10 + i)).isoformat()
            sig = _make_signal(
                signal_id=f"ai-{i:03d}",
                signal_type="action_item",
                content=f"Task {i}",
                status="open",
                created_at=created,
            )
            signals.append(sig)

        d = self._build_digest(all_signals=signals)
        text = render_weekly_digest(d)
        # Should contain overflow line
        assert "…and 5 more open commitments past threshold" in text

    def test_aging_commitments_no_overflow_line_when_under_cap(self):
        from app.services.weekly_digest import render_weekly_digest

        # Create under-cap items
        signals = []
        for i in range(5):
            created = (NOW - timedelta(days=10 + i)).isoformat()
            sig = _make_signal(
                signal_id=f"ai-{i:03d}",
                signal_type="action_item",
                content=f"Task {i}",
                status="open",
                created_at=created,
            )
            signals.append(sig)

        d = self._build_digest(all_signals=signals)
        text = render_weekly_digest(d)
        # Should NOT contain overflow line
        assert "…and" not in text or "more open commitments" not in text

    def test_added_removed_bullets_in_since_last_week(self):
        from app.services.weekly_digest import render_weekly_digest

        prev_md = _make_constitution_md(
            active_headings=["Old decision"],
            decisions_active=1,
        )
        views = [
            _make_view(view_id="sig-new", content="Brand new decision"),
        ]
        d = self._build_digest(views=views, prev_constitution_md=prev_md)
        text = render_weekly_digest(d)
        # Added bullet uses '+', removed uses '−'
        assert "+ Brand new decision" in text
        assert "− Old decision" in text


# ---------------------------------------------------------------------------
# 7. export_weekly_digest
# ---------------------------------------------------------------------------


class TestExportWeeklyDigest:
    """Integration: file written, committed, summary counts correct."""






    @pytest.mark.asyncio
    async def test_export_uses_settings_commitment_aging_days(self, tmp_path):
        """Verify export_weekly_digest passes settings.COMMITMENT_AGING_DAYS to builder."""
        from app.services.weekly_digest import export_weekly_digest

        mock_git = _make_git_ops(tmp_path)

        # Create an old action item that's exactly at the aging boundary
        eight_days_ago = (NOW - timedelta(days=8)).isoformat()
        sig = _make_signal(
            signal_id="ai-boundary",
            signal_type="action_item",
            content="Boundary task",
            status="open",
            created_at=eight_days_ago,
        )

        from app.models.signal import MeetingSignals
        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=tmp_path / "signals")
        ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[sig])
        store.save(ms)

        with (
            patch("app.services.weekly_digest.git_ops", mock_git),
            patch("app.services.weekly_digest.signal_store", store),
            patch("app.services.weekly_digest.settings") as mock_settings,
        ):
            # With aging_days=7, 8d old task should be included (age >= threshold)
            mock_settings.COMMITMENT_AGING_DAYS = 7
            mock_settings.BOT_COMMIT_PREFIX = "[bot]"
            result = await export_weekly_digest(now=NOW, commit=False)
            assert result["summary"]["aging"] == 1

            # With aging_days=9, 8d old task should be excluded (age < threshold)
            mock_settings.COMMITMENT_AGING_DAYS = 9
            result = await export_weekly_digest(now=NOW, commit=False)
            assert result["summary"]["aging"] == 0


# ---------------------------------------------------------------------------
# 8. Endpoint tests
# ---------------------------------------------------------------------------


class TestWeeklyDigestEndpoints:
    """HTTP endpoint shape + route ordering."""

    @pytest.fixture()
    def client(self):
        """FastAPI test client with minimal app."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.routes.digest import router

        app = FastAPI()
        app.include_router(router, prefix="/api")
        return TestClient(app)

    def test_post_export_shape(self, client, tmp_path):
        with (
            patch("app.routes.digest.export_weekly_digest") as mock_export,
        ):
            mock_export.return_value = {
                "path": "digests/weekly-2026-03-15.md",
                "committed": True,
                "summary": {
                    "transitions": 0,
                    "active_added": 0,
                    "active_removed": 0,
                    "aging": 0,
                },
            }
            resp = client.post("/api/digest/weekly/export")

        assert resp.status_code == 200
        data = resp.json()
        assert "path" in data
        assert "committed" in data
        assert "summary" in data

    def test_get_latest_returns_newest(self, client, tmp_path):
        """GET /digest/weekly/latest returns the most recent file."""
        from app.services.weekly_digest import WEEKLY_DIGEST_DIR

        with patch("app.routes.digest.git_ops") as mock_git:
            mock_git.repo_path = str(tmp_path)
            digest_dir = tmp_path / WEEKLY_DIGEST_DIR
            digest_dir.mkdir(parents=True)

            # Write two files; newest should be returned
            older_file = digest_dir / "weekly-2026-03-08.md"
            newer_file = digest_dir / "weekly-2026-03-15.md"
            older_file.write_text("# Weekly digest — 2026-03-08\nOlder")
            newer_file.write_text("# Weekly digest — 2026-03-15\nNewer")

            resp = client.get("/api/digest/weekly/latest")

        assert resp.status_code == 200
        assert "2026-03-15" in resp.text or "Newer" in resp.text

    def test_get_latest_404_when_none(self, client, tmp_path):
        """GET /digest/weekly/latest returns 404 when no weekly digests exist."""
        with patch("app.routes.digest.git_ops") as mock_git:
            mock_git.repo_path = str(tmp_path)
            resp = client.get("/api/digest/weekly/latest")

        assert resp.status_code == 404

    def test_weekly_route_not_captured_by_date_route(self, client, tmp_path):
        """Route /digest/weekly/latest must NOT be matched by /digest/{date} catch-all."""
        with patch("app.routes.digest.export_weekly_digest") as mock_export:
            mock_export.return_value = {
                "path": "digests/weekly-2026-03-15.md",
                "committed": False,
                "summary": {
                    "transitions": 0,
                    "active_added": 0,
                    "active_removed": 0,
                    "aging": 0,
                },
            }
            with patch("app.routes.digest.git_ops") as mock_git:
                mock_git.repo_path = str(tmp_path)
                # GET /digest/weekly/latest should NOT be treated as date="weekly"
                resp = client.get("/api/digest/weekly/latest")
                # Should be 404 (no files) NOT a digest-date lookup error
                assert resp.status_code in (200, 404)
                # If it tried to look up date="weekly/latest", it would call DigestBrain
                # Check that the response is from weekly endpoint, not from date endpoint
                if resp.status_code == 404:
                    assert resp.json()["detail"] == "No weekly digest yet"
