"""Tests for S4-2 — DETECT_CONFLICTS ingest phase + delta report conflicts section.

Part 1: PHASES constant ordering (9 entries, DETECT_CONFLICTS between DETECT_SUPERSESSION and ENRICH_GRAPH)
Part 2: _phase_detect_conflicts behaviour (annotation, skip on no API key, non-fatal on error)
Part 3: delta builder + renderer for potential_conflicts
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

_TS = "2026-06-01T00:00:00+00:00"
GENERATED_AT = "2026-06-11T10:00:00+00:00"


def _entity(eid: str, etype: str = "project") -> EntityRef:
    slug = eid.split("-", 1)[-1] if "-" in eid else eid
    return EntityRef(id=eid, type=etype, name=slug.replace("-", " ").title())


def _decision(
    *,
    signal_id: str | None = None,
    content: str = "A decision",
    source_meeting_id: str = "bot-old",
    entities: list[EntityRef] | None = None,
    metadata: dict | None = None,
    review_status: str = "confirmed",
    provenance_status: str = "generated",
) -> Signal:
    return Signal(
        id=signal_id or str(uuid.uuid4()),
        type="decision",
        content=content,
        source_meeting_id=source_meeting_id,
        source_timestamp=_TS,
        entities=entities or [],
        review_status=review_status,
        provenance_status=provenance_status,
        metadata=metadata or {},
    )


def _make_orchestrator():
    from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

    return IngestOrchestrator(
        classifier=MagicMock(),
        claude_client=MagicMock(),
        graph=MagicMock(),
        signal_writer=MagicMock(),
        git_ops=MagicMock(),
    )


def _make_meeting_signals(*signals):
    return MeetingSignals(
        meeting_id="m-test",
        bot_id="bot-test",
        signals=list(signals),
    )


def _make_signal(
    sig_id: str,
    sig_type: str,
    content: str,
    *,
    metadata: dict | None = None,
) -> Signal:
    return Signal(
        id=sig_id,
        type=sig_type,
        content=content,
        source_meeting_id="bot-abc123",
        source_timestamp=_TS,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Part 1: PHASES constant
# ---------------------------------------------------------------------------


class TestPhasesConstant:

    def test_detect_conflicts_in_phases(self):
        from app.services.orchestrators.ingest_orchestrator import PHASES

        assert "DETECT_CONFLICTS" in PHASES

    def test_detect_conflicts_after_detect_supersession(self):
        from app.services.orchestrators.ingest_orchestrator import PHASES

        sup_idx = PHASES.index("DETECT_SUPERSESSION")
        con_idx = PHASES.index("DETECT_CONFLICTS")
        assert con_idx > sup_idx, (
            f"DETECT_CONFLICTS (idx {con_idx}) must come after "
            f"DETECT_SUPERSESSION (idx {sup_idx})"
        )

    def test_detect_conflicts_before_enrich_graph(self):
        from app.services.orchestrators.ingest_orchestrator import PHASES

        con_idx = PHASES.index("DETECT_CONFLICTS")
        enrich_idx = PHASES.index("ENRICH_GRAPH")
        assert con_idx < enrich_idx, (
            f"DETECT_CONFLICTS (idx {con_idx}) must come before "
            f"ENRICH_GRAPH (idx {enrich_idx})"
        )


    def test_existing_phases_still_present(self):
        """Regression: existing phases not dropped by this change."""
        from app.services.orchestrators.ingest_orchestrator import PHASES

        for phase in [
            "CLASSIFY", "BUILD_MEETING", "PROMOTE_SIGNALS",
            "DETECT_SUPERSESSION", "ENRICH_GRAPH", "PERSIST",
            "DELTA_REPORT", "COMPLETE",
        ]:
            assert phase in PHASES, f"Phase {phase!r} missing from PHASES"


# ---------------------------------------------------------------------------
# Part 2: _phase_detect_conflicts
# ---------------------------------------------------------------------------


class TestDetectConflictsPhase:
    """Tests for IngestOrchestrator._phase_detect_conflicts."""

    def test_annotates_metadata_when_candidates_found(self, tmp_path):
        """Decision signals acquire metadata.conflict_candidates when non-empty."""
        project = _entity("project-kb-llm")
        new_dec = _decision(
            content="We will use microservices",
            source_meeting_id="bot-new",
            entities=[project],
        )
        meeting_signals = _make_meeting_signals(new_dec)

        fake_candidates = [
            {
                "other_signal_id": "old-sig-1",
                "other_content": "We will use a monolith",
                "rationale": "Microservices vs monolith are mutually exclusive",
                "confidence": 0.92,
                "status": "pending",
                "proposed_at": "2026-06-11T10:00:00+00:00",
            }
        ]

        # Load a real signal_store with a standing signal
        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=tmp_path / "signals")
        old_dec = _decision(
            content="We will use a monolith",
            source_meeting_id="bot-old",
            entities=[project],
        )
        store.save(_make_meeting_signals(old_dec))

        orchestrator = _make_orchestrator()

        # Mock find_conflict_candidates at the orchestrator module level
        with patch(
            "app.services.orchestrators.ingest_orchestrator.find_conflict_candidates",
            new=AsyncMock(return_value=fake_candidates),
        ):
            asyncio.get_event_loop().run_until_complete(
                orchestrator._phase_detect_conflicts(meeting_signals, store)
            )

        assert "conflict_candidates" in new_dec.metadata
        assert new_dec.metadata["conflict_candidates"] == fake_candidates

    def test_no_annotation_when_no_candidates(self, tmp_path):
        """Empty candidates list → metadata.conflict_candidates NOT set."""
        project = _entity("project-kb-llm")
        new_dec = _decision(
            content="We will use microservices",
            source_meeting_id="bot-new",
            entities=[project],
        )
        meeting_signals = _make_meeting_signals(new_dec)

        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=tmp_path / "signals")

        orchestrator = _make_orchestrator()

        with patch(
            "app.services.orchestrators.ingest_orchestrator.find_conflict_candidates",
            new=AsyncMock(return_value=[]),
        ):
            asyncio.get_event_loop().run_until_complete(
                orchestrator._phase_detect_conflicts(meeting_signals, store)
            )

        assert "conflict_candidates" not in new_dec.metadata

    def test_non_decision_signals_not_processed(self, tmp_path):
        """action_item signals are skipped — find_conflict_candidates not called."""
        action = Signal(
            id=str(uuid.uuid4()),
            type="action_item",
            content="Do something",
            source_meeting_id="bot-new",
            source_timestamp=_TS,
        )
        meeting_signals = _make_meeting_signals(action)

        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=tmp_path / "signals")
        orchestrator = _make_orchestrator()

        mock_find = AsyncMock(return_value=[])
        with patch(
            "app.services.orchestrators.ingest_orchestrator.find_conflict_candidates",
            new=mock_find,
        ):
            asyncio.get_event_loop().run_until_complete(
                orchestrator._phase_detect_conflicts(meeting_signals, store)
            )

        mock_find.assert_not_called()

    def test_detector_raising_phase_continues_no_annotation(self, tmp_path):
        """find_conflict_candidates raising → phase completes, no annotation, pipeline continues."""
        project = _entity("project-kb-llm")
        new_dec = _decision(
            content="We will use microservices",
            source_meeting_id="bot-new",
            entities=[project],
        )
        meeting_signals = _make_meeting_signals(new_dec)

        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=tmp_path / "signals")
        orchestrator = _make_orchestrator()

        with patch(
            "app.services.orchestrators.ingest_orchestrator.find_conflict_candidates",
            new=AsyncMock(side_effect=RuntimeError("LLM exploded")),
        ):
            # Must not raise
            asyncio.get_event_loop().run_until_complete(
                orchestrator._phase_detect_conflicts(meeting_signals, store)
            )

        assert "conflict_candidates" not in new_dec.metadata

    def test_load_all_failure_phase_continues(self, tmp_path):
        """store.load_all() raising → phase completes, no annotation."""
        project = _entity("project-kb-llm")
        new_dec = _decision(
            content="We will use microservices",
            source_meeting_id="bot-new",
            entities=[project],
        )
        meeting_signals = _make_meeting_signals(new_dec)

        bad_store = MagicMock()
        bad_store.load_all.side_effect = RuntimeError("Disk failure")
        orchestrator = _make_orchestrator()

        mock_find = AsyncMock(return_value=[])
        with patch(
            "app.services.orchestrators.ingest_orchestrator.find_conflict_candidates",
            new=mock_find,
        ):
            asyncio.get_event_loop().run_until_complete(
                orchestrator._phase_detect_conflicts(meeting_signals, bad_store)
            )

        mock_find.assert_not_called()
        assert "conflict_candidates" not in new_dec.metadata

    def test_no_api_key_phase_skipped(self, tmp_path):
        """No ANTHROPIC_API_KEY → phase skipped, find_conflict_candidates never called."""
        project = _entity("project-kb-llm")
        new_dec = _decision(
            content="Use microservices",
            source_meeting_id="bot-new",
            entities=[project],
        )
        meeting_signals = _make_meeting_signals(new_dec)

        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=tmp_path / "signals")
        orchestrator = _make_orchestrator()

        mock_find = AsyncMock(return_value=[])
        with (
            patch(
                "app.services.orchestrators.ingest_orchestrator.find_conflict_candidates",
                new=mock_find,
            ),
            patch("app.services.orchestrators.ingest_orchestrator.settings") as mock_settings,
        ):
            mock_settings.ANTHROPIC_API_KEY = ""
            asyncio.get_event_loop().run_until_complete(
                orchestrator._phase_detect_conflicts(meeting_signals, store)
            )

        mock_find.assert_not_called()

    def test_result_carries_conflict_candidates_count(self, tmp_path):
        """The phase returns the total candidate count."""
        project = _entity("project-kb-llm")
        dec1 = _decision(content="Use A", source_meeting_id="bot-new", entities=[project])
        dec2 = _decision(content="Use B", source_meeting_id="bot-new2", entities=[project])
        meeting_signals = _make_meeting_signals(dec1, dec2)

        fake_candidate = {
            "other_signal_id": "old-1",
            "other_content": "Use Z",
            "rationale": "A vs Z conflict",
            "confidence": 0.85,
            "status": "pending",
            "proposed_at": "2026-06-11T10:00:00+00:00",
        }

        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=tmp_path / "signals")
        orchestrator = _make_orchestrator()

        # First signal gets 2 candidates, second gets 1
        call_count = [0]

        async def fake_find(sig, standing, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [fake_candidate, fake_candidate]
            return [fake_candidate]

        with patch(
            "app.services.orchestrators.ingest_orchestrator.find_conflict_candidates",
            new=fake_find,
        ):
            count = asyncio.get_event_loop().run_until_complete(
                orchestrator._phase_detect_conflicts(meeting_signals, store)
            )

        assert count == 3  # 2 + 1

    def test_none_meeting_signals_returns_zero(self):
        """None meeting_signals → phase completes, returns 0."""
        orchestrator = _make_orchestrator()

        mock_find = AsyncMock(return_value=[])
        with patch(
            "app.services.orchestrators.ingest_orchestrator.find_conflict_candidates",
            new=mock_find,
        ):
            count = asyncio.get_event_loop().run_until_complete(
                orchestrator._phase_detect_conflicts(None)
            )

        assert count == 0
        mock_find.assert_not_called()

    def test_phase_in_process_pipeline_after_detect_supersession(self):
        """Full pipeline smoke-test: DETECT_CONFLICTS runs after DETECT_SUPERSESSION."""
        from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator
        from app.models.ingestion.models import IngestRequest

        fake_git = MagicMock()
        fake_git.commit_file = AsyncMock()

        orch = IngestOrchestrator(
            classifier=None,
            claude_client=None,
            graph=None,
            signal_writer=None,
            git_ops=fake_git,
        )
        ms = _make_meeting_signals(
            _make_signal("s1", "decision", "Some decision")
        )

        orch._phase_classify = AsyncMock(return_value="transcript")
        orch._phase_build_observation = AsyncMock(
            return_value=MagicMock(
                external_id="bot-test",
                title="Test",
                participants=[],
                entities_mentioned={},
            )
        )
        orch._phase_promote_signals = AsyncMock(return_value=ms)
        orch._phase_detect_supersession = AsyncMock(return_value=0)
        orch._phase_detect_conflicts = AsyncMock(return_value=0)
        orch._phase_enrich_graph = AsyncMock(return_value={"signal_count": 0, "edge_count": 0})
        orch._phase_persist = AsyncMock(return_value=None)

        request = IngestRequest(content="Meeting transcript here", title="Test")
        job_store: dict = {}
        result = asyncio.get_event_loop().run_until_complete(
            orch.process(request, job_id="job-smoke-test", job_store=job_store)
        )

        assert result["status"] == "completed"
        orch._phase_detect_conflicts.assert_called_once()

    def test_conflict_candidates_count_in_result(self, tmp_path):
        """result['conflict_candidates'] is set when candidates found."""
        from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator
        from app.models.ingestion.models import IngestRequest

        fake_git = MagicMock()
        fake_git.commit_file = AsyncMock()

        project = _entity("project-kb-llm")
        dec = _decision(content="Use A", source_meeting_id="bot-new", entities=[project])
        ms = _make_meeting_signals(dec)

        orch = IngestOrchestrator(
            classifier=None,
            claude_client=None,
            graph=None,
            signal_writer=None,
            git_ops=fake_git,
        )

        orch._phase_classify = AsyncMock(return_value="transcript")
        orch._phase_build_observation = AsyncMock(
            return_value=MagicMock(
                external_id="bot-test",
                title="Test",
                participants=[],
                entities_mentioned={},
            )
        )
        orch._phase_promote_signals = AsyncMock(return_value=ms)
        orch._phase_detect_supersession = AsyncMock(return_value=0)
        orch._phase_enrich_graph = AsyncMock(return_value={"signal_count": 0, "edge_count": 0})
        orch._phase_persist = AsyncMock(return_value=None)

        fake_candidate = {
            "other_signal_id": "old-1",
            "other_content": "Use Z",
            "rationale": "conflict",
            "confidence": 0.9,
            "status": "pending",
            "proposed_at": "2026-06-11T10:00:00+00:00",
        }

        request = IngestRequest(content="content", title="Test")
        job_store: dict = {}

        with patch(
            "app.services.orchestrators.ingest_orchestrator.find_conflict_candidates",
            new=AsyncMock(return_value=[fake_candidate]),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                orch.process(request, job_id="job-cc-test", job_store=job_store)
            )

        assert result.get("conflict_candidates") == 1


# ---------------------------------------------------------------------------
# Part 3: ConflictCandidate model
# ---------------------------------------------------------------------------


class TestConflictCandidateModel:
    def test_model_fields(self):
        cc = ConflictCandidate(
            new_signal_id="sig-new-1",
            other_signal_id="sig-old-1",
            other_content="Old decision text",
            rationale="These are mutually exclusive",
            confidence=0.88,
            status="pending",
        )
        assert cc.new_signal_id == "sig-new-1"
        assert cc.other_signal_id == "sig-old-1"
        assert cc.other_content == "Old decision text"
        assert cc.rationale == "These are mutually exclusive"
        assert cc.confidence == 0.88
        assert cc.status == "pending"


# ---------------------------------------------------------------------------
# Part 3: DeltaReport gains potential_conflicts
# ---------------------------------------------------------------------------


class TestDeltaReportConflicts:
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

    def test_delta_report_has_potential_conflicts_field(self):
        report = self._base_report()
        assert hasattr(report, "potential_conflicts")
        assert report.potential_conflicts == []

    def test_counts_include_potential_conflicts_key(self):
        report = self._base_report()
        assert "potential_conflicts" in report.counts

    def test_builder_lifts_conflict_candidates_from_decision_metadata(self):
        candidate = {
            "other_signal_id": "sig-old-1",
            "other_content": "Old decision text",
            "rationale": "Mutually exclusive",
            "confidence": 0.88,
            "status": "pending",
            "proposed_at": "2026-06-11T10:00:00+00:00",
        }
        sig = _make_signal(
            "sig-d1",
            "decision",
            "New decision text",
            metadata={"conflict_candidates": [candidate]},
        )
        ms = MeetingSignals(
            meeting_id="meet-1",
            bot_id="bot-test",
            signals=[sig],
        )
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)

        assert len(report.potential_conflicts) == 1
        cc = report.potential_conflicts[0]
        assert cc.new_signal_id == "sig-d1"
        assert cc.other_signal_id == "sig-old-1"
        assert cc.other_content == "Old decision text"
        assert cc.rationale == "Mutually exclusive"
        assert cc.confidence == 0.88
        assert cc.status == "pending"
        assert report.counts["potential_conflicts"] == 1

    def test_builder_no_candidates_empty_list(self):
        sig = _make_signal("sig-d1", "decision", "Some decision")
        ms = MeetingSignals(
            meeting_id="meet-1",
            bot_id="bot-test",
            signals=[sig],
        )
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert report.potential_conflicts == []
        assert report.counts["potential_conflicts"] == 0

    def test_builder_multiple_candidates_across_decisions(self):
        c1 = {
            "other_signal_id": "old-1",
            "other_content": "Old A",
            "rationale": "Conflict A",
            "confidence": 0.9,
            "status": "pending",
        }
        c2 = {
            "other_signal_id": "old-2",
            "other_content": "Old B",
            "rationale": "Conflict B",
            "confidence": 0.75,
            "status": "pending",
        }
        c3 = {
            "other_signal_id": "old-3",
            "other_content": "Old C",
            "rationale": "Conflict C",
            "confidence": 0.82,
            "status": "pending",
        }
        sig1 = _make_signal(
            "sig-d1", "decision", "Decision 1",
            metadata={"conflict_candidates": [c1, c2]},
        )
        sig2 = _make_signal(
            "sig-d2", "decision", "Decision 2",
            metadata={"conflict_candidates": [c3]},
        )
        ms = MeetingSignals(
            meeting_id="meet-1",
            bot_id="bot-test",
            signals=[sig1, sig2],
        )
        report = build_delta_report("j1", "b1", "T", ms, generated_at=GENERATED_AT)
        assert len(report.potential_conflicts) == 3
        assert report.counts["potential_conflicts"] == 3

    def test_builder_empty_signals_empty_conflicts(self):
        report = build_delta_report("j1", "b1", "T", None, generated_at=GENERATED_AT)
        assert report.potential_conflicts == []
        assert report.counts["potential_conflicts"] == 0

    def test_counts_backward_compat_no_new_keys_missing(self):
        """Existing count keys are still present (backward compat)."""
        report = build_delta_report("j1", "b1", "T", None, generated_at=GENERATED_AT)
        for key in [
            "new_decisions",
            "proposed_supersessions",
            "potential_conflicts",
            "commitments_opened",
            "commitments_closed",
            "entities_touched",
        ]:
            assert key in report.counts, f"counts missing key: {key!r}"


# ---------------------------------------------------------------------------
# Part 3: render_delta_markdown — Potential conflicts section
# ---------------------------------------------------------------------------


class TestRenderDeltaMarkdownConflicts:
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

    def test_empty_conflicts_section_renders_none(self):
        report = self._base_report()
        md = render_delta_markdown(report)
        assert "## Potential conflicts" in md
        # Find the section and check it contains _None_
        idx = md.index("## Potential conflicts")
        section = md[idx:]
        assert "_None_" in section

    def test_conflict_section_renders_line_per_item(self):
        cc = ConflictCandidate(
            new_signal_id="sig-new-1",
            other_signal_id="sig-old-1",
            other_content="We will use a monolith",
            rationale="Microservices vs monolith are mutually exclusive",
            confidence=0.88,
            status="pending",
        )
        report = self._base_report(
            new_decisions=[DeltaItem(signal_id="sig-new-1", content="We will use microservices")],
            potential_conflicts=[cc],
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
        assert "## Potential conflicts" in md
        # Should contain new content, old content, rationale, confidence
        assert "use microservices" in md.lower() or "microservices" in md
        assert "monolith" in md
        assert "Microservices vs monolith" in md
        assert "0.88" in md

    def test_conflict_section_format_matches_spec(self):
        """Line format: - "{new content}" may conflict with "{other content}" — {rationale} (confidence 0.88)"""
        cc = ConflictCandidate(
            new_signal_id="sig-new-1",
            other_signal_id="sig-old-1",
            other_content="We use monolith",
            rationale="Mutually exclusive architectures",
            confidence=0.88,
            status="pending",
        )
        report = self._base_report(
            new_decisions=[DeltaItem(signal_id="sig-new-1", content="We use microservices")],
            potential_conflicts=[cc],
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
        # The line should have the format from the spec
        assert "may conflict with" in md
        assert "Mutually exclusive architectures" in md
        assert "0.88" in md

    def test_section_order_conflicts_after_supersessions(self):
        """Potential conflicts section must appear after Proposed supersessions."""
        report = self._base_report()
        md = render_delta_markdown(report)
        sup_idx = md.index("## Proposed supersessions")
        con_idx = md.index("## Potential conflicts")
        assert con_idx > sup_idx, (
            f"'Potential conflicts' (idx {con_idx}) must come after "
            f"'Proposed supersessions' (idx {sup_idx})"
        )

    def test_empty_sections_count_with_conflicts(self):
        """All 6 sections empty → 6 _None_ markers (including Potential conflicts)."""
        report = self._base_report()
        md = render_delta_markdown(report)
        # We now have one more section: Potential conflicts
        assert md.count("_None_") == 6

    def test_multiple_conflicts_rendered(self):
        cc1 = ConflictCandidate(
            new_signal_id="sig-1",
            other_signal_id="old-1",
            other_content="Old approach A",
            rationale="Conflict A rationale",
            confidence=0.9,
            status="pending",
        )
        cc2 = ConflictCandidate(
            new_signal_id="sig-1",
            other_signal_id="old-2",
            other_content="Old approach B",
            rationale="Conflict B rationale",
            confidence=0.75,
            status="pending",
        )
        report = self._base_report(
            new_decisions=[DeltaItem(signal_id="sig-1", content="New approach")],
            potential_conflicts=[cc1, cc2],
            counts={
                "new_decisions": 1,
                "proposed_supersessions": 0,
                "potential_conflicts": 2,
                "commitments_opened": 0,
                "commitments_closed": 0,
                "entities_touched": 0,
            },
        )
        md = render_delta_markdown(report)
        assert md.count("may conflict with") == 2
        assert "Old approach A" in md
        assert "Old approach B" in md
        assert "Conflict A rationale" in md
        assert "Conflict B rationale" in md
