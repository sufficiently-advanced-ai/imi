"""scripts/rebuild_kb.py — metadata resolution chain, manifest, folder scan.

Loads the script via importlib (scripts/ is not a package — same pattern as
tests/issue-909_test_backfill.py) and unit-tests the pure functions that the
seed subcommand depends on. The async ingest/rebuild flows are covered by
tests/test_rebuild_orchestrator.py and the E2E run.
"""

from __future__ import annotations

import importlib.util as _ilu
import os
import sys
import time
from pathlib import Path

_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_SCRIPT_PATH = Path(_ROOT) / "scripts" / "rebuild_kb.py"
_spec = _ilu.spec_from_file_location("rebuild_kb", str(_SCRIPT_PATH))
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# parse_frontmatter_loose
# ---------------------------------------------------------------------------


def test_frontmatter_simple_keys():
    text = "---\ntitle: Kickoff Call\ndate: 2026-03-01\n---\n\nBody"
    fm = _mod.parse_frontmatter_loose(text)
    assert fm == {"title": "Kickoff Call", "date": "2026-03-01"}


def test_frontmatter_block_list_participants():
    text = "---\nparticipants:\n  - Alice\n  - Bob Smith\n---\nBody"
    fm = _mod.parse_frontmatter_loose(text)
    assert fm["participants"] == ["Alice", "Bob Smith"]


def test_frontmatter_inline_list():
    text = "---\nparticipants: [Alice, Bob]\n---\nBody"
    fm = _mod.parse_frontmatter_loose(text)
    assert fm["participants"] == ["Alice", "Bob"]


def test_no_frontmatter_returns_empty():
    assert _mod.parse_frontmatter_loose("Just a transcript.\nAlice: hi") == {}


# ---------------------------------------------------------------------------
# extract_date_from_filename / content
# ---------------------------------------------------------------------------


def test_filename_date_iso():
    assert _mod.extract_date_from_filename("acme-2026-03-15-discovery.md").startswith(
        "2026-03-15"
    )


def test_filename_date_compact():
    assert _mod.extract_date_from_filename("call_20260415.txt").startswith("2026-04-15")


def test_filename_date_us_format():
    assert _mod.extract_date_from_filename("meeting-04-15-2026.md").startswith(
        "2026-04-15"
    )


def test_filename_invalid_date_rejected():
    assert _mod.extract_date_from_filename("notes-2026-13-45.md") is None


def test_filename_no_date():
    assert _mod.extract_date_from_filename("random-notes.md") is None


def test_content_date_in_head():
    text = "Weekly sync\nDate: 2026-05-01\n" + "filler\n" * 100 + "2027-01-01\n"
    assert _mod.extract_date_from_content(text).startswith("2026-05-01")


def test_content_date_beyond_head_ignored():
    text = "filler\n" * 60 + "2026-05-01\n"
    assert _mod.extract_date_from_content(text) is None


# ---------------------------------------------------------------------------
# extract_participants_heuristic
# ---------------------------------------------------------------------------


def test_participants_from_section():
    text = "# Call\n\n## Participants\n- Alice\n- Bob Smith\n\nAlice: hello"
    assert _mod.extract_participants_heuristic(text) == ["Alice", "Bob Smith"]


def test_participants_from_inline():
    text = "Attendees: Alice, Bob, Carol\n\ntranscript..."
    assert _mod.extract_participants_heuristic(text) == ["Alice", "Bob", "Carol"]


def test_participants_from_speaker_labels():
    text = "Alice Smith: We should start.\nBob: Agreed.\nAlice Smith: Great.\n"
    assert _mod.extract_participants_heuristic(text) == ["Alice Smith", "Bob"]


def test_speaker_stopwords_excluded():
    text = "Agenda: items\nNote: remember\nAlice: hi there\n"
    assert _mod.extract_participants_heuristic(text) == ["Alice"]


# ---------------------------------------------------------------------------
# resolve_file_metadata — the resolution chain
# ---------------------------------------------------------------------------


def test_resolution_prefers_frontmatter(tmp_path):
    f = tmp_path / "call-2026-01-01.md"
    f.write_text("---\ndate: 2026-06-01T10:00:00+00:00\ntitle: T\n---\nBody")
    meta = _mod.resolve_file_metadata(f, f.read_text())
    assert meta["timestamp_source"] == "frontmatter"
    assert meta["timestamp"].startswith("2026-06-01")


def test_resolution_falls_back_to_filename(tmp_path):
    f = tmp_path / "call-2026-02-02.md"
    f.write_text("Alice: no dates in here")
    meta = _mod.resolve_file_metadata(f, f.read_text())
    assert meta["timestamp_source"] == "filename"
    assert meta["timestamp"].startswith("2026-02-02")


def test_resolution_falls_back_to_content(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("Sync on 2026-03-03 with the team.\nAlice: hi")
    meta = _mod.resolve_file_metadata(f, f.read_text())
    assert meta["timestamp_source"] == "content"
    assert meta["timestamp"].startswith("2026-03-03")


def test_resolution_falls_back_to_mtime(tmp_path):
    f = tmp_path / "undated.md"
    f.write_text("Alice: no dates anywhere")
    mtime = time.mktime((2026, 4, 4, 12, 0, 0, 0, 0, 0))
    os.utime(f, (mtime, mtime))
    meta = _mod.resolve_file_metadata(f, f.read_text())
    assert meta["timestamp_source"] == "mtime"
    assert meta["timestamp"].startswith("2026-04-04")


def test_title_from_filename_when_no_frontmatter(tmp_path):
    f = tmp_path / "acme_discovery-call.md"
    f.write_text("Alice: hello")
    meta = _mod.resolve_file_metadata(f, f.read_text())
    assert meta["title"] == "acme discovery call"


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_manifest_roundtrip(tmp_path):
    manifest = {"a.md": {"sha256": "x", "job_id": "j1", "status": "completed"}}
    _mod.save_manifest(tmp_path, manifest)
    assert _mod.load_manifest(tmp_path) == manifest


def test_manifest_missing_returns_empty(tmp_path):
    assert _mod.load_manifest(tmp_path) == {}


def test_manifest_corrupt_returns_empty(tmp_path):
    (tmp_path / _mod.MANIFEST_NAME).write_text("{not json")
    assert _mod.load_manifest(tmp_path) == {}


# ---------------------------------------------------------------------------
# Folder scan
# ---------------------------------------------------------------------------


def test_scan_folder_filters_extensions(tmp_path):
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "c.json").write_text("{}")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.md").write_text("x")

    flat = _mod._scan_folder(tmp_path, recursive=False)
    assert [p.name for p in flat] == ["a.md", "b.txt"]

    deep = _mod._scan_folder(tmp_path, recursive=True)
    assert [p.name for p in deep] == ["a.md", "b.txt", "d.md"]


# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------


def test_parser_rebuild_args():
    args = _mod.build_parser().parse_args(
        ["rebuild", "--from", "signals", "--tenant", "org-x", "--dry-run", "-y"]
    )
    assert args.command == "rebuild"
    assert args.tier == "signals"
    assert args.tenant == "org-x"
    assert args.dry_run and args.yes


def test_parser_seed_args():
    args = _mod.build_parser().parse_args(
        ["seed", "--folder", "/data/x", "--resume", "--continue-on-error"]
    )
    assert args.command == "seed"
    assert args.folder == "/data/x"
    assert args.resume and args.continue_on_error
