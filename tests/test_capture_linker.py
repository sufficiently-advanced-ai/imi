"""ADR-011 LINK phase end to end, against fakes for the graph, model and git.

Covers: deterministic sources, seeded selection (never invented), mention
resolution (people only on full names), corroborated creation + back-links,
shadow mode, idempotency, revert, and owner links with pinning.
"""

import json
from types import SimpleNamespace

import pytest

from app.services import capture_link_policy as policy_mod
from app.services import capture_linker
from app.services.capture_link_policy import Corroboration, LinkingPolicy
from app.services.capture_link_store import CaptureLinkStore, LinkAuditLog
from app.services.capture_linker import LinkContext, passes_bar
from app.services.memory_capture import CaptureStore
from app.utils.event_time import to_utc

DOMAIN = {
    "source": "Where library content comes from",
    "topic": "A broad theme",
    "organization": "A company or institution",
    "person": "A human individual",
}


class _Node(SimpleNamespace):
    pass


class _KG:
    def __init__(self, nodes=()):
        self.nodes = {n.id: n for n in nodes}
        self.domain = SimpleNamespace(
            entities={k: SimpleNamespace(description=v) for k, v in DOMAIN.items()}
        )
        self.builds = 0

    async def build_graph(self):
        self.builds += 1


class _Entities:
    def __init__(self, kg):
        self.kg, self.created, self.archived = kg, [], []

    async def create(self, entity_type, name, entity_id, properties, aliases=()):
        entity_id = entity_id or f"{entity_type}-{name.lower().replace(' ', '-')}"
        self.kg.nodes[entity_id] = _Node(
            id=entity_id, name=name, type=entity_type, metadata={**properties, "aliases": list(aliases)}
        )
        self.created.append(entity_id)
        return entity_id

    async def update(self, entity_id, changes):
        self.kg.nodes[entity_id].metadata.update(changes)

    async def archive(self, entity_id):
        self.kg.nodes.pop(entity_id)
        self.archived.append(entity_id)


class _Graph:
    def __init__(self):
        self.writes = []

    async def write(self, items):
        self.writes.append([(m.id, r.model_copy(deep=True)) for m, r in items])
        return {"documents": len(items), "edges": 0}


class _Git:
    def __init__(self):
        self.commits = []

    async def commit_and_push(self, paths, message):
        self.commits.append((paths, message))


class _Claude:
    """Answers per capture title from a script; records payloads."""

    def __init__(self, script):
        self.script, self.payloads = script, []

    async def generate_message(self, **kw):
        payload = json.loads(kw["messages"][0]["content"])
        assert kw["operation"] == "capture_linking"
        self.payloads.append(payload)
        answers = [
            {"ref": c["ref"], **self.script.get(c["title"], {})} for c in payload["captures"]
        ]
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"captures": answers}))])


class _NoAdmission:
    def mode(self, operation):
        return "off"


