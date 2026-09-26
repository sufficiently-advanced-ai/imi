"""ADR-003 backfill: verdicts -> lanes, audited rejections, dry run, idempotency."""

import json
import subprocess
import sys
from pathlib import Path

from app.models.signal import MeetingSignals, Signal
from app.services.memory_capture import capture_memory

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "stamp_lanes.py"


def _corpus(tmp_path):
    caps = tmp_path / "memory" / "captures"
    caps.mkdir(parents=True)
    mine = capture_memory("Scott's pricing decision", source="openbrain-import")
    article = capture_memory("An article", source="web")
    junk = capture_memory("Your statement is ready", source="mail")
    for c in (mine, article, junk):
        (caps / f"{c.id}.json").write_text(c.model_dump_json(indent=2))
    sig = Signal(id="s1", type="decision", content="OpenAI shipped X", source_meeting_id="b1",
                 source_timestamp="2026-09-01T00:00:00+00:00")
    (tmp_path / "signals").mkdir()
    ms = MeetingSignals(meeting_id="m1", bot_id="b1", extracted_at="2026-09-01T00:00:00+00:00",
                        signal_count=1, signals=[sig])
    (tmp_path / "signals" / "meeting-b1.json").write_text(ms.model_dump_json(indent=2))
    verdicts = [
        {"kind": "capture", "id": mine.id, "key": f"capture:{mine.id}", "status": "ok",
         "lane_verdict": {"choice": "memory", "p": 0.9}, "durable": 0.8},
        {"kind": "capture", "id": article.id, "key": f"capture:{article.id}", "status": "ok",
         "lane_verdict": {"choice": "library", "p": 0.99}, "durable": 0.1},
        {"kind": "capture", "id": junk.id, "key": f"capture:{junk.id}", "status": "ok",
         "lane": "junk", "rule": "automated mail"},
        {"kind": "document", "id": "m1", "key": "document:m1", "status": "ok",
         "kind_verdict": {"choice": "third_party", "p": 0.97}},
        {"kind": "signal", "id": "s1", "key": "signal:s1", "status": "ok", "standalone": 0.8},
    ]
    vpath = tmp_path / "verdicts.jsonl"
    vpath.write_text("".join(json.dumps(v) + "\n" for v in verdicts))
    return caps, mine, article, junk, vpath


def _run(tmp_path, vpath, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus", str(tmp_path), "--verdicts", str(vpath), *extra],
        capture_output=True, text=True, check=True,
    ).stdout


def _load(caps, c):
    return json.loads((caps / f"{c.id}.json").read_text())


def test_dry_run_writes_nothing(tmp_path):
    caps, mine, article, junk, vpath = _corpus(tmp_path)
    before = _load(caps, article)
    out = _run(tmp_path, vpath, "--reject-junk")
    assert "DRY RUN" in out and "capture: lane -> library" in out
    assert _load(caps, article) == before
    assert not (tmp_path / "memory" / "audit").exists()


def test_apply_stamps_lanes_and_rejects_through_audit_then_is_idempotent(tmp_path):
    caps, mine, article, junk, vpath = _corpus(tmp_path)
    _run(tmp_path, vpath, "--reject-junk", "--apply")

    assert _load(caps, mine)["lane"] == "record"
    art = _load(caps, article)
    assert art["lane"] == "library" and art["stale_after"]
    bad = _load(caps, junk)
    assert bad["review_status"] == "rejected" and bad["lane"] == "library"
    audit = (tmp_path / "memory" / "audit" / f"{junk.id}.jsonl").read_text()
    assert "automated mail" in audit and "reject" in audit

    sig = json.loads((tmp_path / "signals" / "meeting-b1.json").read_text())["signals"][0]
    assert sig["lane"] == "library" and sig["stale_after"]

    rerun = _run(tmp_path, vpath, "--reject-junk")
    assert "capture:" not in rerun and "signal:" not in rerun


def test_library_meeting_frontmatter_gets_lane_and_authors(tmp_path):
    caps, mine, article, junk, vpath = _corpus(tmp_path)
    meetings = tmp_path / "meetings"
    meetings.mkdir()
    doc = "---\nmeeting_id: m1\nbot_id: b1\nparticipants:\n  - Blogwatcher\n---\n\n# Body\n"
    (meetings / "meeting-b1.md").write_text(doc)
    (meetings / "meeting-b2.md").write_text(doc.replace("m1", "m2"))  # no verdict: untouched

    _run(tmp_path, vpath, "--apply")

    stamped = (meetings / "meeting-b1.md").read_text()
    assert "lane: library\n---" in stamped and "authors:\n  - Blogwatcher" in stamped
    assert "participants:" not in stamped and stamped.endswith("# Body\n")
    assert (meetings / "meeting-b2.md").read_text() == doc.replace("m1", "m2")
    assert "meeting:" not in _run(tmp_path, vpath)  # idempotent
