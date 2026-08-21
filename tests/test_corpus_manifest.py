"""Tests for app/services/corpus_manifest.py — filesystem-keyed reconcile."""

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.corpus_manifest import (
    CorpusReconciler,
    Manifest,
    diff_stamps,
    load_manifest,
    save_manifest,
    scan_corpus,
)


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ── scan / diff / persistence ───────────────────────────────────────────────


def test_scan_corpus_only_markdown_and_skips_git(tmp_path):
    _write(tmp_path, "entities/person/a.md", "---\nname: A\n---\n")
    _write(tmp_path, "notes.txt", "nope")
    _write(tmp_path, ".git/objects/x.md", "not corpus")
    stamps = scan_corpus(str(tmp_path))
    assert set(stamps) == {os.path.join("entities", "person", "a.md")}
    mtime_ns, size = stamps[os.path.join("entities", "person", "a.md")]
    assert size == len("---\nname: A\n---\n")
    assert mtime_ns > 0


def test_scan_missing_dir_is_empty(tmp_path):
    assert scan_corpus(str(tmp_path / "nope")) == {}


def test_diff_stamps_classifies_changes():
    old = {"a.md": (1, 10), "b.md": (1, 10), "c.md": (1, 10)}
    new = {"a.md": (1, 10), "b.md": (2, 10), "d.md": (1, 5)}
    d = diff_stamps(old, new)
    assert d.added == ["d.md"]
    assert d.modified == ["b.md"]
    assert d.removed == ["c.md"]
    assert d.changed == ["d.md", "b.md"]
    assert not d.is_empty
    assert diff_stamps(old, old).is_empty


def test_manifest_roundtrip(tmp_path):
    path = tmp_path / "data" / "corpus_manifest.json"
    m = Manifest(build_id="abc", built_at="now", files={"x.md": (5, 6)})
    save_manifest(path, m)
    loaded = load_manifest(path)
    assert loaded is not None
    assert loaded.build_id == "abc"
    assert loaded.files == {"x.md": (5, 6)}
    assert not (tmp_path / "data" / "corpus_manifest.json.tmp").exists()


