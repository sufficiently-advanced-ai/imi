"""Inferred relationships: Jev verifies each proposed edge, accepted edges go
to the owning entity's frontmatter first (files are the source of truth), and
the profile writer can neither drop nor invent them."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from app.services.domain_aware_entity_processor import DomainAwareEntityProcessor
from app.services.inference.decisions import DecisionResult, DecisionUnavailable, NoulAnswer
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator
from app.services.relationship_verification import verify_relationships

TRANSCRIPT = (
    "Dan Kauppi: I'll show Brian the quoting app on Friday. "
    "Brian Vigilani runs rentals at Foley and owns the Foley relationship with us."
)


def _rel(src, src_type, rtype, tgt, tgt_type):
    return {
        "source_id": f"{src_type}-{src.lower().replace(' ', '-')}", "source_name": src, "source_type": src_type,
        "type": rtype,
        "target_id": f"{tgt_type}-{tgt.lower().replace(' ', '-')}", "target_name": tgt, "target_type": tgt_type,
    }


class _FakeDecisions:
    """P(supported) scripted by relationship type."""

    def __init__(self, script, mode="on", fail=False):
        self.script, self._mode, self.fail = script, mode, fail
        self.states: list[dict] = []

    def mode(self, operation):
        assert operation == "relationship_verify"
        return self._mode

    async def decide(self, state, questions, *, operation):
        self.states.append(state)
        if self.fail:
            raise DecisionUnavailable("down", status=503)
        rtype = state["proposal"]["relationship"].split("—")[1].split("→")[0]
        return DecisionResult({"supported": NoulAnswer(self.script[rtype])}, "jev", "fake", 0, 0, 0.0, 0, {})


@pytest.mark.asyncio
async def test_on_mode_writes_only_supported_relationships():
    rels = [
        _rel("Dan Kauppi", "person", "reports_to", "Brian Vigilani", "person"),
        _rel("Foley", "account", "managed_by", "Brian Vigilani", "person"),
    ]
    client = _FakeDecisions({"reports_to": 0.10, "managed_by": 0.92})
    kept = await verify_relationships(rels, TRANSCRIPT, client=client)
    assert [r["type"] for r in kept] == ["managed_by"]
    # evidence gathered for the judge: excerpts around both names
    assert any("Brian Vigilani" in w for w in client.states[0]["transcript_excerpts"])


@pytest.mark.asyncio
async def test_shadow_off_and_failures_keep_every_proposal():
    rels = [_rel("Dan Kauppi", "person", "reports_to", "Brian Vigilani", "person")]
    assert await verify_relationships(rels, TRANSCRIPT, client=_FakeDecisions({"reports_to": 0.1}, mode="shadow")) == rels
    assert await verify_relationships(rels, TRANSCRIPT, client=_FakeDecisions({}, mode="off")) == rels
    assert await verify_relationships(rels, TRANSCRIPT, client=_FakeDecisions({}, fail=True)) == rels


def _domain():
    def rel(t, target, inverse=None):
        return SimpleNamespace(type=t, target=target, inverse_name=inverse)

    return SimpleNamespace(entities={
        "person": SimpleNamespace(relationships=[rel("manages_accounts", "account", "managed_by"),
                                                 rel("reports_to", "person", "manages")]),
        "account": SimpleNamespace(relationships=[rel("managed_by", "person", "manages_accounts"),
                                                  rel("has_projects", "project", "belongs_to_account")]),
        "project": SimpleNamespace(relationships=[rel("belongs_to_account", "account", "has_projects")]),
    })


def test_relationship_holder_places_edges_on_the_defining_entity():
    holder = IngestOrchestrator._relationship_holder
    d = _domain()
    assert holder("managed_by", "account-foley", "person-brian", d) == ("account-foley", "managed_by", "person-brian")
    assert holder("belongs_to_account", "project-quoting", "account-foley", d) == (
        "project-quoting", "belongs_to_account", "account-foley")
    # the wrong direction for the type is not placed at all
    assert holder("belongs_to_account", "account-foley", "project-quoting", d) is None
    # an inverse name is stored on the entity whose definition it inverts
    assert holder("manages", "person-brian", "person-dan", d) == ("person-dan", "reports_to", "person-brian")
    assert holder("collaborates_with", "person-a", "account-b", d) is None


@pytest.mark.asyncio
async def test_accepted_edges_go_to_files_then_the_graph(monkeypatch):
    orch = IngestOrchestrator.__new__(IngestOrchestrator)
    graph = SimpleNamespace(
        nodes={},
        git_ops=SimpleNamespace(repo_path="/nonexistent"),
        add_frontmatter_relationships=AsyncMock(return_value="accounts/foley.md"),
        ingest_files=AsyncMock(return_value=1),
        create_semantic_relationship=AsyncMock(side_effect=AssertionError("no direct Neo4j edge writes")),
    )
    orch._graph = graph
    orch._record_live_files = AsyncMock()
    monkeypatch.setattr(
        "app.core.domain_config.domain_config_service.get_domain_config_service",
        lambda: SimpleNamespace(get_active_domain=_domain),
    )
    client = _FakeDecisions({"managed_by": 0.9, "reports_to": 0.1})
    monkeypatch.setattr("app.services.relationship_verification._default_client", lambda: client)

    n = await orch._write_relationship_edges(
        [
            {"source": "account-foley", "target": "person-brian-vigilani", "type": "managed_by"},
            {"source": "person-dan-kauppi", "target": "person-brian-vigilani", "type": "reports_to"},
        ],
        transcript=TRANSCRIPT,
        names={"account-foley": "Foley", "person-brian-vigilani": "Brian Vigilani", "person-dan-kauppi": "Dan Kauppi"},
    )

    assert n == 1
    graph.add_frontmatter_relationships.assert_awaited_once_with(
        "account-foley", {"managed_by": ["person-brian-vigilani"]})
    graph.ingest_files.assert_awaited_once_with(["accounts/foley.md"])
    orch._record_live_files.assert_awaited_once_with(["accounts/foley.md"])


@pytest.mark.asyncio
async def test_add_frontmatter_relationships_appends_once(tmp_path):
    from app.services.graph.neo4j_graph import Neo4jKnowledgeGraph

    (tmp_path / "accounts").mkdir()
    f = tmp_path / "accounts" / "foley.md"
    f.write_text("---\nid: account-foley\nname: Foley\nmanaged_by:\n- person-dan\n---\n# Foley\n")
    kg = Neo4jKnowledgeGraph.__new__(Neo4jKnowledgeGraph)
    kg._git_ops = SimpleNamespace(repo_path=str(tmp_path), commit_and_push=AsyncMock())
    kg._find_entity_file = lambda eid: str(f)
    kg._file_locks = {}

    path = await kg.add_frontmatter_relationships(
        "account-foley", {"managed_by": ["person-brian", "person-dan"]})
    assert path == os.path.join("accounts", "foley.md")
    fm = yaml.safe_load(f.read_text().split("---")[1])
    assert fm["managed_by"] == ["person-dan", "person-brian"]
    assert await kg.add_frontmatter_relationships("account-foley", {"managed_by": ["person-brian"]}) is None


def test_profile_rewrites_keep_verified_relationships_and_drop_invented_ones():
    existing = {"id": "account-foley", "name": "Foley", "managed_by": ["person-brian-vigilani"]}
    generated = ("---\nid: account-foley\nname: Foley\nhas_projects:\n- project-invented\n---\n# Foley\n")
    out = DomainAwareEntityProcessor._preserve_bookkeeping(
        generated, existing, "", relationship_keys=("managed_by", "has_projects"))
    fm = yaml.safe_load(out.split("---", 2)[1])
    assert fm["managed_by"] == ["person-brian-vigilani"]
    assert "has_projects" not in fm


@pytest.mark.asyncio
async def test_record_live_files_stamps_the_manifest(tmp_path):
    from app.services.corpus_manifest import CorpusReconciler, Manifest, load_manifest, save_manifest

    (tmp_path / "accounts").mkdir()
    (tmp_path / "accounts" / "foley.md").write_text("---\nid: account-foley\n---\n")
    (tmp_path / "accounts" / "bad.md").write_text("---\nid: account-bad\n---\n")
    neo4j = SimpleNamespace(execute_read=AsyncMock(return_value=[{"build_id": "b1"}]))
    mpath = tmp_path / "manifest.json"
    save_manifest(mpath, Manifest(build_id="b1", built_at="t", files={}))

    async def ingest_file(path, content):
        if "bad" in path:
            raise RuntimeError("vector write failed")
        return True

    sk = SimpleNamespace(ingest_file=ingest_file)
    rec = CorpusReconciler(kg=None, neo4j=neo4j, repo_path=str(tmp_path), sk=sk, manifest_path=mpath)
    out = await rec.record_live_files(["accounts/foley.md", "accounts/bad.md", "signals/x.json"])

    assert out["action"] == "recorded" and out["semantica_indexed"] == 1
    files = load_manifest(mpath).files
    assert "accounts/foley.md" in files and "accounts/bad.md" not in files  # bad retried at next boot


def test_state_carries_owner_and_relationship_meaning(monkeypatch):
    from app.config import settings
    from app.services.relationship_verification import build_verify_state

    monkeypatch.setattr(settings, "KB_OWNER_NAME", "Scott Jennings", raising=False)
    rel = {**_rel("Scott Jennings", "person", "works_on_projects", "Open Brain", "project"),
           "type_description": "The person does work on the project"}
    state = build_verify_state(rel, [], {"title": "Open Brain sync"})
    assert state["knowledge_base_owner"].startswith("Scott Jennings")
    assert state["proposal"]["relationship_meaning"] == "The person does work on the project"

    monkeypatch.setattr(settings, "KB_OWNER_NAME", None, raising=False)
    assert "knowledge_base_owner" not in build_verify_state(rel, [], None)


def test_domain_relationships_accept_a_description():
    from app.model_schemas.domain_config import DomainRelationship

    r = DomainRelationship(type="manages_accounts", target="account", cardinality="one_to_many",
                           description="Owns our relationship with that organisation")
    assert r.description.startswith("Owns")
    assert DomainRelationship(type="x", target="y", cardinality="one_to_many").description is None


@pytest.mark.asyncio
async def test_a_second_heard_form_of_the_same_entity_is_kept_for_link_verification():
    # "Faulkner Media Group" (from the existing-entities context) and "F&G"
    # (what was said) both resolve to account-faulkner-media-group; the heard
    # form must survive so the transcript excerpt ("F and G") is found.
    orch = IngestOrchestrator.__new__(IngestOrchestrator)
    orch._graph = SimpleNamespace(upgrade_entity_name=AsyncMock(return_value=False))

    class _Resolver:
        def resolve(self, etype, name):
            return SimpleNamespace(id="account-faulkner-media-group", canonical_name="Faulkner Media Group",
                                   matched_via="tiebreak" if name == "F&G" else "exact", score=1.0)

        def register(self, *a, **k):
            pass

        async def prefetch(self, *a, **k):
            return 0

    out, _ = await orch._resolve_collected_entities(
        [{"type": "account", "name": "Faulkner Media Group", "id": "account-faulkner-media-group"},
         {"type": "account", "name": "F&G", "id": "account-f-g"}],
        resolver=_Resolver(),
    )
    assert len(out) == 1 and out[0]["also_heard"] == ["F&G"]


@pytest.mark.asyncio
async def test_entity_files_are_ingested_like_a_rebuild(tmp_path):
    orch = IngestOrchestrator.__new__(IngestOrchestrator)
    files = {"account-edf": str(tmp_path / "accounts" / "edf.md")}
    orch._graph = SimpleNamespace(
        _find_entity_file=lambda eid: files.get(eid),
        git_ops=SimpleNamespace(repo_path=str(tmp_path)),
    )
    orch._link_document_in_graph = AsyncMock()
    await orch._link_entity_files([{"id": "account-edf"}, {"id": "account-edf"}, {"id": "person-nofile"}])
    orch._link_document_in_graph.assert_awaited_once_with(os.path.join("accounts", "edf.md"))


def test_glossary_maps_misheard_names_to_canonical(tmp_path):
    from app.services.glossary import canonicalize_mentions, load_glossary

    (tmp_path / "glossary.yaml").write_text("imi: [EME, Emmy]\nPharmerica: Farmerica\n")
    g = load_glossary(str(tmp_path))
    assert g == {"eme": "imi", "emmy": "imi", "farmerica": "Pharmerica"}
    out = canonicalize_mentions(
        [{"type": "project", "name": "EME", "id": "project-eme"}, {"type": "person", "name": "Dan"}], g)
    assert out[0]["name"] == "imi" and out[0]["surface"] == "EME"
    assert out[1] == {"type": "person", "name": "Dan"}
    assert load_glossary(str(tmp_path / "missing")) == {}


@pytest.mark.asyncio
async def test_misheard_mention_resolves_to_the_canonical_entity(tmp_path):
    (tmp_path / "glossary.yaml").write_text("imi: [EME]\n")
    orch = IngestOrchestrator.__new__(IngestOrchestrator)
    orch._graph = SimpleNamespace(git_ops=SimpleNamespace(repo_path=str(tmp_path)),
                                  upgrade_entity_name=AsyncMock(return_value=False))
    asked = []

    class _Resolver:
        def resolve(self, etype, name):
            asked.append(name)
            return SimpleNamespace(id=f"{etype}-{name.lower()}", canonical_name=name, matched_via="new", score=1.0)

        def register(self, *a, **k):
            pass

        async def prefetch(self, *a, **k):
            return 0

    out, id_map = await orch._resolve_collected_entities(
        [{"type": "project", "name": "EME", "id": "project-eme"}], resolver=_Resolver())
    assert asked == ["imi"]
    assert out[0]["id"] == "project-imi" and out[0]["name"] == "imi" and out[0]["surface"] == "EME"
    assert id_map == {"project-eme": "project-imi"}  # signal refs follow
