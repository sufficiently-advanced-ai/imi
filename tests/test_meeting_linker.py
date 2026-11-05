"""ADR-013: call transcripts linked to storylines, with who said it and who was there.

End to end through ``capture_linker.run_link`` / ``run_backfill`` /
``revert_run`` against fakes for the graph, the model and git, plus the pure
attribution and selection rules. One test per fixture in ADR-013 §8.
"""

import json
from types import SimpleNamespace

import pytest
import yaml

from app.services import capture_linker
from app.services.capture_link_policy import (
    LinkingPolicy,
    MeetingLinkingPolicy,
    effective_mode,
    parse_linking_policy,
)
from app.services.capture_link_store import CaptureLinkStore, LinkAuditLog
from app.services.capture_linker import LinkContext
from app.services.graph.meeting_graph_writer import MeetingGraphWriter
from app.services.meeting_link_store import MeetingLinkStore
from app.services.meeting_linker import select_excerpts
from app.services.meeting_transcript import (
    PersonRef,
    attribute,
    excerpt_id,
    owner_names,
    parse_meeting,
    parts,
)
from app.services.memory_capture import CaptureStore

OWNER = "Sam Rivera"
SL = "storyline-small-firm-adoption"
SL2 = "storyline-local-models"
DOMAIN = {"storyline": "A tracked arc", "person": "A human individual"}
PEOPLE = {
    "person-dana-kim": PersonRef(id="person-dana-kim", name="Dana Kim", aliases=("Dana",)),
    "person-lee-park": PersonRef(id="person-lee-park", name="Lee Park"),
    "person-ana-cruz": PersonRef(id="person-ana-cruz", name="Ana Cruz"),
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Node(SimpleNamespace):
    pass


class _KG:
    def __init__(self):
        self.nodes = {}
        self.domain = SimpleNamespace(
            entities={k: SimpleNamespace(description=v) for k, v in DOMAIN.items()}
        )

    async def build_graph(self):
        pass


class _Entities:
    def __init__(self, kg):
        self.kg = kg

    async def create(self, entity_type, name, entity_id, properties, aliases=()):
        self.kg.nodes[entity_id] = _Node(id=entity_id, name=name, type=entity_type,
                                         metadata={**properties, "aliases": list(aliases)})
        return entity_id

    async def update(self, entity_id, changes):
        self.kg.nodes[entity_id].metadata.update(changes)

    async def archive(self, entity_id):
        self.kg.nodes.pop(entity_id, None)


class _Neo4j:
    """Documents that exist, and meeting ADVANCES edges keyed by excerpt id."""

    def __init__(self, docs=()):
        self.docs = set(docs)
        self.edges: dict[tuple[str, str, str], dict] = {}

    async def execute_read(self, query, params):
        if "RETURN d.id AS id" in query:
            return [{"id": i} for i in params["ids"] if i in self.docs]
        return []

    async def execute_write(self, query, params):
        if "DELETE r" in query:
            assert "r.meeting_link = true" in query
            ids = set(params["ids"])
            self.edges = {k: v for k, v in self.edges.items() if k[0] not in ids}
        elif "MERGE (d)-[r:ADVANCES {excerpt_id: row.id}]->(s)" in query:
            for row in params["rows"]:
                if row["doc"] in self.docs:
                    self.edges[(row["doc"], row["storyline_id"], row["id"])] = dict(row["props"])
        else:
            raise AssertionError(f"unexpected write: {query}")


class _Git:
    def __init__(self):
        self.commits = []

    async def commit_and_push(self, paths, message):
        self.commits.append((paths, message))


class _Claude:
    """Answers from markers in the numbered lines: ``#sup`` supports (0.9),
    ``#cha`` challenges (0.85), ``#ctx`` context (0.8), ``#low`` supports
    (0.5), ``#span`` an excerpt over this line and the next. ``#sl2`` targets
    the second storyline. ``fail_part`` makes the call for a part with that
    first line fail."""

    def __init__(self, fail_part=None):
        self.calls, self.fail_part = [], fail_part

    async def generate_message(self, **kw):
        assert kw["operation"] == "meeting_linking"
        payload = json.loads(kw["messages"][0]["content"])
        self.calls.append(payload)
        if self.fail_part is not None and payload["part"]["first"] == self.fail_part:
            raise RuntimeError("model unavailable")
        offered = {s["id"] for s in payload["storylines"]}
        out = []
        for row in payload["lines"].split("\n"):
            n, text = row.split("\t", 1)
            n = int(n)
            sid = SL2 if "#sl2" in text else SL
            if sid not in offered:
                continue
            for marker, stance, conf in (("#sup", "supports", 0.9), ("#cha", "challenges", 0.85),
                                         ("#ctx", "context", 0.8), ("#low", "supports", 0.5)):
                if marker in text:
                    out.append({"storyline_id": sid, "lines": [n, n], "stance": stance,
                                "confidence": conf, "claim": f"claim {n}"})
            if "#span" in text:
                out.append({"storyline_id": sid, "lines": [n, n + 1], "stance": "supports",
                            "confidence": 0.95, "claim": "spans"})
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"excerpts": out}))])


def meeting_text(title, participants, lines, start="2026-10-01T15:00:00+00:00"):
    front = yaml.safe_dump({"meeting_id": "m", "updated_at": start, "title": title,
                            "start_time": start, "participants": participants}, sort_keys=False)
    return (f"---\n{front}---\n\n# {title}\n\n## Summary\n\nNot numbered. #sup\n\n"
            "## Full Transcript\n\n" + "\n".join(lines) + "\n")


