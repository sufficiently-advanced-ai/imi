"""ADR-011 building blocks: linking policy, deterministic sources, link files,
seed files and the capture graph projection."""

from types import SimpleNamespace

import pytest

from app.models.captured_memory import CapturedMemory
from app.services import capture_link_policy as policy_mod
from app.services.capture_link_policy import LinkingPolicy, parse_linking_policy
from app.services.capture_link_store import (
    CaptureLink,
    CaptureLinkFile,
    CaptureLinkStore,
    LinkAuditLog,
)
from app.services.capture_sources import derive_source
from app.services.graph.capture_graph_writer import CaptureGraphWriter, document_props
from app.services.library_seeds import (
    CANONICAL,
    PROVISIONAL,
    RETIRED,
    offered,
    parse_seed_file,
    sync_role,
)

# --- policy -----------------------------------------------------------------


def test_policy_defaults_leave_linking_off():
    policy = parse_linking_policy({})
    assert policy.mode == "off"
    assert policy.corroboration.min_captures == 3
    assert policy.corroboration.min_sources == 2
    assert policy.corroboration.window_days == 30


def test_policy_parses_values_and_yaml_booleans():
    policy = parse_linking_policy({"library": {"linking": {
        "mode": True,  # YAML `on`
        "batch_size": 5,
        "min_confidence": 0.75,
        "roles": {"source": "publisher", "essay": None},
        "corroboration": {"min_captures": 4, "window_days": 14},
    }}})
    assert policy.mode == "on"
    assert policy.batch_size == 5
    assert policy.min_confidence == 0.75
    assert policy.roles["source"] == "publisher"
    assert policy.roles["essay"] is None
    assert policy.corroboration.min_captures == 4
    assert policy.corroboration.min_sources == 2
    assert policy.corroboration.window_days == 14
    assert parse_linking_policy({"library": {"linking": {"mode": False}}}).mode == "off"


def test_policy_bad_values_fall_back_to_defaults():
    policy = parse_linking_policy({"library": {"linking": {
        "mode": "sometimes", "batch_size": 0, "min_confidence": 2, "roles": ["x"],
        "corroboration": {"min_sources": "two"},
    }}})
    assert policy == LinkingPolicy()


def test_role_types_are_matched_to_the_domain():
    policy = LinkingPolicy()
    assert policy.role_type("topic", {"Topic", "person"}) == "Topic"
    assert policy.role_type("essay", {"Topic", "person"}) is None


def test_create_types_exclude_role_owned_types(monkeypatch):
    monkeypatch.setattr(
        policy_mod, "library_create_types",
        lambda domain_types: frozenset({"organization", "topic"}),
    )
    assert LinkingPolicy().create_types({"organization", "topic"}) == frozenset({"organization"})


# --- sources ----------------------------------------------------------------


def test_web_source_is_the_host():
    ref = derive_source("web", "https://www.stratechery.com/2026/x", "Some article")
    assert (ref.kind, ref.key, ref.name) == ("web", "web:stratechery.com", "stratechery.com")
    assert ref.entity_id("source") == "source-stratechery-com"
    assert (ref.medium, ref.url) == ("other", "https://stratechery.com")


def test_youtube_source_is_the_channel():
    content = "# A talk\n**Channel:** Dwarkesh Patel\n**Date:** 2026-10-20\n"
    ref = derive_source("youtube", "https://www.youtube.com/shorts/abc", content)
    assert (ref.kind, ref.key, ref.name) == ("youtube", "youtube:dwarkesh patel", "Dwarkesh Patel")
    assert ref.entity_id("source") == "source-dwarkesh-patel"


def test_mail_source_is_the_sender_address():
    content = '# Subject\n\nFrom: "Nate from Nate\'s Zero to AI" <NatesZeroToAI@substack.com>\nDate: x\n'
    ref = derive_source("mail", "1a145e0891fe6686", content)
    assert ref.key == "mail:nateszerotoai@substack.com"
    assert ref.name == "Nate from Nate's Zero to AI"
    bare = derive_source("mail", None, "From: news@example.com\n")
    assert (bare.key, bare.name) == ("mail:news@example.com", "news@example.com")


def test_no_derivable_source():
    assert derive_source("openbrain-import", "openbrain:123", "just notes") is None


# --- link files -------------------------------------------------------------


def test_link_file_round_trip_and_mutate(tmp_path):
    store = CaptureLinkStore(repo_root=tmp_path)
    record = CaptureLinkFile(capture_id="c1", run_id="r1", linked_at="2026-10-29T00:00:00+00:00",
                             links=[CaptureLink(edge="ABOUT", target_id="topic-a", target_type="topic")])
    store.save(record)
    assert store.relative_path("c1") == "memory/capture_links/c1.json"
    assert store.get("c1").links[0].target_id == "topic-a"

    def add(r):
        r.links.append(CaptureLink(edge="MENTIONED_IN", target_id="org-x", target_type="organization"))
        return r

    store.mutate("c1", add)
    assert [link.edge for link in store.get("c1").links] == ["ABOUT", "MENTIONED_IN"]
    fresh = store.mutate("c2", add)
    assert fresh.mode == "owner" and store.get("c2") is not None
    assert {r.capture_id for r in store.iter_all()} == {"c1", "c2"}
    with pytest.raises(ValueError):
        store.get("../escape")


