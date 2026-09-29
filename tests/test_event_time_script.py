"""scripts/event_time.py — audit a corpus and bring an existing one up to date."""

import json
import os
import sys
from datetime import UTC, datetime

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import event_time as script  # noqa: E402

from app.utils.event_time import ASSERTIONS_KEY  # noqa: E402
from tests.test_neo4j_graph import _make_domain_config  # noqa: E402

MEETING = """---
meeting_id: ingest-{bot}
bot_id: {bot}
updated_at: {when}
update_count: 1
is_finalized: true
status: completed
title: {title}
start_time: {when}
entities_mentioned:
  person:
    - Alice
entity_ids:
{ids}
---

# {title}

Body text — untouched by migration.

## Full Transcript

Alice: hello
"""


def _meeting(root, bot, when, ids, title="Kickoff", extra=""):
    path = root / "meetings" / f"meeting-{bot}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = MEETING.format(
        bot=bot, when=when, title=title, ids="\n".join(f'  - "{i}"' for i in ids)
    )
    if extra:
        text = text.replace("start_time:", f"{extra}\nstart_time:", 1)
    path.write_text(text, encoding="utf-8")
    return path


def _entity(root, folder, eid, **frontmatter):
    path = root / folder / f"{eid.split('-', 1)[1]}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"id": eid, "entity_type": eid.split("-")[0], "name": eid, **frontmatter}
    path.write_text(
        "---\n" + yaml.dump(meta, sort_keys=False) + "---\n\n# Profile\n\nProse.\n", encoding="utf-8"
    )
    return path


def _signals(root, bot, extracted_at):
    path = root / "signals" / f"meeting-{bot}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"bot_id": bot, "extracted_at": extracted_at, "signals": []}), encoding="utf-8")


def _frontmatter(path):
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])


@pytest.fixture
def corpus(tmp_path):
    """Two meetings, both naming Alice and Atlas; Alice holds an undated
    relationship to Atlas and one to a project no meeting names."""
    _meeting(tmp_path, "aug", "2026-08-11T09:00:00-04:00",
             ["person-alice", "project-atlas"], title="Review")
    _meeting(tmp_path, "jun", "2026-06-02T10:00:00-04:00",
             ["person-alice", "project-atlas", "person-bob"])
    _signals(tmp_path, "jun", "2026-09-27T22:58:30+00:00")
    _entity(tmp_path, "people", "person-alice",
            has_projects=["project-atlas", "project-ghost"])
    _entity(tmp_path, "projects", "project-atlas")
    (tmp_path / "README.md").write_text("---\ntitle: x\n---\n", encoding="utf-8")
    return tmp_path


def _load(root):
    return script.Corpus(root, _make_domain_config())


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_audit_counts_what_a_legacy_corpus_is_missing(corpus):
    counts, examples = script.audit(_load(corpus), corpus)
    assert counts["observations"] == 2
    assert counts["entities"] == 2
    assert counts["relationships"] == 2
    assert counts["relationship_unattributed"] == 2
    assert counts["observation_not_recorded"] == 2
    assert counts["observation_unrecorded"] == 2
    assert counts["observation_without_time"] == 0
    assert "people/alice.md: has_projects -> project-atlas" in examples["relationship_unattributed"]


def test_audit_flags_fallback_and_undated_observations(tmp_path):
    _meeting(tmp_path, "fb", "2026-09-28T10:00:00+00:00", ["person-alice"],
             extra="time_source: fallback_now")
    bad = tmp_path / "meetings" / "meeting-bad.md"
    bad.write_text("---\nmeeting_id: m\nbot_id: bad\nupdated_at: whenever\n---\nbody\n", encoding="utf-8")
    counts, examples = script.audit(_load(tmp_path), tmp_path)
    assert counts["observation_fallback_now"] == 1
    assert counts["observation_without_time"] == 1
    assert examples["observation_without_time"] == ["meetings/meeting-bad.md"]