AGENTS_ANSWER = {
    "about": [
        {"id": "topic-ai-agents", "confidence": 0.9},
        {"id": "topic-invented", "confidence": 0.95},  # not offered: discarded
        {"id": "topic-robotics", "confidence": 0.3},  # below the floor
    ],
    "mentions": [
        {"name": "Acme Robotics", "type": "organization", "confidence": 0.9},
        {"name": "Old Corp", "type": "organization", "confidence": 0.8},
        {"name": "Jane Doe", "type": "person", "confidence": 0.9},
        {"name": "Jane", "type": "person", "confidence": 0.9},  # first name: never linked
        {"name": "Ignored", "type": "account", "confidence": 0.9},  # not a mention type
    ],
}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(
        policy_mod, "library_create_types",
        lambda domain_types: frozenset({"organization", "person"}) & set(domain_types or ()),
    )
    (tmp_path / "seeds").mkdir()
    (tmp_path / "seeds" / "topic.yaml").write_text("- name: AI agents\n- name: Robotics\n")
    kg = _KG([
        _Node(id="organization-old-corp", name="Old Corp", type="organization", metadata={}),
        _Node(id="person-jane-doe", name="Jane Doe", type="person", metadata={"relationship": "contact"}),
        _Node(id="person-jane-smith", name="Jane Smith", type="person", metadata={}),
    ])
    captures = CaptureStore(capture_dir=tmp_path / "memory" / "captures", repo_root=tmp_path)
    made = []
    for title, source, source_id, date in [
        ("Acme ships agents", "web", "https://www.stratechery.com/a", "2026-10-01T09:00:00Z"),
        ("Acme agents, take two", "web", "https://techcrunch.com/b", "2026-10-05T09:00:00Z"),
        ("Agent newsletter", "mail", "m-1", "2026-10-09T09:00:00Z"),
    ]:
        content = f"# {title}\n\nFrom: Agent Weekly <weekly@agents.example>\n\nBody about Acme."
        made.append(captures.capture(content, source, source_id, source_date=date, lane="library").memory)
    captures.capture("My own note", "manual", lane="record")  # never linked by the run

    claude = _Claude({m.content.splitlines()[0][2:]: AGENTS_ANSWER for m in made})

    def ctx(mode="on", **policy):
        return LinkContext(
            kg=kg,
            policy=LinkingPolicy(mode=mode, batch_size=2, **policy),
            repo_root=tmp_path,
            captures=captures,
            links=CaptureLinkStore(repo_root=tmp_path),
            audit=LinkAuditLog(repo_root=tmp_path),
            entities=entities,
            graph=graph,
            claude_client=claude,
            decision_client=_NoAdmission(),
            git=git,
        )

    entities, graph, git = _Entities(kg), _Graph(), _Git()
    return SimpleNamespace(kg=kg, captures=captures, made=made, claude=claude, ctx=ctx,
                           entities=entities, graph=graph, git=git, links=CaptureLinkStore(tmp_path))


def _edges(world, capture):
    return sorted((link.edge, link.target_id) for link in world.links.get(capture.id).links)


@pytest.mark.asyncio
async def test_off_mode_does_nothing(world):
    result = await capture_linker.run_link(10, ctx=world.ctx("off"))
    assert result["processed"] == 0 and result["errors"]
    assert list(world.links.iter_all()) == []


@pytest.mark.asyncio
async def test_on_run_links_sources_seeds_mentions_and_corroborates(world):
    result = await capture_linker.run_link(10, ctx=world.ctx())

    assert result["processed"] == 3 and result["remaining"] == 0
    assert result["seeds"]["topic"]["created"] == ["topic-ai-agents", "topic-robotics"]
    assert set(result["sources_created"]) == {
        "source-stratechery-com", "source-techcrunch-com", "source-weekly-agents-example",
    }
    # Acme Robotics: 3 captures, 3 sources, 8 days -> created and back-linked everywhere.
    assert [e["id"] for e in result["entities_created"]] == ["organization-acme-robotics"]
    acme = world.kg.nodes["organization-acme-robotics"]
    assert acme.metadata["created_by"] == "link_phase" and acme.metadata["run_id"] == result["run_id"]

    first = world.made[0]
    assert _edges(world, first) == [
        ("ABOUT", "topic-ai-agents"),
        ("FROM_SOURCE", "source-stratechery-com"),
        ("MENTIONED_IN", "organization-acme-robotics"),
        ("MENTIONED_IN", "organization-old-corp"),
        ("MENTIONED_IN", "person-jane-doe"),
    ]
    record = world.links.get(first.id)
    assert record.unresolved == []  # Acme moved from text to a link
    assert record.occurred_at == first.source_date and record.mode == "on"
    assert result["links_by_type"]["MENTIONED_IN"] == 9

    # The model saw title/summary/source, the seeded topics and known entities.
    payload = world.claude.payloads[0]
    assert {t["id"] for t in payload["topics"]} == {"topic-ai-agents", "topic-robotics"}
    assert {"name": "Old Corp", "type": "organization"} not in payload["known_entities"]  # not in text
    assert set(payload["types"]) == {"organization", "person"}
    assert "Body about Acme" in payload["captures"][0]["summary"]

    # Projected, audited, committed in one commit.
    projected = {cid for batch in world.graph.writes for cid, _ in batch}
    assert projected == {m.id for m in world.made}
    paths, message = world.git.commits[-1]
    assert any(p.startswith("memory/audit/links-") for p in paths)
    assert result["run_id"] in message


@pytest.mark.asyncio
async def test_second_run_is_a_no_op(world):
    await capture_linker.run_link(10, ctx=world.ctx())
    again = await capture_linker.run_link(10, ctx=world.ctx())
    assert again["processed"] == 0 and again["entities_created"] == []


