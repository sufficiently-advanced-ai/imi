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
        node.metadata.update({k: v for k, v in changes.items() if k != "name"})
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


def test_guard_refuses_server_fields_on_storylines_only(kg):
    with pytest.raises(StorylineError, match="status"):
        storylines.guard_client_properties(kg, "storyline", {"status": "active"})
    storylines.guard_client_properties(kg, "storyline", {"state": "fine"})
    storylines.guard_client_properties(kg, "project", {"status": "active"})  # other types untouched
    # an edit form echoing an unchanged value is not a change
    assert storylines.server_field_changes(kg, "storyline", {"status": "active"}, {"status": "active"}) == []
    assert storylines.server_field_changes(kg, "storyline", {"status": "closed"}, {"status": "active"}) == ["status"]


# --- reads ------------------------------------------------------------------------


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