ONE_ON_ONE = [  # owner labeled, the rest unlabeled, one other participant
    "Sam Rivera: Hi, how is the office?",
    "Busy. We finally bought the scheduling tool, the narrow one. #sup",
    "Sam Rivera: I think every small firm ends up there. #ctx",
    "We tried the big platform first and dropped it after a month. #cha",
    "Sam Rivera: Makes sense.",
]


@pytest.fixture
def world(tmp_path):
    (tmp_path / "seeds").mkdir()
    (tmp_path / "seeds" / "storyline.yaml").write_text(
        "- name: Small firm adoption\n  thesis: Small firms adopt narrow tools, not platforms.\n"
        "- name: Local models\n  thesis: Local models take over private workloads.\n"
    )
    (tmp_path / "meetings").mkdir()
    kg = _KG()
    neo4j = _Neo4j()
    store = MeetingLinkStore(repo_root=tmp_path)
    people_by_doc: dict[str, list[PersonRef]] = {}

    async def people(doc_ids):
        return {d: people_by_doc.get(d, []) for d in doc_ids}

    claude = _Claude()

    def make_ctx(mode="on", meetings_mode="on", **meeting_kw):
        policy = LinkingPolicy(mode=mode, meetings=MeetingLinkingPolicy(mode=meetings_mode, **meeting_kw))
        return LinkContext(
            kg=kg, policy=policy, repo_root=tmp_path,
            captures=CaptureStore(capture_dir=tmp_path / "memory" / "captures", repo_root=tmp_path),
            links=CaptureLinkStore(repo_root=tmp_path), audit=LinkAuditLog(repo_root=tmp_path),
            entities=_Entities(kg), graph=None, claude_client=world_ns.claude, git=_Git(),
            meeting_links=store, meeting_graph=MeetingGraphWriter(neo4j),
            meeting_people=people, owner=OWNER,
        )

    def write_meeting(stem, title, participants, lines, **kw):
        (tmp_path / "meetings" / f"{stem}.md").write_text(meeting_text(title, participants, lines, **kw))
        neo4j.docs.add(f"doc:meetings/{stem}.md")

    world_ns = SimpleNamespace(tmp=tmp_path, kg=kg, neo4j=neo4j, store=store, claude=claude,
                               people_by_doc=people_by_doc, ctx=make_ctx, write=write_meeting)
    return world_ns


async def _run(world, **kw):
    return await capture_linker.run_link(ctx=world.ctx(**kw))


def _audit(world):
    return list(LinkAuditLog(repo_root=world.tmp).iter_rows())


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def test_meeting_policy_defaults_off_and_effective_mode_is_the_lower():
    assert LinkingPolicy().meeting_mode == "off"
    assert effective_mode("on", "shadow") == "shadow" and effective_mode("shadow", "on") == "shadow"
    policy = parse_linking_policy({"library": {"linking": {
        "mode": "on", "meetings": {"mode": "on", "storyline_min_confidence": 0.8,
                                   "max_lines_per_part": 100, "overlap_lines": 500}}}})
    assert policy.meeting_mode == "on" and policy.meetings.storyline_min_confidence == 0.8
    assert policy.meetings.max_lines_per_part == 100
    assert policy.meetings.overlap_lines == 40  # invalid overlap falls back
    bare = parse_linking_policy({"library": {"linking": {"mode": "shadow", "meetings": {"mode": True}}}})
    assert bare.meetings.mode == "on" and bare.meeting_mode == "shadow"


# ---------------------------------------------------------------------------
# Transcript parsing and attribution (§2)
# ---------------------------------------------------------------------------


def _meeting(participants, lines):
    return parse_meeting(meeting_text("Call", participants, lines), "meetings/m.md")


def test_numbering_excludes_front_matter_and_generated_sections():
    m = _meeting([OWNER, "Dana Kim"], ["", *ONE_ON_ONE, ""])
    assert len(m.lines) == 5 and m.lines[0].startswith("Sam Rivera:")
    assert m.document_id == "doc:meetings/m.md" and m.stem == "m"
    assert [(t.start, t.end, t.label) for t in m.turns] == [
        (1, 1, OWNER), (2, 2, None), (3, 3, OWNER), (4, 4, None), (5, 5, OWNER)]


def _attr(participants, lines, turn, people=()):
    m = _meeting(participants, lines)
    return attribute(m, turn, owner_names(OWNER, list(people)), list(people))


def test_category_every_line_labeled():
    lines = ["Sam Rivera: Hello.", "Dana Kim: We bought the narrow tool.", "Sam Rivera: Nice."]
    a = _attr([OWNER, "Dana Kim"], lines, 1, PEOPLE.values())
    assert (a.speaker_name, a.speaker_id, a.basis, a.is_owner) == ("Dana Kim", "person-dana-kim", "label", False)
    owner = _attr([OWNER, "Dana Kim"], lines, 0)
    assert owner.basis == "label" and owner.is_owner is True


def test_category_owner_labeled_one_other_participant_is_sole_other():
    a = _attr([OWNER, "Dana Kim"], ONE_ON_ONE, 1, PEOPLE.values())
    assert (a.speaker_name, a.speaker_id, a.basis, a.is_owner) == ("Dana Kim", "person-dana-kim", "sole_other", False)
    assert a.with_people == (("Dana Kim", "person-dana-kim"),) and a.with_basis == "participants"