@pytest.mark.asyncio
async def test_below_the_bar_stays_text(world):
    result = await capture_linker.run_link(
        10, ctx=world.ctx(corroboration=Corroboration(min_captures=3, min_sources=4))
    )
    assert result["entities_created"] == []
    assert result["below_bar"][0] == {"name": "Acme Robotics", "entity_type": "organization",
                                      "kind": "mention", "captures": 3, "sources": 3}
    assert world.links.get(world.made[0].id).unresolved[0].name == "Acme Robotics"


@pytest.mark.asyncio
async def test_shadow_writes_files_only_then_on_relinks(world):
    shadow = await capture_linker.run_link(10, ctx=world.ctx("shadow"))

    assert shadow["processed"] == 3
    assert shadow["entities_created"] == [] and shadow["sources_created"] == []
    assert [w["id"] for w in shadow["would_create"]] == ["organization-acme-robotics"]
    assert world.graph.writes == []
    assert all(r.mode == "shadow" for r in world.links.iter_all())
    assert "organization-acme-robotics" not in world.kg.nodes

    on = await capture_linker.run_link(10, ctx=world.ctx("on"))
    assert on["processed"] == 3
    assert [e["id"] for e in on["entities_created"]] == ["organization-acme-robotics"]
    assert all(r.mode == "on" for r in world.links.iter_all())


@pytest.mark.asyncio
async def test_revert_removes_links_and_unreferenced_entities(world):
    run = await capture_linker.run_link(10, ctx=world.ctx())
    first = world.made[0]
    await capture_linker.attach(first.id, "Old Corp", "about", ctx=world.ctx())
    # A clean rebuild keeps only declared attributes: revert must not need them.
    for node in world.kg.nodes.values():
        node.metadata.pop("run_id", None)
        node.metadata.pop("created_by", None)

    summary = await capture_linker.revert_run(run["run_id"], ctx=world.ctx())

    assert summary["captures_changed"] == 3
    assert "organization-acme-robotics" in summary["entities_archived"]
    assert "source-stratechery-com" in summary["entities_archived"]
    assert _edges(world, first) == [("ABOUT", "organization-old-corp")]  # the owner link stays
    record = world.links.get(first.id)
    assert run["run_id"] in record.reverted_runs and record.linked_at  # not re-linked automatically
    again = await capture_linker.run_link(10, ctx=world.ctx())
    assert again["processed"] == 0


@pytest.mark.asyncio
async def test_owner_links_pin_and_survive_relinking(world):
    article = world.made[0]
    note = world.captures.capture("Thoughts on the Acme piece", "manual", lane="record").memory

    about = await capture_linker.attach_about(note.id, [article.id, "Jane Doe", "Nobody Known"],
                                              ctx=world.ctx())
    assert about["linked"] == [{"target_id": article.id, "target_type": "capture"},
                               {"target_id": "person-jane-doe", "target_type": "person"}]
    assert about["unresolved"] == ["Nobody Known"]
    to_person = await capture_linker.attach(note.id, "Jane Doe", "mentions", ctx=world.ctx())
    assert to_person["pinned"] is False  # a record note is never pinned
    pinned = world.captures.get(article.id)
    assert pinned.pinned_by == [f"{note.id}:ABOUT:{article.id}"]
    assert world.captures.get(note.id).pinned_by == []  # record notes do not decay

    # An owner link on a library capture survives the linker's (re)run.
    await capture_linker.attach(article.id, "Old Corp", "mentions", ctx=world.ctx())
    await capture_linker.run_link(10, ctx=world.ctx())
    owner = [link for link in world.links.get(article.id).links if link.created_by == "owner"]
    assert [(link.edge, link.target_id) for link in owner] == [("MENTIONED_IN", "organization-old-corp")]

    removed = await capture_linker.detach(note.id, article.id, "about", ctx=world.ctx())
    assert removed["removed"] is True
    assert world.captures.get(article.id).pinned_by == [
        f"{article.id}:MENTIONED_IN:organization-old-corp"
    ]


