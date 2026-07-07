"""Tests for the capture service orchestrator (Phase 1 of the OB1 absorption).

Covers the full capture flow — persist (CaptureStore) → enrich (best-effort)
→ index (best-effort) → git commit (one commit: capture + audit files) →
audit row (action="capture") — and the audited review path over captures.

The G2/G4 guarantees under test:
  - persist-first: enrichment/index/git failures never lose the capture
  - dedup returns the existing record without re-committing
  - review transitions emit audit rows committed alongside the record
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.memory_capture import CaptureStore


# ---------------------------------------------------------------------------
# CaptureStore accessors (get / update / list)
# ---------------------------------------------------------------------------


def _make_store(tmp_path) -> CaptureStore:
    return CaptureStore(
        capture_dir=tmp_path / "memory" / "captures", repo_root=tmp_path
    )


def test_store_get_returns_persisted_capture(tmp_path):
    store = _make_store(tmp_path)
    result = store.capture("A thought.", source="manual")
    fetched = store.get(result.memory.id)
    assert fetched is not None
    assert fetched.id == result.memory.id
    assert fetched.content == "A thought."


def test_store_get_missing_returns_none(tmp_path):
    assert _make_store(tmp_path).get("nope") is None


def test_store_update_overwrites_record(tmp_path):
    store = _make_store(tmp_path)
    mem = store.capture("A thought.", source="manual").memory
    updated = mem.model_copy(update={"enrichment": {"type": "idea"}})
    store.update(updated)
    assert store.get(mem.id).enrichment == {"type": "idea"}


def test_store_list_filters_and_sorts_newest_first(tmp_path):
    store = _make_store(tmp_path)
    a = store.capture("Alpha.", source="manual").memory
    b = store.capture("Beta.", source="web").memory
    newer = b.model_copy(update={"created_at": "2099-01-01T00:00:00+00:00"})
    store.update(newer)

    all_records = store.list()
    assert [m.id for m in all_records] == [b.id, a.id]

    assert [m.id for m in store.list(source="web")] == [b.id]
    assert store.list(review_status="confirmed") == []
    assert len(store.list(limit=1)) == 1


# ---------------------------------------------------------------------------
# capture_and_persist — the orchestrated flow
# ---------------------------------------------------------------------------


class _FakeIndexing:
    def __init__(self, vec_id="vec-1"):
        self.vec_id = vec_id
        self.captures = []

    def __call__(self, capture):
        self.captures.append(capture)
        return self.vec_id


async def _fake_enrich(content, claude_client=None):
    return {
        "type": "decision",
        "topics": ["architecture"],
        "people": [],
        "action_items": [],
        "dates_mentioned": [],
    }


@pytest.mark.asyncio
async def test_capture_and_persist_full_flow(tmp_path, monkeypatch):
    from app.services import capture_service
    from app.services import signal_indexing

    store = _make_store(tmp_path)
    fake_index = _FakeIndexing()
    monkeypatch.setattr(signal_indexing, "index_capture_one", fake_index)
    monkeypatch.setattr(capture_service, "enrich_capture", _fake_enrich)

    mock_git = MagicMock()
    mock_git.commit_and_push = AsyncMock()

    with patch("app.services.capture_service.git_ops", mock_git):
        result = await capture_service.capture_and_persist(
            "We standardized on FastAPI.",
            source="manual",
            actor="scott",
            store=store,
            repo_root=tmp_path,
        )

    assert result["success"] is True
    assert result["deduped"] is False
    assert result["vector_indexed"] is True
    assert result["committed"] is True
    assert result["enrichment"]["type"] == "decision"

    # persisted record carries the enrichment
    persisted = store.get(result["id"])
    assert persisted.enrichment["topics"] == ["architecture"]
    # provenance is server-injected, never client-supplied (ADR-002)
    assert persisted.provenance_status == "imported"
    assert persisted.can_use_as_instruction is False

    # audit row: action="capture", record_kind="capture"
    from app.services.memory_governance import capture_audit_store

    history = capture_audit_store(repo_root=tmp_path).read_for_signal(result["id"])
    assert len(history) == 1
    assert history[0].action == "capture"
    assert history[0].record_kind == "capture"
    assert history[0].actor == "scott"

    # ONE commit containing both the capture file and the audit JSONL
    assert mock_git.commit_and_push.await_count == 1
    paths, msg = mock_git.commit_and_push.call_args.args
    assert f"memory/captures/{result['id']}.json" in paths
    assert f"memory/audit/{result['id']}.jsonl" in paths
    assert "capture" in msg

    # the indexed record is the enriched one
    assert fake_index.captures[0].enrichment["type"] == "decision"


@pytest.mark.asyncio
async def test_capture_dedup_returns_existing_without_side_effects(
    tmp_path, monkeypatch
):
    from app.services import capture_service
    from app.services import signal_indexing

    store = _make_store(tmp_path)
    monkeypatch.setattr(capture_service, "enrich_capture", _fake_enrich)
    monkeypatch.setattr(signal_indexing, "index_capture_one", _FakeIndexing())

    mock_git = MagicMock()
    mock_git.commit_and_push = AsyncMock()

    with patch("app.services.capture_service.git_ops", mock_git):
        first = await capture_service.capture_and_persist(
            "Same thought.", source="manual", store=store, repo_root=tmp_path
        )
        second = await capture_service.capture_and_persist(
            "Same thought.", source="manual", store=store, repo_root=tmp_path
        )

    assert second["deduped"] is True
    assert second["id"] == first["id"]
    # only the first capture commits
    assert mock_git.commit_and_push.await_count == 1


@pytest.mark.asyncio
async def test_enrichment_failure_never_blocks_persistence(tmp_path, monkeypatch):
    from app.services import capture_service
    from app.services import signal_indexing
    from app.services.capture_enrichment import FALLBACK_METADATA

    async def _boom_enrich(content, claude_client=None):
        raise RuntimeError("enrichment exploded")

    store = _make_store(tmp_path)
    monkeypatch.setattr(capture_service, "enrich_capture", _boom_enrich)
    monkeypatch.setattr(signal_indexing, "index_capture_one", _FakeIndexing())

    mock_git = MagicMock()
    mock_git.commit_and_push = AsyncMock()

    with patch("app.services.capture_service.git_ops", mock_git):
        result = await capture_service.capture_and_persist(
            "A thought.", source="manual", store=store, repo_root=tmp_path
        )

    assert result["success"] is True
    assert result["enrichment"] == FALLBACK_METADATA
    assert store.get(result["id"]) is not None


@pytest.mark.asyncio
async def test_git_failure_is_non_fatal(tmp_path, monkeypatch):
    from app.services import capture_service
    from app.services import signal_indexing

    store = _make_store(tmp_path)
    monkeypatch.setattr(capture_service, "enrich_capture", _fake_enrich)
    monkeypatch.setattr(signal_indexing, "index_capture_one", _FakeIndexing())

    mock_git = MagicMock()
    mock_git.commit_and_push = AsyncMock(side_effect=RuntimeError("no remote"))

    with patch("app.services.capture_service.git_ops", mock_git):
        result = await capture_service.capture_and_persist(
            "A thought.", source="manual", store=store, repo_root=tmp_path
        )

    assert result["success"] is True
    assert result["committed"] is False
    assert store.get(result["id"]) is not None


# ---------------------------------------------------------------------------
# review_capture — audited governance transitions over captures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_capture_confirm_persists_and_audits(tmp_path, monkeypatch):
    from app.services import capture_service
    from app.services import signal_indexing
    from app.services.memory_governance import capture_audit_store

    store = _make_store(tmp_path)
    monkeypatch.setattr(capture_service, "enrich_capture", _fake_enrich)
    monkeypatch.setattr(signal_indexing, "index_capture_one", _FakeIndexing())

    mock_git = MagicMock()
    mock_git.commit_and_push = AsyncMock()

    with patch("app.services.capture_service.git_ops", mock_git):
        created = await capture_service.capture_and_persist(
            "Confirm me.", source="manual", store=store, repo_root=tmp_path
        )
        result = await capture_service.review_capture(
            created["id"],
            "confirm",
            actor="scott",
            store=store,
            repo_root=tmp_path,
        )

    assert result["success"] is True
    assert result["review_applied"] is True
    assert result["gate_response"] == "allow"

    persisted = store.get(created["id"])
    assert persisted.can_use_as_instruction is True
    assert persisted.provenance_status == "user_confirmed"

    history = capture_audit_store(repo_root=tmp_path).read_for_signal(created["id"])
    assert [r.action for r in history] == ["capture", "confirm"]


@pytest.mark.asyncio
async def test_review_capture_unknown_id_errors(tmp_path):
    from app.services import capture_service

    result = await capture_service.review_capture(
        "missing", "confirm", store=_make_store(tmp_path), repo_root=tmp_path
    )
    assert result["success"] is False
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_review_capture_invalid_action_errors(tmp_path, monkeypatch):
    from app.services import capture_service
    from app.services import signal_indexing

    store = _make_store(tmp_path)
    monkeypatch.setattr(capture_service, "enrich_capture", _fake_enrich)
    monkeypatch.setattr(signal_indexing, "index_capture_one", _FakeIndexing())

    mock_git = MagicMock()
    mock_git.commit_and_push = AsyncMock()
    with patch("app.services.capture_service.git_ops", mock_git):
        created = await capture_service.capture_and_persist(
            "A thought.", source="manual", store=store, repo_root=tmp_path
        )
        result = await capture_service.review_capture(
            created["id"], "bless", store=store, repo_root=tmp_path
        )
    assert result["success"] is False