def test_audit_reports_captures(tmp_path):
    caps = tmp_path / "memory" / "captures" / "2026-03"
    caps.mkdir(parents=True)
    (caps / "a.json").write_text(json.dumps({"id": "a", "source_date": "2026-03-04T10:00:00+00:00"}), encoding="utf-8")
    (caps / "b.json").write_text(json.dumps({"id": "b", "source_date": "2026-03-04T10:00:00"}), encoding="utf-8")
    (caps / "c.json").write_text(json.dumps({"id": "c", "source_date": None}), encoding="utf-8")
    (caps / "broken.json").write_text("{not json", encoding="utf-8")
    counts, _ = script.audit(_load(tmp_path), tmp_path)
    assert counts["captures"] == 3
    assert counts["capture_naive_date"] == 1
    assert counts["capture_without_date"] == 1


def test_blocking_gaps_fail_the_audit(corpus, capsys):
    assert script.print_report(*script.audit(_load(corpus), corpus)) == 1
    out = capsys.readouterr().out
    assert "relationship_unattributed" in out and "[BLOCKING]" in out


def test_informational_gaps_do_not_fail_the_audit(tmp_path, capsys):
    _meeting(tmp_path, "jun", "2026-06-02T10:00:00-04:00", ["person-alice"])
    assert script.print_report(*script.audit(_load(tmp_path), tmp_path)) == 0
    assert "[info]" in capsys.readouterr().out


def test_archived_entities_are_ignored(tmp_path):
    _entity(tmp_path, "people", "person-gone", is_archived=True, has_projects=["project-atlas"])
    counts, _ = script.audit(_load(tmp_path), tmp_path)
    assert counts["entities"] == 0 and counts["relationships"] == 0


# ---------------------------------------------------------------------------
# Migrate
# ---------------------------------------------------------------------------


def test_dry_run_changes_no_file(corpus):
    before = {p: p.read_bytes() for p in corpus.rglob("*") if p.is_file()}
    done, changed = script.migrate(_load(corpus), corpus, apply=False)
    assert done["observation_recorded_at"] == 2
    assert done["relationship_attributed"] == 1
    assert done["relationship_no_evidence"] == 1
    assert sorted(set(changed)) == [
        "meetings/meeting-aug.md", "meetings/meeting-jun.md", "people/alice.md"]
    assert {p: p.read_bytes() for p in corpus.rglob("*") if p.is_file()} == before


def test_relationship_is_dated_from_the_earliest_shared_document(corpus):
    script.migrate(_load(corpus), corpus, apply=True)
    meta = _frontmatter(corpus / "people" / "alice.md")
    assert meta["has_projects"] == ["project-atlas", "project-ghost"]
    assert meta[ASSERTIONS_KEY] == [
        {
            "type": "has_projects",
            "target": "project-atlas",
            "source_id": "doc:meetings/meeting-jun.md",  # June, not August
            "occurred_at": "2026-06-02T14:00:00+00:00",
            "time_source": "inferred",
            "recorded_at": "2026-09-27T22:58:30+00:00",
        }
    ]
    # The profile body is preserved.
    assert (corpus / "people" / "alice.md").read_text(encoding="utf-8").endswith("# Profile\n\nProse.\n")


def test_relationship_with_no_supporting_document_stays_unattributed(corpus, capsys):
    script.migrate(_load(corpus), corpus, apply=True)
    assert "project-ghost" in capsys.readouterr().out
    counts, examples = script.audit(_load(corpus), corpus)
    assert counts["relationship_unattributed"] == 1
    assert examples["relationship_unattributed"] == ["people/alice.md: has_projects -> project-ghost"]


def test_recorded_at_comes_from_the_signal_file_then_the_file_time(corpus):
    aug = corpus / "meetings" / "meeting-aug.md"
    stamp = datetime(2026, 9, 28, 1, 2, 3, tzinfo=UTC).timestamp()
    os.utime(aug, (stamp, stamp))
    script.migrate(_load(corpus), corpus, apply=True)
    assert _frontmatter(corpus / "meetings" / "meeting-jun.md")["recorded_at"] == datetime(
        2026, 9, 27, 22, 58, 30, tzinfo=UTC)
    assert _frontmatter(aug)["recorded_at"] == datetime(2026, 9, 28, 1, 2, 3, tzinfo=UTC)


