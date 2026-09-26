"""Migration bundle: selection rule, cleaning, doc conversion, idempotent apply."""

import json
import subprocess
import sys
from pathlib import Path

from app.models.captured_memory import CapturedMemory
from app.services.memory_capture import capture_memory

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_captures.py"


def _cap(text, source, created, **kw):
    return capture_memory(text, source=source).model_copy(update={"created_at": created, **kw})


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, check=True).stdout


def test_build_selects_cleans_and_converts_then_apply_is_idempotent(tmp_path):
    old = tmp_path / "old"
    caps_dir = old / "memory" / "captures"
    caps_dir.mkdir(parents=True)
    note = _cap("My ICP is the COO of an AI-native firm.", "openbrain-import", "2026-05-01T00:00:00+00:00")
    durable = _cap("[Skip to content](x)\n\nA durable essay body.", "web", "2026-05-01T00:00:00+00:00")
    recent = _cap("A recent article body.", "web", "2026-09-20T00:00:00+00:00")
    stale = _cap("An old, not durable article.", "web", "2026-05-01T00:00:00+00:00")
    junk = _cap("Your statement is ready", "mail", "2026-09-20T00:00:00+00:00")
    for c in (note, durable, recent, stale, junk):
        (caps_dir / f"{c.id}.json").write_text(c.model_dump_json())
    (old / "meetings").mkdir()
    (old / "meetings" / "meeting-b1.md").write_text(
        "---\nmeeting_id: m1\nbot_id: b1\ntitle: \"A newsletter\"\nstart_time: 2026-05-01T00:00:00+00:00\n"
        "participants:\n  - Blogwatcher\n---\n\n# A newsletter\n\n## Discussion\n\nscaffold\n\n"
        "## Full Transcript\n\nFrom: News <n@example.com>\n\nThe newsletter text.\n"
    )
    verdicts = [
        {"key": f"capture:{note.id}", "status": "ok", "lane_verdict": {"choice": "memory", "p": 0.9}, "durable": 0.2},
        {"key": f"capture:{durable.id}", "status": "ok", "lane_verdict": {"choice": "library", "p": 0.9}, "durable": 0.8},
        {"key": f"capture:{recent.id}", "status": "ok", "lane_verdict": {"choice": "library", "p": 0.9}, "durable": 0.35},
        {"key": f"capture:{stale.id}", "status": "ok", "lane_verdict": {"choice": "library", "p": 0.9}, "durable": 0.1},
        {"key": f"capture:{junk.id}", "status": "ok", "lane": "junk", "rule": "automated mail"},
    ]
    (tmp_path / "v.jsonl").write_text("".join(json.dumps(v) + "\n" for v in verdicts))
    (tmp_path / "d.jsonl").write_text(json.dumps({"id": "m1", "durable": 0.7}) + "\n")

    _run("build", "--corpus", old, "--verdicts", tmp_path / "v.jsonl", "--doc-durable", tmp_path / "d.jsonl",
         "--out", tmp_path / "bundle", "--as-of", "2026-09-26T00:00:00+00:00")
    rows = [CapturedMemory.model_validate_json(line)
            for line in (tmp_path / "bundle" / "bundle.jsonl").read_text().splitlines()]
    by_id = {r.id: r for r in rows}

    assert set(by_id) >= {note.id, durable.id, recent.id}
    assert stale.id not in by_id and junk.id not in by_id
    assert by_id[note.id].lane == "record" and by_id[note.id].content == note.content  # own notes untouched
    assert by_id[durable.id].lane == "library" and "Skip to content" not in by_id[durable.id].content
    assert by_id[durable.id].stale_after
    doc = next(r for r in rows if r.source_id == "prev-kb-doc:m1")
    assert doc.lane == "library" and doc.source == "mail"
    assert "The newsletter text." in doc.content and "scaffold" not in doc.content
    assert len(rows) == 4

    target = tmp_path / "target"
    first = _run("apply", "--bundle", tmp_path / "bundle" / "bundle.jsonl", "--repo", target, "--apply")
    assert "write: library" in first and "write: record" in first
    assert len(list((target / "memory" / "captures").glob("*.json"))) == 4
    assert len(list((target / "memory" / "audit").glob("*.jsonl"))) == 4
    again = _run("apply", "--bundle", tmp_path / "bundle" / "bundle.jsonl", "--repo", target, "--apply")
    assert "skip: already present" in again and "write:" not in again