def test_category_two_or_more_others_is_unknown_but_not_the_owner():
    a = _attr([OWNER, "Dana Kim", "Lee Park"], ONE_ON_ONE, 1, PEOPLE.values())
    assert (a.speaker_name, a.basis, a.is_owner) == (None, "unknown", False)
    assert {n for n, _ in a.with_people} == {"Dana Kim", "Lee Park"}


def test_category_only_owner_participant_is_unknown_with_no_audience():
    a = _attr([OWNER], ONE_ON_ONE, 1)
    assert (a.speaker_name, a.basis, a.is_owner) == (None, "unknown", False)
    assert a.with_people == () and a.with_basis == "unknown"


def test_owner_first_name_and_alias_labels_are_the_owner():
    owner_ref = PersonRef(id="person-sam-rivera", name=OWNER, aliases=("S. Rivera",))
    norms = owner_names(OWNER, [owner_ref])
    for label in ("Sam", "S. Rivera", "Sam Rivera"):
        m = _meeting([OWNER], [f"{label}: hi", "Dana Kim: yo"])
        assert attribute(m, 0, norms, [owner_ref]).is_owner is True


def test_unlabeled_turns_at_start_and_end_are_not_known():
    lines = ["Someone opens the call.", *ONE_ON_ONE, "Someone closes it."]
    m = _meeting([OWNER, "Dana Kim"], lines)
    norms = owner_names(OWNER)
    first, last = attribute(m, 0, norms, []), attribute(m, len(m.turns) - 1, norms, [])
    assert first.basis == last.basis == "unknown" and first.is_owner is None and last.is_owner is None


def test_unlabeled_turn_next_to_a_non_owner_label_is_not_known():
    lines = ["Sam Rivera: hi", "unlabeled reply", "Dana Kim: labeled"]
    a = _attr([OWNER, "Dana Kim"], lines, 1)
    assert a.basis == "unknown" and a.is_owner is None


def test_parts_overlap_and_cover_everything():
    assert parts(10, 4, 1) == [(1, 4), (4, 7), (7, 10)]
    assert parts(3, 400, 40) == [(1, 3)]


def test_excerpt_id_depends_on_words_not_line_numbers():
    a = excerpt_id("doc:meetings/m.md", SL, "We  bought the Narrow tool.")
    assert a == excerpt_id("doc:meetings/m.md", SL, "we bought the narrow tool.")
    assert a != excerpt_id("doc:meetings/m.md", SL, "we bought the big platform.")
    assert a != excerpt_id("doc:meetings/m.md", SL2, "we bought the narrow tool.")


# ---------------------------------------------------------------------------
# Selection (§3)
# ---------------------------------------------------------------------------


def _select(m, raw, **kw):
    opts = {"min_confidence": 0.75, "max_per_storyline": 3, "max_storylines": 5,
            "speaker_key": lambda turn: str(m.turns[turn].label)}
    opts.update(kw)
    return select_excerpts(m, raw, {SL, SL2}, **opts)


def test_selection_drops_cross_turn_long_low_and_unoffered():
    m = _meeting([OWNER, "Dana Kim"], ONE_ON_ONE + [f"Sam Rivera: line {i}" for i in range(10)])
    kept, dropped = _select(m, [
        {"storyline_id": SL, "lines": [2, 3], "stance": "supports", "confidence": 0.9},  # two turns
        {"storyline_id": SL, "lines": [6, 14], "stance": "supports", "confidence": 0.9},  # 9 lines
        {"storyline_id": SL, "lines": [2, 2], "stance": "supports", "confidence": 0.5},
        {"storyline_id": "storyline-invented", "lines": [2, 2], "stance": "supports", "confidence": 0.9},
        {"storyline_id": SL, "lines": [99, 99], "stance": "supports", "confidence": 0.9},
        {"storyline_id": SL, "lines": [4, 4], "stance": "nonsense", "confidence": 0.8},
    ])
    assert dropped == {"cross_turn": 1, "too_long": 1, "below_bar": 1, "not_offered": 1, "bad_lines": 1}
    assert [(c.start, c.stance) for c in kept] == [(4, "context")]  # unknown stance -> context


def test_opposing_stances_on_one_storyline_are_both_kept():
    lines = [f"Dana Kim: supports {i}" for i in range(4)] + ["Lee Park: challenges"]
    m = _meeting([OWNER, "Dana Kim", "Lee Park"], lines)
    raw = [{"storyline_id": SL, "lines": [1, 4], "stance": "supports", "confidence": 0.95},
           {"storyline_id": SL, "lines": [1, 1], "stance": "supports", "confidence": 0.94},
           {"storyline_id": SL, "lines": [5, 5], "stance": "challenges", "confidence": 0.76}]
    kept, dropped = _select(m, raw, max_per_storyline=2)
    assert sorted(c.stance for c in kept) == ["challenges", "supports"]
    assert dropped["merged"] == 1  # 1-1 overlaps 1-4


def test_tied_excerpts_prefer_a_new_speaker():
    lines = ["Dana Kim: a", "Sam Rivera: x", "Dana Kim: b", "Sam Rivera: y", "Lee Park: c"]
    m = _meeting([OWNER, "Dana Kim", "Lee Park"], lines)
    raw = [{"storyline_id": SL, "lines": [1, 1], "stance": "supports", "confidence": 0.9},
           {"storyline_id": SL, "lines": [3, 3], "stance": "supports", "confidence": 0.88},
           {"storyline_id": SL, "lines": [5, 5], "stance": "supports", "confidence": 0.86}]
    kept, _ = _select(m, raw, max_per_storyline=2)
    assert [c.start for c in kept] == [1, 5]