@pytest.mark.asyncio
async def test_owner_link_validation(world):
    article = world.made[0]
    with pytest.raises(capture_linker.AttachError):
        await capture_linker.attach(article.id, "Jane Doe", "advances", ctx=world.ctx())
    with pytest.raises(capture_linker.AttachError):
        await capture_linker.attach(article.id, "Jane Doe", "cites", ctx=world.ctx())
    with pytest.raises(capture_linker.AttachError):
        await capture_linker.attach("missing", "Jane Doe", ctx=world.ctx())
    assert (await capture_linker.attach(article.id, "Nobody", ctx=world.ctx())) == {
        "linked": False, "unresolved": "Nobody"}


def test_resolve_mention_people_need_full_names():
    resolver = capture_linker._resolver(_KG([
        _Node(id="person-jane-doe", name="Jane Doe", type="person", metadata={}),
    ]))
    assert capture_linker.resolve_mention(resolver, "person", "Jane Doe") == ("link", "person-jane-doe")
    assert capture_linker.resolve_mention(resolver, "person", "Jane")[0] == "ambiguous"
    assert capture_linker.resolve_mention(resolver, "person", "Ada Lovelace") == ("new", None)


def test_passes_bar_needs_captures_and_sources_inside_one_window():
    def at(day):
        return to_utc(f"2026-10-{day:02d}T00:00:00Z")

    assert passes_bar([(at(1), "a"), (at(5), "b"), (at(9), "a")], 3, 2, 30)
    assert not passes_bar([(at(1), "a"), (at(5), "a"), (at(9), "a")], 3, 2, 30)
    assert not passes_bar([(at(1), "a"), (at(5), "b"), (at(28), "c")], 3, 2, 10)
    assert passes_bar([(at(1), "a"), (at(20), "b"), (at(25), "c")], 2, 2, 10)


@pytest.mark.asyncio
async def test_concurrent_run_is_refused(world):
    async with capture_linker._run_lock:
        with pytest.raises(capture_linker.LinkRunBusy):
            await capture_linker.run_link(10, ctx=world.ctx())


@pytest.mark.asyncio
async def test_failed_model_call_leaves_captures_unlinked(world):
    class _Broken:
        async def generate_message(self, **kw):
            return SimpleNamespace(content=[SimpleNamespace(text="not json")])

    ctx = world.ctx()
    ctx.claude_client = _Broken()
    result = await capture_linker.run_link(10, ctx=ctx)

    assert result["processed"] == 0 and result["remaining"] == 3
    assert "capture_linking call failed" in result["errors"][0]
    assert list(world.links.iter_all()) == []
    retry = await capture_linker.run_link(10, ctx=world.ctx())
    assert retry["processed"] == 3


@pytest.mark.asyncio
async def test_owner_link_added_mid_run_is_not_clobbered(world):
    article = world.made[0]
    real = world.claude.generate_message

    async def attach_then_answer(**kw):
        await capture_linker.attach(article.id, "Old Corp", "about", ctx=world.ctx())
        return await real(**kw)

    world.claude.generate_message = attach_then_answer
    await capture_linker.run_link(10, ctx=world.ctx())

    owner = [link for link in world.links.get(article.id).links if link.created_by == "owner"]
    assert [(link.edge, link.target_id) for link in owner] == [("ABOUT", "organization-old-corp")]


@pytest.mark.asyncio
async def test_capture_left_out_of_the_answer_stays_unlinked(world):
    left_out = world.made[1]
    real = world.claude.generate_message

    async def drop_one(**kw):
        response = await real(**kw)
        data = json.loads(response.content[0].text)
        payload = world.claude.payloads[-1]
        refs = {c["ref"] for c in payload["captures"] if c["title"] == "Acme agents, take two"}
        data["captures"] = [c for c in data["captures"] if c["ref"] not in refs]
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(data))])

    world.claude.generate_message = drop_one
    result = await capture_linker.run_link(10, ctx=world.ctx())

    assert result["processed"] == 2 and result["remaining"] == 1
    assert world.links.get(left_out.id) is None


@pytest.mark.asyncio
async def test_recreating_an_archived_entity_unarchives_its_file():
    calls = []

    class _KGArchived:
        async def add_node(self, entity_type, name, entity_id=None, properties=None):
            return {"id": entity_id}

        def _is_entity_archived(self, entity_id):
            return True

    writer = capture_linker.LibraryEntityWriter(_KGArchived())

    async def fake_update(entity_id, changes):
        calls.append((entity_id, changes))

    writer.update = fake_update
    await writer.create("organization", "Acme", "organization-acme", {})
    assert calls == [("organization-acme", {"is_archived": False})]
