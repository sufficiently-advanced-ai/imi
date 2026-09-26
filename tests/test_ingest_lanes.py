"""ADR-003 in the ingest pipeline: ADMIT drops before anything is built;
library observations have authors not participants, produce claims, link only
to existing entities, never infer relationships or feed profiles; the lane
round-trips through the meeting file so a rebuild applies the same gates."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.ingestion.models import IngestRequest
from app.models.observation import Observation
from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.lane_admission import LaneDecision
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator


def _obs(**kw):
    base = dict(
        observation_id="m1", external_id="ingest-abc", observed_at=datetime(2026, 9, 21, tzinfo=UTC),
        content="body", entities_mentioned={"person": ["Dan"]},
    )
    base.update(kw)
    return Observation(**base)


def _signal(sid="s1", **kw):
    base = dict(id=sid, type="decision", content="OpenAI isolated impacted systems",
                source_meeting_id="b1", source_timestamp="2026-09-21T00:00:00+00:00")
    base.update(kw)
    return Signal(**base)


# ---- observation round trip -------------------------------------------------


def test_lane_and_authors_round_trip_and_record_files_are_unchanged():
    lib = _obs(lane="library", authors=["Nate B Jones"])
    parsed = Observation.from_markdown(lib.to_markdown())
    assert parsed.lane == "library" and parsed.authors == ["Nate B Jones"]

    record_md = _obs().to_markdown()
    assert "lane:" not in record_md and "authors:" not in record_md
    assert Observation.from_markdown(record_md).lane == "record"


# ---- ADMIT ------------------------------------------------------------------


def _orch(**kw):
    base = dict(classifier=MagicMock(), claude_client=None, graph=None,
                signal_writer=None, git_ops=None, tools={})
    base.update(kw)
    return IngestOrchestrator(**base)


@pytest.mark.asyncio
async def test_dropped_content_stops_before_anything_is_built():
    orch = _orch()
    orch._phase_classify = AsyncMock(return_value="document")
    orch._phase_build_observation = AsyncMock()
    drop = LaneDecision(lane="library", drop=True, reason="rule: automated mail (dmarc)")
    job_store: dict = {}
    with (
        patch("app.services.lane_admission.admit", AsyncMock(return_value=drop)),
        patch("app.services.lane_admission.AdmissionLog") as log,
    ):
        result = await orch.process(IngestRequest(content="From: noreply-dmarc-support@google.com\n"),
                                    job_id="j1", job_store=job_store)

    assert result["status"] == "dropped"
    assert result["admission"]["reason"].startswith("rule:")
    assert job_store["job:j1"]["status"] == "dropped"
    assert job_store["job:j1"]["phases_completed"] == ["ADMIT"]
    orch._phase_classify.assert_not_called()
    orch._phase_build_observation.assert_not_called()
    log.return_value.append.assert_called_once()


@pytest.mark.asyncio
async def test_library_observation_has_authors_not_participants():
    orch = _orch()
    request = IngestRequest(content="Newsletter body", title="Issue 12", participants=["Nate B Jones"])
    obs = await orch._phase_build_observation(request, "ingest-abc", "document", "library")
    assert obs.lane == "library"
    assert obs.participants == [] and obs.authors == ["Nate B Jones"]
    assert "person" not in obs.entities_mentioned

    record = await orch._phase_build_observation(request, "ingest-abc", "document")
    assert record.lane == "record" and record.participants == ["Nate B Jones"]


# ---- claims -----------------------------------------------------------------


def test_library_signals_become_attributed_dated_claims():
    obs = _obs(lane="library", authors=["The AI Enterprise"], occurred_at=datetime(2026, 9, 2, tzinfo=UTC))
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        _signal(),
        _signal("s2", type="action_item", owner=EntityRef(id="person-x", type="person", name="X"),
                status="open", due_date="2026-10-01"),
    ])
    IngestOrchestrator._apply_lane_to_signals(ms, obs)

    for sig in ms.signals:
        assert sig.type == "claim" and sig.lane == "library" and sig.stale_after
        assert sig.owner is None and sig.status is None and sig.due_date is None
        assert sig.metadata["attributed_to"] == "The AI Enterprise"
        assert sig.metadata["as_of"].startswith("2026-09-02")
    assert [s.metadata["extracted_type"] for s in ms.signals] == ["decision", "action_item"]


def test_record_signals_are_untouched():
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[_signal()])
    IngestOrchestrator._apply_lane_to_signals(ms, _obs())
    assert ms.signals[0].type == "decision" and ms.signals[0].lane == "record"


# ---- ENRICH_GRAPH -----------------------------------------------------------


class _Graph:
    def __init__(self, nodes):
        self.nodes = dict(nodes)
        self.added: list[str] = []

    async def add_node(self, entity_type, name, entity_id=None, properties=None):
        self.added.append(entity_id)
        self.nodes[entity_id] = name
        return True

    async def create_semantic_relationship(self, **kw):
        raise AssertionError("library content must not write relationships")


class _Writer:
    async def write_meeting_signals(self, ms):
        return len(ms.signals)


def _library_state(**kw):
    base = dict(
        lane="library", participants=[], authors=["Blog Author"], title="An article",
        entities_mentioned={"account": ["Acme", "Brand New"]},
        entity_ids=[], raw_content="Acme and Brand New", content="Acme and Brand New",
        occurred_at=None, external_id="b1", observation_id="m1", metadata={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_library_links_only_to_existing_entities():
    graph = _Graph({"account-acme": "Acme"})
    orch = _orch(graph=graph, signal_writer=_Writer())
    orch._infer_relationships = AsyncMock(side_effect=AssertionError("no inference for library"))
    orch._admit_new_entities = AsyncMock(side_effect=AssertionError("library never admits new entities"))
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[_signal(entities=[
        EntityRef(id="account-acme", type="account", name="Acme"),
        EntityRef(id="account-brand-new", type="account", name="Brand New"),
    ])])
    state = _library_state()

    await orch._phase_enrich_graph(ms, state.raw_content, state)

    assert graph.added == []  # no node created or MERGE-overwritten
    assert "account-brand-new" not in graph.nodes
    assert state.entity_ids == ["account-acme"]
    assert [e.id for e in ms.signals[0].entities] == ["account-acme"]


@pytest.mark.asyncio
async def test_library_skips_profile_enrichment():
    orch = _orch(claude_client=MagicMock())
    state = _library_state(entity_ids=["account-acme"])
    with patch("app.services.signal_store.signal_store") as store, \
         patch("app.services.domain_aware_entity_processor.DomainAwareEntityProcessor") as proc:
        result = await orch._phase_enrich_profiles(state, None, "b1")
    assert result == {"rich_profiles_generated": 0}
    proc.assert_not_called()
    store.save.assert_not_called()  # no signals to save in this case


@pytest.mark.asyncio
async def test_link_only_verification_never_renames_or_splits():
    """entity_link verdicts on library text: rename keeps our name, split is
    an unlink — third-party text never changes or adds graph entities."""
    graph = _Graph({"person-dan-kauppi": "Dan Kauppi", "person-ada": "Ada", "person-bo": "Bo"})
    graph.upgrade_entity_name = AsyncMock()
    orch = _orch(graph=graph)
    verdicts = {
        "person-dan-kauppi": SimpleNamespace(action="split", name="Dan Brown"),
        "person-ada": SimpleNamespace(action="rename", name="Ada Lovelace"),
        "person-bo": SimpleNamespace(action="reassign", name="Bo Participant"),
    }
    entities = [
        {"id": "person-dan-kauppi", "type": "person", "name": "Dan"},
        {"id": "person-ada", "type": "person", "name": "Ada"},
        {"id": "person-bo", "type": "person", "name": "Bo"},
    ]
    with patch("app.services.entity_linking.judge_links", AsyncMock(return_value=verdicts)), \
         patch("app.services.entity_resolver.EntityResolver") as resolver:
        resolver.return_value._candidates.return_value = []
        kept, remap, unlinked = await orch._verify_links(
            entities, _library_state(), "text", link_only=True
        )
    assert [e["id"] for e in kept] == ["person-ada"] and kept[0]["name"] == "Ada"
    assert remap == {} and unlinked == {"person-dan-kauppi", "person-bo"}
    graph.upgrade_entity_name.assert_not_called()


# ---- rebuild ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuild_never_mints_stubs_from_library_name_fields():
    from app.services.graph.neo4j_graph import Neo4jKnowledgeGraph
    from tests.test_neo4j_graph import _make_domain_config

    kg = Neo4jKnowledgeGraph(neo4j_client=AsyncMock(), domain_config=_make_domain_config())
    kg._ensure_entity_exists = AsyncMock()
    names = {"entities_mentioned": {"person": ["Mira Murati"]}, "authors": ["Nate"], "participants": ["Nate"]}

    none = await kg._extract_entity_references("meetings/m.md", {"lane": "library", **names})
    assert none == set()
    kg._ensure_entity_exists.assert_not_called()

    linked = await kg._extract_entity_references(
        "meetings/m.md", {"lane": "library", "entity_ids": ["person-scott-jennings"], **names}
    )
    assert linked == {"person-scott-jennings"}


@pytest.mark.parametrize("link_only,upgraded", [(True, False), (False, True)])
@pytest.mark.asyncio
async def test_library_resolution_never_adopts_a_fuller_name(monkeypatch, link_only, upgraded):
    """A newsletter saying "Ankit Patel" links to our person-ankit "Ankit" but
    must not rename it (record content still may — the control case)."""
    import app.services.entity_resolver as er

    monkeypatch.setattr(er, "_default_decision_client", lambda: None)
    node = SimpleNamespace(id="person-ankit", type="person", name="Ankit",
                           metadata={"aliases": ["Ankit Patel"]})
    graph = SimpleNamespace(nodes={"person-ankit": node}, upgrade_entity_name=AsyncMock(return_value=True))
    orch = _orch(graph=graph)

    entities, id_map = await orch._resolve_collected_entities(
        [{"id": "person-ankit-patel", "type": "person", "name": "Ankit Patel"}], link_only=link_only,
    )

    assert [e["id"] for e in entities] == ["person-ankit"]
    assert id_map == {"person-ankit-patel": "person-ankit"}
    assert graph.upgrade_entity_name.await_count == (1 if upgraded else 0)


def test_dropped_is_terminal_for_the_ingest_stream():
    """The orchestrator emits ingest_dropped; a stream client must not hang."""
    from app.routes.ingest import _INGEST_TERMINAL_TYPES

    assert "ingest_dropped" in _INGEST_TERMINAL_TYPES
