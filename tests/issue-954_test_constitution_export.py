"""Tests for constitution export service and routes (Issue #954, Task 8).

Tests run in TDD order: render_constitution pure-function tests first, then
export_constitution async integration tests, then HTTP endpoint tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers for building minimal decision_view dicts
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
    review_status: str | None = None,
) -> dict:
    global _COUNTER
    _COUNTER += 1
    # Default review_status: confirmed for active, else the state name
    if review_status is None:
        review_status = "confirmed" if state == "active" else state
    return {
        "id": view_id or f"sig-{_COUNTER:04d}",
        "content": content,
        "state": state,
        "state_reason": state_reason,
        "review_status": review_status,
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


# ---------------------------------------------------------------------------
# 1. render_constitution — pure-function tests
# ---------------------------------------------------------------------------


class TestRenderConstitution:
    """Pure render_constitution unit tests — no I/O."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from app.services.constitution import render_constitution

        self.render = render_constitution

    def _parse_frontmatter(self, text: str) -> dict:
        """Extract YAML frontmatter as a flat key->value dict (string values)."""
        lines = text.split("\n")
        assert lines[0].strip() == "---", f"Expected '---', got {lines[0]!r}"
        end = lines.index("---", 1)
        fm = {}
        for line in lines[1:end]:
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()
        return fm

    # ------------------------------------------------------------------
    # Frontmatter
    # ------------------------------------------------------------------

    def test_frontmatter_keys_present(self):
        view = _make_view()
        out = self.render([view], tenant_id="t1")
        fm = self._parse_frontmatter(out)
        assert "artifact" in fm
        assert "version" in fm
        assert "tenant_id" in fm
        assert "generated_at" in fm
        assert "stale_threshold_days" in fm
        assert "decisions_total" in fm
        assert "decisions_active" in fm
        assert "decisions_temporary" in fm
        assert "decisions_stale" in fm
        assert "decisions_zombie" in fm
        assert "decisions_superseded" in fm
        assert "decisions_pending_review" in fm

    def test_frontmatter_values_correct(self):
        views = [
            _make_view(state="active"),
            _make_view(
                state="stale",
                state_reason="age 142d > 90d threshold",
                age_days=142,
                review_status="confirmed",
            ),
            _make_view(state="superseded", superseded_by="sig-newer"),
        ]
        fixed_now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
        out = self.render(views, tenant_id="t42", now=fixed_now)
        fm = self._parse_frontmatter(out)

        assert fm["artifact"] == "constitution"
        assert fm["version"] == "0"
        assert fm["tenant_id"] == "t42"
        assert fm["stale_threshold_days"] == "90"
        assert fm["decisions_total"] == "3"
        assert fm["decisions_active"] == "1"
        assert fm["decisions_stale"] == "1"
        assert fm["decisions_superseded"] == "1"
        assert fm["decisions_pending_review"] == "0"
        assert "2026-06-11" in fm["generated_at"]

    def test_tenant_id_none_renders_as_DEFAULT(self):
        out = self.render([_make_view()], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["tenant_id"] == "DEFAULT"

    # ------------------------------------------------------------------
    # Active entry — full field set
    # ------------------------------------------------------------------

    def test_active_entry_full_fields(self):
        view = _make_view(
            content="We will use Postgres for all relational data",
            owner="Alice",
            state="active",
            state_reason="review_status=confirmed",
            source_meeting_id="bot-abc123",
            source_meeting_title="Product Sync",
            source_timestamp="2026-01-15T10:00:00Z",
            can_use_as_instruction=True,
            can_use_as_evidence=True,
        )
        out = self.render([view], tenant_id=None)

        assert "#### We will use Postgres for all relational data" in out
        assert "**Owner:** Alice" in out
        assert "**State:** active (review_status=confirmed)" in out
        assert "**Decided:** 2026-01-15" in out
        assert "Product Sync" in out
        assert "signals/meeting-bot-abc123.json" in out
        assert "**Authority:** instruction-grade" in out

    def test_active_entry_unassigned_owner(self):
        view = _make_view(owner=None, state="active")
        out = self.render([view], tenant_id=None)
        assert "**Owner:** Unassigned" in out

    def test_authority_evidence_grade(self):
        view = _make_view(
            can_use_as_instruction=False,
            can_use_as_evidence=True,
            state="active",
        )
        out = self.render([view], tenant_id=None)
        assert "**Authority:** evidence-grade" in out

    def test_authority_blocked(self):
        view = _make_view(
            can_use_as_instruction=False,
            can_use_as_evidence=False,
            state="active",
        )
        out = self.render([view], tenant_id=None)
        assert "**Authority:** blocked" in out

    # ------------------------------------------------------------------
    # Grouping by client_id — "General" for None
    # ------------------------------------------------------------------

    def test_grouping_by_client_id(self):
        v1 = _make_view(client_id="acme", content="Acme decision")
        v2 = _make_view(client_id="beta", content="Beta decision")
        out = self.render([v1, v2], tenant_id=None)

        # Both groups must appear as H3 headings
        assert "### acme" in out
        assert "### beta" in out
        # Each decision under its group
        acme_pos = out.index("### acme")
        beta_pos = out.index("### beta")
        acme_dec_pos = out.index("Acme decision")
        beta_dec_pos = out.index("Beta decision")
        assert acme_pos < acme_dec_pos
        assert beta_pos < beta_dec_pos

    def test_none_client_id_renders_as_general(self):
        view = _make_view(client_id=None, state="active")
        out = self.render([view], tenant_id=None)
        assert "### General" in out

    # ------------------------------------------------------------------
    # Stale section
    # ------------------------------------------------------------------

    def test_stale_section_with_age_reason(self):
        """Confirmed-stale view appears in the Stale Decisions section."""
        view = _make_view(
            state="stale",
            state_reason="age 142d > 90d threshold",
            age_days=142,
            review_status="confirmed",
        )
        out = self.render([view], tenant_id=None)
        assert "## Stale Decisions" in out
        assert "stale (age 142d > 90d threshold)" in out

    # ------------------------------------------------------------------
    # Superseded
    # ------------------------------------------------------------------

    def test_superseded_strikethrough_list(self):
        view = _make_view(
            state="superseded",
            content="Old approach was fine",
            superseded_by="sig-newer-99",
            source_meeting_id="bot-xyz",
        )
        out = self.render([view], tenant_id=None)
        assert "## Superseded" in out
        assert "~~Old approach was fine~~" in out
        assert "sig-newer-99" in out

    def test_superseded_signals_link(self):
        """Link MUST use real filename pattern: signals/meeting-{bot_id}.json"""
        view = _make_view(
            state="superseded",
            source_meeting_id="bot-xyz999",
            superseded_by="sig-new",
        )
        out = self.render([view], tenant_id=None)
        # Confirm the link uses meeting- prefix
        assert "signals/meeting-bot-xyz999.json" in out

    # ------------------------------------------------------------------
    # Rationale fallback chain
    # ------------------------------------------------------------------

    def test_rationale_from_metadata(self):
        view = _make_view(
            state="active",
            metadata={"rationale": "Chosen for ACID compliance"},
        )
        out = self.render([view], tenant_id=None)
        assert "**Rationale:** Chosen for ACID compliance" in out

    def test_rationale_from_audit_reasoning(self):
        """When metadata has no rationale, enrich from audit reasoning."""
        view = _make_view(state="active", metadata={})
        mock_audit = MagicMock()
        mock_audit.read_for_signal.return_value = [
            MagicMock(reasoning="Audit reasoning text", action="approve"),
        ]
        out = self.render([view], tenant_id=None, audit_store=mock_audit)
        assert "**Rationale:** Audit reasoning text" in out

    def test_rationale_fallback_no_recorded(self):
        """When neither metadata nor audit has rationale, show fallback text."""
        view = _make_view(state="active", metadata={})
        mock_audit = MagicMock()
        mock_audit.read_for_signal.return_value = []
        out = self.render([view], tenant_id=None, audit_store=mock_audit)
        assert "_(no recorded rationale)_" in out

    # ------------------------------------------------------------------
    # Empty corpus
    # ------------------------------------------------------------------

    def test_empty_corpus_frontmatter_zeros(self):
        out = self.render([], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_total"] == "0"
        assert fm["decisions_active"] == "0"
        assert fm["decisions_temporary"] == "0"
        assert fm["decisions_stale"] == "0"
        assert fm["decisions_zombie"] == "0"
        assert fm["decisions_superseded"] == "0"

    def test_empty_corpus_no_confirmed_decisions_message(self):
        out = self.render([], tenant_id=None)
        assert "_No confirmed decisions yet._" in out

    # ------------------------------------------------------------------
    # Excluded states (rejected / candidate)
    # ------------------------------------------------------------------

    def test_rejected_and_candidate_excluded(self):
        rejected = _make_view(state="rejected", content="Rejected idea")
        candidate = _make_view(state="candidate", content="Candidate idea")
        active = _make_view(state="active", content="Live decision")
        out = self.render([rejected, candidate, active], tenant_id=None)
        assert "Rejected idea" not in out
        assert "Candidate idea" not in out
        assert "Live decision" in out

    # ------------------------------------------------------------------
    # Signals link in active section uses real filename
    # ------------------------------------------------------------------

    def test_active_signals_link_uses_meeting_prefix(self):
        view = _make_view(
            state="active",
            source_meeting_id="bot-meeting-001",
        )
        out = self.render([view], tenant_id=None)
        assert "signals/meeting-bot-meeting-001.json" in out

    # ------------------------------------------------------------------
    # Fix 1: multi-line rationale collapses to single inline bullet line
    # ------------------------------------------------------------------

    def test_multiline_rationale_collapsed_to_single_line(self):
        """Rationale with newlines must render as one bullet with no raw newlines."""
        view = _make_view(
            state="active",
            metadata={"rationale": "Line one.\nLine two.\n- fake bullet"},
        )
        out = self.render([view], tenant_id=None)
        # Find the Rationale bullet line
        rationale_line = next(
            (line for line in out.splitlines() if "**Rationale:**" in line), None
        )
        assert rationale_line is not None, "Rationale bullet not found in output"
        # The bullet must contain all three segments collapsed
        assert "Line one." in rationale_line
        assert "Line two." in rationale_line
        assert "- fake bullet" in rationale_line
        # No raw newlines inside the bullet (it is a single line)
        assert "\n" not in rationale_line

    # ------------------------------------------------------------------
    # Fix 2: content starting with '#' must not produce a double-heading
    # ------------------------------------------------------------------

    def test_content_heading_strips_leading_markdown_markers(self):
        """Content '# We will adopt Postgres' must yield heading '#### We will adopt Postgres'."""
        view = _make_view(
            state="active",
            content="# We will adopt Postgres",
        )
        out = self.render([view], tenant_id=None)
        assert "#### We will adopt Postgres" in out
        # Must NOT contain '###### ...' or '#### # ...'
        assert "#### # We will adopt Postgres" not in out

    # ------------------------------------------------------------------
    # Fix CR-1: content_heading safety — empty / whitespace-only content
    # ------------------------------------------------------------------

    def test_content_heading_empty_string_returns_placeholder(self):
        """content_heading('') must not raise IndexError; returns placeholder."""
        from app.services.artifact_markdown import content_heading

        assert content_heading("") == "(untitled decision)"

    def test_content_heading_whitespace_only_returns_placeholder(self):
        """content_heading('   \\n  ') must not raise; returns placeholder."""
        from app.services.artifact_markdown import content_heading

        assert content_heading("   \n  ") == "(untitled decision)"

    def test_render_with_empty_content_does_not_crash(self):
        """render_constitution must not raise when a view has empty content."""
        view = _make_view(state="active", content="")
        out = self.render([view], tenant_id=None)
        assert "(untitled decision)" in out

    def test_render_with_whitespace_content_does_not_crash(self):
        """render_constitution must not raise when a view has whitespace-only content."""
        view = _make_view(state="active", content="   \n  ")
        out = self.render([view], tenant_id=None)
        assert "(untitled decision)" in out

    # ------------------------------------------------------------------
    # Product fix: stale section gated on confirmed, pending backlog surfaced
    # ------------------------------------------------------------------

    def test_stale_unconfirmed_excluded_from_stale_section(self):
        """Stale view with review_status != 'confirmed' must NOT appear in Stale section."""
        view = _make_view(
            state="stale",
            state_reason="age 100d > 90d threshold",
            review_status="stale",  # unconfirmed — never went through review
        )
        out = self.render([view], tenant_id=None)
        # The Stale Decisions section must not contain this entry
        assert "## Stale Decisions" not in out

    def test_stale_unconfirmed_counted_in_pending(self):
        """Stale-unconfirmed views are counted in decisions_pending_review frontmatter."""
        view = _make_view(
            state="stale",
            state_reason="age 100d > 90d threshold",
            review_status="stale",
        )
        out = self.render([view], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_pending_review"] == "1"

    def test_candidate_counted_in_pending(self):
        """Candidate views are counted in decisions_pending_review even though excluded from body."""
        candidate = _make_view(state="candidate", content="Candidate idea")
        active = _make_view(state="active", content="Live decision")
        out = self.render([candidate, active], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_pending_review"] == "1"

    def test_pending_line_renders_when_nonzero(self):
        """When decisions_pending_review > 0 a visible notice appears after the intro blockquote."""
        candidate = _make_view(state="candidate")
        active = _make_view(state="active")
        out = self.render([candidate, active], tenant_id=None)
        assert "awaiting review" in out

    def test_pending_line_absent_when_zero(self):
        """When all decisions are confirmed-active/stale/superseded the notice must not appear."""
        active = _make_view(state="active")
        out = self.render([active], tenant_id=None)
        assert "awaiting review" not in out

    def test_pending_count_combines_candidate_and_stale_unconfirmed(self):
        """decisions_pending_review = count(candidate) + count(stale-unconfirmed)."""
        views = [
            _make_view(state="candidate"),
            _make_view(state="candidate"),
            _make_view(state="stale", review_status="stale"),  # unconfirmed
            _make_view(state="active"),  # not pending
        ]
        out = self.render(views, tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_pending_review"] == "3"

    def test_stale_confirmed_appears_in_stale_section(self):
        """Confirmed-stale decisions continue to appear in the Stale Decisions section."""
        view = _make_view(
            state="stale",
            state_reason="age 120d > 90d threshold",
            review_status="confirmed",
        )
        out = self.render([view], tenant_id=None)
        assert "## Stale Decisions" in out

    def test_stale_unconfirmed_not_counted_in_decisions_stale_frontmatter(self):
        """decisions_stale in frontmatter reflects confirmed-stale count only."""
        confirmed = _make_view(state="stale", review_status="confirmed")
        unconfirmed = _make_view(state="stale", review_status="stale")
        out = self.render([confirmed, unconfirmed], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_stale"] == "1"
        assert fm["decisions_pending_review"] == "1"
        # decisions_total counts rendered entries only — the unconfirmed-stale
        # view is pending, not part of the constitution body.
        assert fm["decisions_total"] == "1"

    # ------------------------------------------------------------------
    # S3-7: confirmed temporary renders in Active; confirmed zombie in Stale
    # ------------------------------------------------------------------

    def test_confirmed_temporary_renders_in_active_section(self):
        """Confirmed-temporary view must appear under Active Decisions."""
        view = _make_view(
            state="temporary",
            state_reason="temporary until 2026-12-31",
            review_status="confirmed",
            content="Use feature flags temporarily",
        )
        out = self.render([view], tenant_id=None)
        assert "## Active Decisions" in out
        assert "Use feature flags temporarily" in out
        assert "temporary (temporary until 2026-12-31)" in out

    def test_confirmed_temporary_state_line_shows_temporary_prefix(self):
        """State line for a confirmed-temporary entry must read 'temporary (...)'."""
        view = _make_view(
            state="temporary",
            state_reason="temporary until 2027-03-01",
            review_status="confirmed",
        )
        out = self.render([view], tenant_id=None)
        assert "**State:** temporary (temporary until 2027-03-01)" in out

    def test_confirmed_zombie_renders_in_stale_section(self):
        """Confirmed-zombie view must appear under Stale Decisions."""
        view = _make_view(
            state="zombie",
            state_reason="revisit_date 2025-01-01 passed without action",
            review_status="confirmed",
            content="Use legacy API v1 temporarily",
        )
        out = self.render([view], tenant_id=None)
        assert "## Stale Decisions" in out
        assert "Use legacy API v1 temporarily" in out
        assert "zombie (revisit_date 2025-01-01 passed without action)" in out

    def test_unconfirmed_temporary_excluded_from_render(self):
        """Unconfirmed-temporary view must NOT appear in any rendered section."""
        view = _make_view(
            state="temporary",
            state_reason="temporary until 2026-12-31",
            review_status="temporary",
            content="Unconfirmed temp decision",
        )
        out = self.render([view], tenant_id=None)
        assert "Unconfirmed temp decision" not in out
        assert "## Active Decisions" not in out

    def test_unconfirmed_zombie_excluded_from_render(self):
        """Unconfirmed-zombie view must NOT appear in any rendered section."""
        view = _make_view(
            state="zombie",
            state_reason="revisit_date passed",
            review_status="zombie",
            content="Unconfirmed zombie decision",
        )
        out = self.render([view], tenant_id=None)
        assert "Unconfirmed zombie decision" not in out

    def test_unconfirmed_temporary_counted_in_pending(self):
        """Unconfirmed-temporary views are counted in decisions_pending_review."""
        view = _make_view(
            state="temporary",
            state_reason="temporary until 2026-12-31",
            review_status="temporary",
        )
        out = self.render([view], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_pending_review"] == "1"

    def test_unconfirmed_zombie_counted_in_pending(self):
        """Unconfirmed-zombie views are counted in decisions_pending_review."""
        view = _make_view(
            state="zombie",
            state_reason="revisit_date passed",
            review_status="zombie",
        )
        out = self.render([view], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_pending_review"] == "1"

    def test_frontmatter_decisions_temporary_and_zombie_counts(self):
        """decisions_temporary and decisions_zombie frontmatter keys count confirmed entries."""
        views = [
            _make_view(state="active"),
            _make_view(state="temporary", review_status="confirmed"),
            _make_view(state="temporary", review_status="confirmed"),
            _make_view(state="zombie", review_status="confirmed"),
            _make_view(state="temporary", review_status="temporary"),  # unconfirmed
        ]
        out = self.render(views, tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_temporary"] == "2"
        assert fm["decisions_zombie"] == "1"
        assert fm["decisions_pending_review"] == "1"

    def test_decisions_total_includes_confirmed_temporary_and_zombie(self):
        """decisions_total = active + confirmed-temporary + confirmed-stale + confirmed-zombie + superseded."""
        views = [
            _make_view(state="active"),
            _make_view(state="temporary", review_status="confirmed"),
            _make_view(state="stale", review_status="confirmed"),
            _make_view(state="zombie", review_status="confirmed"),
            _make_view(state="superseded", superseded_by="sig-x"),
            _make_view(
                state="temporary", review_status="temporary"
            ),  # unconfirmed → pending
        ]
        out = self.render(views, tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_total"] == "5"
        assert fm["decisions_pending_review"] == "1"

    def test_pending_count_includes_all_unconfirmed_types(self):
        """decisions_pending_review sums candidates + unconfirmed stale + temporary + zombie."""
        views = [
            _make_view(state="candidate"),
            _make_view(state="stale", review_status="stale"),
            _make_view(state="temporary", review_status="temporary"),
            _make_view(state="zombie", review_status="zombie"),
            _make_view(state="active"),  # confirmed — not pending
        ]
        out = self.render(views, tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_pending_review"] == "4"

    def test_confirmed_temporary_grouped_by_client_in_active_section(self):
        """Confirmed-temporary entries participate in client grouping inside Active Decisions."""
        t = _make_view(
            state="temporary",
            review_status="confirmed",
            client_id="acme",
            content="Temporary ACME policy",
        )
        a = _make_view(state="active", client_id="acme", content="Active ACME policy")
        out = self.render([t, a], tenant_id=None)
        assert "### acme" in out
        acme_pos = out.index("### acme")
        assert out.index("Temporary ACME policy") > acme_pos
        assert out.index("Active ACME policy") > acme_pos

    # ------------------------------------------------------------------
    # S4-3: conflicting state constitution section
    # ------------------------------------------------------------------

    def test_frontmatter_includes_decisions_conflicting_key(self):
        """decisions_conflicting key must be present in frontmatter."""
        out = self.render([], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert "decisions_conflicting" in fm

    def test_frontmatter_decisions_conflicting_zero_for_empty(self):
        """decisions_conflicting = 0 for empty corpus."""
        out = self.render([], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_conflicting"] == "0"

    def test_confirmed_conflicting_renders_in_conflicting_section(self):
        """Confirmed-conflicting view appears under '## Conflicting Decisions'."""
        other_id = "sig-other-001"
        view = _make_view(
            state="conflicting",
            state_reason="conflicts with 1 decision(s): sig-othe",
            review_status="confirmed",
            content="Use SSO for all authentication",
            metadata={"conflicts_with": [other_id]},
            view_id="sig-sso-001",
        )
        out = self.render([view], tenant_id=None)
        assert "## Conflicting Decisions" in out
        assert "Use SSO for all authentication" in out

    def test_confirmed_conflicting_shows_conflict_link(self):
        """Conflicting entry must show ⚠ Conflicts with: line with the other signal ID."""
        other_id = "sig-other-001"
        other_view = _make_view(
            state="active",
            review_status="confirmed",
            content="Use per-service auth",
            view_id=other_id,
            source_meeting_id="bot-other-meeting",
        )
        view = _make_view(
            state="conflicting",
            state_reason="conflicts with 1 decision(s): sig-othe",
            review_status="confirmed",
            content="Use SSO for all authentication",
            metadata={"conflicts_with": [other_id]},
            view_id="sig-sso-001",
        )
        out = self.render([view, other_view], tenant_id=None)
        assert "⚠ Conflicts with:" in out
        assert other_id in out

    def test_confirmed_conflicting_link_uses_meeting_file_when_resolvable(self):
        """Conflict link uses signals/meeting-{meeting_id}.json when other view is known."""
        other_id = "sig-other-link-001"
        other_view = _make_view(
            state="active",
            review_status="confirmed",
            content="Other content",
            view_id=other_id,
            source_meeting_id="bot-linked-meeting",
        )
        view = _make_view(
            state="conflicting",
            review_status="confirmed",
            content="Use SSO",
            metadata={"conflicts_with": [other_id]},
            view_id="sig-sso-link",
        )
        out = self.render([view, other_view], tenant_id=None)
        assert "signals/meeting-bot-linked-meeting.json" in out

    def test_unconfirmed_conflicting_excluded_from_render(self):
        """Unconfirmed-conflicting view must NOT appear in the Conflicting section."""
        other_id = "sig-other-002"
        view = _make_view(
            state="conflicting",
            review_status="conflicting",  # not confirmed
            content="Unconfirmed conflicting decision",
            metadata={"conflicts_with": [other_id]},
        )
        out = self.render([view], tenant_id=None)
        assert "Unconfirmed conflicting decision" not in out
        assert "## Conflicting Decisions" not in out

    def test_unconfirmed_conflicting_counted_in_pending(self):
        """Unconfirmed-conflicting views are counted in decisions_pending_review."""
        other_id = "sig-other-003"
        view = _make_view(
            state="conflicting",
            review_status="conflicting",
            metadata={"conflicts_with": [other_id]},
        )
        out = self.render([view], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_pending_review"] == "1"

    def test_frontmatter_decisions_conflicting_counts_confirmed_only(self):
        """decisions_conflicting counts only confirmed-conflicting entries."""
        other_id_a = "sig-other-conf"
        other_id_b = "sig-other-unconf"
        views = [
            _make_view(
                state="conflicting",
                review_status="confirmed",
                metadata={"conflicts_with": [other_id_a]},
            ),
            _make_view(
                state="conflicting",
                review_status="conflicting",  # unconfirmed
                metadata={"conflicts_with": [other_id_b]},
            ),
        ]
        out = self.render(views, tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_conflicting"] == "1"
        assert fm["decisions_pending_review"] == "1"

    def test_decisions_total_includes_confirmed_conflicting(self):
        """decisions_total includes confirmed-conflicting entries."""
        other_id = "sig-other-total"
        views = [
            _make_view(state="active"),
            _make_view(
                state="conflicting",
                review_status="confirmed",
                metadata={"conflicts_with": [other_id]},
            ),
        ]
        out = self.render(views, tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_total"] == "2"

    def test_conflicting_section_position_between_active_and_stale(self):
        """Conflicting Decisions section appears after Active and before Stale."""
        other_id = "sig-x"
        views = [
            _make_view(state="active", content="Active decision"),
            _make_view(
                state="conflicting",
                review_status="confirmed",
                content="Conflicting decision",
                metadata={"conflicts_with": [other_id]},
            ),
            _make_view(state="stale", review_status="confirmed", content="Stale decision"),
        ]
        out = self.render(views, tenant_id=None)
        assert "## Active Decisions" in out
        assert "## Conflicting Decisions" in out
        assert "## Stale Decisions" in out
        active_pos = out.index("## Active Decisions")
        conflicting_pos = out.index("## Conflicting Decisions")
        stale_pos = out.index("## Stale Decisions")
        assert active_pos < conflicting_pos < stale_pos

    def test_empty_corpus_frontmatter_zeros_includes_conflicting(self):
        """Empty corpus: decisions_conflicting = 0 alongside all other zeros."""
        out = self.render([], tenant_id=None)
        fm = self._parse_frontmatter(out)
        assert fm["decisions_conflicting"] == "0"
        assert fm["decisions_total"] == "0"


# ---------------------------------------------------------------------------
# 2. export_constitution — async integration tests
# ---------------------------------------------------------------------------


class TestExportConstitution:
    """Tests for export_constitution: file writing, git commit, error handling."""

    @pytest.fixture
    def tmp_repo(self, tmp_path):
        """Create a minimal fake repo directory."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()  # satisfy git_ops check
        return repo

    @pytest.fixture
    def mock_store(self):
        """Mock SignalStore that returns one active decision."""
        from app.models.signal import MeetingSignals, Signal

        sig = Signal(
            id="sig-export-001",
            type="decision",
            content="Use Redis for session storage",
            source_meeting_id="bot-session-1",
            source_timestamp="2026-03-01T09:00:00Z",
            review_status="confirmed",
            provenance_status="user_confirmed",
            can_use_as_evidence=True,
            can_use_as_instruction=True,
        )
        ms = MeetingSignals(
            meeting_id="meet-session-1", bot_id="bot-session-1", signals=[sig]
        )
        store = MagicMock()
        store.load_all.return_value = [ms]
        return store

    @pytest.mark.asyncio
    async def test_file_written_with_expected_content(self, tmp_repo, mock_store):
        from app.services.constitution import export_constitution

        with (
            patch("app.services.constitution.git_ops") as mock_git,
            patch("app.services.constitution.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock()

            await export_constitution(commit=True)

        out_path = tmp_repo / "constitution" / "constitution.md"
        assert out_path.exists(), "constitution.md should be written"
        content = out_path.read_text()
        assert "Use Redis for session storage" in content
        assert "artifact" in content

    @pytest.mark.asyncio
    async def test_commit_and_push_called_with_correct_args(self, tmp_repo, mock_store):
        from app.services.constitution import (
            CONSTITUTION_RELATIVE_PATH,
            export_constitution,
        )

        with (
            patch("app.services.constitution.git_ops") as mock_git,
            patch("app.services.constitution.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock()

            await export_constitution(commit=True)

        mock_git.commit_and_push.assert_awaited_once()
        call_args = mock_git.commit_and_push.call_args
        files_arg = call_args[0][0]  # first positional: list of files
        assert CONSTITUTION_RELATIVE_PATH in files_arg

    @pytest.mark.asyncio
    async def test_commit_false_skips_git(self, tmp_repo, mock_store):
        from app.services.constitution import export_constitution

        with (
            patch("app.services.constitution.git_ops") as mock_git,
            patch("app.services.constitution.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock()

            result = await export_constitution(commit=False)

        mock_git.commit_and_push.assert_not_awaited()
        assert result["committed"] is False

    @pytest.mark.asyncio
    async def test_git_exception_returns_committed_false_file_exists(
        self, tmp_repo, mock_store
    ):
        from app.services.constitution import export_constitution

        with (
            patch("app.services.constitution.git_ops") as mock_git,
            patch("app.services.constitution.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock(side_effect=RuntimeError("git boom"))

            result = await export_constitution(commit=True)

        assert result["committed"] is False
        out_path = tmp_repo / "constitution" / "constitution.md"
        assert out_path.exists(), "File must be written even when git fails"

    @pytest.mark.asyncio
    async def test_returns_expected_keys(self, tmp_repo, mock_store):
        from app.services.constitution import export_constitution

        with (
            patch("app.services.constitution.git_ops") as mock_git,
            patch("app.services.constitution.signal_store", mock_store),
        ):
            mock_git.repo_path = str(tmp_repo)
            mock_git.commit_and_push = AsyncMock()

            result = await export_constitution(commit=True)

        assert "path" in result
        assert "committed" in result
        assert "counts_by_state" in result
        assert result["committed"] is True


# ---------------------------------------------------------------------------
# 3. HTTP endpoint tests
# ---------------------------------------------------------------------------


class TestConstitutionEndpoints:
    """Integration tests for GET /constitution and POST /constitution/export."""

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

    def test_get_constitution_404_before_export(self):
        with patch("app.routes.decisions.git_ops") as mock_git:
            mock_git.repo_path = str(self.tmp_repo)
            resp = self.client.get("/api/decisions/constitution")
        assert resp.status_code == 404

    def test_get_constitution_200_after_file_written(self):
        # Write a constitution file manually
        const_dir = self.tmp_repo / "constitution"
        const_dir.mkdir()
        (const_dir / "constitution.md").write_text(
            "# Constitution\n\n_No confirmed decisions yet._"
        )

        with patch("app.routes.decisions.git_ops") as mock_git:
            mock_git.repo_path = str(self.tmp_repo)
            resp = self.client.get("/api/decisions/constitution")

        assert resp.status_code == 200
        assert "text/markdown" in resp.headers.get("content-type", "")
        assert "Constitution" in resp.text

    def test_post_constitution_export_returns_path_and_committed(self):
        import app.routes.decisions as _mod

        mock_result = {
            "path": "constitution/constitution.md",
            "committed": True,
            "counts_by_state": {"active": 1},
        }

        async def _fake_export(**kwargs):
            return mock_result

        with patch.object(_mod, "export_constitution", _fake_export):
            resp = self.client.post("/api/decisions/constitution/export")

        assert resp.status_code == 200
        data = resp.json()
        assert "path" in data
        assert "committed" in data

    def test_route_order_constitution_before_decision_id(self):
        """Verify /constitution and /constitution/export are not swallowed by /{id}."""
        from app.routes.decisions import router

        route_paths = [r.path for r in router.routes]
        const_path = "/api/decisions/constitution"
        detail_path = "/api/decisions/{decision_id}"
        assert const_path in route_paths, f"Missing {const_path}: {route_paths}"
        assert route_paths.index(const_path) < route_paths.index(detail_path)
