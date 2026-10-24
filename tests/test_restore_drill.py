"""Pure parts of scripts/restore_drill.py: backup/restore, counting, comparison.

The live drill needs a running stack (CI: .github/workflows/restore-drill.yml).
"""

import importlib.util
import io
import json
import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restore_drill.py"
_spec = importlib.util.spec_from_file_location("restore_drill", _SCRIPT)
rd = importlib.util.module_from_spec(_spec)
sys.modules["restore_drill"] = rd
_spec.loader.exec_module(rd)


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _kb(tmp_path):
    repo, data = tmp_path / "repo", tmp_path / "data"
    _write(repo, "entities/person/person-a.md", "---\nname: A\n---\n")
    _write(repo, "signals/audit/sig-1.jsonl", '{"action":"confirm"}\n{"action":"reject"}\n\n')
    _write(repo, "signals/audit/sig-2.jsonl", '{"action":"confirm"}\n')
    _write(repo, ".git/HEAD", "ref: refs/heads/main\n")
    _write(data, "corpus_manifest.json", "{}")
    return repo, data


def test_count_audit_rows_ignores_blank_lines(tmp_path):
    repo, _ = _kb(tmp_path)
    assert rd.count_audit_rows(repo) == {"sig-1": 2, "sig-2": 1}
    assert rd.count_audit_rows(tmp_path / "nowhere") == {}


def test_count_vectors_groups_by_content_type(tmp_path):
    db = tmp_path / "vectors.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE vectors (id TEXT, content_type TEXT, embedding BLOB)")
    conn.executemany(
        "INSERT INTO vectors VALUES (?, ?, x'00')",
        [("a", "entity"), ("b", "entity"), ("c", "signal"), ("d", None)],
    )
    conn.commit()
    conn.close()
    assert rd.count_vectors(tmp_path) == {"entity": 2, "signal": 1, "": 1}
    assert rd.count_vectors(tmp_path / "missing") == {}


def test_backup_wipe_restore_roundtrip_keeps_dirs_and_bytes(tmp_path):
    repo, data = _kb(tmp_path)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    repo_inode = repo.stat().st_ino

    tar = tmp_path / "work" / "kb.tar.gz"
    counts = rd.make_backup(tar, {"repo": repo, "data": data})
    assert counts == {"repo": 4, "data": 1}

    assert rd.wipe_dir_contents(repo) == 3  # entities/, signals/, .git/
    assert rd.wipe_dir_contents(data) == 1
    assert list(repo.iterdir()) == [] and list(data.iterdir()) == []

    restored = rd.restore_backup(tar, {"repo": repo, "data": data})
    assert restored == {"repo": 4, "data": 1}
    assert repo.stat().st_ino == repo_inode  # restored into, not replaced (bind mounts)
    after = {
        p.relative_to(tmp_path): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file() and "work" not in p.parts
    }
    assert after == before


def _tar_with(tmp_path, name, *, linkname=None):
    path = tmp_path / "evil.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(name)
        if linkname is not None:
            info.type = tarfile.SYMTYPE
            info.linkname = linkname
            tar.addfile(info)
        else:
            payload = b"x"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return path


@pytest.mark.parametrize(
    ("name", "linkname"),
    [
        ("repo/../../escape.md", None),
        ("/etc/passwd", None),
        ("other/file.md", None),
        ("repo/link", "/etc/passwd"),
        ("repo/link", "../../outside"),
    ],
)
def test_restore_rejects_unsafe_members(tmp_path, name, linkname):
    tar = _tar_with(tmp_path, name, linkname=linkname)
    with pytest.raises(rd.DrillError):
        rd.restore_backup(tar, {"repo": tmp_path / "repo", "data": tmp_path / "data"})
    assert not (tmp_path.parent / "escape.md").exists()


def _state(nodes, edges, audit=None, vectors=None):
    return {
        "graph": rd.snapshot_to_json(set(nodes), set(edges)),
        "audit_rows": audit or {},
        "vectors": vectors or {},
    }


def test_compare_states_identical_after_json_roundtrip():
    nodes = {("person-a", ("Entity", "Person")), ("sig-1", ("Signal",))}
    edges = {("person-a", "MENTIONED_IN", "doc-1", "src", "2026-01-01T00:00:00Z")}
    before = _state(nodes, edges, {"sig-1": 2}, {"entity": 3})
    after = json.loads(json.dumps(_state(nodes, edges, {"sig-1": 2}, {"entity": 3})))
    assert rd.compare_states(before, after) == []


def test_compare_states_reports_readable_differences():
    before = _state(
        {("person-a", ("Person",)), ("person-b", ("Person",))},
        {("person-a", "KNOWS", "person-b", "", "")},
        {"sig-1": 2},
        {"entity": 2},
    )
    after = _state({("person-a", ("Person",))}, set(), {"sig-1": 1}, {"entity": 1})
    problems = rd.compare_states(before, after)
    text = "\n".join(problems)
    assert "graph nodes: before=2 after=1 missing=1 extra=0" in text
    assert "missing node: ('person-b', ('Person',))" in text
    assert "graph edges: before=1 after=0" in text
    assert "sig-1: 2 -> 1" in text
    assert "vector counts differ" in text


def test_pick_decision_prefers_unreviewed_non_instruction():
    decisions = [
        {"id": "d1", "can_use_as_instruction": True, "review_status": "confirmed"},
        {"id": "d2", "can_use_as_instruction": False, "review_status": "rejected"},
        {"id": "d3", "can_use_as_instruction": False, "review_status": "pending"},
    ]
    assert rd.pick_decision(decisions)["id"] == "d3"
    assert rd.pick_decision(decisions[:2])["id"] == "d2"
    assert rd.pick_decision(decisions[:1]) is None


def test_pick_entity_and_search_match():
    nodes = [["doc-1", ["Document"]], ["person-elena-voss", ["Entity", "Person"]]]
    assert rd.pick_entity_id(nodes) == "person-elena-voss"
    assert rd.pick_entity_id([["doc-1", ["Document"]]]) is None
    assert rd.entity_name_from_id("person-elena-voss") == "elena voss"
    assert rd.search_finds([{"id": "x", "name": "Elena Voss"}], "person-elena-voss", "elena voss")
    assert rd.search_finds([{"id": "person-elena-voss"}], "person-elena-voss", "elena voss")
    assert not rd.search_finds([{"id": "x", "name": "Other"}], "person-elena-voss", "elena voss")
    assert not rd.search_finds({"detail": "error"}, "person-elena-voss", "elena voss")
    wrapped = [{"title": "Elena Voss", "entity": {"id": "person-elena-voss", "name": "Elena Voss"}}]
    assert rd.search_finds(wrapped, "person-elena-voss", "elena voss")
    assert not rd.search_finds([{"entity": "nope"}], "person-elena-voss", "elena voss")


def test_main_requires_neo4j_password(monkeypatch, capsys):
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    assert rd.main([]) == 2
    assert "NEO4J_PASSWORD" in capsys.readouterr().err