def test_meeting_keeps_the_storylines_with_the_best_excerpts():
    m = _meeting([OWNER, "Dana Kim"], ["Dana Kim: a", "Sam Rivera: b"])
    raw = [{"storyline_id": SL, "lines": [1, 1], "stance": "supports", "confidence": 0.8},
           {"storyline_id": SL2, "lines": [2, 2], "stance": "context", "confidence": 0.9}]
    kept, dropped = _select(m, raw, max_storylines=1)
    assert [c.storyline_id for c in kept] == [SL2] and dropped["over_meeting_cap"] == 1


# ---------------------------------------------------------------------------
# Runs (§5-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_link_run_writes_file_projects_edges_and_audits(world):
    world.write("call-a", "Call with Dana", [OWNER, "Dana Kim"], ONE_ON_ONE)
    world.people_by_doc["doc:meetings/call-a.md"] = [PEOPLE["person-dana-kim"]]
    log = await _run(world)
    assert log["meetings"]["processed"] == 1 and log["meetings"]["excerpts"] == 3
    record = world.store.get("call-a")
    assert record.mode == "on" and set(record.storylines_checked) == {SL, SL2}
    by_line = {e.lines[0]: e for e in record.excerpts}
    assert by_line[2].quote == ONE_ON_ONE[1] and by_line[2].speaker.basis == "sole_other"
    assert by_line[2].speaker.id == "person-dana-kim" and by_line[2].speaker_is_owner is False
    assert by_line[3].speaker_is_owner is True and by_line[3].stance == "context"
    assert by_line[4].stance == "challenges"
    assert [p.id for p in by_line[3].with_] == ["person-dana-kim"]
    # one edge per excerpt, MERGEd on its id, never carrying capture_link
    edges = world.neo4j.edges
    assert len(edges) == 3 and all(k[1] == SL for k in edges)
    props = edges[("doc:meetings/call-a.md", SL, by_line[2].id)]
    assert props["meeting_link"] is True and "capture_link" not in props
    assert props["speaker_basis"] == "sole_other" and props["with_ids"] == ["person-dana-kim"]
    assert props["lines"] == [2, 2] and props["linked_at"].tzinfo is not None
    rows = [r for r in _audit(world) if r.get("kind") == "meeting" and r["action"] == "link"]
    assert len(rows) == 3
    # only the transcript is offered, never the summary
    assert "Not numbered" not in world.claude.calls[0]["lines"]
    # idempotent: nothing new on a second run
    world.claude.calls.clear()
    again = await _run(world)
    assert again["meetings"]["processed"] == 0 and world.claude.calls == []
    assert len(world.neo4j.edges) == 3


@pytest.mark.asyncio
async def test_unknown_speaker_with_null_owner_flag_is_absent_on_the_edge(world):
    world.write("call-b", "Cohort", [OWNER], ["Opening remark about narrow tools. #sup", *ONE_ON_ONE])
    await _run(world)
    record = world.store.get("call-b")
    first = next(e for e in record.excerpts if e.lines == [1, 1])
    assert first.speaker_is_owner is None and first.speaker.basis == "unknown"
    props = world.neo4j.edges[("doc:meetings/call-b.md", SL, first.id)]
    assert "speaker_is_owner" not in props and props["with_basis"] == "unknown"


@pytest.mark.asyncio
async def test_unknown_speakers_on_a_two_plus_participant_call(world):
    world.write("call-c", "Team", [OWNER, "Dana Kim", "Lee Park"], ONE_ON_ONE)
    await _run(world)
    e = next(e for e in world.store.get("call-c").excerpts if e.lines == [2, 2])
    assert e.speaker.basis == "unknown" and e.speaker.name is None and e.speaker_is_owner is False
    assert sorted(p.name for p in e.with_) == ["Dana Kim", "Lee Park"]


@pytest.mark.asyncio
async def test_excerpt_across_two_turns_is_dropped(world):
    world.write("call-d", "Call", [OWNER, "Dana Kim"], ["Sam Rivera: hi #span", "reply"])
    log = await _run(world)
    assert world.store.get("call-d").excerpts == []
    assert log["meetings"]["dropped"]["cross_turn"] == 1


