"""ADR-006 §1/§4/§5 in the ingest pipeline.

- link_only (default): library never creates an entity and never consults
  admission — ADR-003 unchanged.
- allowlist: listed types may be created, but only through entity admission
  (it lifts the ban, not the gate); a retype onto an unlisted type is dropped.
- infer_relationships is opt-in for library.
- A claim's source resolves to a Person/Organization; the resolved ids live in
  the signal file (metadata.attributed_to_ids) and become ATTRIBUTED_TO edges,
  so a rebuild from files reproduces them. The attributed_to string stays.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.entity_resolver as er
import app.services.lane_admission as la
from app.models.signal import EntityRef, MeetingSignals, Signal
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

DOMAIN = {"person": "People", "organization": "Companies and institutions", "technology": "Tech"}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("LANES_CONFIG_PATH", str(tmp_path / "absent.yaml"))
    monkeypatch.setattr(er, "_default_decision_client", lambda: None)
    la.reset_lanes_config()
    yield
    la.reset_lanes_config()


@pytest.fixture
def use(monkeypatch, tmp_path):
    def write(text: str) -> None:
        cfg = tmp_path / "lanes.yaml"
        cfg.write_text(text)
        monkeypatch.setenv("LANES_CONFIG_PATH", str(cfg))
        la.reset_lanes_config()

    return write


ALLOW_ORGS = "library:\n  entities:\n    mode: allowlist\n    create_types: [Organization]\n"


def _node(eid, etype, name, aliases=()):
    return SimpleNamespace(id=eid, type=etype, name=name, metadata={"aliases": list(aliases)})


class _Graph:
    def __init__(self, *nodes):
        self.nodes = {n.id: n for n in nodes}
        self.added: list[str] = []
        self.relationships_written = 0

    async def add_node(self, entity_type, name, entity_id=None, properties=None):
        self.added.append(entity_id)
        self.nodes[entity_id] = _node(entity_id, entity_type, name)
        return True

    async def create_semantic_relationship(self, **kw):
        self.relationships_written += 1
        return True


class _Writer:
    def __init__(self):
        self.written: list[MeetingSignals] = []

    async def write_meeting_signals(self, ms):
        self.written.append(ms)
        return len(ms.signals)


def _orch(graph, writer=None):
    orch = IngestOrchestrator(
        classifier=MagicMock(), claude_client=None, graph=graph,
        signal_writer=writer or _Writer(), git_ops=None, tools={},
    )
    orch._entity_type_descriptions = lambda: dict(DOMAIN)
    orch._filter_to_domain_entities = lambda entities: IngestOrchestrator._filter_to_domain_entities(
        entities, valid_types=set(DOMAIN)
    )
    orch._link_entity_files = AsyncMock()
    return orch


def _claim(sid="s1", entities=(), **meta):
    return Signal(
        id=sid, type="claim", lane="library", content="Direct air capture costs fall below $200/t",
        source_meeting_id="b1", source_timestamp="2026-05-02T00:00:00+00:00",
        entities=list(entities), metadata={"attributed_to": "Carbon Desk", **meta},
    )


def _state(**kw):
    base = dict(
        lane="library", participants=[], authors=["Carbon Desk"], title="Weekly outlook",
        entities_mentioned={}, entity_ids=[], raw_content="body", content="body",
        occurred_at=datetime(2026, 5, 2, tzinfo=UTC), external_id="b1", observation_id="m1",
        metadata={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _refs(*pairs):
    return [EntityRef(id=f"{t}-{n.lower().replace(' ', '-')}", type=t, name=n) for t, n in pairs]


async def _enrich(orch, ms, state, verdicts=None):
    with (
        patch("app.services.entity_linking.judge_links", AsyncMock(return_value={})),
        patch(
            "app.services.entity_admission.judge_entities", AsyncMock(return_value=verdicts or {})
        ) as judge,
    ):
        result = await orch._phase_enrich_graph(ms, state.raw_content, state)
    return result, judge


# ---- §4 entity policy -----------------------------------------------------------


@pytest.mark.asyncio
async def test_link_only_default_creates_nothing_and_never_asks_admission():
    graph = _Graph(_node("technology-dac", "technology", "DAC"))
    orch = _orch(graph)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        _claim(entities=_refs(("technology", "DAC"), ("organization", "Aircap Labs")))
    ])

    _, judge = await _enrich(orch, ms, _state())

    judge.assert_not_called()
    assert graph.added == []
    assert [e.id for e in ms.signals[0].entities] == ["technology-dac"]


@pytest.mark.asyncio
async def test_allowlist_creates_listed_types_through_admission(use):
    use(ALLOW_ORGS)
    graph = _Graph(_node("technology-dac", "technology", "DAC"))
    orch = _orch(graph)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        _claim(entities=_refs(("technology", "DAC"), ("organization", "Aircap Labs"), ("person", "Jo Analyst")))
    ])
    state = _state(authors=[])

    _, judge = await _enrich(orch, ms, state)

    # Only the listed type reaches the gate; the person is dropped unasked
    judge.assert_awaited_once()
    mentions = judge.await_args.args[0]
    assert [(m["type"], m["name"]) for m in mentions] == [("organization", "Aircap Labs")]
    assert graph.added == ["organization-aircap-labs"]  # existing DAC not re-written
    assert sorted(e.id for e in ms.signals[0].entities) == ["organization-aircap-labs", "technology-dac"]
    assert state.entity_ids == ["organization-aircap-labs", "technology-dac"]


@pytest.mark.asyncio
async def test_allowlist_respects_an_admission_drop(use):
    use(ALLOW_ORGS)
    graph = _Graph()
    orch = _orch(graph)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        _claim(entities=_refs(("organization", "The Industry")))
    ])
    verdicts = {("organization", "The Industry"): SimpleNamespace(action="drop")}

    await _enrich(orch, ms, _state(authors=[]), verdicts)

    assert graph.added == [] and ms.signals[0].entities == []


@pytest.mark.asyncio
async def test_allowlist_drops_a_retype_onto_an_unlisted_type(use):
    use(ALLOW_ORGS)
    graph = _Graph()
    orch = _orch(graph)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        _claim(entities=_refs(("organization", "Ada Ng")))
    ])
    verdicts = {("organization", "Ada Ng"): SimpleNamespace(action="retype", new_type="person")}

    await _enrich(orch, ms, _state(authors=[]), verdicts)

    assert graph.added == [] and ms.signals[0].entities == []


@pytest.mark.asyncio
async def test_allowlist_create_types_outside_the_domain_create_nothing(use):
    use("library:\n  entities:\n    mode: allowlist\n    create_types: [spaceship]\n")
    graph = _Graph()
    orch = _orch(graph)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        _claim(entities=_refs(("organization", "Aircap Labs")))
    ])

    _, judge = await _enrich(orch, ms, _state(authors=[]))

    judge.assert_not_called()
    assert graph.added == []


@pytest.mark.asyncio
async def test_allowlist_verification_resolves_a_new_entitys_fuller_name():
    """link verification on library text still never renames OUR entities,
    but an entity being created in this ingest may take its fuller name."""
    graph = _Graph(_node("organization-acme", "organization", "Acme"))
    graph.upgrade_entity_name = AsyncMock()
    orch = _orch(graph)
    verdicts = {
        "organization-acme": SimpleNamespace(action="rename", name="Acme Holdings"),
        "organization-aircap": SimpleNamespace(action="rename", name="Aircap Labs"),
    }
    entities = [
        {"id": "organization-acme", "type": "organization", "name": "Acme"},
        {"id": "organization-aircap", "type": "organization", "name": "Aircap"},
    ]
    with patch("app.services.entity_linking.judge_links", AsyncMock(return_value=verdicts)):
        kept, remap, unlinked = await orch._verify_links(entities, _state(), "text", link_only=True)

    assert {e["id"]: e["name"] for e in kept} == {
        "organization-acme": "Acme", "organization-aircap-labs": "Aircap Labs",
    }
    assert remap == {"organization-aircap": "organization-aircap-labs"} and unlinked == set()
    graph.upgrade_entity_name.assert_not_called()


# ---- §1 infer_relationships -------------------------------------------------------


@pytest.mark.parametrize("config, expected", [("", 0), ("library:\n  infer_relationships: true\n", 1)])
@pytest.mark.asyncio
async def test_library_relationship_inference_is_opt_in(use, config, expected):
    if config:
        use(config)
    graph = _Graph(_node("technology-dac", "technology", "DAC"), _node("organization-acme", "organization", "Acme"))
    orch = _orch(graph)
    orch._infer_relationships = AsyncMock(return_value=[])
    orch._write_relationship_edges = AsyncMock(return_value=0)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        _claim(entities=_refs(("technology", "DAC"), ("organization", "Acme")))
    ])

    await _enrich(orch, ms, _state(authors=[]))

    assert orch._infer_relationships.await_count == expected


# ---- §5 attribution ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_attribution_links_an_existing_source():
    graph = _Graph(_node("organization-carbon-desk", "organization", "Carbon Desk"))
    orch = _orch(graph)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[_claim(), _claim("s2")])

    await _enrich(orch, ms, _state())

    for sig in ms.signals:
        assert sig.metadata["attributed_to_ids"] == ["organization-carbon-desk"]
        assert sig.metadata["attributed_to"] == "Carbon Desk"  # string kept
    assert graph.added == []


@pytest.mark.asyncio
async def test_attribution_prefers_people_then_organizations_and_matches_aliases():
    graph = _Graph(
        _node("person-jo-analyst", "person", "Jo Analyst", aliases=["J. Analyst"]),
        _node("organization-carbon-desk", "organization", "Carbon Desk"),
    )
    orch = _orch(graph)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[_claim()])

    await _enrich(orch, ms, _state(authors=["J. Analyst", "Carbon Desk"]))

    assert ms.signals[0].metadata["attributed_to_ids"] == ["person-jo-analyst", "organization-carbon-desk"]


@pytest.mark.asyncio
async def test_link_only_attribution_never_creates_a_source():
    graph = _Graph()
    orch = _orch(graph)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[_claim()])

    _, judge = await _enrich(orch, ms, _state())

    judge.assert_not_called()
    assert graph.added == []
    assert "attributed_to_ids" not in ms.signals[0].metadata
    assert ms.signals[0].metadata["attributed_to"] == "Carbon Desk"


@pytest.mark.asyncio
async def test_allowlisted_attribution_creates_the_source_through_admission(use):
    use(ALLOW_ORGS)
    graph = _Graph()
    orch = _orch(graph)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[_claim()])

    _, judge = await _enrich(orch, ms, _state())

    mentions = judge.await_args.args[0]
    assert [(m["type"], m["name"]) for m in mentions] == [("organization", "Carbon Desk")]
    assert graph.added == ["organization-carbon-desk"]
    assert ms.signals[0].metadata["attributed_to_ids"] == ["organization-carbon-desk"]


@pytest.mark.asyncio
async def test_allowlisted_attribution_respects_an_admission_drop(use):
    use(ALLOW_ORGS)
    graph = _Graph()
    orch = _orch(graph)
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[_claim()])
    verdicts = {("organization", "Carbon Desk"): SimpleNamespace(action="drop")}

    await _enrich(orch, ms, _state(), verdicts)

    assert graph.added == [] and "attributed_to_ids" not in ms.signals[0].metadata


@pytest.mark.asyncio
async def test_record_content_is_never_attributed():
    graph = _Graph(_node("organization-carbon-desk", "organization", "Carbon Desk"))
    orch = _orch(graph)
    orch._admit_new_entities = AsyncMock(return_value=([], {}, set()))
    orch._infer_relationships = AsyncMock(return_value=[])
    sig = Signal(id="s1", type="decision", content="x", source_meeting_id="b1",
                 source_timestamp="2026-05-02T00:00:00+00:00")
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[sig])

    await _enrich(orch, ms, _state(lane="record", authors=["Carbon Desk"]))

    assert "attributed_to_ids" not in sig.metadata


@pytest.mark.asyncio
async def test_publisher_metadata_becomes_a_claim_source():
    from app.models.ingestion.models import IngestRequest

    orch = IngestOrchestrator(classifier=MagicMock(), claude_client=None, graph=None,
                              signal_writer=None, git_ops=None, tools={})
    request = IngestRequest(content="Body", title="Outlook", participants=["Jo Analyst"],
                            metadata={"publisher": "Carbon Desk"})
    obs = await orch._phase_build_observation(request, "ingest-abc", "document", "library")
    assert obs.authors == ["Jo Analyst", "Carbon Desk"] and obs.participants == []

    record = await orch._phase_build_observation(request, "ingest-abc", "document")
    assert record.authors == [] and record.participants == ["Jo Analyst"]


# ---- graph write + rebuild reproducibility ------------------------------------------


class _Client:
    def __init__(self):
        self.writes: list[tuple[str, dict]] = []

    async def execute_write(self, query, params):
        self.writes.append((query, params))
        return [{"id": params.get("id")}]


def _attributed(client):
    return [p["entity_id"] for q, p in client.writes if "ATTRIBUTED_TO" in q]


@pytest.mark.asyncio
async def test_writer_reproduces_attribution_from_the_signal_file():
    """The edge comes from the persisted signal JSON alone — what a rebuild replays."""
    from app.services.graph.signal_graph_writer import SignalGraphWriter

    sig = _claim(attributed_to_ids=["organization-carbon-desk", "person-jo-analyst"])
    sig.stale_after = "2026-11-01T00:00:00+00:00"
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[sig])
    from_file = MeetingSignals.model_validate_json(ms.model_dump_json())

    client = _Client()
    await SignalGraphWriter(client).write_meeting_signals(from_file)

    assert _attributed(client) == ["organization-carbon-desk", "person-jo-analyst"]
    node = next(p for q, p in client.writes if "MERGE (s:Signal" in q)
    assert node["lane"] == "library"
    assert node["stale_after"] == datetime(2026, 11, 1, tzinfo=UTC)  # typed DATETIME
    assert node["attributed_to"] == "Carbon Desk"


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [None, "organization-x", 7, ["", None]])
async def test_writer_tolerates_missing_or_malformed_attribution(ids):
    from app.services.graph.signal_graph_writer import SignalGraphWriter

    sig = _claim() if ids is None else _claim(attributed_to_ids=ids)
    client = _Client()
    await SignalGraphWriter(client).write_meeting_signals(
        MeetingSignals(meeting_id="m1", bot_id="b1", signals=[sig])
    )
    assert _attributed(client) == (["organization-x"] if ids == "organization-x" else [])


@pytest.mark.asyncio
async def test_record_signals_never_get_attribution_edges():
    from app.services.graph.signal_graph_writer import SignalGraphWriter

    sig = Signal(id="s1", type="decision", content="x", source_meeting_id="b1",
                 source_timestamp="2026-05-02T00:00:00+00:00",
                 metadata={"attributed_to_ids": ["organization-x"]})
    client = _Client()
    await SignalGraphWriter(client).write_meeting_signals(
        MeetingSignals(meeting_id="m1", bot_id="b1", signals=[sig])
    )
    assert _attributed(client) == []
    node = next(p for q, p in client.writes if "MERGE (s:Signal" in q)
    assert node["lane"] == "record" and node["stale_after"] is None


def test_entity_merges_rewrite_attribution_in_the_signal_file():
    from app.services.signal_store import remap_entity_refs

    ms = MeetingSignals(meeting_id="m1", bot_id="b1", signals=[
        _claim(attributed_to_ids=["organization-carbon-desk-inc", "person-jo"])
    ])
    changed = remap_entity_refs(ms, {"organization-carbon-desk-inc": "organization-carbon-desk"})
    assert changed
    assert ms.signals[0].metadata["attributed_to_ids"] == ["organization-carbon-desk", "person-jo"]
