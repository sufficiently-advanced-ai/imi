"""Tests for decision audit artifact service and routes (Issue #954, Task 9 — R4.2).

Tests run in TDD order:
1. render_decision_audit — pure function tests (frontmatter, table, stale, superseded,
   None sections, sanitization)
2. export_decision_audit — async integration (file path, commit args, commit=False,
   git failure)
3. HTTP endpoint — POST /api/decisions/audit/export shape
4. Route order — audit/export not captured by /{decision_id}
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers for building minimal decision-view dicts + stats dicts
# ---------------------------------------------------------------------------

_COUNTER = 0


def _make_view(
    *,
    state: str = "active",
    state_reason: str = "review_status=confirmed",
    content: str = "We will use Postgres for all relational data",
    owner: str | None = "Alice",
    client_id: str | None = "acme",
    source_meeting_id: str = "bot-abc123",
    source_meeting_title: str | None = "Product Sync",
    source_timestamp: str = "2026-01-15T10:00:00Z",
    superseded_by: str | None = None,
    can_use_as_instruction: bool = True,
    can_use_as_evidence: bool = True,
    age_days: int = 5,
    metadata: dict | None = None,
    view_id: str | None = None,
) -> dict:
    global _COUNTER
    _COUNTER += 1
    return {
        "id": view_id or f"sig-{_COUNTER:04d}",
        "content": content,
        "state": state,
        "state_reason": state_reason,
        "review_status": "confirmed" if state == "active" else state,
        "provenance_status": "verified",
        "can_use_as_evidence": can_use_as_evidence,
        "can_use_as_instruction": can_use_as_instruction,
        "owner": owner,
        "owner_id": "alice" if owner else None,
        "client_id": client_id,
        "source_meeting_id": source_meeting_id,
        "source_meeting_title": source_meeting_title,
        "source_timestamp": source_timestamp,
        "superseded_by": superseded_by,
        "age_days": age_days,
        "tenant_id": None,
        "metadata": metadata or {},
    }


def _make_stats(
    meetings: int = 2,
    decisions: int = 3,
    stale: int = 1,
    superseded: int = 1,
    counts_by_state: dict | None = None,
) -> dict:
    """Build a stats dict as returned by compute_decision_stats."""
    cbs = counts_by_state or {
        "active": decisions - stale - superseded,
        "stale": stale,
        "superseded": superseded,
    }
    headline = (
        f"Across {meetings} meetings: {decisions} decisions, "
        f"{stale} stale, {superseded} superseded"
    )
    return {
        "meetings": meetings,
        "decisions": decisions,
        "counts_by_state": cbs,
        "stale": stale,
        "superseded": superseded,
        "headline": headline,
    }


# ---------------------------------------------------------------------------
# 1. render_decision_audit — pure-function tests
# ---------------------------------------------------------------------------


class TestRenderDecisionAudit:
    """Pure render_decision_audit unit tests — no I/O."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.services.decision_audit_artifact import render_decision_audit

        self.render = render_decision_audit

    # convenience: parse frontmatter
    def _parse_frontmatter(self, text: str) -> dict:
        lines = text.split("\n")
        assert lines[0].strip() == "---", f"Expected '---', got {lines[0]!r}"
        end = lines.index("---", 1)
        fm: dict = {}
        for line in lines[1:end]:
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()
        return fm

    # ------------------------------------------------------------------
    # Frontmatter
    # ------------------------------------------------------------------

    def test_frontmatter_artifact_key(self):
        out = self.render(_make_stats(), [], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["artifact"] == "decision-audit"

    def test_frontmatter_version_zero(self):
        out = self.render(_make_stats(), [], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["version"] == "0"

    def test_frontmatter_tenant_id_explicit(self):
        out = self.render(_make_stats(), [], tenant_id="t1")
        fm = self._parse_frontmatter(out)
        assert fm["tenant_id"] == "t1"

    def test_frontmatter_tenant_id_none_renders_DEFAULT(self):
        out = self.render(_make_stats(), [], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["tenant_id"] == "DEFAULT"

    def test_frontmatter_generated_at_uses_now(self):
        fixed_now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
        out = self.render(_make_stats(), [], tenant_id=None, now=fixed_now)
        fm = self._parse_frontmatter(out)
        assert "2026-06-11" in fm["generated_at"]

    # ------------------------------------------------------------------
    # Heading
    # ------------------------------------------------------------------

    def test_heading_contains_date(self):
        fixed_now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
        out = self.render(_make_stats(), [], tenant_id=None, now=fixed_now)
        assert "# Decision Audit — 2026-06-11" in out

    # ------------------------------------------------------------------
    # Headline rendering
    # ------------------------------------------------------------------

    def test_headline_exact(self):
        """Headline must be bolded with trailing period."""
        stats = _make_stats(meetings=2, decisions=3, stale=1, superseded=1)
        out = self.render(stats, [], tenant_id=None)
        assert "**Across 2 meetings: 3 decisions, 1 stale, 1 superseded.**" in out

    def test_headline_period_appended(self):
        """Period is appended even when original headline ends without one."""
        stats = _make_stats(meetings=0, decisions=0, stale=0, superseded=0)
        # headline from _make_stats won't end in period by construction
        out = self.render(stats, [], tenant_id=None)
        # Find the bold headline line
        bold_line = next(
            (line for line in out.splitlines() if line.startswith("**Across")), None
        )
        assert bold_line is not None
        assert bold_line.endswith(".**")

    # ------------------------------------------------------------------
    # State table
    # ------------------------------------------------------------------

    def test_state_table_has_header_row(self):
        out = self.render(_make_stats(), [], tenant_id=None)
        assert "| State | Count |" in out
        assert "|-------|-------|" in out

    def test_state_table_rows_sorted_alphabetically(self):
        stats = _make_stats(
            counts_by_state={"superseded": 2, "active": 9, "stale": 1},
            decisions=12,
            stale=1,
            superseded=2,
        )
        out = self.render(stats, [], tenant_id=None)
        active_pos = out.index("| active |")
        stale_pos = out.index("| stale |")
        superseded_pos = out.index("| superseded |")
        assert active_pos < stale_pos < superseded_pos

    def test_state_table_row_values(self):
        stats = _make_stats(
            counts_by_state={"active": 9},
            decisions=9,
            stale=0,
            superseded=0,
        )
        out = self.render(stats, [], tenant_id=None)
        assert "| active | 9 |" in out

    def test_zero_count_states_omitted(self):
        """States with count 0 must NOT appear in the table."""
        stats = _make_stats(
            counts_by_state={"active": 5, "stale": 0, "superseded": 0},
            decisions=5,
            stale=0,
            superseded=0,
        )
        out = self.render(stats, [], tenant_id=None)
        assert "| active | 5 |" in out
        # stale and superseded lines must not appear
        lines = out.splitlines()
        table_lines = [ln for ln in lines if ln.startswith("| ") and "stale" in ln]
        assert table_lines == [], f"Unexpected stale row: {table_lines}"
        table_lines2 = [
            ln for ln in lines if ln.startswith("| ") and "superseded" in ln
        ]
        assert table_lines2 == [], f"Unexpected superseded row: {table_lines2}"

    # ------------------------------------------------------------------
    # Stale section
    # ------------------------------------------------------------------

    def test_stale_section_heading(self):
        stale_view = _make_view(state="stale", state_reason="age > threshold")
        stats = _make_stats(stale=1, decisions=2)
        out = self.render(stats, [stale_view], tenant_id=None)
        assert "## Stale" in out

    def test_stale_entry_link(self):
        stale_view = _make_view(
            state="stale",
            state_reason="age > threshold",
            source_meeting_id="bot-xyz123",
        )
        out = self.render(
            _make_stats(stale=1, decisions=2), [stale_view], tenant_id=None
        )
        assert "signals/meeting-bot-xyz123.json" in out

    def test_stale_entry_content_first_line(self):
        stale_view = _make_view(
            state="stale",
            state_reason="age > threshold",
            content="Use Redis for sessions\nThis is detail",
        )
        out = self.render(
            _make_stats(stale=1, decisions=2), [stale_view], tenant_id=None
        )
        # first line of content must appear in a bullet
        assert "Use Redis for sessions" in out

    def test_stale_entry_state_reason(self):
        stale_view = _make_view(
            state="stale",
            state_reason="age 142d > 90d threshold",
        )
        out = self.render(
            _make_stats(stale=1, decisions=2), [stale_view], tenant_id=None
        )
        assert "age 142d > 90d threshold" in out

    def test_stale_entry_owner_named(self):
        stale_view = _make_view(state="stale", owner="Bob")
        out = self.render(
            _make_stats(stale=1, decisions=2), [stale_view], tenant_id=None
        )
        assert "owner Bob" in out

    def test_stale_entry_owner_unassigned_fallback(self):
        stale_view = _make_view(state="stale", owner=None)
        out = self.render(
            _make_stats(stale=1, decisions=2), [stale_view], tenant_id=None
        )
        assert "owner Unassigned" in out

    def test_stale_entries_newest_first(self):
        older = _make_view(
            state="stale",
            source_timestamp="2026-01-01T00:00:00Z",
            content="Older stale decision",
            source_meeting_id="bot-old",
        )
        newer = _make_view(
            state="stale",
            source_timestamp="2026-06-01T00:00:00Z",
            content="Newer stale decision",
            source_meeting_id="bot-new",
        )
        out = self.render(
            _make_stats(stale=2, decisions=3), [older, newer], tenant_id=None
        )
        older_pos = out.index("Older stale decision")
        newer_pos = out.index("Newer stale decision")
        assert newer_pos < older_pos, "Newer stale entry must appear before older"

    def test_stale_none_when_empty(self):
        # No stale views → section shows _None_
        active_view = _make_view(state="active")
        out = self.render(
            _make_stats(stale=0, decisions=1, superseded=0),
            [active_view],
            tenant_id=None,
        )
        # Find the Stale section content
        lines = out.splitlines()
        stale_idx = next(
            (i for i, ln in enumerate(lines) if ln.strip() == "## Stale"), None
        )
        assert stale_idx is not None, "## Stale section must always appear"
        # Next non-blank line after ## Stale must be _None_
        content_line = next((ln for ln in lines[stale_idx + 1 :] if ln.strip()), None)
        assert content_line is not None
        assert "_None_" in content_line

    # ------------------------------------------------------------------
    # Superseded section
    # ------------------------------------------------------------------

    def test_superseded_section_heading(self):
        sup_view = _make_view(
            state="superseded",
            superseded_by="sig-newer-id",
            source_meeting_id="bot-sup",
        )
        out = self.render(
            _make_stats(superseded=1, decisions=2), [sup_view], tenant_id=None
        )
        assert "## Superseded" in out

    def test_superseded_entry_content_first_line(self):
        sup_view = _make_view(
            state="superseded",
            content="Old approach\nwas fine",
            superseded_by="sig-new",
            source_meeting_id="bot-s",
        )
        out = self.render(
            _make_stats(superseded=1, decisions=2), [sup_view], tenant_id=None
        )
        assert "Old approach" in out

    def test_superseded_entry_link(self):
        sup_view = _make_view(
            state="superseded",
            superseded_by="sig-new",
            source_meeting_id="bot-sup-999",
        )
        out = self.render(
            _make_stats(superseded=1, decisions=2), [sup_view], tenant_id=None
        )
        assert "signals/meeting-bot-sup-999.json" in out

    def test_superseded_entry_superseded_by_code(self):
        sup_view = _make_view(
            state="superseded",
            superseded_by="sig-newer-id",
            source_meeting_id="bot-sup",
        )
        out = self.render(
            _make_stats(superseded=1, decisions=2), [sup_view], tenant_id=None
        )
        assert "`sig-newer-id`" in out

    def test_superseded_entries_newest_first(self):
        older = _make_view(
            state="superseded",
            source_timestamp="2026-01-01T00:00:00Z",
            content="Old superseded decision",
            superseded_by="x",
            source_meeting_id="bot-o",
        )
        newer = _make_view(
            state="superseded",
            source_timestamp="2026-06-01T00:00:00Z",
            content="New superseded decision",
            superseded_by="y",
            source_meeting_id="bot-n",
        )
        out = self.render(
            _make_stats(superseded=2, decisions=3), [older, newer], tenant_id=None
        )
        older_pos = out.index("Old superseded decision")
        newer_pos = out.index("New superseded decision")
        assert newer_pos < older_pos, "Newer superseded entry must appear before older"

    def test_superseded_none_when_empty(self):
        active_view = _make_view(state="active")
        out = self.render(
            _make_stats(stale=0, decisions=1, superseded=0),
            [active_view],
            tenant_id=None,
        )
        lines = out.splitlines()
        sup_idx = next(
            (i for i, ln in enumerate(lines) if ln.strip() == "## Superseded"), None
        )
        assert sup_idx is not None, "## Superseded section must always appear"
        content_line = next((ln for ln in lines[sup_idx + 1 :] if ln.strip()), None)
        assert content_line is not None
        assert "_None_" in content_line

    # ------------------------------------------------------------------
    # Sanitization — multi-line content stays on one bullet line
    # ------------------------------------------------------------------

    def test_stale_multiline_content_single_bullet_line(self):
        """Content with newlines must produce a single bullet line (no raw newlines)."""
        stale_view = _make_view(
            state="stale",
            content="First line of decision\nSecond line detail\n- fake sub-bullet",
            state_reason="age > threshold",
        )
        out = self.render(
            _make_stats(stale=1, decisions=2), [stale_view], tenant_id=None
        )
        # Find the bullet line(s) that contain 'First line'
        bullet_lines = [ln for ln in out.splitlines() if "First line of decision" in ln]
        assert (
            len(bullet_lines) == 1
        ), f"Expected exactly 1 bullet line for stale entry, got {len(bullet_lines)}: {bullet_lines}"
        # No raw newlines inside
        assert "\n" not in bullet_lines[0]

    def test_superseded_multiline_content_single_bullet_line(self):
        sup_view = _make_view(
            state="superseded",
            content="Line one decision\nLine two continues",
            superseded_by="sig-new",
            source_meeting_id="bot-s",
        )
        out = self.render(
            _make_stats(superseded=1, decisions=2), [sup_view], tenant_id=None
        )
        bullet_lines = [ln for ln in out.splitlines() if "Line one decision" in ln]
        assert len(bullet_lines) == 1
        assert "\n" not in bullet_lines[0]


# ---------------------------------------------------------------------------
# 2. export_decision_audit — async integration tests
# ---------------------------------------------------------------------------


class TestExportDecisionAudit:
    """Tests for export_decision_audit: file writing, git commit, error handling."""

    @pytest.fixture
    def tmp_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        return repo

    @pytest.fixture
    def mock_store(self):
        from app.models.signal import MeetingSignals, Signal

        sig1 = Signal(
            id="sig-audit-001",
            type="decision",
            content="Use Redis for session storage",
            source_meeting_id="bot-session-1",
            source_timestamp="2026-03-01T09:00:00Z",
            review_status="confirmed",
            provenance_status="user_confirmed",
            can_use_as_evidence=True,
            can_use_as_instruction=True,
        )
        sig2 = Signal(
            id="sig-audit-002",
            type="decision",
            content="Use Postgres for relational data",
            source_meeting_id="bot-session-2",
            source_timestamp="2026-04-01T09:00:00Z",
            review_status="stale",
            provenance_status="generated",
            can_use_as_evidence=True,
            can_use_as_instruction=False,
        )
        ms1 = MeetingSignals(
            meeting_id="meet-1", bot_id="bot-session-1", signals=[sig1]
        )
        ms2 = MeetingSignals(
            meeting_id="meet-2", bot_id="bot-session-2", signals=[sig2]
        )
        store = MagicMock()
        store.load_all.return_value = [ms1, ms2]
        return store

    @pytest.mark.asyncio
    async def test_file_written_with_dated_name(self, tmp_repo, mock_store):
        from app.services.decision_audit_artifact import export_decision_audit

        fixed_now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)

        with (
            patch("app.services.decision_audit_artifact.git_ops") as mock_git,
            patch("app.services.decision_audit_artifact.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock()

            await export_decision_audit(commit=True, now=fixed_now)

        expected_file = tmp_repo / "constitution" / "decision-audit-2026-06-11.md"
        assert expected_file.exists(), f"Expected {expected_file} to be written"

    @pytest.mark.asyncio
    async def test_file_content_has_artifact_frontmatter(self, tmp_repo, mock_store):
        from app.services.decision_audit_artifact import export_decision_audit

        fixed_now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)

        with (
            patch("app.services.decision_audit_artifact.git_ops") as mock_git,
            patch("app.services.decision_audit_artifact.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock()

            await export_decision_audit(commit=True, now=fixed_now)

        content = (
            tmp_repo / "constitution" / "decision-audit-2026-06-11.md"
        ).read_text()
        assert "artifact: decision-audit" in content
        assert "Use Redis for session storage" in content or "Use Postgres" in content

    @pytest.mark.asyncio
    async def test_commit_called_with_relative_path_and_date(
        self, tmp_repo, mock_store
    ):
        from app.services.decision_audit_artifact import export_decision_audit

        fixed_now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)

        with (
            patch("app.services.decision_audit_artifact.git_ops") as mock_git,
            patch("app.services.decision_audit_artifact.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock()

            await export_decision_audit(commit=True, now=fixed_now)

        mock_git.commit_and_push.assert_awaited_once()
        call_args = mock_git.commit_and_push.call_args
        files_arg = call_args[0][0]
        assert "constitution/decision-audit-2026-06-11.md" in files_arg
        commit_msg = call_args[0][1]
        assert "2026-06-11" in commit_msg

    @pytest.mark.asyncio
    async def test_commit_false_skips_git(self, tmp_repo, mock_store):
        from app.services.decision_audit_artifact import export_decision_audit

        fixed_now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)

        with (
            patch("app.services.decision_audit_artifact.git_ops") as mock_git,
            patch("app.services.decision_audit_artifact.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock()

            result = await export_decision_audit(commit=False, now=fixed_now)

        mock_git.commit_and_push.assert_not_awaited()
        assert result["committed"] is False

    @pytest.mark.asyncio
    async def test_git_failure_committed_false_file_exists(self, tmp_repo, mock_store):
        from app.services.decision_audit_artifact import export_decision_audit

        fixed_now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)

        with (
            patch("app.services.decision_audit_artifact.git_ops") as mock_git,
            patch("app.services.decision_audit_artifact.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock(side_effect=RuntimeError("git boom"))

            result = await export_decision_audit(commit=True, now=fixed_now)

        assert result["committed"] is False
        expected_file = tmp_repo / "constitution" / "decision-audit-2026-06-11.md"
        assert expected_file.exists(), "File must be written even when git fails"

    @pytest.mark.asyncio
    async def test_returns_expected_keys(self, tmp_repo, mock_store):
        from app.services.decision_audit_artifact import export_decision_audit

        fixed_now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)

        with (
            patch("app.services.decision_audit_artifact.git_ops") as mock_git,
            patch("app.services.decision_audit_artifact.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock()

            result = await export_decision_audit(commit=True, now=fixed_now)

        assert "path" in result
        assert "committed" in result
        assert "headline" in result
        assert result["committed"] is True

    @pytest.mark.asyncio
    async def test_returned_path_is_relative(self, tmp_repo, mock_store):
        from app.services.decision_audit_artifact import export_decision_audit

        fixed_now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)

        with (
            patch("app.services.decision_audit_artifact.git_ops") as mock_git,
            patch("app.services.decision_audit_artifact.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock()

            result = await export_decision_audit(commit=False, now=fixed_now)

        # Path should be relative (not an absolute /tmp/... path)
        assert not result["path"].startswith(
            "/"
        ), f"Path should be relative: {result['path']}"
        assert "constitution/decision-audit-" in result["path"]


# ---------------------------------------------------------------------------
# 3. HTTP endpoint tests — POST /api/decisions/audit/export
# ---------------------------------------------------------------------------


class TestAuditExportEndpoint:
    """Tests for POST /api/decisions/audit/export endpoint."""

    @pytest.fixture(autouse=True)
    def _client(self, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        self.tmp_repo = tmp_path / "repo"
        self.tmp_repo.mkdir()
        (self.tmp_repo / ".git").mkdir()

        from app.routes.decisions import router

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_post_audit_export_200(self):
        """POST /api/decisions/audit/export returns 200 with path, committed, headline."""
        import app.routes.decisions as _mod

        mock_result = {
            "path": "constitution/decision-audit-2026-06-11.md",
            "committed": True,
            "headline": "Across 2 meetings: 3 decisions, 1 stale, 1 superseded",
        }

        async def _fake_export(**kwargs):
            return mock_result

        with patch.object(_mod, "export_decision_audit", _fake_export):
            resp = self.client.post("/api/decisions/audit/export")

        assert resp.status_code == 200
        data = resp.json()
        assert "path" in data
        assert "committed" in data
        assert "headline" in data

    def test_post_audit_export_response_shape(self):
        """Response keys match AuditExportResponse model."""
        import app.routes.decisions as _mod

        async def _fake_export(**kwargs):
            return {
                "path": "constitution/decision-audit-2026-06-11.md",
                "committed": False,
                "headline": "Across 0 meetings: 0 decisions, 0 stale, 0 superseded",
            }

        with patch.object(_mod, "export_decision_audit", _fake_export):
            resp = self.client.post("/api/decisions/audit/export")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["path"], str)
        assert isinstance(data["committed"], bool)
        assert isinstance(data["headline"], str)


# ---------------------------------------------------------------------------
# 4. Route order — audit/export must not be captured by /{decision_id}
# ---------------------------------------------------------------------------


class TestAuditExportRouteOrder:
    """Verify /audit/export is declared before /{decision_id}."""

    def test_audit_export_path_registered_before_decision_id(self):
        from app.routes.decisions import router

        route_paths = [r.path for r in router.routes]
        audit_path = "/api/decisions/audit/export"
        detail_path = "/api/decisions/{decision_id}"

        assert (
            audit_path in route_paths
        ), f"Missing {audit_path} in routes: {route_paths}"
        assert (
            detail_path in route_paths
        ), f"Missing {detail_path} in routes: {route_paths}"
        assert route_paths.index(audit_path) < route_paths.index(
            detail_path
        ), f"audit/export route must be declared before {{decision_id}}: {route_paths}"

    def test_audit_export_not_swallowed_as_decision_id(self):
        """POST /audit/export returns 200, not 404 or the single-decision shape."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import app.routes.decisions as _mod

        from app.routes.decisions import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)

        async def _fake_export(**kwargs):
            return {
                "path": "constitution/decision-audit-2026-06-11.md",
                "committed": False,
                "headline": "Across 0 meetings: 0 decisions, 0 stale, 0 superseded",
            }

        with patch.object(_mod, "export_decision_audit", _fake_export):
            resp = client.post("/api/decisions/audit/export")

        # Must be 200, not 404 (which would mean it was swallowed by /{id})
        assert resp.status_code == 200
        data = resp.json()
        # Must have audit export shape, not decision detail shape
        assert "headline" in data
        assert "decision_id" not in data