def test_load_manifest_rejects_garbage(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_manifest(path) is None
    path.write_text('{"version": 99, "build_id": "x", "files": {}}', encoding="utf-8")
    assert load_manifest(path) is None
    assert load_manifest(tmp_path / "missing.json") is None


# ── reconciler ──────────────────────────────────────────────────────────────


class FakeKG:
    def __init__(self, has_entities=True):
        self._has = has_entities
        self.ingested: list[list[str]] = []
        self.removed: list[list[str]] = []

    async def has_entities(self):
        return self._has

    async def ingest_files(self, paths):
        self.ingested.append(list(paths))
        return len(paths)

    async def remove_files(self, paths):
        self.removed.append(list(paths))
        return {"documents_deleted": len(paths), "entities_stubbed": 0}


class FakeSK:
    def __init__(self):
        self.ingested: list[str] = []

    async def ingest_file(self, path, content):
        self.ingested.append(path)
        return True


def _neo4j(build_id=None):
    client = AsyncMock()
    client.execute_read = AsyncMock(return_value=[{"build_id": build_id}] if build_id else [])
    client.execute_write = AsyncMock(return_value=[])
    return client


def _reconciler(tmp_path, kg, neo4j, sk=None):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return CorpusReconciler(
        kg=kg, neo4j=neo4j, repo_path=str(repo), sk=sk,
        manifest_path=tmp_path / "data" / "corpus_manifest.json",
    )


@pytest.mark.asyncio
async def test_reconcile_empty_graph_requests_full_rebuild(tmp_path):
    kg = FakeKG(has_entities=False)
    rec = _reconciler(tmp_path, kg, _neo4j())
    result = await rec.reconcile()
    assert result["action"] == "full_rebuild_required"
    assert kg.ingested == [] and kg.removed == []


@pytest.mark.asyncio
async def test_reconcile_adopts_existing_graph_without_build_id(tmp_path, caplog):
    kg = FakeKG(has_entities=True)
    neo4j = _neo4j()
    rec = _reconciler(tmp_path, kg, neo4j)
    _write(tmp_path / "repo", "a.md", "x")
    neo4j.execute_read = AsyncMock(side_effect=[[], [{"n": 26351}]])  # build_id lookup, entity count
    result = await rec.reconcile()
    assert result["action"] == "adopted"
    assert result["files"] == 1 and result["entities"] == 26351
    # baseline written to Neo4j and to disk
    assert neo4j.execute_write.await_count == 1
    manifest = load_manifest(rec.manifest_path)
    assert manifest is not None and "a.md" in manifest.files
    assert kg.ingested == []
    # adoption is loud: counts in a WARNING so silent blessing of unseen files is visible
    assert any(r.levelname == "WARNING" and "ADOPTED" in r.getMessage() and "26351 entities" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_reconcile_mismatched_manifest_adopts_disk(tmp_path):
    kg = FakeKG()
    rec = _reconciler(tmp_path, kg, _neo4j(build_id="graphid"))
    save_manifest(rec.manifest_path, Manifest(build_id="other", built_at="", files={"gone.md": (1, 1)}))
    result = await rec.reconcile()
    assert result["action"] == "adopted" and result["reason"] == "manifest_mismatch"
    assert result["files"] == 0
    assert kg.removed == []  # never act on a manifest we don't trust
    # a fresh baseline is stamped in BOTH places so they agree from now on
    adopted = load_manifest(rec.manifest_path)
    assert adopted.build_id != "graphid" and len(adopted.build_id) == 32
    assert "gone.md" not in adopted.files


@pytest.mark.asyncio
async def test_reconcile_unchanged(tmp_path):
    kg = FakeKG()
    rec = _reconciler(tmp_path, kg, _neo4j(build_id="b1"))
    _write(tmp_path / "repo", "a.md", "x")
    save_manifest(rec.manifest_path, Manifest(build_id="b1", built_at="", files=scan_corpus(str(tmp_path / "repo"))))
    result = await rec.reconcile()
    assert result["action"] == "unchanged"
    assert kg.ingested == [] and kg.removed == []


@pytest.mark.asyncio
async def test_reconcile_applies_delta_and_updates_manifest(tmp_path):
    kg = FakeKG()
    sk = FakeSK()
    repo = tmp_path / "repo"
    rec = _reconciler(tmp_path, kg, _neo4j(build_id="b1"), sk=sk)
    _write(repo, "keep.md", "same")
    _write(repo, "old.md", "will be deleted")
    _write(repo, "mod.md", "v1")
    baseline = scan_corpus(str(repo))
    save_manifest(rec.manifest_path, Manifest(build_id="b1", built_at="", files=baseline))

    # mutate disk: delete one, modify one (force a different mtime), add one
    (repo / "old.md").unlink()
    _write(repo, "mod.md", "v2 — longer")
    os.utime(repo / "mod.md", ns=(baseline["mod.md"][0] + 10**9, baseline["mod.md"][0] + 10**9))
    _write(repo, "new.md", "brand new")

    result = await rec.reconcile()
    assert result["action"] == "reconciled"
    assert result["added"] == 1 and result["modified"] == 1 and result["removed"] == 1
    assert kg.removed == [["old.md"]]
    assert kg.ingested == [["new.md", "mod.md"]]
    assert sorted(sk.ingested) == ["mod.md", "new.md"]
    assert result["semantica_indexed"] == 2

    # manifest now matches disk → next reconcile is a no-op
    again = await rec.reconcile()
    assert again["action"] == "unchanged"


@pytest.mark.asyncio
async def test_record_full_build_stamps_neo4j_and_disk(tmp_path):
    kg = FakeKG()
    neo4j = _neo4j()
    rec = _reconciler(tmp_path, kg, neo4j)
    _write(tmp_path / "repo", "a.md", "x")
    manifest = await rec.record_full_build(source="test")
    assert len(manifest.build_id) == 32
    query, params = neo4j.execute_write.await_args.args
    assert "_CorpusState" in query
    assert params["props"]["build_id"] == manifest.build_id
    assert params["props"]["source"] == "test"
    assert load_manifest(rec.manifest_path).build_id == manifest.build_id


class FailingKG(FakeKG):
    async def ingest_files(self, paths):
        raise RuntimeError("neo4j down")


@pytest.mark.asyncio
async def test_failed_ingest_keeps_old_stamps_so_next_boot_retries(tmp_path):
    kg = FailingKG()
    repo = tmp_path / "repo"
    rec = _reconciler(tmp_path, kg, _neo4j(build_id="b1"))
    _write(repo, "a.md", "v1")
    baseline = scan_corpus(str(repo))
    save_manifest(rec.manifest_path, Manifest(build_id="b1", built_at="", files=baseline))
    _write(repo, "a.md", "v2 longer")
    _write(repo, "new.md", "new")

    result = await rec.reconcile()
    assert result["action"] == "reconciled" and "graph_ingest_error" in result
    assert result["retry_next_boot"] == 2

    saved = load_manifest(rec.manifest_path)
    assert saved.files["a.md"] == baseline["a.md"]  # old stamp retained → still "modified" next time
    assert "new.md" not in saved.files  # never recorded → still "added" next time
    again = diff_stamps(saved.files, scan_corpus(str(repo)))
    assert again.modified == ["a.md"] and again.added == ["new.md"]


@pytest.mark.asyncio
async def test_reconcile_skips_manifest_write_if_build_id_changed_underneath(tmp_path):
    kg = FakeKG()
    repo = tmp_path / "repo"
    neo4j = _neo4j(build_id="b1")
    # first read → b1 (reconcile start), second read → b2 (a rebuild finished meanwhile)
    neo4j.execute_read = AsyncMock(side_effect=[[{"build_id": "b1"}], [{"build_id": "b2"}]])
    rec = _reconciler(tmp_path, kg, neo4j)
    _write(repo, "a.md", "v1")
    save_manifest(rec.manifest_path, Manifest(build_id="b1", built_at="", files={}))
    result = await rec.reconcile()
    assert result["action"] == "reconciled" and result["manifest"] == "skipped_build_id_changed"
    assert load_manifest(rec.manifest_path).build_id == "b1"  # untouched; the rebuild owns the newer one