def test_meeting_files_change_by_one_line_only(corpus):
    path = corpus / "meetings" / "meeting-jun.md"
    before = path.read_text(encoding="utf-8").split("\n")
    script.migrate(_load(corpus), corpus, apply=True)
    after = path.read_text(encoding="utf-8").split("\n")
    added = [line for line in after if line not in before]
    assert added == ["recorded_at: 2026-09-27T22:58:30+00:00"]
    assert [line for line in after if line not in added] == before
    # Placed with the other time fields, and the event time is untouched.
    assert after[after.index(added[0]) - 1].startswith("start_time:")
    assert _frontmatter(path)["start_time"] == datetime.fromisoformat("2026-06-02T10:00:00-04:00")


def test_migration_is_idempotent(corpus):
    script.migrate(_load(corpus), corpus, apply=True)
    snapshot = {p: p.read_bytes() for p in corpus.rglob("*") if p.is_file()}
    done, changed = script.migrate(_load(corpus), corpus, apply=True)
    assert done["observation_recorded_at"] == 0
    assert done["relationship_attributed"] == 0
    assert changed == []
    assert {p: p.read_bytes() for p in corpus.rglob("*") if p.is_file()} == snapshot


def test_existing_assertions_are_kept(tmp_path):
    _meeting(tmp_path, "jun", "2026-06-02T10:00:00-04:00", ["person-alice", "project-atlas"])
    real = {"type": "has_projects", "target": "project-atlas",
            "source_id": "doc:meetings/meeting-real.md",
            "occurred_at": "2026-07-01T00:00:00+00:00", "time_source": "explicit"}
    _entity(tmp_path, "people", "person-alice", has_projects=["project-atlas"],
            **{ASSERTIONS_KEY: [real]})
    done, _ = script.migrate(_load(tmp_path), tmp_path, apply=True)
    assert done["relationship_attributed"] == 0
    assert _frontmatter(tmp_path / "people" / "alice.md")[ASSERTIONS_KEY] == [real]


def test_merged_ids_are_followed_when_matching_documents(tmp_path):
    """The meeting names the duplicate; the relationship names the survivor."""
    _meeting(tmp_path, "jun", "2026-06-02T10:00:00-04:00", ["person-alice", "project-atlas-old"])
    _entity(tmp_path, "people", "person-alice", has_projects=["project-atlas"])
    _entity(tmp_path, "projects", "project-atlas", merged_ids=["project-atlas-old"])
    done, _ = script.migrate(_load(tmp_path), tmp_path, apply=True)
    assert done["relationship_attributed"] == 1


def test_undated_document_cannot_date_a_relationship(tmp_path):
    bad = tmp_path / "meetings" / "meeting-bad.md"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "---\nmeeting_id: m\nbot_id: bad\nupdated_at: whenever\n"
        'entity_ids:\n  - "person-alice"\n  - "project-atlas"\n---\nbody\n',
        encoding="utf-8",
    )
    _entity(tmp_path, "people", "person-alice", has_projects=["project-atlas"])
    done, _ = script.migrate(_load(tmp_path), tmp_path, apply=False)
    assert done["relationship_attributed"] == 0
    assert done["relationship_no_evidence"] == 1


def test_migrated_corpus_rebuilds_to_dated_edges(corpus):
    """End to end on files: migrate, then the graph reader sees a dated edge."""
    from app.utils.event_time import edge_event_props, read_assertions

    script.migrate(_load(corpus), corpus, apply=True)
    meta = _frontmatter(corpus / "people" / "alice.md")
    (assertion,) = read_assertions(meta)
    props = edge_event_props(assertion)
    assert props["occurred_at"] == datetime(2026, 6, 2, 14, 0, tzinfo=UTC)
    assert props["source_id"] == "doc:meetings/meeting-jun.md"
    assert props["time_source"] == "inferred"
