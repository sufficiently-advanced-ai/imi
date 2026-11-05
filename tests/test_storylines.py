"""Storylines: seed-file fields and status, the reviewer write path, the
server-owned field guard, history folding, proposals and the MCP surface."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import storylines
from app.services.library_seeds import CANONICAL, PROVISIONAL, offered, parse_seed_file, sync_role
from app.services.storylines import StorylineError


class _Node(SimpleNamespace):
    pass


class _Neo4j:
    def __init__(self):
        self.queries = []

    async def execute_read(self, query, params):
        self.queries.append((query, params))
        return []


class _Git:
    def __init__(self, repo_path):
        self.repo_path, self.commits = str(repo_path), []

    async def commit_and_push(self, paths, message):
        self.commits.append((paths, message))


class _KG:
    def __init__(self, repo_path, nodes=()):
        self.nodes = {n.id: n for n in nodes}
        self.domain = SimpleNamespace(entities={"storyline": None, "topic": None, "technology": None})
        self.neo4j = _Neo4j()
        self.git_ops = _Git(repo_path)


class _Writer:
    def __init__(self, kg):
        self.kg, self.created, self.updates = kg, [], []

    async def create(self, entity_type, name, entity_id, properties, aliases=()):
        self.kg.nodes[entity_id] = _Node(id=entity_id, name=name, type=entity_type,
                                         metadata=dict(properties, aliases=list(aliases)))
        self.created.append(entity_id)
        return entity_id

    async def update(self, entity_id, changes):
        node = self.kg.nodes[entity_id]
        if "name" in changes:
            node.name = changes["name"]
        for k, v in changes.items():
            if k == "name":
                continue
            if v is None:  # None clears, as kg.update_node does
                node.metadata.pop(k, None)
            else:
                node.metadata[k] = v
        self.updates.append((entity_id, changes))


@pytest.fixture
def kg(tmp_path, monkeypatch):
    graph = _KG(tmp_path)
    writer = _Writer(graph)
    monkeypatch.setattr(storylines, "storyline_type", lambda _kg: "storyline")
    monkeypatch.setattr(storylines, "_writer", lambda _kg: writer)
    monkeypatch.setattr(storylines, "_graph", lambda: graph)
    graph.writer = writer
    return graph


def _seed(tmp_path, text):
    (tmp_path / "seeds").mkdir(exist_ok=True)
    (tmp_path / "seeds" / "storyline.yaml").write_text(text)


# --- seed file ------------------------------------------------------------------


def test_parse_reads_storyline_fields_and_only_closed_status():
    seeds = parse_seed_file(
        "- name: Local models closing on frontier\n"
        "  thesis: Open weights reach frontier parity within a year.\n"
        "  stakes: [build, writing]\n"
        "  would_change: A widening gap on agentic evals.\n"
        "  status: closed\n"
        "- name: Bad stakes\n  stakes: [gambling]\n  status: active\n",
        "storyline", "storyline",
    )
    first, second = seeds
    assert first.fields == {"thesis": "Open weights reach frontier parity within a year.",
                            "stakes": ["build", "writing"],
                            "would_change": "A widening gap on agentic evals."}
    assert first.status == "closed"
    assert second.fields == {} and second.status is None  # bad stakes skipped, not the seed
    # other roles ignore the extra keys
    assert parse_seed_file("- name: X\n  thesis: t\n", "topic", "topic")[0].fields == {}


@pytest.mark.asyncio
async def test_seed_sync_creates_active_storyline_with_fields_and_history(tmp_path, kg):
    _seed(tmp_path, "- name: Embedded boards as compute\n  thesis: Cheap boards run real models.\n"
                    "  stakes: build\n  open_question: Which workloads first?\n")

    result = await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")

    sid = "storyline-embedded-boards-as-compute"
    assert result.created == [sid]
    meta = kg.nodes[sid].metadata
    assert meta["status"] == "active" and meta["seed_status"] == CANONICAL
    assert meta["thesis"] == "Cheap boards run real models." and meta["stakes"] == ["build"]
    assert meta["open_question"] == "Which workloads first?"
    assert result.paths == [storylines.history_relpath(sid)]
    rows = storylines.read_history(tmp_path, sid)
    assert rows[0]["status_to"] == "active" and rows[0]["actor"] == "seed_sync"

    again = await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")
    assert again.as_dict() == {} and again.paths == []


@pytest.mark.asyncio
async def test_seed_owns_thesis_but_only_seeds_reviewer_fields_while_empty(tmp_path, kg):
    _seed(tmp_path, "- name: Arc\n  thesis: Old.\n  would_change: Seeded framing.\n")
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")
    kg.nodes["storyline-arc"].metadata["would_change"] = "Reviewer's framing."

    _seed(tmp_path, "- name: Arc\n  thesis: New.\n  would_change: Changed seed framing.\n"
                    "  open_question: Seeded question.\n")
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")

    meta = kg.nodes["storyline-arc"].metadata
    assert meta["thesis"] == "New."                          # owned: synced
    assert meta["would_change"] == "Reviewer's framing."     # if-absent: not overwritten
    assert meta["open_question"] == "Seeded question."       # if-absent: filled while empty


@pytest.mark.asyncio
async def test_seed_closes_reopens_and_adopts_proposals(tmp_path, kg):
    kg.nodes["storyline-proposal"] = _Node(id="storyline-proposal", name="Proposal", type="storyline",
                                           metadata={"seed_status": PROVISIONAL, "status": "proposed"})
    _seed(tmp_path, "- name: Arc\n  status: closed\n- name: Proposal\n  id: storyline-proposal\n")
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")
    assert kg.nodes["storyline-arc"].metadata["status"] == "closed"
    assert kg.nodes["storyline-proposal"].metadata["status"] == "active"
    assert kg.nodes["storyline-proposal"].metadata["seed_status"] == CANONICAL
    assert "storyline-arc" not in {o["id"] for o in offered(kg, "storyline")}  # closed: not offered

    _seed(tmp_path, "- name: Arc\n- name: Proposal\n  id: storyline-proposal\n")
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")
    assert kg.nodes["storyline-arc"].metadata["status"] == "active"  # the file closed it, so it reopens

    kg.nodes["storyline-arc"].metadata.update(status="dormant")  # lifecycle's, not the file's
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")
    assert kg.nodes["storyline-arc"].metadata["status"] == "dormant"


# --- reviewer write path -----------------------------------------------------------


def test_validate_review_refuses_server_fields_and_bad_enums():
    with pytest.raises(StorylineError, match="status is set by the server only"):
        storylines.validate_review({"state": "x", "status": "active"})
    with pytest.raises(StorylineError, match="direction must be one of"):
        storylines.validate_review({"direction": "sideways"})
    assert storylines.validate_review({"direction": "Fading", "state": "  s  "}) == {
        "direction": "fading", "state": "s"}


@pytest.mark.asyncio
async def test_update_writes_fields_and_versions(tmp_path, kg):
    kg.nodes["storyline-arc"] = _Node(id="storyline-arc", name="Arc", type="storyline",
                                      metadata={"status": "active"})
    out = await storylines.update_storyline(
        "storyline-arc", {"state": "Gap narrowing.", "confidence": "med"}, actor="mcp:lcars")

    meta = kg.nodes["storyline-arc"].metadata
    assert meta["state"] == "Gap narrowing." and meta["confidence"] == "med" and meta["last_reviewed"]
    assert out["id"] == "storyline-arc" and out["versions"] == 1
    row = storylines.read_history(tmp_path, "storyline-arc")[0]
    assert row["actor"] == "mcp:lcars" and row["fields"] == {"state": "Gap narrowing.", "confidence": "med"}
    assert kg.git_ops.commits[-1][0] == [storylines.history_relpath("storyline-arc")]

    with pytest.raises(StorylineError, match="no storyline"):
        await storylines.update_storyline("topic-x", {"state": "s"}, actor="mcp")
    kg.nodes["storyline-arc"].metadata["status"] = "closed"
    with pytest.raises(StorylineError, match="closed"):
        await storylines.update_storyline("storyline-arc", {"state": "s"}, actor="mcp")


def test_state_as_of_folds_versions_up_to_the_timestamp():
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    history = [
        {"at": t0.isoformat(), "event": "status", "status_to": "active"},
        {"at": (t0 + timedelta(days=7)).isoformat(), "event": "review", "fields": {"state": "A", "confidence": "low"}},
        {"at": (t0 + timedelta(days=14)).isoformat(), "event": "review", "fields": {"state": "B"}},
    ]
    mid = storylines.state_as_of(history, t0 + timedelta(days=10))
    assert mid["state"] == "A" and mid["confidence"] == "low" and mid["status"] == "active"
    assert storylines.state_as_of(history, t0 + timedelta(days=30))["state"] == "B"


@pytest.mark.asyncio
async def test_propose_is_provisional_and_idempotent(tmp_path, kg):
    out = await storylines.propose_storyline("Agent control-plane land grab", thesis="Platforms race.",
                                             stakes=["practice"], actor="mcp:lcars")
    sid = "storyline-agent-control-plane-land-grab"
    assert out["created"] is True and out["id"] == sid
    meta = kg.nodes[sid].metadata
    assert meta["status"] == "proposed" and meta["seed_status"] == PROVISIONAL
    assert meta["proposed_by"] == "mcp:lcars"

    again = await storylines.propose_storyline("Agent control-plane land grab", actor="mcp:other")
    assert again["created"] is False and kg.writer.created == [sid]

    with pytest.raises(StorylineError, match="stakes"):
        await storylines.propose_storyline("Other", stakes=["fun"], actor="mcp")


# --- server-owned field guard -----------------------------------------------------


def test_guard_refuses_server_and_review_fields_on_storylines_only(kg):
    with pytest.raises(StorylineError, match="status"):
        storylines.guard_client_properties(kg, "storyline", {"status": "active"})
    # reviewer fields go through update_storyline (validation, closed check, history)
    with pytest.raises(StorylineError, match="update_storyline"):
        storylines.guard_client_properties(kg, "storyline", {"state": "changed while closed",
                                                             "direction": "invalid"})
    storylines.guard_client_properties(kg, "storyline", {"description": "fine"})
    storylines.guard_client_properties(kg, "project", {"status": "active", "state": "x"})  # other types untouched
    # an edit form echoing an unchanged value is not a change
    assert storylines.server_field_changes(kg, "storyline", {"status": "active"}, {"status": "active"}) == []
    assert storylines.server_field_changes(kg, "storyline", {"status": "closed"}, {"status": "active"}) == ["status"]
    assert storylines.server_field_changes(kg, "storyline", {"state": "same"}, {"state": "same"}) == []
    assert storylines.server_field_changes(kg, "storyline", {"state": "new", "thesis": "t"},
                                           {"state": "old"}) == ["state"]


@pytest.mark.asyncio
async def test_generic_update_node_tool_refuses_review_fields(kg, monkeypatch):
    from app.services.tools import graph_node_tools

    kg.nodes["storyline-arc"] = _Node(id="storyline-arc", name="Arc", type="storyline",
                                      metadata={"status": "closed"})
    monkeypatch.setattr(graph_node_tools, "_get_graph_service", lambda: kg)
    tool = graph_node_tools.UpdateNodeTool(None, kg.git_ops, None)
    result = await tool.execute({"entity_id": "storyline-arc",
                                 "properties": {"state": "changed while closed"}})
    assert result.success is False and "update_storyline" in result.error
    assert "state" not in kg.nodes["storyline-arc"].metadata


def test_rest_entity_editor_refuses_changed_review_fields(kg, monkeypatch):
    from app.services import entity_crud
    from app.services.graph import factory

    monkeypatch.setattr(factory, "get_knowledge_graph", lambda: kg)
    assert "update_storyline" in entity_crud._storyline_server_fields(
        "storyline", {"open_question": "new?"}, {"open_question": "old?"})
    assert entity_crud._storyline_server_fields(
        "storyline", {"open_question": "old?", "description": "d"}, {"open_question": "old?"}) is None


# --- reviewer write path ------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_storyline_clears_a_field_and_history_records_it(tmp_path, kg):
    kg.nodes["storyline-arc"] = _Node(id="storyline-arc", name="Arc", type="storyline",
                                      metadata={"status": "active"})
    await storylines.update_storyline("storyline-arc", {"state": "Set", "open_question": "Q?"}, actor="me")
    assert kg.nodes["storyline-arc"].metadata["state"] == "Set"
    await storylines.update_storyline("storyline-arc", {"state": "", "open_question": None}, actor="me")
    meta = kg.nodes["storyline-arc"].metadata
    assert "state" not in meta and "open_question" not in meta
    # the writer was asked to clear (None), not to skip the fields
    assert kg.writer.updates[-1][1]["state"] is None and kg.writer.updates[-1][1]["open_question"] is None
    folded = storylines.state_as_of(storylines.read_history(tmp_path, "storyline-arc"),
                                    datetime.now(UTC) + timedelta(seconds=1))
    assert folded["state"] is None and folded["open_question"] is None


# --- reads ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_filters_by_link_time_with_typed_parameters(kg):
    kg.nodes["storyline-x"] = _Node(id="storyline-x", name="X", type="storyline", metadata={})
    out = await storylines.storyline_evidence("storyline-x", linked_since="2026-11-03", kg=kg)
    query, params = kg.neo4j.queries[-1]
    assert "datetime(r.linked_at) >= $linked_since" in query and "properties(x)] AS edges" in query
    assert params["linked_since"] == datetime(2026, 11, 3, tzinfo=UTC)  # typed (ADR-004)
    assert params["since"] is None and out["linked_since"] == "2026-11-03T00:00:00+00:00"
    await storylines.storyline_evidence("storyline-x", kg=kg)
    assert kg.neo4j.queries[-1][1]["linked_since"] is None
    with pytest.raises(StorylineError, match="linked_since"):
        await storylines.storyline_evidence("storyline-x", linked_since="last tuesday", kg=kg)


def test_link_edges_carry_linked_at_as_datetime():
    from app.services.capture_link_store import CaptureLink
    from app.services.graph.capture_graph_writer import _edge_props

    link = CaptureLink(edge="ADVANCES", target_id="storyline-x", target_type="storyline",
                       run_id="r1", linked_at="2026-11-03T19:08:12+00:00", stance="challenges")
    assert _edge_props(link, "record")["linked_at"] == datetime(2026, 11, 3, 19, 8, 12, tzinfo=UTC)



@pytest.mark.asyncio
async def test_activity_picks_the_edge_per_type_and_validates(kg, monkeypatch):
    from app.services import capture_link_policy

    monkeypatch.setattr(capture_link_policy, "linking_policy", lambda: capture_link_policy.LinkingPolicy())
    await storylines.activity("topic", 14, kg=kg)
    await storylines.activity("technology", 7, kg=kg)
    (q_topic, params), (q_tech, _) = kg.neo4j.queries
    assert "-[:ABOUT]->(e:Topic)" in q_topic
    assert "(e:Technology)-[:MENTIONED_IN]->(d:Document)" in q_tech
    assert params["end"] - params["recent_start"] == timedelta(days=14)
    assert isinstance(params["prior_start"], datetime)  # typed, never a string (ADR-004)
    with pytest.raises(StorylineError, match="unknown type"):
        await storylines.activity("spaceship", 14, kg=kg)
    with pytest.raises(StorylineError, match="window"):
        await storylines.activity("topic", 0, kg=kg)


@pytest.mark.asyncio
async def test_mcp_update_refuses_status(monkeypatch, kg):
    from app.routes import mcp_server

    kg.nodes["storyline-arc"] = _Node(id="storyline-arc", name="Arc", type="storyline", metadata={})
    out = await mcp_server._storyline_tool(
        "update_storyline", {"entity_id": "storyline-arc", "status": "active"}, "mcp:x")
    assert "set by the server only" in out[0].text


# --- lifecycle --------------------------------------------------------------------


def test_lifecycle_rules():
    now = datetime(2026, 11, 2, tzinfo=UTC)
    old = (now - timedelta(days=40)).isoformat()
    new = (now - timedelta(days=3)).isoformat()
    rule = storylines.lifecycle_transition
    assert rule("proposed", 5, 5, old, now)[0] == "active"
    assert rule("proposed", 4, 9, old, now) is None
    assert rule(None, 5, 5, old, now)[0] == "active"          # no status counts as proposed
    assert rule("active", 0, 0, old, now)[0] == "dormant"
    assert rule("active", 0, 0, new, now) is None             # grace period after a change
    assert rule("active", 0, 1, old, now) is None
    assert rule("dormant", 0, 1, old, now)[0] == "active"
    assert rule("closed", 9, 9, old, now) is None


class _LifecycleNeo4j:
    def __init__(self, rows):
        self.rows, self.params = rows, None

    async def execute_read(self, query, params):
        self.params = params
        return self.rows


@pytest.mark.asyncio
async def test_apply_and_revert_lifecycle(tmp_path, kg):
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    kg.nodes["storyline-a"] = _Node(id="storyline-a", name="A", type="storyline",
                                    metadata={"status": "proposed"})
    kg.nodes["storyline-b"] = _Node(id="storyline-b", name="B", type="storyline",
                                    metadata={"status": "active", "status_changed_at": old})
    kg.neo4j = _LifecycleNeo4j([
        {"id": "storyline-a", "status": "proposed", "changed_at": old, "promote": 6, "dormant": 6},
        {"id": "storyline-b", "status": "active", "changed_at": old, "promote": 0, "dormant": 0},
    ])
    rows, paths = await storylines.apply_lifecycle(kg, kg.writer, tmp_path, "link-1")
    assert [(r["entity_id"], r["status_from"], r["status_to"]) for r in rows] == [
        ("storyline-a", "proposed", "active"), ("storyline-b", "active", "dormant")]
    assert kg.nodes["storyline-a"].metadata["status"] == "active"
    assert isinstance(kg.neo4j.params["now"], datetime)  # typed parameters (ADR-004)
    assert len(paths) == 2 and kg.git_ops.commits == []  # the run commits them

    await storylines.revert_lifecycle(kg, kg.writer, tmp_path, "link-1", rows)
    assert kg.nodes["storyline-a"].metadata["status"] == "proposed"
    assert kg.nodes["storyline-b"].metadata["status"] == "active"
    last = storylines.read_history(tmp_path, "storyline-a")[-1]
    assert last["actor"] == "revert" and last["status_to"] == "proposed"


@pytest.mark.asyncio
async def test_revert_does_not_undo_a_later_runs_transition(tmp_path, kg):
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    kg.nodes["storyline-a"] = _Node(id="storyline-a", name="A", type="storyline",
                                    metadata={"status": "proposed"})
    kg.neo4j = _LifecycleNeo4j([
        {"id": "storyline-a", "status": "proposed", "changed_at": old, "promote": 6, "dormant": 6}])
    rows_a, _ = await storylines.apply_lifecycle(kg, kg.writer, tmp_path, "run-a")   # proposed -> active
    kg.neo4j = _LifecycleNeo4j([
        {"id": "storyline-a", "status": "active", "changed_at": old, "promote": 0, "dormant": 0}])
    await storylines.apply_lifecycle(kg, kg.writer, tmp_path, "run-b")              # active -> dormant
    kg.neo4j = _LifecycleNeo4j([
        {"id": "storyline-a", "status": "dormant", "changed_at": old, "promote": 1, "dormant": 1}])
    await storylines.apply_lifecycle(kg, kg.writer, tmp_path, "run-c")              # dormant -> active
    meta = kg.nodes["storyline-a"].metadata
    assert meta["status"] == "active" and meta["lifecycle_run_id"] == "run-c"

    assert await storylines.revert_lifecycle(kg, kg.writer, tmp_path, "run-a", rows_a) == []
    assert kg.nodes["storyline-a"].metadata["status"] == "active"  # run C's decision stands


@pytest.mark.asyncio
async def test_seed_close_reopen_takes_ownership_from_the_run(tmp_path, kg):
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    sid = "storyline-arc"
    _seed(tmp_path, "- name: Arc\n")
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")      # seeded active
    kg.nodes[sid].metadata["status"] = "proposed"
    kg.neo4j = _LifecycleNeo4j([
        {"id": sid, "status": "proposed", "changed_at": old, "promote": 6, "dormant": 6}])
    rows, _ = await storylines.apply_lifecycle(kg, kg.writer, tmp_path, "run-a")
    assert kg.nodes[sid].metadata["lifecycle_run_id"] == "run-a"

    _seed(tmp_path, "- name: Arc\n  status: closed\n")
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")      # closed by the seed
    _seed(tmp_path, "- name: Arc\n")
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")      # reopened: active again
    meta = kg.nodes[sid].metadata
    assert meta["status"] == "active" and "lifecycle_run_id" not in meta and "closed_by" not in meta

    assert await storylines.revert_lifecycle(kg, kg.writer, tmp_path, "run-a", rows) == []
    assert kg.nodes[sid].metadata["status"] == "active"


# --- seed fields in history -------------------------------------------------------


@pytest.mark.asyncio
async def test_seeded_reviewer_fields_are_in_history(tmp_path, kg):
    sid = "storyline-arc"
    _seed(tmp_path, "- name: Arc\n  open_question: Original question\n"
                    "  would_change: Original condition\n")
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")
    later = datetime.now(UTC) + timedelta(seconds=1)
    folded = storylines.state_as_of(storylines.read_history(tmp_path, sid), later)
    assert folded["status"] == "active"
    assert folded["open_question"] == "Original question"
    assert folded["would_change"] == "Original condition"

    # the reviewer clears a field, the seed sync refills it: history follows both
    await storylines.update_storyline(sid, {"open_question": None}, actor="me")
    result = await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")
    assert result.updated == [sid] and result.paths == [storylines.history_relpath(sid)]
    assert kg.nodes[sid].metadata["open_question"] == "Original question"
    last = storylines.read_history(tmp_path, sid)[-1]
    assert last["event"] == "seed" and last["fields"] == {"open_question": "Original question"}
    folded = storylines.state_as_of(storylines.read_history(tmp_path, sid),
                                    datetime.now(UTC) + timedelta(seconds=1))
    assert folded["open_question"] == "Original question" and folded["status"] == "active"

    # nothing changed, nothing logged
    again = await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")
    assert again.paths == []


# --- rebuild carries storyline fields; seed history status_from ---------------------


def test_rebuild_keeps_storyline_fields_the_domain_does_not_declare(monkeypatch):
    from app.model_schemas.domain_config import DomainAttribute, DomainEntity
    from app.services import capture_link_policy
    from app.services.graph.neo4j_models import build_node_properties

    monkeypatch.setattr(capture_link_policy, "linking_policy", lambda: capture_link_policy.LinkingPolicy())
    declared = [DomainAttribute(name="status", type="string", description="s"),
                DomainAttribute(name="thesis", type="string", description="t")]
    storyline = DomainEntity(name="storyline", description="d", plural="storylines", attributes=declared)
    project = DomainEntity(name="project", description="d", plural="projects", attributes=declared)
    metadata = {
        "name": "Arc", "status": "closed", "thesis": "T", "stakes": ["build", "writing"],
        "status_changed_at": "2026-11-02T18:01:26+00:00", "status_changed_by": "seed_sync",
        "closed_by": "seed", "lifecycle_run_id": "link-1", "last_reviewed": "2026-11-02T19:00:00+00:00",
        "proposed_by": "mcp:x", "state": "Now", "direction": "steady", "open_question": "Q?",
        "would_change": None,
    }
    props = build_node_properties(metadata, storyline, "storyline-arc")
    for key in ("status_changed_at", "status_changed_by", "closed_by", "lifecycle_run_id",
                "last_reviewed", "proposed_by", "state", "direction", "open_question"):
        assert props[key] == metadata[key], key
    assert props["stakes"] == ["build", "writing"] and props["thesis"] == "T"
    assert "would_change" not in props  # None is not carried
    # other types are untouched: only the domain's attributes + the generic extras
    other = build_node_properties(metadata, project, "project-arc")
    assert not {"status_changed_at", "closed_by", "stakes", "state", "lifecycle_run_id"} & set(other)


@pytest.mark.asyncio
async def test_seed_status_history_records_the_status_it_started_from(tmp_path, kg):
    sid = "storyline-arc"
    _seed(tmp_path, "- name: Arc\n")
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")
    kg.nodes[sid].metadata["status"] = "dormant"  # e.g. the lifecycle's
    _seed(tmp_path, "- name: Arc\n  status: closed\n")
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")
    _seed(tmp_path, "- name: Arc\n")
    await sync_role(kg, kg.writer, tmp_path, "storyline", "storyline")
    rows = [(r["status_from"], r["status_to"]) for r in storylines.read_history(tmp_path, sid)
            if r.get("event") == "status"]
    assert rows == [(None, "active"), ("dormant", "closed"), ("closed", "active")]
