"""Tests for Task 19 — delta report model, builder, renderer, phase, and endpoint.

Part 1: pure model/builder/renderer (no I/O)
Part 2: orchestrator phase + GET /api/ingest/{job_id}/delta endpoint
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.delta_report import (
    ConflictCandidate,
    DeltaItem,
    DeltaReport,
    SupersessionProposal,
    build_delta_report,
    render_delta_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GENERATED_AT = "2026-06-11T10:00:00+00:00"


def _make_signal(
    sig_id: str,
    sig_type: str,
    content: str,
    *,
    status: str | None = None,
    owner_name: str | None = None,
    due_date: str | None = None,
    entities: list[dict] | None = None,
    metadata: dict | None = None,
    source_meeting_id: str = "bot-abc123",
) -> Signal:
    entity_refs = [
        EntityRef(id=e["id"], type=e.get("type", "person"), name=e["name"])
        for e in (entities or [])
    ]
    owner_ref = None
    if owner_name:
        owner_ref = EntityRef(
            id=f"person-{owner_name.lower().replace(' ', '-')}",
            type="person",
            name=owner_name,
        )
    return Signal(
        id=sig_id,
        type=sig_type,
        content=content,
        source_meeting_id=source_meeting_id,
        source_timestamp="2026-06-11T09:00:00+00:00",
        entities=entity_refs,
        status=status,
        owner=owner_ref,
        due_date=due_date,
        metadata=metadata or {},
    )


def _make_meeting_signals(
    signals: list[Signal], bot_id: str = "bot-abc123"
) -> MeetingSignals:
    return MeetingSignals(
        meeting_id="meet-001",
        bot_id=bot_id,
        meeting_title="Test Meeting",
        signals=signals,
        signal_count=len(signals),
    )


# ---------------------------------------------------------------------------
# Part 1: build_delta_report
# ---------------------------------------------------------------------------


class TestBuildDeltaReport:
    def test_empty_signals_produces_empty_report(self):
        report = build_delta_report(
            "job-001", "bot-abc", "My Meeting", None, generated_at=GENERATED_AT
        )
        assert report.job_id == "job-001"
        assert report.bot_id == "bot-abc"
        assert report.meeting_title == "My Meeting"
        assert report.generated_at == GENERATED_AT
        assert report.new_decisions == []
        assert report.proposed_supersessions == []
        assert report.commitments_opened == []
        assert report.commitments_closed == []
        assert report.entities_touched == []
        assert report.counts == {
            "new_decisions": 0,
            "proposed_supersessions": 0,
            "potential_conflicts": 0,
            "commitments_opened": 0,
            "commitments_closed": 0,
            "entities_touched": 0,
        }

    def test_decisions_go_to_new_decisions(self):
        sig = _make_signal("sig-d1", "decision", "We chose Python")
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert len(report.new_decisions) == 1
        assert report.new_decisions[0].signal_id == "sig-d1"
        assert report.new_decisions[0].content == "We chose Python"
        assert report.counts["new_decisions"] == 1

    def test_action_item_status_none_goes_to_opened(self):
        sig = _make_signal("sig-a1", "action_item", "Do the thing", status=None)
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert len(report.commitments_opened) == 1
        assert report.commitments_opened[0].signal_id == "sig-a1"
        assert report.commitments_closed == []

    def test_action_item_status_open_goes_to_opened(self):
        sig = _make_signal("sig-a2", "action_item", "File report", status="open")
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert len(report.commitments_opened) == 1
        assert report.commitments_closed == []

    def test_action_item_status_in_progress_goes_to_opened(self):
        sig = _make_signal(
            "sig-a3", "action_item", "Review draft", status="in_progress"
        )
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert len(report.commitments_opened) == 1
        assert report.commitments_closed == []

    def test_action_item_status_done_goes_to_closed(self):
        sig = _make_signal("sig-a4", "action_item", "Send email", status="done")
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert report.commitments_opened == []
        assert len(report.commitments_closed) == 1
        assert report.commitments_closed[0].signal_id == "sig-a4"

    def test_action_item_carries_owner_and_due_date(self):
        sig = _make_signal(
            "sig-a5",
            "action_item",
            "Fix bug",
            status="open",
            owner_name="Alice Smith",
            due_date="2026-06-20",
        )
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        item = report.commitments_opened[0]
        assert item.owner == "Alice Smith"
        assert item.due_date == "2026-06-20"

    def test_supersession_proposals_lifted_from_decision_metadata(self):
        candidate = {
            "old_signal_id": "sig-old-1",
            "old_content": "Old decision text",
            "matched_entities": ["project-kb-llm"],
            "reason": "Shared entities: KB-LLM",
            "confidence": 0.75,
            "status": "pending",
            "proposed_at": "2026-06-11T09:30:00+00:00",
        }
        sig = _make_signal(
            "sig-d2",
            "decision",
            "New decision text",
            metadata={"supersession_candidates": [candidate]},
        )
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert len(report.proposed_supersessions) == 1
        prop = report.proposed_supersessions[0]
        assert prop.new_signal_id == "sig-d2"
        assert prop.old_signal_id == "sig-old-1"
        assert prop.old_content == "Old decision text"
        assert prop.reason == "Shared entities: KB-LLM"
        assert prop.confidence == 0.75
        assert prop.status == "pending"

    def test_multiple_candidates_from_one_decision(self):
        candidates = [
            {
                "old_signal_id": "old-1",
                "old_content": "Prev 1",
                "reason": "r1",
                "confidence": 0.8,
                "status": "pending",
            },
            {
                "old_signal_id": "old-2",
                "old_content": "Prev 2",
                "reason": "r2",
                "confidence": 0.5,
                "status": "pending",
            },
        ]
        sig = _make_signal(
            "sig-d3",
            "decision",
            "New text",
            metadata={"supersession_candidates": candidates},
        )
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert len(report.proposed_supersessions) == 2

    def test_entities_deduped_across_all_signals(self):
        shared_entity = {"id": "project-kb-llm", "name": "KB-LLM", "type": "project"}
        sig1 = _make_signal("sig-d1", "decision", "Dec 1", entities=[shared_entity])
        sig2 = _make_signal(
            "sig-a1", "action_item", "Act 1", status="open", entities=[shared_entity]
        )
        ms = _make_meeting_signals([sig1, sig2])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert len(report.entities_touched) == 1
        assert report.entities_touched[0]["id"] == "project-kb-llm"
        assert report.counts["entities_touched"] == 1

    def test_entities_deduped_across_multiple_unique_entities(self):
        e1 = {"id": "person-alice", "name": "Alice", "type": "person"}
        e2 = {"id": "project-kb-llm", "name": "KB-LLM", "type": "project"}
        sig1 = _make_signal("sig-d1", "decision", "Dec 1", entities=[e1, e2])
        sig2 = _make_signal(
            "sig-a1", "action_item", "Act 1", status="open", entities=[e1]
        )
        ms = _make_meeting_signals([sig1, sig2])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert len(report.entities_touched) == 2
        ids = {e["id"] for e in report.entities_touched}
        assert ids == {"person-alice", "project-kb-llm"}

    def test_non_decision_action_item_signals_not_in_decisions(self):
        sig = _make_signal("sig-k1", "key_point", "Key insight", status=None)
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert report.new_decisions == []
        assert report.commitments_opened == []
        assert report.commitments_closed == []

    def test_generated_at_injected_deterministically(self):
        sig = _make_signal("sig-d1", "decision", "Decision")
        ms = _make_meeting_signals([sig])
        r1 = build_delta_report(
            "j1", "b1", "T", ms, generated_at="2026-01-01T00:00:00+00:00"
        )
        r2 = build_delta_report(
            "j1", "b1", "T", ms, generated_at="2026-01-01T00:00:00+00:00"
        )
        assert r1.generated_at == r2.generated_at == "2026-01-01T00:00:00+00:00"

    def test_counts_reflect_all_sections(self):
        d = _make_signal("sig-d1", "decision", "Dec")
        a_open = _make_signal("sig-a1", "action_item", "Open act", status="open")
        a_done = _make_signal("sig-a2", "action_item", "Done act", status="done")
        ms = _make_meeting_signals([d, a_open, a_done])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert report.counts["new_decisions"] == 1
        assert report.counts["commitments_opened"] == 1
        assert report.counts["commitments_closed"] == 1
        assert report.counts["proposed_supersessions"] == 0

    def test_owner_only_entity_appears_in_entities_touched(self):
        """Fix E: action_item whose owner is not in entities appears in entities_touched."""
        # The owner entity has an id NOT present in sig.entities
        sig = _make_signal(
            "sig-a6",
            "action_item",
            "Review the plan",
            status="open",
            owner_name="Bob Jones",
            # No entities — owner is the only entity reference
        )
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)

        entity_ids = {e["id"] for e in report.entities_touched}
        assert (
            "person-bob-jones" in entity_ids
        ), f"Owner entity 'person-bob-jones' missing from entities_touched: {entity_ids}"
        assert report.counts["entities_touched"] >= 1


# ---------------------------------------------------------------------------
# Part 1: build_delta_report — conflict candidate validation
# ---------------------------------------------------------------------------


class TestBuildDeltaReportConflictValidation:
    """Builder skips malformed conflict_candidates with a warning; never aborts."""

    def test_one_good_one_malformed_gives_one_conflict(self):
        """One valid + one malformed entry → report has 1 conflict, no exception."""
        good = {
            "other_signal_id": "sig-other",
            "other_content": "Some contradicting decision",
            "rationale": "Direct conflict",
            "confidence": 0.85,
            "status": "pending",
        }
        bad = {
            # missing other_signal_id and other_content
            "rationale": "incomplete entry",
        }
        sig = _make_signal(
            "sig-d1",
            "decision",
            "Use Python",
            metadata={"conflict_candidates": [good, bad]},
        )
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)

        assert len(report.potential_conflicts) == 1
        assert report.potential_conflicts[0].other_signal_id == "sig-other"
        assert report.counts["potential_conflicts"] == 1

    def test_all_malformed_gives_no_conflicts(self):
        """All malformed entries → empty potential_conflicts, no exception."""
        bad_entries = [
            None,
            "not a dict",
            {"no_required_keys": True},
            {"other_signal_id": 123, "other_content": "valid content"},  # int id
        ]
        sig = _make_signal(
            "sig-d1",
            "decision",
            "Use Python",
            metadata={"conflict_candidates": bad_entries},
        )
        ms = _make_meeting_signals([sig])
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)

        assert report.potential_conflicts == []
        assert report.counts["potential_conflicts"] == 0


# ---------------------------------------------------------------------------
# Part 1: render_delta_markdown
# ---------------------------------------------------------------------------


class TestRenderDeltaMarkdown:
    def _base_report(self, **kwargs) -> DeltaReport:
        defaults = dict(
            job_id="job-001",
            bot_id="bot-abc",
            meeting_title="Sprint Demo",
            generated_at=GENERATED_AT,
            new_decisions=[],
            proposed_supersessions=[],
            potential_conflicts=[],
            commitments_opened=[],
            commitments_closed=[],
            entities_touched=[],
            counts={
                "new_decisions": 0,
                "proposed_supersessions": 0,
                "potential_conflicts": 0,
                "commitments_opened": 0,
                "commitments_closed": 0,
                "entities_touched": 0,
            },
        )
        defaults.update(kwargs)
        return DeltaReport(**defaults)

    def test_heading_contains_title_and_date(self):
        report = self._base_report()
        md = render_delta_markdown(report)
        assert "# What your brain learned — Sprint Demo (2026-06-11)" in md

    def test_heading_date_format_YYYY_MM_DD(self):
        report = self._base_report(generated_at="2026-12-25T15:30:00+00:00")
        md = render_delta_markdown(report)
        assert "(2026-12-25)" in md

    def test_heading_uses_bot_id_when_no_title(self):
        report = self._base_report(meeting_title=None)
        md = render_delta_markdown(report)
        assert "# What your brain learned — bot-abc (2026-06-11)" in md

    def test_empty_sections_render_none(self):
        report = self._base_report()
        md = render_delta_markdown(report)
        assert md.count("_None_") == 6  # all 6 sections empty (includes Potential conflicts)

    def test_new_decisions_section(self):
        report = self._base_report(
            new_decisions=[
                DeltaItem(signal_id="s1", content="We chose React", entities=["KB-LLM"])
            ],
            counts={
                "new_decisions": 1,
                "proposed_supersessions": 0,
                "commitments_opened": 0,
                "commitments_closed": 0,
                "entities_touched": 0,
            },
        )
        md = render_delta_markdown(report)
        assert "## New decisions" in md
        assert "We chose React" in md
        assert "KB-LLM" in md

    def test_proposed_supersessions_section_with_confirm_link(self):
        report = self._base_report(
            new_decisions=[DeltaItem(signal_id="new-1", content="New way")],
            proposed_supersessions=[
                SupersessionProposal(
                    new_signal_id="new-1",
                    old_signal_id="old-1",
                    old_content="Old way",
                    reason="Shared entities: X",
                    confidence=0.67,
                    status="pending",
                )
            ],
            counts={
                "new_decisions": 1,
                "proposed_supersessions": 1,
                "commitments_opened": 0,
                "commitments_closed": 0,
                "entities_touched": 0,
            },
        )
        md = render_delta_markdown(report)
        assert "## Proposed supersessions" in md
        assert '"New way" supersedes → "Old way"' in md
        assert "0.67" in md
        assert "POST /api/supersession/candidates/confirm" in md

    def test_commitments_opened_with_owner_and_due_date(self):
        report = self._base_report(
            commitments_opened=[
                DeltaItem(
                    signal_id="a1",
                    content="Write report",
                    owner="Bob",
                    due_date="2026-07-01",
                )
            ],
            counts={
                "new_decisions": 0,
                "proposed_supersessions": 0,
                "commitments_opened": 1,
                "commitments_closed": 0,
                "entities_touched": 0,
            },
        )
        md = render_delta_markdown(report)
        assert "## Commitments opened" in md
        assert "Write report" in md
        assert "Bob" in md
        assert "2026-07-01" in md

    def test_commitments_closed_section(self):
        report = self._base_report(
            commitments_closed=[DeltaItem(signal_id="a2", content="Sent email")],
            counts={
                "new_decisions": 0,
                "proposed_supersessions": 0,
                "commitments_opened": 0,
                "commitments_closed": 1,
                "entities_touched": 0,
            },
        )
        md = render_delta_markdown(report)
        assert "## Commitments closed" in md
        assert "Sent email" in md

    def test_entities_touched_section(self):
        report = self._base_report(
            entities_touched=[
                {"id": "person-alice", "name": "Alice", "type": "person"},
                {"id": "project-kb-llm", "name": "KB-LLM", "type": "project"},
            ],
            counts={
                "new_decisions": 0,
                "proposed_supersessions": 0,
                "commitments_opened": 0,
                "commitments_closed": 0,
                "entities_touched": 2,
            },
        )
        md = render_delta_markdown(report)
        assert "## Entities touched" in md
        assert "Alice" in md
        assert "KB-LLM" in md

    def test_multiline_content_collapsed_to_single_line(self):
        """inline_text() collapses newlines so bullets stay single-line."""
        report = self._base_report(
            new_decisions=[
                DeltaItem(signal_id="s1", content="We decided\nto use\nPython")
            ],
            counts={
                "new_decisions": 1,
                "proposed_supersessions": 0,
                "commitments_opened": 0,
                "commitments_closed": 0,
                "entities_touched": 0,
            },
        )
        md = render_delta_markdown(report)
        # The bullet should have collapsed content
        assert "We decided to use Python" in md

    def test_conflict_rationale_inline_text_applied(self):
        """Rationale is rendered via inline_text (newlines collapsed)."""
        report = self._base_report(
            new_decisions=[DeltaItem(signal_id="d1", content="Use React")],
            potential_conflicts=[
                ConflictCandidate(
                    new_signal_id="d1",
                    other_signal_id="other-1",
                    other_content="Use Vue",
                    rationale="They are\nmutually exclusive frameworks",
                    confidence=0.8,
                    status="pending",
                )
            ],
            counts={
                "new_decisions": 1,
                "proposed_supersessions": 0,
                "potential_conflicts": 1,
                "commitments_opened": 0,
                "commitments_closed": 0,
                "entities_touched": 0,
            },
        )
        md = render_delta_markdown(report)
        # Newline in rationale must be collapsed
        assert "They are mutually exclusive frameworks" in md
        # The raw newline must not appear between "are" and "mutually"
        assert "They are\nmutually" not in md

    def test_conflict_empty_rationale_omits_fragment(self):
        """When rationale is empty, the '— {rationale}' fragment is omitted from the conflict bullet."""
        report = self._base_report(
            meeting_title=None,  # avoid "—" in the heading to simplify assertion
            new_decisions=[DeltaItem(signal_id="d1", content="Use React")],
            potential_conflicts=[
                ConflictCandidate(
                    new_signal_id="d1",
                    other_signal_id="other-1",
                    other_content="Use Vue",
                    rationale="",
                    confidence=0.75,
                    status="pending",
                )
            ],
            counts={
                "new_decisions": 1,
                "proposed_supersessions": 0,
                "potential_conflicts": 1,
                "commitments_opened": 0,
                "commitments_closed": 0,
                "entities_touched": 0,
            },
        )
        md = render_delta_markdown(report)
        # Extract just the Potential conflicts section to avoid noise from the heading
        conflicts_section = md[md.index("## Potential conflicts"):]
        # The ' — ' fragment must not appear in the conflicts section bullet
        bullet_line = next(
            (line for line in conflicts_section.splitlines() if line.startswith("- ")),
            "",
        )
        assert " — " not in bullet_line, f"Unexpected '— rationale' fragment in: {bullet_line!r}"
        # But the confidence must still appear
        assert "0.75" in bullet_line

    def test_section_order(self):
        """Sections appear in the correct order in the markdown."""
        report = self._base_report()
        md = render_delta_markdown(report)
        idx_decisions = md.index("## New decisions")
        idx_supersessions = md.index("## Proposed supersessions")
        idx_opened = md.index("## Commitments opened")
        idx_closed = md.index("## Commitments closed")
        idx_entities = md.index("## Entities touched")
        assert (
            idx_decisions < idx_supersessions < idx_opened < idx_closed < idx_entities
        )

    def test_has_job_bot_metadata_line(self):
        report = self._base_report()
        md = render_delta_markdown(report)
        assert "job-001" in md
        assert "bot-abc" in md


# ---------------------------------------------------------------------------
# Part 2: orchestrator _phase_delta_report
# ---------------------------------------------------------------------------


class TestDeltaReportPhase:
    """Test the _phase_delta_report method of IngestOrchestrator."""

    def _make_orchestrator(self, fake_git=None):
        from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

        orch = IngestOrchestrator(
            classifier=None,
            claude_client=None,
            graph=None,
            signal_writer=None,
            git_ops=fake_git,
        )
        return orch

    def _make_job_store(self, job_id: str) -> tuple[str, dict]:
        job_key = f"job:{job_id}"
        store = {
            job_key: {
                "job_id": job_id,
                "status": "running",
                "content_type": "transcript",
                "phases_completed": [],
                "current_phase": "DELTA_REPORT",
                "result": None,
                "error": None,
            }
        }
        return job_key, store

    def _make_full_signals(self) -> MeetingSignals:
        decision = _make_signal("sig-d1", "decision", "Use Python")
        action = _make_signal("sig-a1", "action_item", "Write tests", status="open")
        return MeetingSignals(
            meeting_id="meet-1",
            bot_id="bot-test",
            meeting_title="Planning",
            signals=[decision, action],
            signal_count=2,
        )

    def test_phase_stores_delta_report_in_result(self):
        fake_git = MagicMock()
        fake_git.commit_file = AsyncMock()

        orch = self._make_orchestrator(fake_git)
        ms = self._make_full_signals()

        result = {}
        asyncio.get_event_loop().run_until_complete(
            orch._phase_delta_report(
                job_id="job-001",
                bot_id="bot-test",
                meeting_title="Planning",
                meeting_signals=ms,
                result=result,
            )
        )

        assert "delta_report" in result
        dr = result["delta_report"]
        assert dr["job_id"] == "job-001"
        assert dr["bot_id"] == "bot-test"
        assert dr["counts"]["new_decisions"] == 1
        assert dr["counts"]["commitments_opened"] == 1

    def test_phase_calls_commit_file_with_correct_path(self):
        fake_git = MagicMock()
        fake_git.commit_file = AsyncMock()

        orch = self._make_orchestrator(fake_git)
        ms = self._make_full_signals()
        result = {}

        asyncio.get_event_loop().run_until_complete(
            orch._phase_delta_report(
                job_id="job-001",
                bot_id="bot-test",
                meeting_title="Planning",
                meeting_signals=ms,
                result=result,
            )
        )

        fake_git.commit_file.assert_called_once()
        call_args = fake_git.commit_file.call_args
        path_arg = call_args[0][0]
        commit_msg = call_args[0][2]
        assert path_arg == "deltas/delta-bot-test.md"
        assert "[delta]" in commit_msg
        assert "Planning" in commit_msg

    def test_phase_git_failure_is_nonfatal(self):
        """git failure must not prevent the report from being stored in result."""

        fake_git = MagicMock()
        fake_git.commit_file = AsyncMock(side_effect=RuntimeError("git boom"))

        orch = self._make_orchestrator(fake_git)
        ms = self._make_full_signals()
        result = {}

        asyncio.get_event_loop().run_until_complete(
            orch._phase_delta_report(
                job_id="job-001",
                bot_id="bot-test",
                meeting_title="Planning",
                meeting_signals=ms,
                result=result,
            )
        )

        # Report still produced despite git error
        assert "delta_report" in result
        assert result["delta_report"]["counts"]["new_decisions"] == 1

    def test_phase_without_git_ops_is_nonfatal(self):
        """No git_ops (None) → report still produced."""

        orch = self._make_orchestrator(fake_git=None)
        ms = self._make_full_signals()
        result = {}

        asyncio.get_event_loop().run_until_complete(
            orch._phase_delta_report(
                job_id="job-001",
                bot_id="bot-test",
                meeting_title="Planning",
                meeting_signals=ms,
                result=result,
            )
        )

        assert "delta_report" in result

    def test_phase_exposes_report_on_self_for_sse(self):
        """Task 20 seam: phase stores report on self._last_delta_report."""

        fake_git = MagicMock()
        fake_git.commit_file = AsyncMock()

        orch = self._make_orchestrator(fake_git)
        ms = self._make_full_signals()
        result = {}

        asyncio.get_event_loop().run_until_complete(
            orch._phase_delta_report(
                job_id="job-001",
                bot_id="bot-test",
                meeting_title="Planning",
                meeting_signals=ms,
                result=result,
            )
        )

        assert hasattr(orch, "_last_delta_report")
        assert orch._last_delta_report is not None
        assert orch._last_delta_report.bot_id == "bot-test"

    def test_full_pipeline_integrates_delta_phase(self):
        """Smoke-test: process() with stubbed phases includes delta_report in result."""
        from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

        fake_git = MagicMock()
        fake_git.commit_file = AsyncMock()

        orch = IngestOrchestrator(
            classifier=None,
            claude_client=None,
            graph=None,
            signal_writer=None,
            git_ops=fake_git,
        )
        ms = self._make_full_signals()

        # Patch internal phases to skip actual LLM calls
        orch._phase_classify = AsyncMock(return_value="transcript")
        orch._phase_build_observation = AsyncMock(
            return_value=MagicMock(
                external_id="bot-test",
                title="Planning",
                participants=[],
                entities_mentioned={},
            )
        )
        orch._phase_promote_signals = AsyncMock(return_value=ms)
        orch._phase_detect_supersession = AsyncMock(return_value=0)
        orch._phase_enrich_graph = AsyncMock(
            return_value={"signal_count": 0, "edge_count": 0}
        )
        orch._phase_persist = AsyncMock(return_value=None)

        from app.models.ingestion.models import IngestRequest

        request = IngestRequest(content="Meeting transcript here", title="Planning")
        job_store: dict[str, Any] = {}
        result = asyncio.get_event_loop().run_until_complete(
            orch.process(request, job_id="job-full-test", job_store=job_store)
        )

        assert "delta_report" in result
        assert result["delta_report"]["counts"]["new_decisions"] == 1


# ---------------------------------------------------------------------------
# Part 2: GET /api/ingest/{job_id}/delta endpoint
# ---------------------------------------------------------------------------


def _build_test_app(job_store_data: dict) -> TestClient:
    """Build a minimal FastAPI app with the ingest router, job_store pre-seeded.

    The ingest router already carries prefix="/ingest", so we mount it under
    "/api" to get the full path /api/ingest/{job_id}/delta.
    """
    from app.routes.ingest import router, _get_job_store

    app = FastAPI()
    app.include_router(router, prefix="/api")

    app.dependency_overrides[_get_job_store] = lambda: job_store_data

    return TestClient(app)


def _delta_url(job_id: str) -> str:
    return f"/api/ingest/{job_id}/delta"


class TestDeltaEndpoint:
    def _seed_completed_job_with_delta(self, job_id: str) -> dict:
        delta_report = {
            "job_id": job_id,
            "bot_id": "bot-xyz",
            "meeting_title": "Test Meeting",
            "generated_at": GENERATED_AT,
            "new_decisions": [
                {
                    "signal_id": "s1",
                    "content": "Use Rust",
                    "entities": [],
                    "owner": None,
                    "due_date": None,
                }
            ],
            "proposed_supersessions": [],
            "commitments_opened": [],
            "commitments_closed": [],
            "entities_touched": [],
            "counts": {
                "new_decisions": 1,
                "proposed_supersessions": 0,
                "commitments_opened": 0,
                "commitments_closed": 0,
                "entities_touched": 0,
            },
        }
        return {
            f"job:{job_id}": {
                "job_id": job_id,
                "status": "completed",
                "content_type": "transcript",
                "phases_completed": [
                    "CLASSIFY",
                    "BUILD_MEETING",
                    "PROMOTE_SIGNALS",
                    "DETECT_SUPERSESSION",
                    "ENRICH_GRAPH",
                    "PERSIST",
                    "DELTA_REPORT",
                    "COMPLETE",
                ],
                "current_phase": None,
                "result": {"status": "completed", "delta_report": delta_report},
                "error": None,
                "created_at": GENERATED_AT,
            }
        }

    def test_200_with_delta_report(self):
        job_id = "job-delta-test"
        store = self._seed_completed_job_with_delta(job_id)
        client = _build_test_app(store)
        resp = client.get(_delta_url(job_id))
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["counts"]["new_decisions"] == 1

    def test_404_when_job_unknown(self):
        store = {}
        client = _build_test_app(store)
        resp = client.get(_delta_url("job-missing"))
        assert resp.status_code == 404

    def test_404_when_delta_absent_job_still_running(self):
        job_id = "job-running"
        store = {
            f"job:{job_id}": {
                "job_id": job_id,
                "status": "running",
                "content_type": None,
                "phases_completed": ["CLASSIFY"],
                "current_phase": "BUILD_MEETING",
                "result": None,
                "error": None,
                "created_at": GENERATED_AT,
            }
        }
        client = _build_test_app(store)
        resp = client.get(_delta_url(job_id))
        assert resp.status_code == 404

    def test_404_when_job_completed_but_delta_absent(self):
        """Job completed but delta_report not in result (edge case)."""
        job_id = "job-no-delta"
        store = {
            f"job:{job_id}": {
                "job_id": job_id,
                "status": "completed",
                "content_type": "transcript",
                "phases_completed": [],
                "current_phase": None,
                "result": {"status": "completed"},  # no delta_report key
                "error": None,
                "created_at": GENERATED_AT,
            }
        }
        client = _build_test_app(store)
        resp = client.get(_delta_url(job_id))
        assert resp.status_code == 404

    def test_200_returns_full_delta_structure(self):
        job_id = "job-full-delta"
        store = self._seed_completed_job_with_delta(job_id)
        client = _build_test_app(store)
        resp = client.get(_delta_url(job_id))
        assert resp.status_code == 200
        data = resp.json()
        assert "new_decisions" in data
        assert "proposed_supersessions" in data
        assert "commitments_opened" in data
        assert "commitments_closed" in data
        assert "entities_touched" in data
        assert "counts" in data
        assert "generated_at" in data