def test_is_linked_and_projection_rules():
    on = CaptureLinkFile(capture_id="c", mode="on", linked_at="x")
    shadow = CaptureLinkFile(capture_id="c", mode="shadow", linked_at="x",
                             links=[CaptureLink(edge="ABOUT", target_id="t", target_type="topic"),
                                    CaptureLink(edge="ABOUT", target_id="u", target_type="topic",
                                                created_by="owner")])
    owner = CaptureLinkFile(capture_id="c", mode="owner")
    old = CaptureLinkFile(capture_id="c", mode="on", linked_at="x", link_version=0)
    assert on.is_linked("on") and on.is_linked("shadow")
    assert shadow.is_linked("shadow") and not shadow.is_linked("on")
    assert not owner.is_linked("on") and not old.is_linked("on")
    assert [link.target_id for link in shadow.graph_links()] == ["u"]
    assert shadow.projected()
    assert not CaptureLinkFile(capture_id="c", mode="shadow").projected()


def test_audit_log_appends_jsonl(tmp_path):
    log = LinkAuditLog(repo_root=tmp_path)
    log.append_many([{"run_id": "r1", "action": "link"}, {"run_id": "r1", "action": "unresolved"}])
    lines = log.path().read_text().splitlines()
    assert len(lines) == 2 and '"action": "link"' in lines[0]
    assert log.relative_path().startswith("memory/audit/links-")


# --- seeds ------------------------------------------------------------------


def test_parse_seed_file_accepts_lists_and_wrapped_lists():
    seeds = parse_seed_file(
        "- name: AI agents\n  aliases: [agentic AI]\n  description: Agents.\n"
        "- AI safety\n- {title: Essay One, id: essay-one}\n- {nope: 1}\n",
        "topic",
    )
    assert [(s.entity_id, s.name) for s in seeds] == [
        ("topic-ai-agents", "AI agents"), ("topic-ai-safety", "AI safety"), ("essay-one", "Essay One"),
    ]
    assert seeds[0].aliases == ["agentic AI"]
    wrapped = parse_seed_file("topics:\n  - name: Robotics\n", "topic")
    assert [s.entity_id for s in wrapped] == ["topic-robotics"]


class _Node(SimpleNamespace):
    pass


class _KG:
    def __init__(self, nodes=()):
        self.nodes = {n.id: n for n in nodes}


class _Writer:
    def __init__(self, kg):
        self.kg, self.created, self.updates = kg, [], []

    async def create(self, entity_type, name, entity_id, properties, aliases=()):
        metadata = dict(properties, aliases=list(aliases))
        self.kg.nodes[entity_id] = _Node(id=entity_id, name=name, type=entity_type, metadata=metadata)
        self.created.append(entity_id)
        return entity_id

    async def update(self, entity_id, changes):
        node = self.kg.nodes[entity_id]
        if "name" in changes:
            node.name = changes["name"]
        node.metadata.update({k: v for k, v in changes.items() if k != "name"})
        self.updates.append((entity_id, changes))


@pytest.mark.asyncio
async def test_sync_creates_promotes_and_retires(tmp_path):
    (tmp_path / "seeds").mkdir()
    (tmp_path / "seeds" / "topic.yaml").write_text(
        "- name: AI agents\n  aliases: [agentic AI]\n- name: Robotics\n"
    )
    kg = _KG([
        _Node(id="topic-robotics-x", name="Robotics", type="topic", metadata={"seed_status": PROVISIONAL}),
        _Node(id="topic-old", name="Old theme", type="topic", metadata={"seed_status": CANONICAL}),
        _Node(id="topic-mine", name="Owner made", type="topic", metadata={}),
    ])
    writer = _Writer(kg)

    result = await sync_role(kg, writer, tmp_path, "topic", "topic")

    assert result.created == ["topic-ai-agents"]
    assert kg.nodes["topic-ai-agents"].metadata["aliases"] == ["agentic AI"]
    assert kg.nodes["topic-robotics-x"].metadata["seed_status"] == CANONICAL  # provisional promoted
    assert result.retired == ["topic-old"]
    assert "seed_status" not in kg.nodes["topic-mine"].metadata  # owner-created untouched
    assert {o["id"] for o in offered(kg, "topic")} == {"topic-ai-agents", "topic-robotics-x", "topic-mine"}

    again = await sync_role(kg, writer, tmp_path, "topic", "topic")
    assert again.as_dict() == {}
    assert kg.nodes["topic-old"].metadata["seed_status"] == RETIRED