@pytest.mark.asyncio
async def test_inserting_lines_before_an_excerpt_keeps_its_id(world):
    world.write("call-e", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    await _run(world)
    before = {e.quote: e.id for e in world.store.get("call-e").excerpts}
    world.write("call-e", "Call", [OWNER, "Dana Kim"], ["Sam Rivera: new opening line", *ONE_ON_ONE])
    log = await _run(world)
    assert log["meetings"]["processed"] == 1  # transcript changed: re-linked
    record = world.store.get("call-e")
    after = {e.quote: e.id for e in record.excerpts}
    assert after == before and {e.lines[0] for e in record.excerpts} == {3, 4, 5}
    assert len(world.neo4j.edges) == 3


@pytest.mark.asyncio
async def test_changing_an_excerpts_words_relinks_it(world):
    world.write("call-f", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    await _run(world)
    old = next(e.id for e in world.store.get("call-f").excerpts if e.lines == [2, 2])
    edited = list(ONE_ON_ONE)
    edited[1] = "Busy. We bought the narrow scheduling tool after all. #sup"
    world.write("call-f", "Call", [OWNER, "Dana Kim"], edited)
    await _run(world)
    new = next(e.id for e in world.store.get("call-f").excerpts if e.lines == [2, 2])
    assert new != old
    assert ("doc:meetings/call-f.md", SL, old) not in world.neo4j.edges
    assert ("doc:meetings/call-f.md", SL, new) in world.neo4j.edges


@pytest.mark.asyncio
async def test_passage_returned_by_two_overlapping_parts_is_one_excerpt(world):
    lines = [f"Sam Rivera: filler {i}" for i in range(5)] + ["Dana Kim: the overlap #sup"] + \
            [f"Sam Rivera: more {i}" for i in range(5)]
    world.write("call-g", "Long", [OWNER, "Dana Kim"], lines)
    await _run(world, max_lines_per_part=7, overlap_lines=3)
    assert len(world.claude.calls) == 2
    assert all("the overlap" in c["lines"] for c in world.claude.calls)
    assert [e.lines for e in world.store.get("call-g").excerpts] == [[6, 6]]


@pytest.mark.asyncio
async def test_a_failed_part_writes_no_file_and_the_meeting_stays_eligible(world):
    lines = [f"Dana Kim: line {i} #sup" for i in range(10)]
    world.write("call-h", "Long", [OWNER, "Dana Kim"], lines)
    world.claude.fail_part = 5
    log = await _run(world, max_lines_per_part=6, overlap_lines=2)
    assert world.store.get("call-h") is None and world.neo4j.edges == {}
    assert log["meetings"]["failed"] == ["meetings/call-h.md"] and log["meetings"]["remaining"] == 1
    world.claude.fail_part = None
    log = await _run(world, max_lines_per_part=6, overlap_lines=2)
    assert log["meetings"]["processed"] == 1 and world.store.get("call-h") is not None


@pytest.mark.asyncio
async def test_participants_change_reattributes_without_a_model_call(world):
    world.write("call-i", "Call", [OWNER], ONE_ON_ONE)
    await _run(world)
    e = next(e for e in world.store.get("call-i").excerpts if e.lines == [2, 2])
    assert e.speaker.basis == "unknown" and e.with_ == []
    # the import is fixed: the other participant is now recorded
    world.write("call-i", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    world.people_by_doc["doc:meetings/call-i.md"] = [PEOPLE["person-dana-kim"]]
    world.claude.calls.clear()
    log = await _run(world)
    assert world.claude.calls == [] and log["meetings"]["reattributed"] == ["call-i"]
    e = next(e for e in world.store.get("call-i").excerpts if e.lines == [2, 2])
    assert e.speaker.basis == "sole_other" and e.speaker.id == "person-dana-kim"
    props = world.neo4j.edges[("doc:meetings/call-i.md", SL, e.id)]
    assert props["speaker_id"] == "person-dana-kim" and props["with_names"] == ["Dana Kim"]
    assert any(r["action"] == "reattribute" for r in _audit(world))


@pytest.mark.asyncio
async def test_shadow_then_on_relinks_and_projects(world):
    world.write("call-j", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    log = await _run(world, meetings_mode="shadow")
    assert log["meetings"]["mode"] == "shadow" and world.store.get("call-j").mode == "shadow"
    assert world.neo4j.edges == {}
    # library mode shadow caps an "on" meeting mode
    capped = await _run(world, mode="shadow", meetings_mode="on")
    assert capped["meetings"]["processed"] == 0 and world.neo4j.edges == {}
    log = await _run(world)
    assert log["meetings"]["processed"] == 1 and world.store.get("call-j").mode == "on"
    assert len(world.neo4j.edges) == 3


@pytest.mark.asyncio
async def test_meetings_off_by_default_leave_everything_alone(world):
    world.write("call-k", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    log = await _run(world, meetings_mode="off")
    assert "meetings" not in log and world.store.get("call-k") is None and world.claude.calls == []


@pytest.mark.asyncio
async def test_missing_document_is_skipped_and_reported(world):
    world.write("call-l", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    world.neo4j.docs.discard("doc:meetings/call-l.md")
    log = await _run(world)
    assert log["meetings"]["missing_document"] == ["doc:meetings/call-l.md"]
    assert world.neo4j.edges == {} and world.store.get("call-l").excerpts


@pytest.mark.asyncio
async def test_backfill_with_no_qualifying_excerpt_records_the_check(world):
    world.write("call-m", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    await _run(world)
    # A proposed storyline appears after the call was linked.
    world.kg.nodes["storyline-new-arc"] = _Node(id="storyline-new-arc", name="New arc", type="storyline",
                                               metadata={"status": "proposed", "seed_status": "provisional"})
    world.claude.calls.clear()
    log = await capture_linker.run_backfill(ctx=world.ctx(), kinds=["meeting"])
    assert log["meetings"]["processed"] == 1
    assert [s["id"] for s in world.claude.calls[0]["storylines"]] == ["storyline-new-arc"]
    record = world.store.get("call-m")
    assert record.storylines_checked["storyline-new-arc"] == log["run_id"]
    assert len(record.excerpts) == 3  # untouched
    again = await capture_linker.run_backfill(ctx=world.ctx(), kinds=["meeting"])
    assert again.get("meetings", {}).get("processed", 0) == 0


@pytest.mark.asyncio
async def test_backfill_rejects_unknown_kinds_and_needs_meeting_mode_on(world):
    bad = await capture_linker.run_backfill(ctx=world.ctx(), kinds=["email"])
    assert any("kinds" in e for e in bad["errors"])
    world.write("call-n", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    await _run(world)
    shadow = await capture_linker.run_backfill(ctx=world.ctx(meetings_mode="shadow"), kinds=["meeting"],
                                               storyline_ids=[SL], recheck_before="2999-01-01")
    assert shadow["errors"]  # recheck_before in the future is refused first
    shadow = await capture_linker.run_backfill(ctx=world.ctx(meetings_mode="shadow"), kinds=["meeting"])
    assert any("meetings.mode: on" in e for e in shadow["errors"])


@pytest.mark.asyncio
async def test_recheck_that_removes_excerpts_then_revert_restores_them(world):
    world.write("call-o", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    await _run(world)
    original = {e.id for e in world.store.get("call-o").excerpts}
    assert len(original) == 3
    # The thesis was reworded; the model now finds nothing.
    world.write("call-o", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)  # same transcript
    quiet = _Claude()

    async def nothing(**kw):
        quiet.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(text='{"excerpts": []}')])

    quiet.generate_message = nothing
    ctx = world.ctx()
    ctx.claude_client = quiet
    log = await capture_linker.run_backfill(ctx=ctx, storyline_ids=[SL], kinds=["meeting"],
                                            recheck_before=_now_plus(0))
    assert log["meetings"]["unlinked"] == 3 and world.store.get("call-o").excerpts == []
    assert world.neo4j.edges == {}
    summary = await capture_linker.revert_run(log["run_id"], ctx=world.ctx())
    assert summary["meetings_changed"] == 1
    record = world.store.get("call-o")
    assert {e.id for e in record.excerpts} == original
    assert log["run_id"] in record.reverted_runs and record.storylines_checked[SL] != log["run_id"]
    assert len(world.neo4j.edges) == 3


def _now_plus(seconds):
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


@pytest.mark.asyncio
async def test_revert_of_the_link_run_empties_a_new_file_and_restores_shadow(world):
    world.write("call-p", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    shadow = await _run(world, meetings_mode="shadow")
    on = await _run(world)
    assert len(world.neo4j.edges) == 3
    await capture_linker.revert_run(on["run_id"], ctx=world.ctx())
    record = world.store.get("call-p")
    assert record.mode == "shadow" and len(record.excerpts) == 3  # the shadow run's excerpts
    assert world.neo4j.edges == {}
    await capture_linker.revert_run(shadow["run_id"], ctx=world.ctx())
    assert world.store.get("call-p").excerpts == []


@pytest.mark.asyncio
async def test_deleted_meeting_is_orphaned_then_restored(world):
    world.write("call-q", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    await _run(world)
    (world.tmp / "meetings" / "call-q.md").unlink()
    world.neo4j.docs.discard("doc:meetings/call-q.md")
    world.neo4j.edges.clear()  # stale-file cleanup deleted the Document and its edges
    log = await _run(world)
    record = world.store.get("call-q")
    assert log["meetings"]["orphaned"] == ["call-q"] and record.orphaned_at is not None
    assert not record.projected() and len(record.excerpts) == 3
    world.claude.calls.clear()
    world.write("call-q", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)  # same transcript is back
    log = await _run(world)
    assert log["meetings"]["restored"] == ["call-q"] and world.claude.calls == []
    assert world.store.get("call-q").orphaned_at is None and len(world.neo4j.edges) == 3


@pytest.mark.asyncio
async def test_clean_rebuild_reproduces_the_meeting_edges_exactly(world):
    world.write("call-r", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    world.write("call-s", "Cohort", [OWNER], ["Opening #sup", *ONE_ON_ONE], start="2026-10-02T15:00:00+00:00")
    world.write("call-t", "Shadowed", [OWNER, "Lee Park"], ONE_ON_ONE)
    await _run(world)
    live = dict(world.neo4j.edges)
    rebuilt = _Neo4j(world.neo4j.docs)
    stats = await MeetingGraphWriter(rebuilt).project_all(world.store)
    assert rebuilt.edges == live and stats["edges"] == len(live) and stats["documents"] == 3


@pytest.mark.asyncio
async def test_commit_includes_meeting_link_files_and_the_audit_log(world):
    world.write("call-u", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    ctx = world.ctx()
    log = await capture_linker.run_link(ctx=ctx)
    assert log["committed"]
    paths, message = ctx.git.commits[-1]
    assert "memory/meeting_links/call-u.json" in paths and any("audit" in p for p in paths)
    assert "1 meetings" in message


# ---------------------------------------------------------------------------
# Reads (§7)
# ---------------------------------------------------------------------------


class _ReadNeo4j:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    async def execute_read(self, query, params):
        self.queries.append((query, params))
        return self.rows


def _edge(**kw):
    base = {"meeting_link": True, "excerpt_id": "x1", "stance": "supports", "confidence": 0.9,
            "claim": "c", "quote": "q", "lines": [5, 5], "speaker_basis": "label",
            "with_ids": ["person-dana-kim", ""], "with_names": ["Dana Kim", "Guest"],
            "with_basis": "participants"}
    base.update(kw)
    return base


@pytest.fixture
def reads(monkeypatch):
    from app.services import storylines

    kg = SimpleNamespace(nodes={SL: _Node(id=SL, name="Small firm adoption", type="storyline", metadata={}),
                                "person-dana-kim": _Node(id="person-dana-kim", name="Dana Kim",
                                                         type="person", metadata={})})
    monkeypatch.setattr(storylines, "storyline_type", lambda _kg: "storyline")
    return storylines, kg


@pytest.mark.asyncio
async def test_evidence_is_one_row_per_document_with_excerpts_in_line_order(reads):
    storylines, kg = reads
    kg.neo4j = _ReadNeo4j([
        {"document_id": "doc:meetings/call.md", "title": "Call", "occurred_at": None, "lane": None,
         "edges": [_edge(excerpt_id="b", lines=[9, 9], speaker_name="Dana Kim", speaker_id="person-dana-kim",
                         speaker_is_owner=False),
                   _edge(excerpt_id="a", lines=[2, 3], speaker_basis="unknown")]},
        {"document_id": "doc:memory/captures/c.md", "capture_id": "c", "title": "Article",
         "edges": [{"stance": "challenges", "confidence": 0.8, "created_by": "link_phase"}]},
    ])
    out = await storylines.storyline_evidence(SL, kg=kg)
    meeting, capture = out["evidence"]
    assert meeting["kind"] == "meeting" and [e["id"] for e in meeting["excerpts"]] == ["a", "b"]
    assert "stance" not in meeting and "capture_id" not in meeting
    assert meeting["excerpts"][0]["speaker_is_owner"] is None  # not known, never False
    assert meeting["excerpts"][1]["with"] == [{"name": "Dana Kim", "id": "person-dana-kim"},
                                              {"name": "Guest", "id": None}]
    assert capture["kind"] == "capture" and capture["stance"] == "challenges"
    assert out["count"] == 2 and out["excerpt_count"] == 2


@pytest.mark.asyncio
async def test_evidence_speaker_and_with_filters_are_typed_parameters(reads):
    storylines, kg = reads
    kg.neo4j = _ReadNeo4j([])
    await storylines.storyline_evidence(SL, speaker="owner", with_person="person-dana-kim", kg=kg)
    query, params = kg.neo4j.queries[-1]
    assert params["speaker"] == "owner" and params["with"] == "person-dana-kim"
    assert "r.speaker_is_owner IS NULL" in query and "$with IN coalesce(r.with_ids, [])" in query
    assert "count(DISTINCT" not in query and "LIMIT $limit" in query


@pytest.mark.asyncio
async def test_voices_count_said_present_and_unattributed(reads):
    storylines, kg = reads
    kg.neo4j = _ReadNeo4j([
        {"stance": "supports", "speaker_id": "person-dana-kim", "speaker_name": "Dana Kim",
         "speaker_basis": "sole_other", "speaker_is_owner": False, "with_ids": ["person-dana-kim"],
         "with_names": ["Dana Kim"]},
        {"stance": "context", "speaker_id": None, "speaker_name": OWNER, "speaker_basis": "label",
         "speaker_is_owner": True, "with_ids": ["person-dana-kim", ""], "with_names": ["Dana Kim", "Guest"]},
        {"stance": "challenges", "speaker_basis": "unknown", "speaker_is_owner": None},
        {"stance": "supports", "speaker_basis": "unknown", "speaker_is_owner": False},
    ])
    out = await storylines.storyline_voices(SL, kg=kg)
    dana = next(v for v in out["voices"] if v["id"] == "person-dana-kim")
    assert dana["said"]["by_stance"] == {"supports": 1} and dana["said"]["by_basis"] == {"sole_other": 1}
    assert dana["present_when_owner_said"]["by_stance"] == {"context": 1}
    guest = next(v for v in out["voices"] if v["name"] == "Guest")
    assert guest["id"] is None and guest["present_when_owner_said"]["total"] == 1
    assert out["unattributed"] == {"total": 2, "by_stance": {"challenges": 1, "supports": 1}}
    # the owner is never a voice of their own
    assert all(v["name"] != OWNER for v in out["voices"])


@pytest.mark.asyncio
async def test_person_voices_per_storyline(reads):
    storylines, kg = reads
    kg.neo4j = _ReadNeo4j([
        {"storyline_id": SL, "storyline_name": "Small firm adoption", "stance": "supports",
         "speaker_id": "person-dana-kim", "speaker_name": "Dana Kim", "speaker_basis": "label",
         "speaker_is_owner": False},
        {"storyline_id": SL2, "storyline_name": "Local models", "stance": "context",
         "speaker_name": OWNER, "speaker_is_owner": True, "with_ids": ["person-dana-kim"],
         "with_names": ["Dana Kim"]},
        {"storyline_id": SL2, "stance": "supports", "speaker_id": "person-lee-park",
         "speaker_name": "Lee Park", "speaker_is_owner": False, "with_ids": ["person-dana-kim"]},
    ])
    out = await storylines.person_storyline_voices("person-dana-kim", kg=kg)
    by_id = {s["storyline_id"]: s for s in out["storylines"]}
    assert by_id[SL]["said"]["total"] == 1 and by_id[SL]["present_when_owner_said"]["total"] == 0
    assert by_id[SL2]["said"]["total"] == 0 and by_id[SL2]["present_when_owner_said"]["total"] == 1
    with pytest.raises(storylines.StorylineError, match="no person"):
        await storylines.person_storyline_voices("person-nobody", kg=kg)


@pytest.mark.asyncio
async def test_mcp_voices_tool_takes_exactly_one_id(monkeypatch):
    from app.routes import mcp_server
    from app.services.mcp_tool_definitions import TOOL_DEFS

    assert "list_storyline_voices" in TOOL_DEFS and "with" in TOOL_DEFS["get_storyline"]["inputSchema"]["properties"]
    out = await mcp_server._storyline_tool("list_storyline_voices", {}, actor="t")
    assert "exactly one" in out[0].text
    out = await mcp_server._storyline_tool("list_storyline_voices",
                                           {"storyline_id": SL, "person_id": "p"}, actor="t")
    assert "exactly one" in out[0].text


# ---------------------------------------------------------------------------
# Eval fixture and scorer (offline; the live run is scripts/eval_meeting_linking.py)
# ---------------------------------------------------------------------------


def _eval_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "eval_meeting_linking.py"
    spec = importlib.util.spec_from_file_location("eval_meeting_linking", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_eval_fixture_is_consistent_with_the_parser():
    module = _eval_module()
    data = json.loads(module.FIXTURE.read_text(encoding="utf-8"))
    ids = {s["id"] for s in data["storylines"]}
    for case in data["cases"]:
        meeting = parse_meeting(module.meeting_markdown(case), f"meetings/{case['id']}.md")
        assert len(meeting.lines) == len(case["lines"]), case["id"]
        for g in case["gold"]:
            assert g["storyline_id"] in ids and g["stance"] in ("supports", "challenges", "context")
            assert 1 <= g["lines"][0] <= g["lines"][-1] <= len(meeting.lines), case["id"]
            assert meeting.turn_index(g["lines"][0], g["lines"][-1]) is not None, case["id"]


def test_eval_scorer_counts_recall_false_and_stance():
    module = _eval_module()
    cases = [{"id": "a", "gold": [{"storyline_id": SL, "lines": [2, 2], "stance": "supports"},
                                  {"storyline_id": SL, "lines": [5, 5], "stance": "context", "optional": True}]},
             {"id": "b", "gold": []}]
    preds = {"a": [{"storyline_id": SL, "lines": [2, 3], "stance": "challenges", "confidence": 0.9},
                   {"storyline_id": SL, "lines": [5, 5], "stance": "context", "confidence": 0.6}],
             "b": [{"storyline_id": SL, "lines": [1, 1], "stance": "supports", "confidence": 0.8}]}
    high = module.score(cases, preds, 0.75)
    assert high["recall"] == 1.0 and high["false"] == 1 and high["stance_accuracy"] == 0.0
    low = module.score(cases, preds, 0.5)
    assert low["excerpts"] == 3 and low["stance_accuracy"] == 0.5


@pytest.mark.asyncio
async def test_relink_keeps_first_linked_at_and_revert_restores_the_earlier_file(world):
    world.write("call-v", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    first = await _run(world)
    before = world.store.get("call-v")
    linked = {e.id: e.linked_at for e in before.excerpts}
    world.write("call-v", "Call", [OWNER, "Dana Kim"], ["Sam Rivera: inserted", *ONE_ON_ONE])
    second = await _run(world)
    after = world.store.get("call-v")
    assert {e.id: e.linked_at for e in after.excerpts} == linked
    assert {e.run_id for e in after.excerpts} == {second["run_id"]}
    await capture_linker.revert_run(second["run_id"], ctx=world.ctx())
    reverted = world.store.get("call-v")
    assert reverted.transcript_sha256 == before.transcript_sha256 and reverted.run_id == first["run_id"]
    assert {e.lines[0] for e in reverted.excerpts} == {2, 3, 4}
    # the transcript still differs from the restored file: the next run re-links it
    again = await _run(world)
    assert again["meetings"]["processed"] == 1


def test_eval_applies_the_floor_before_the_cap():
    module = _eval_module()
    case = {"id": "c", "title": "T", "date": "2026-10-01", "participants": [OWNER],
            "lines": ["Dana Kim: a", "Dana Kim: b", "Sam Rivera: x", "Dana Kim: c", "Sam Rivera: y",
                      "Dana Kim: d"]}
    raw = {"c": [{"storyline_id": SL, "lines": [n, n], "stance": "supports", "confidence": conf}
                 for n, conf in ((1, 0.6), (4, 0.62), (6, 0.64), (2, 0.9))]}
    assert [p["lines"] for p in module.select_at([case], [{"id": SL}], raw, 0.8)["c"]] == [[2, 2]]
    # at 0.6 the cap (3) keeps the best three of four
    assert [p["lines"] for p in module.select_at([case], [{"id": SL}], raw, 0.6)["c"]] == [[2, 2], [4, 4], [6, 6]]


def test_string_participants_are_one_person():
    text = "---\ntitle: T\nparticipants: Dana Kim\n---\n\n## Full Transcript\n\nSam Rivera: hi\n"
    assert parse_meeting(text, "meetings/x.md").participants == ["Dana Kim"]


@pytest.mark.asyncio
async def test_failed_people_lookup_rewrites_nothing(world):
    world.write("call-w", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    world.people_by_doc["doc:meetings/call-w.md"] = [PEOPLE["person-dana-kim"]]
    await _run(world)
    before = world.store.get("call-w").model_dump()
    ctx = world.ctx()

    async def broken(_ids):
        raise RuntimeError("graph down")

    ctx.meeting_people = broken
    log = await capture_linker.run_link(ctx=ctx)
    assert any("people lookup failed" in e for e in log["errors"])
    assert world.store.get("call-w").model_dump() == before and not log["meetings"]["reattributed"]


@pytest.mark.asyncio
async def test_unreadable_link_file_is_skipped_not_fatal(world):
    world.write("call-x", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    world.write("call-y", "Call", [OWNER, "Dana Kim"], ONE_ON_ONE)
    world.store.dir.mkdir(parents=True, exist_ok=True)
    (world.store.dir / "call-x.json").write_text("{not json")
    log = await _run(world)
    assert log["meetings"]["processed"] == 1 and world.store.get("call-y") is not None
    assert any("unreadable meeting link file for call-x" in e for e in log["errors"])
