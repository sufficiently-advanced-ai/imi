"""Tests for supersession candidate matching (R2.4) and DETECT_SUPERSESSION phase.

This file covers:
  Part 1 — pure matcher (find_supersession_candidates)
  Part 2 — DETECT_SUPERSESSION ingest orchestrator phase
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock


from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.supersession_candidates import find_supersession_candidates

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = "2026-06-01T00:00:00+00:00"
_TS2 = "2026-06-02T00:00:00+00:00"


def _decision(
    *,
    signal_id: str | None = None,
    content: str = "A decision",
    source_meeting_id: str = "bot-old",
    entities: list[EntityRef] | None = None,
    provenance_status: str = "generated",
    review_status: str = "pending",
) -> Signal:
    return Signal(
        id=signal_id or str(uuid.uuid4()),
        type="decision",
        content=content,
        source_meeting_id=source_meeting_id,
        source_timestamp=_TS,
        entities=entities or [],
        provenance_status=provenance_status,
        review_status=review_status,
    )


def _entity(eid: str, etype: str = "project") -> EntityRef:
    slug = eid.split("-", 1)[-1] if "-" in eid else eid
    return EntityRef(id=eid, type=etype, name=slug.replace("-", " ").title())


# ---------------------------------------------------------------------------
# Part 1: Pure matcher tests
# ---------------------------------------------------------------------------


class TestFindSupersessionCandidates:
    def test_overlap_on_non_person_entity_returns_candidate(self):
        """A shared non-person entity → candidate is returned."""
        project = _entity("project-kb-llm")
        new_sig = _decision(
            content="New DB decision", source_meeting_id="bot-new", entities=[project]
        )
        old_sig = _decision(
            content="Old DB decision", source_meeting_id="bot-old", entities=[project]
        )

        results = find_supersession_candidates(new_sig, [old_sig])

        assert len(results) == 1
        assert results[0]["old_signal_id"] == old_sig.id
        assert "project-kb-llm" in results[0]["matched_entities"]
        assert results[0]["status"] == "pending"
        assert results[0]["confidence"] > 0

    def test_person_only_overlap_returns_no_candidates(self):
        """Overlap only on person entities → no candidates (persons are excluded)."""
        person = _entity("person-alice", etype="person")
        new_sig = _decision(source_meeting_id="bot-new", entities=[person])
        old_sig = _decision(source_meeting_id="bot-old", entities=[person])

        results = find_supersession_candidates(new_sig, [old_sig])

        assert results == []

    def test_superseded_signal_excluded(self):
        """Standing signal with provenance_status='superseded' is excluded."""
        project = _entity("project-kb-llm")
        new_sig = _decision(source_meeting_id="bot-new", entities=[project])
        old_sig = _decision(
            source_meeting_id="bot-old",
            entities=[project],
            provenance_status="superseded",
        )

        results = find_supersession_candidates(new_sig, [old_sig])

        assert results == []

    def test_rejected_signal_excluded(self):
        """Standing signal with review_status='rejected' is excluded."""
        project = _entity("project-kb-llm")
        new_sig = _decision(source_meeting_id="bot-new", entities=[project])
        old_sig = _decision(
            source_meeting_id="bot-old",
            entities=[project],
            review_status="rejected",
        )

        results = find_supersession_candidates(new_sig, [old_sig])

        assert results == []

    def test_same_meeting_excluded(self):
        """Signal from the same source_meeting_id is excluded."""
        project = _entity("project-kb-llm")
        same_meeting_id = "bot-same"
        new_sig = _decision(source_meeting_id=same_meeting_id, entities=[project])
        old_sig = _decision(source_meeting_id=same_meeting_id, entities=[project])

        results = find_supersession_candidates(new_sig, [old_sig])

        assert results == []

    def test_self_id_excluded(self):
        """Signal with same id as new_signal is excluded."""
        project = _entity("project-kb-llm")
        sid = str(uuid.uuid4())
        new_sig = _decision(
            signal_id=sid, source_meeting_id="bot-new", entities=[project]
        )
        same_sig = _decision(
            signal_id=sid, source_meeting_id="bot-old", entities=[project]
        )

        results = find_supersession_candidates(new_sig, [same_sig])

        assert results == []

    def test_non_decision_new_signal_returns_empty(self):
        """Non-decision new_signal → [] regardless of standing pool."""
        project = _entity("project-kb-llm")
        non_decision = Signal(
            id=str(uuid.uuid4()),
            type="action_item",
            content="An action",
            source_meeting_id="bot-new",
            source_timestamp=_TS,
            entities=[project],
        )
        old_sig = _decision(source_meeting_id="bot-old", entities=[project])

        results = find_supersession_candidates(non_decision, [old_sig])

        assert results == []

    def test_non_decision_standing_signal_excluded(self):
        """Standing action_item is not included even with entity overlap."""
        project = _entity("project-kb-llm")
        new_sig = _decision(source_meeting_id="bot-new", entities=[project])
        action = Signal(
            id=str(uuid.uuid4()),
            type="action_item",
            content="An action",
            source_meeting_id="bot-old",
            source_timestamp=_TS,
            entities=[project],
        )

        results = find_supersession_candidates(new_sig, [action])

        assert results == []

    def test_jaccard_value_exact(self):
        """Jaccard = shared / union; with shared=1, union=2 → confidence=0.5."""
        e1 = _entity("project-alpha")
        e2 = _entity("client-beta")
        new_sig = _decision(source_meeting_id="bot-new", entities=[e1, e2])
        # old_sig only has e1 → shared={e1}, union={e1,e2}
        old_sig = _decision(source_meeting_id="bot-old", entities=[e1])

        results = find_supersession_candidates(new_sig, [old_sig])

        assert len(results) == 1
        assert results[0]["confidence"] == 0.5

    def test_perfect_overlap_jaccard_is_one(self):
        """Perfect match → confidence=1.0."""
        e1 = _entity("project-alpha")
        new_sig = _decision(source_meeting_id="bot-new", entities=[e1])
        old_sig = _decision(source_meeting_id="bot-old", entities=[e1])

        results = find_supersession_candidates(new_sig, [old_sig])

        assert results[0]["confidence"] == 1.0

    def test_cap_and_sort_top_three_returned(self):
        """More than 3 candidates → only top 3 by confidence returned."""
        # 5 old signals with varying overlap levels
        e1 = _entity("project-alpha")
        e2 = _entity("client-acme")
        e3 = _entity("project-beta")
        e4 = _entity("client-delta")
        e5 = _entity("project-gamma")

        new_sig = _decision(
            source_meeting_id="bot-new",
            entities=[e1, e2, e3, e4, e5],
        )

        # Build 5 old signals with 1, 2, 3, 4, 5 shared entities respectively
        old_1 = _decision(source_meeting_id="bot-1", entities=[e1])  # 1/5 = 0.2
        old_2 = _decision(source_meeting_id="bot-2", entities=[e1, e2])  # 2/5 = 0.4
        old_3 = _decision(source_meeting_id="bot-3", entities=[e1, e2, e3])  # 3/5 = 0.6
        old_4 = _decision(
            source_meeting_id="bot-4", entities=[e1, e2, e3, e4]
        )  # 4/5 = 0.8
        old_5 = _decision(
            source_meeting_id="bot-5", entities=[e1, e2, e3, e4, e5]
        )  # 5/5 = 1.0

        results = find_supersession_candidates(
            new_sig, [old_1, old_2, old_3, old_4, old_5]
        )

        assert len(results) == 3
        # Top 3 by confidence: old_5 (1.0), old_4 (0.8), old_3 (0.6)
        assert results[0]["old_signal_id"] == old_5.id
        assert results[1]["old_signal_id"] == old_4.id
        assert results[2]["old_signal_id"] == old_3.id

    def test_candidate_dict_has_required_keys(self):
        """Returned candidate dicts have all required keys."""
        project = _entity("project-kb-llm")
        new_sig = _decision(source_meeting_id="bot-new", entities=[project])
        old_sig = _decision(source_meeting_id="bot-old", entities=[project])

        results = find_supersession_candidates(new_sig, [old_sig])

        assert len(results) == 1
        required = {
            "old_signal_id",
            "old_content",
            "matched_entities",
            "reason",
            "confidence",
            "status",
            "proposed_at",
        }
        assert required <= set(results[0].keys())

    def test_reason_contains_entity_name(self):
        """reason field mentions the shared entity name."""
        project = _entity("project-kb-llm")
        new_sig = _decision(source_meeting_id="bot-new", entities=[project])
        old_sig = _decision(source_meeting_id="bot-old", entities=[project])

        results = find_supersession_candidates(new_sig, [old_sig])

        # EntityRef name for "project-kb-llm" is "Kb Llm" from the helper
        assert (
            "Kb Llm" in results[0]["reason"] or "kb-llm" in results[0]["reason"].lower()
        )

    def test_empty_standing_returns_empty(self):
        """Empty standing pool → []."""
        project = _entity("project-kb-llm")
        new_sig = _decision(source_meeting_id="bot-new", entities=[project])

        results = find_supersession_candidates(new_sig, [])

        assert results == []

    def test_max_candidates_parameter(self):
        """max_candidates kwarg overrides default cap."""
        e1 = _entity("project-alpha")
        e2 = _entity("client-acme")
        new_sig = _decision(source_meeting_id="bot-new", entities=[e1, e2])

        old_sigs = [
            _decision(source_meeting_id=f"bot-{i}", entities=[e1]) for i in range(5)
        ]

        results = find_supersession_candidates(new_sig, old_sigs, max_candidates=2)
        assert len(results) == 2

    def test_none_meeting_id_does_not_trigger_same_meeting_exclusion(self):
        """Fix I: two signals with None/empty source_meeting_id are NOT excluded
        by the same-meeting rule (both-sides truthiness guard)."""
        project = _entity("project-kb-llm")
        # Both signals have empty/None source_meeting_id — they are effectively
        # from 'unknown' meetings and should NOT be treated as same-meeting.
        new_sig = Signal(
            id=str(uuid.uuid4()),
            type="decision",
            content="New decision",
            source_meeting_id="",  # empty — falsy
            source_timestamp=_TS,
            entities=[project],
        )
        old_sig = Signal(
            id=str(uuid.uuid4()),
            type="decision",
            content="Old decision",
            source_meeting_id="",  # empty — falsy
            source_timestamp=_TS,
            entities=[project],
        )

        results = find_supersession_candidates(new_sig, [old_sig])

        assert (
            len(results) == 1
        ), "Signals with empty source_meeting_id should NOT be excluded by same-meeting guard"


# ---------------------------------------------------------------------------
# Part 2: DETECT_SUPERSESSION phase tests
# ---------------------------------------------------------------------------


class TestDetectSupersessionPhase:
    """Tests for IngestOrchestrator._phase_detect_supersession."""

    def _make_orchestrator(self):
        """Build a minimal IngestOrchestrator with mocked dependencies."""
        from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

        return IngestOrchestrator(
            classifier=MagicMock(),
            claude_client=MagicMock(),
            graph=MagicMock(),
            signal_writer=MagicMock(),
            git_ops=MagicMock(),
        )

    def _make_meeting_signals(self, *signals):
        """Wrap signals in a MeetingSignals container."""
        return MeetingSignals(
            meeting_id="m-test",
            bot_id="bot-test",
            signals=list(signals),
        )

    def test_decision_gets_candidates_annotation(self, tmp_path):
        """Decision signals acquire metadata.supersession_candidates when non-empty."""
        project = _entity("project-kb-llm")
        new_decision = _decision(
            source_meeting_id="bot-new",
            entities=[project],
        )
        old_decision = _decision(
            source_meeting_id="bot-old",
            entities=[project],
        )
        meeting_signals = self._make_meeting_signals(new_decision)

        # Build a store with the old decision
        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=tmp_path / "signals")
        store.save(
            MeetingSignals(
                meeting_id="m-old",
                bot_id="bot-old",
                signals=[old_decision],
            )
        )

        orchestrator = self._make_orchestrator()

        import asyncio

        asyncio.get_event_loop().run_until_complete(
            orchestrator._phase_detect_supersession(meeting_signals, store)
        )

        # The decision in meeting_signals should now have candidates
        result_signal = meeting_signals.signals[0]
        assert "supersession_candidates" in result_signal.metadata
        candidates = result_signal.metadata["supersession_candidates"]
        assert len(candidates) >= 1
        assert candidates[0]["old_signal_id"] == old_decision.id

    def test_empty_store_phase_completes_no_annotation(self, tmp_path):
        """Empty store → phase completes, no annotation added."""
        project = _entity("project-kb-llm")
        new_decision = _decision(source_meeting_id="bot-new", entities=[project])
        meeting_signals = self._make_meeting_signals(new_decision)

        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=tmp_path / "signals")

        orchestrator = self._make_orchestrator()

        import asyncio

        asyncio.get_event_loop().run_until_complete(
            orchestrator._phase_detect_supersession(meeting_signals, store)
        )

        # No annotation because no candidates found
        result_signal = meeting_signals.signals[0]
        assert "supersession_candidates" not in result_signal.metadata

    def test_store_load_failure_pipeline_continues(self, tmp_path):
        """Store load failure → phase completes, pipeline is not interrupted."""
        project = _entity("project-kb-llm")
        new_decision = _decision(source_meeting_id="bot-new", entities=[project])
        meeting_signals = self._make_meeting_signals(new_decision)

        # Provide a store mock that raises on load_all
        store = MagicMock()
        store.load_all.side_effect = RuntimeError("Disk failure simulated")

        orchestrator = self._make_orchestrator()

        import asyncio

        # Should not raise
        asyncio.get_event_loop().run_until_complete(
            orchestrator._phase_detect_supersession(meeting_signals, store)
        )

        # Signal is untouched
        assert "supersession_candidates" not in meeting_signals.signals[0].metadata

    def test_none_meeting_signals_phase_completes(self, tmp_path):
        """None meeting_signals → phase completes safely."""
        from app.services.signal_store import SignalStore

        store = SignalStore(signals_dir=tmp_path / "signals")
        orchestrator = self._make_orchestrator()

        import asyncio

        # Should not raise
        asyncio.get_event_loop().run_until_complete(
            orchestrator._phase_detect_supersession(None, store)
        )

    def test_phases_constant_includes_detect_supersession(self):
        """PHASES constant should include DETECT_SUPERSESSION and DELTA_REPORT."""
        from app.services.orchestrators.ingest_orchestrator import PHASES

        assert "DETECT_SUPERSESSION" in PHASES
        assert "DELTA_REPORT" in PHASES

    def test_detect_supersession_after_promote_signals(self):
        """DETECT_SUPERSESSION must come after PROMOTE_SIGNALS in PHASES."""
        from app.services.orchestrators.ingest_orchestrator import PHASES

        promote_idx = PHASES.index("PROMOTE_SIGNALS")
        detect_idx = PHASES.index("DETECT_SUPERSESSION")
        assert detect_idx > promote_idx

    def test_detect_supersession_before_enrich_graph(self):
        """DETECT_SUPERSESSION must come before ENRICH_GRAPH in PHASES."""
        from app.services.orchestrators.ingest_orchestrator import PHASES

        detect_idx = PHASES.index("DETECT_SUPERSESSION")
        enrich_idx = PHASES.index("ENRICH_GRAPH")
        assert detect_idx < enrich_idx

    def test_delta_report_after_persist(self):
        """DELTA_REPORT must run AFTER PERSIST (it reports what was actually persisted)."""
        from app.services.orchestrators.ingest_orchestrator import PHASES

        persist_idx = PHASES.index("PERSIST")
        delta_idx = PHASES.index("DELTA_REPORT")
        assert (
            delta_idx > persist_idx
        ), f"DELTA_REPORT (index {delta_idx}) must come after PERSIST (index {persist_idx})"

    def test_delta_report_before_complete(self):
        """DELTA_REPORT must come before COMPLETE in PHASES."""
        from app.services.orchestrators.ingest_orchestrator import PHASES

        delta_idx = PHASES.index("DELTA_REPORT")
        complete_idx = PHASES.index("COMPLETE")
        assert (
            delta_idx < complete_idx
        ), f"DELTA_REPORT (index {delta_idx}) must come before COMPLETE (index {complete_idx})"

    def test_phase_order_full_sequence(self):
        """Pin the full required phase sequence for DETECT_SUPERSESSION and DELTA_REPORT placement."""
        from app.services.orchestrators.ingest_orchestrator import PHASES

        # Required order per spec: PROMOTE_SIGNALS < DETECT_SUPERSESSION < ENRICH_GRAPH
        #                          PERSIST < DELTA_REPORT < COMPLETE
        promote_idx = PHASES.index("PROMOTE_SIGNALS")
        detect_idx = PHASES.index("DETECT_SUPERSESSION")
        enrich_idx = PHASES.index("ENRICH_GRAPH")
        persist_idx = PHASES.index("PERSIST")
        delta_idx = PHASES.index("DELTA_REPORT")
        complete_idx = PHASES.index("COMPLETE")

        assert (
            promote_idx < detect_idx < enrich_idx
        ), "DETECT_SUPERSESSION must be between PROMOTE_SIGNALS and ENRICH_GRAPH"
        assert (
            persist_idx < delta_idx < complete_idx
        ), "DELTA_REPORT must be between PERSIST and COMPLETE"


# ---------------------------------------------------------------------------
# Part 3: Persisted metadata E2E tests
# ---------------------------------------------------------------------------


class TestPersistedMetadataE2E:
    """Prove that supersession_candidates survive serialisation and disk round-trip.

    These tests do NOT need to run the full process() pipeline (which requires
    heavy I/O and container services). Instead they:
    1. Run _phase_detect_supersession directly to attach candidates to a signal.
    2. Serialise MeetingSignals to JSON the same way _phase_persist does
       (model_dump_json), then parse it back to assert the key is present.
    3. Write/read a real file in a tmp dir to prove the disk path is sound.
    """

    def _make_orchestrator(self):
        from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

        return IngestOrchestrator(
            classifier=MagicMock(),
            claude_client=MagicMock(),
            graph=MagicMock(),
            signal_writer=MagicMock(),
            git_ops=MagicMock(),
        )

    def test_supersession_candidates_survive_json_serialisation(self, tmp_path):
        """metadata.supersession_candidates must round-trip through model_dump_json."""
        import asyncio
        import json

        from app.services.signal_store import SignalStore

        project = _entity("project-kb-llm")

        # Build the new decision signal and an old standing one
        new_decision = _decision(source_meeting_id="bot-new", entities=[project])
        old_decision = _decision(source_meeting_id="bot-old", entities=[project])

        meeting_signals = MeetingSignals(
            meeting_id="m-e2e",
            bot_id="bot-e2e",
            signals=[new_decision],
        )

        # Populate the store with the old signal
        store = SignalStore(signals_dir=tmp_path / "signals")
        store.save(
            MeetingSignals(
                meeting_id="m-old",
                bot_id="bot-old",
                signals=[old_decision],
            )
        )

        # Run the detect phase (directly, no full orchestrator)
        orchestrator = self._make_orchestrator()
        asyncio.get_event_loop().run_until_complete(
            orchestrator._phase_detect_supersession(meeting_signals, store)
        )

        # Candidate should be attached in memory
        sig = meeting_signals.signals[0]
        assert (
            "supersession_candidates" in sig.metadata
        ), "supersession_candidates not attached before serialisation"

        # Serialise exactly as _phase_persist does
        serialised = meeting_signals.model_dump_json(indent=2)

        # Parse back
        parsed_data = json.loads(serialised)
        signals_data = parsed_data.get("signals", [])
        assert len(signals_data) == 1

        signal_data = signals_data[0]
        assert "metadata" in signal_data, "metadata key missing in parsed JSON"
        assert (
            "supersession_candidates" in signal_data["metadata"]
        ), "supersession_candidates lost during model_dump_json → json.loads round-trip"
        candidates = signal_data["metadata"]["supersession_candidates"]
        assert len(candidates) >= 1
        assert candidates[0]["old_signal_id"] == old_decision.id

    def test_supersession_candidates_survive_disk_roundtrip(self, tmp_path):
        """Candidates written to a tmp file via model_dump_json can be read back via SignalStore."""
        import asyncio
        import json

        from app.services.signal_store import SignalStore

        project = _entity("project-kb-llm")

        new_decision = _decision(source_meeting_id="bot-new2", entities=[project])
        old_decision = _decision(source_meeting_id="bot-old2", entities=[project])

        signals_dir = tmp_path / "signals"
        store = SignalStore(signals_dir=signals_dir)
        store.save(
            MeetingSignals(
                meeting_id="m-old2",
                bot_id="bot-old2",
                signals=[old_decision],
            )
        )

        meeting_signals = MeetingSignals(
            meeting_id="m-new2",
            bot_id="bot-new2",
            signals=[new_decision],
        )

        orchestrator = self._make_orchestrator()
        asyncio.get_event_loop().run_until_complete(
            orchestrator._phase_detect_supersession(meeting_signals, store)
        )

        # Write to disk the way _phase_persist would (via SignalStore.save)
        written_path = store.save(meeting_signals)
        assert written_path.is_file(), "save() must return the written path"

        # Read back using the store (same as load_all)
        raw = json.loads(written_path.read_text(encoding="utf-8"))
        signals_in_file = raw.get("signals", [])
        assert len(signals_in_file) == 1

        sig_in_file = signals_in_file[0]
        assert "supersession_candidates" in sig_in_file.get(
            "metadata", {}
        ), "supersession_candidates not present in the persisted artifact on disk"
        assert (
            sig_in_file["metadata"]["supersession_candidates"][0]["old_signal_id"]
            == old_decision.id
        )

        # Also confirm it round-trips through MeetingSignals.model_validate
        reloaded = store.load(meeting_signals.bot_id)
        assert reloaded is not None
        reloaded_sig = reloaded.signals[0]
        assert (
            "supersession_candidates" in reloaded_sig.metadata
        ), "supersession_candidates lost when reloading via SignalStore.load()"
