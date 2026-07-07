"""Tests for the writeback service (Phase 2 of the OB1 absorption).

Ports OB1's agent-memory-api writeback semantics with the ADR-002 hardening:
  - unsafe_reasons gate (private keys, api keys, credential-like strings,
    large code blocks, raw-transcript-likeness) — faithful port of
    /tmp/ob1/integrations/agent-memory-api/index.ts unsafeReasons.
  - memory_payload fan-out into typed AgentMemory rows ("Next step:" prefix
    for next_steps → work_log; artifacts → artifact_reference).
  - Idempotent replay by idempotency_key ({base}:{index} per row).
  - Provenance CLAMP: callers may suggest observed/inferred/generated only;
    user_confirmed/imported are rejected (OB1 lets callers mint
    instruction-grade — deliberate divergence, see plan "Do NOT copy" #3).
  - One git commit per writeback batch; audit row per memory.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent_memory_store import AgentMemoryStore
from app.services.memory_writeback import (
    WritebackRequest,
    unsafe_reasons,
    writeback,
)


def _store(tmp_path) -> AgentMemoryStore:
    return AgentMemoryStore(agent_dir=tmp_path / "memory" / "agent", repo_root=tmp_path)


def _request(**overrides) -> WritebackRequest:
    payload: dict = dict(
        memory_payload={
            "decisions": ["Use FastAPI for the new service."],
            "lessons": ["Batch embedding calls."],
        },
        task_id="task-1",
        runtime={"name": "openclaw", "version": "1.2"},
        idempotency_key="task-1-run-9",
    )
    payload.update(overrides)
    return WritebackRequest(**payload)


# ---------------------------------------------------------------------------
# unsafe_reasons — faithful OB1 port
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("-----BEGIN RSA PRIVATE KEY-----\nabc", ["private_key"]),
        ("my key is sk-" + "a" * 24, ["api_key"]),
        ("password: supersecretvalue123", ["credential_like_string"]),
        ("```\ncode\n```\n```\nmore\n```", ["large_code_block"]),
        ("x" * 15001, ["raw_transcript_like"]),
        (
            "\n".join(f"user: line {i}" for i in range(9)),
            ["raw_transcript_like"],
        ),
        ("A perfectly ordinary lesson about batching.", []),
    ],
)
def test_unsafe_reasons(text, expected):
    assert unsafe_reasons(text) == expected


def test_unsafe_reasons_long_lines_flag_large_code_block():
    text = "\n".join("y" * 130 for _ in range(21))
    assert "large_code_block" in unsafe_reasons(text)


# ---------------------------------------------------------------------------
# Schema clamps (ADR-002 hardening vs OB1)
# ---------------------------------------------------------------------------


def test_provenance_clamp_rejects_confirmed_and_imported():
    for status in ("user_confirmed", "imported"):
        with pytest.raises(ValueError):
            _request(provenance={"default_status": status})


def test_provenance_accepts_observed_inferred_generated():
    for status in ("observed", "inferred", "generated"):
        req = _request(provenance={"default_status": status})
        assert req.provenance.default_status == status


def test_schema_version_validated():
    with pytest.raises(ValueError):
        _request(schema_version="openbrain.judge.recall.v1")


# ---------------------------------------------------------------------------
# writeback — fan-out, idempotency, batch commit, audit
# ---------------------------------------------------------------------------


def _mock_git():
    git = MagicMock()
    git.commit_and_push = AsyncMock()
    return git


@pytest.mark.asyncio
async def test_writeback_fans_out_typed_rows(tmp_path, monkeypatch):
    from app.services import memory_writeback as mw

    store = _store(tmp_path)
    indexed = []
    monkeypatch.setattr(mw, "_index_memory", lambda m: indexed.append(m.id) or "vec")

    req = WritebackRequest(
        memory_payload={
            "decisions": ["Pick A."],
            "outputs": ["Report drafted."],
            "lessons": ["Batch calls."],
            "constraints": ["Never push to main."],
            "unresolved_questions": ["Who owns rollout?"],
            "next_steps": ["Wire the review queue."],
            "failures": ["Retry loop looped forever."],
            "artifacts": [
                {"kind": "pr", "uri": "https://github.com/x/y/pull/1", "description": "the PR"}
            ],
        },
        task_id="task-2",
        idempotency_key="task-2-run-1",
    )
    with patch("app.services.memory_writeback.git_ops", _mock_git()) as git:
        result = await writeback(req, store=store, repo_root=tmp_path)

    assert result["success"] is True
    assert result["replayed"] is False
    created_types = [c["memory_type"] for c in result["created"]]
    assert created_types == [
        "decision",
        "output",
        "lesson",
        "constraint",
        "open_question",
        "work_log",
        "failure",
        "artifact_reference",
    ]
    # next_steps → work_log with the OB1 prefix
    work_log = store.get(
        next(c["id"] for c in result["created"] if c["memory_type"] == "work_log")
    )
    assert work_log.content == "Next step: Wire the review queue."
    # artifact content carries kind/description/uri
    art = store.get(
        next(
            c["id"]
            for c in result["created"]
            if c["memory_type"] == "artifact_reference"
        )
    )
    assert "pr: the PR" in art.content
    assert "https://github.com/x/y/pull/1" in art.content
    # every row indexed; every row born pending/generated, never instruction
    assert len(indexed) == 8
    for c in result["created"]:
        mem = store.get(c["id"])
        assert mem.review_status == "pending"
        assert mem.can_use_as_instruction is False
        assert mem.runtime_name is None or isinstance(mem.runtime_name, str)
    # ONE commit for the whole batch
    assert git.commit_and_push.await_count == 1


@pytest.mark.asyncio
async def test_writeback_replay_returns_same_ids_without_new_rows(
    tmp_path, monkeypatch
):
    from app.services import memory_writeback as mw

    store = _store(tmp_path)
    monkeypatch.setattr(mw, "_index_memory", lambda m: "vec")

    req = _request()
    with patch("app.services.memory_writeback.git_ops", _mock_git()) as git1:
        first = await writeback(req, store=store, repo_root=tmp_path)
    with patch("app.services.memory_writeback.git_ops", _mock_git()) as git2:
        second = await writeback(req, store=store, repo_root=tmp_path)

    assert second["replayed"] is True
    assert [c["id"] for c in second["created"]] == [c["id"] for c in first["created"]]
    assert len(store.list(limit=100)) == 2  # no duplicates
    assert git2.commit_and_push.await_count == 0  # replay commits nothing


@pytest.mark.asyncio
async def test_writeback_blocks_unsafe_content_entirely(tmp_path, monkeypatch):
    from app.services import memory_writeback as mw

    store = _store(tmp_path)
    monkeypatch.setattr(mw, "_index_memory", lambda m: "vec")

    req = WritebackRequest(
        memory_payload={
            "lessons": ["A fine lesson."],
            "outputs": ["password: supersecretvalue123"],
        },
        idempotency_key="task-3-run-1",
    )
    with patch("app.services.memory_writeback.git_ops", _mock_git()) as git:
        result = await writeback(req, store=store, repo_root=tmp_path)

    assert result["success"] is False
    assert any(r["reason"] == "credential_like_string" for r in result["rejected"])
    # nothing persisted — the batch is atomic
    assert store.list(limit=100) == []
    assert git.commit_and_push.await_count == 0


@pytest.mark.asyncio
async def test_writeback_stamps_provenance_and_audit_rows(tmp_path, monkeypatch):
    from app.services import memory_writeback as mw
    from app.services.memory_governance import capture_audit_store

    store = _store(tmp_path)
    monkeypatch.setattr(mw, "_index_memory", lambda m: "vec")

    req = _request(provenance={"default_status": "observed"}, confidence=0.9)
    with patch("app.services.memory_writeback.git_ops", _mock_git()):
        result = await writeback(req, store=store, repo_root=tmp_path)

    for c in result["created"]:
        mem = store.get(c["id"])
        assert mem.provenance_status == "observed"
        assert mem.confidence == 0.9
        assert mem.runtime_name == "openclaw"
        assert mem.task_id == "task-1"
        history = capture_audit_store(repo_root=tmp_path).read_for_signal(mem.id)
        assert len(history) == 1
        assert history[0].action == "capture"
        assert history[0].record_kind == "agent_memory"