@pytest.mark.asyncio
async def test_missing_seed_file_changes_nothing(tmp_path):
    kg = _KG([_Node(id="topic-a", name="A", type="topic", metadata={"seed_status": CANONICAL})])
    result = await sync_role(kg, _Writer(kg), tmp_path, "topic", "topic")
    assert result.as_dict() == {}
    assert kg.nodes["topic-a"].metadata["seed_status"] == CANONICAL


# --- graph projection -------------------------------------------------------


class _Neo4j:
    def __init__(self):
        self.writes = []

    async def execute_write(self, query, params=None):
        self.writes.append((query, params))
        return []


def _memory(**kw):
    base = {"content": "# Big news\nbody", "source": "web", "source_id": "https://ex.com/a",
            "source_date": "2026-10-01T12:00:00-04:00", "lane": "library",
            "created_at": "2026-10-20T00:00:00+00:00"}
    return CapturedMemory(**{**base, **kw})


def test_document_props_use_content_time_not_ingest_time():
    memory = _memory()
    props = document_props(memory, f"memory/captures/{memory.id}.json")
    assert props["kind"] == "capture" and props["lane"] == "library"
    assert props["title"] == "Big news" and props["url"] == "https://ex.com/a"
    assert props["occurred_at"].isoformat() == "2026-10-01T16:00:00+00:00"
    assert props["recorded_at"].isoformat() == "2026-10-20T00:00:00+00:00"
    undated = document_props(_memory(source_date=None), "p")
    assert "occurred_at" not in undated and undated["time_source"] == "fallback_now"


@pytest.mark.asyncio
async def test_writer_projects_edges_in_adr_directions():
    neo4j = _Neo4j()
    writer = CaptureGraphWriter(neo4j, lambda cid: f"memory/captures/{cid}.json")
    memory = _memory()
    record = CaptureLinkFile(capture_id=memory.id, mode="on", links=[
        CaptureLink(edge="MENTIONED_IN", target_id="organization-acme", target_type="organization"),
        CaptureLink(edge="ABOUT", target_id="topic-a", target_type="topic"),
        CaptureLink(edge="ABOUT", target_id="other-capture", target_type="capture", created_by="owner"),
    ])

    stats = await writer.write([(memory, record)])

    assert stats == {"documents": 1, "edges": 3}
    queries = [q for q, _ in neo4j.writes]
    assert any("MERGE (e)-[r:MENTIONED_IN]->(d)" in q for q in queries)
    assert any("MERGE (d)-[r:ABOUT]->(t)" in q and "t:Entity" in q for q in queries)
    capture_edge = next(p for q, p in neo4j.writes if "t:Document" in q)
    assert capture_edge["rows"][0]["target"] == "doc:memory/captures/other-capture.json"
    assert all(r["props"]["capture_link"] for q, p in neo4j.writes if "row.props" in q and "MERGE (d:" not in q
               for r in p["rows"])


@pytest.mark.asyncio
async def test_shadow_file_projects_nothing_but_clears_old_edges():
    neo4j = _Neo4j()
    writer = CaptureGraphWriter(neo4j, lambda cid: f"memory/captures/{cid}.json")
    memory = _memory()
    record = CaptureLinkFile(capture_id=memory.id, mode="shadow",
                             links=[CaptureLink(edge="ABOUT", target_id="topic-a", target_type="topic")])
    stats = await writer.write([(memory, record)])
    assert stats == {"documents": 0, "edges": 0}
    assert all("DELETE r" in q for q, _ in neo4j.writes)


@pytest.mark.asyncio
async def test_project_all_adds_owner_target_captures(tmp_path):
    neo4j = _Neo4j()
    note, article = _memory(lane="record"), _memory()
    captures = {note.id: note, article.id: article}
    links = CaptureLinkStore(repo_root=tmp_path)
    links.save(CaptureLinkFile(capture_id=note.id, mode="owner", links=[
        CaptureLink(edge="ABOUT", target_id=article.id, target_type="capture", created_by="owner"),
    ]))
    writer = CaptureGraphWriter(neo4j, lambda cid: f"memory/captures/{cid}.json")

    stats = await writer.project_all(links, SimpleNamespace(get=captures.get))

    assert stats == {"documents": 2, "edges": 1}
    merged = [r["id"] for q, p in neo4j.writes if q.startswith("UNWIND $rows AS row MERGE (d:Document")
              for r in p["rows"]]
    assert merged.index(f"doc:memory/captures/{article.id}.json") < merged.index(
        f"doc:memory/captures/{note.id}.json")


def test_capture_title_prefers_heading_then_summary():
    from app.services.graph.capture_graph_writer import capture_title

    assert capture_title(_memory(content="Privacy preferences\n\n# Real Title\nbody")) == "Real Title"
    nav = _memory(content="| | |\nSkip to content\nbody", summary="An article about X. More text.")
    assert capture_title(nav) == "An article about X"
    assert capture_title(_memory(content="Just a line\nmore")) == "Just a line"
