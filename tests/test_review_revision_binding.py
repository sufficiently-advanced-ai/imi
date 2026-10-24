"""ADR-009: review authority and revision binding.

  - A review binds to the content revision it was applied to
    (``reviewed_content_hash``); editing that content sends the signal back to
    pending without instruction-grade authority, audit row first.
  - Status / owner / due_date edits and identical-content edits never demote.
  - MCP stamps the audit actor server-side; the caller's ``actor`` is kept as
    an unverified ``claimed_actor``.
  - ``MCP_REVIEW_ACTIONS=False`` refuses review actions over MCP (including the
    ask_kb sub-agent) while the REST review endpoint keeps working.
  - Recall never serves a demoted signal as instruction-grade.
  - Capture and agent-memory reviews are audit-first too.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.models.signal import MeetingSignals, Signal
from app.services.signal_audit import SignalAuditStore, content_edit_with_audit
from app.services.signal_governance import (
    apply_review,
    content_hash,
    demote_for_content_change,
)
from app.services.signal_store import SignalStore

BOT_ID = "bot-revision"
SIGNAL_ID = "sig-revision-1"
ORIGINAL = "Ship the billing migration on Friday."
EDITED = "Ship the billing migration next quarter."


def _make_signal(**overrides) -> Signal:
    fields = dict(
        id=SIGNAL_ID,
        type="decision",
        content=ORIGINAL,
        source_meeting_id=BOT_ID,
        source_timestamp="2026-06-05T12:00:00+00:00",
    )
    fields.update(overrides)
    return Signal(**fields)  # type: ignore[arg-type]


def _confirmed() -> Signal:
    return apply_review(_make_signal(), "confirm")


# ---------------------------------------------------------------------------
# Pure governance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["confirm", "reject", "evidence_only"])
def test_review_actions_bind_the_content_hash(action):
    reviewed = apply_review(_make_signal(), action)
    assert reviewed.reviewed_content_hash == content_hash(ORIGINAL)


def test_dispute_does_not_bind_a_revision():
    assert apply_review(_make_signal(), "dispute").reviewed_content_hash is None


def test_content_change_demotes_confirmed_signal():
    confirmed = _confirmed()
    demoted = demote_for_content_change(confirmed, EDITED)
    assert demoted.content == EDITED
    assert demoted.review_status == "pending"
    assert demoted.can_use_as_instruction is False
    # Provenance kept: demotion only removes authority (ADR-009).
    assert demoted.provenance_status == "user_confirmed"
    assert demoted.reviewed_content_hash == content_hash(ORIGINAL)
    # Original untouched; model invariant still holds on the copy.
    assert confirmed.can_use_as_instruction is True
    Signal.model_validate(demoted.model_dump())


def test_identical_content_does_not_demote():
    confirmed = _confirmed()
    same = demote_for_content_change(confirmed, ORIGINAL)
    assert same.review_status == "confirmed"
    assert same.can_use_as_instruction is True


def test_pending_signal_edit_is_not_a_governance_event():
    new, record = content_edit_with_audit(_make_signal(), EDITED)
    assert new.content == EDITED
    assert new.review_status == "pending"
    assert record is None


def test_legacy_reviewed_signal_without_hash_uses_current_content():
    legacy = _confirmed().model_copy(update={"reviewed_content_hash": None})
    assert demote_for_content_change(legacy, ORIGINAL).review_status == "confirmed"
    assert demote_for_content_change(legacy, EDITED).review_status == "pending"


def test_instruction_grade_imported_signal_is_demoted_even_if_pending():
    imported = _make_signal(provenance_status="imported", can_use_as_instruction=True)
    demoted = demote_for_content_change(imported, EDITED)
    assert demoted.can_use_as_instruction is False


def test_content_edited_audit_record_shape():
    _, record = content_edit_with_audit(
        _confirmed(), EDITED, actor="mcp:test-client", claimed_actor="alice"
    )
    assert record is not None
    assert record.action == "content_edited"
    assert record.gate_response is None
    assert record.actor == "mcp:test-client"
    assert record.claimed_actor == "alice"
    assert record.before["content_hash"] == content_hash(ORIGINAL)
    assert record.after["content_hash"] == content_hash(EDITED)
    assert record.before["review_status"] == "confirmed"
    assert record.after["review_status"] == "pending"
    assert record.after["can_use_as_instruction"] is False


# ---------------------------------------------------------------------------
# Chokepoint wiring through chat_tools.update_signal (real stores in tmp_path)
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path):
    store = SignalStore(signals_dir=tmp_path / "signals")
    audit_store = SignalAuditStore(audit_dir=tmp_path / "signals" / "audit", repo_root=tmp_path)
    with patch("app.services.signal_indexing.index_meeting_signals"):
        store.save(
            MeetingSignals(meeting_id=BOT_ID, bot_id=BOT_ID, signals=[_make_signal()])
        )
    signal_file = store._file_path(BOT_ID)

    mock_git = MagicMock()
    mock_git.commit_file = AsyncMock()
    mock_git.commit_and_push = AsyncMock()

    with (
        patch("app.services.chat_tools.SignalStore", return_value=store),
        patch("app.services.chat_tools.SignalAuditStore", return_value=audit_store),
        patch("app.services.chat_tools.git_ops", mock_git),
        patch("app.neo4j_client.get_neo4j_client", return_value=None),
        patch("app.services.signal_indexing.index_meeting_signals"),
    ):
        yield store, audit_store, signal_file


def _persisted(signal_file: Path) -> dict:
    data = json.loads(signal_file.read_text(encoding="utf-8"))
    return next(s for s in data["signals"] if s["id"] == SIGNAL_ID)


async def _confirm(actor: str = "reviewer") -> dict:
    from app.services.chat_tools import update_signal

    result = await update_signal(SIGNAL_ID, review_action="confirm", actor=actor)
    assert result["success"] is True, result
    return result


@pytest.mark.asyncio
async def test_confirm_persists_the_content_hash(env):
    _, audit_store, signal_file = env
    await _confirm()
    assert _persisted(signal_file)["reviewed_content_hash"] == content_hash(ORIGINAL)
    (row,) = audit_store.read_for_signal(SIGNAL_ID)
    assert row.after["reviewed_content_hash"] == content_hash(ORIGINAL)


@pytest.mark.asyncio
async def test_content_edit_of_confirmed_signal_demotes_audit_first(env):
    store, audit_store, signal_file = env
    await _confirm()

    from app.services.chat_tools import update_signal

    order: list[str] = []
    real_append = audit_store.append
    real_save = store.save

    def tracking_append(record):
        order.append(f"audit:{record.action}")
        return real_append(record)

    def tracking_save(container):
        order.append("persist")
        return real_save(container)

    with (
        patch.object(audit_store, "append", side_effect=tracking_append),
        patch.object(store, "save", side_effect=tracking_save),
    ):
        result = await update_signal(SIGNAL_ID, content=EDITED, actor="editor")

    assert result["success"] is True, result
    assert result["review_voided"] is True
    assert order == ["audit:content_edited", "persist"]

    persisted = _persisted(signal_file)
    assert persisted["content"] == EDITED
    assert persisted["review_status"] == "pending"
    assert persisted["can_use_as_instruction"] is False

    rows = audit_store.read_for_signal(SIGNAL_ID)
    assert [r.action for r in rows] == ["confirm", "content_edited"]
    assert rows[1].id == result["audit_row_id"]
    assert rows[1].actor == "editor"


@pytest.mark.asyncio
async def test_demoting_edit_with_failed_audit_persists_nothing(env):
    _, audit_store, signal_file = env
    await _confirm()
    before_bytes = signal_file.read_bytes()

    from app.services.chat_tools import update_signal

    with patch.object(audit_store, "append", side_effect=OSError("disk full")):
        result = await update_signal(SIGNAL_ID, content=EDITED)

    assert result["success"] is False
    assert "disk full" in result["error"]
    assert signal_file.read_bytes() == before_bytes
    assert _persisted(signal_file)["can_use_as_instruction"] is True


@pytest.mark.asyncio
async def test_demoting_edit_with_failed_persist_appends_compensating_row(env):
    _, audit_store, signal_file = env
    await _confirm()
    before_bytes = signal_file.read_bytes()

    from app.services.chat_tools import update_signal

    with patch(
        "app.services.signal_store.atomic_write_text",
        side_effect=OSError("read-only filesystem"),
    ):
        result = await update_signal(SIGNAL_ID, content=EDITED)

    assert result["success"] is False
    assert signal_file.read_bytes() == before_bytes
    rows = audit_store.read_for_signal(SIGNAL_ID)
    assert [r.action for r in rows] == ["confirm", "content_edited", "content_edited_failed"]
    assert rows[2].after == rows[1].before
    assert rows[1].id in rows[2].reasoning


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [
        {"status": "done"},
        {"owner_id": "person-sarah-chen"},
        {"due_date": "2026-12-01"},
        {"content": ORIGINAL},
    ],
)
async def test_non_content_and_identical_edits_do_not_demote(env, fields):
    _, audit_store, signal_file = env
    await _confirm()

    from app.services.chat_tools import update_signal

    with patch("app.services.chat_tools.get_knowledge_graph", side_effect=Exception("no graph")):
        result = await update_signal(SIGNAL_ID, **fields)

    assert result["success"] is True, result
    assert "review_voided" not in result
    persisted = _persisted(signal_file)
    assert persisted["review_status"] == "confirmed"
    assert persisted["can_use_as_instruction"] is True
    assert [r.action for r in audit_store.read_for_signal(SIGNAL_ID)] == ["confirm"]


@pytest.mark.asyncio
async def test_rest_content_edit_route_demotes(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.routes.signal_mutations as mutations

    _, _, signal_file = env
    await _confirm()
    test_app = FastAPI()
    test_app.include_router(mutations.router)
    resp = TestClient(test_app).post(
        f"/api/signals/{SIGNAL_ID}/update", json={"content": EDITED}
    )
    assert resp.status_code == 200, resp.text
    assert _persisted(signal_file)["review_status"] == "pending"


# ---------------------------------------------------------------------------
# MCP: server-stamped actor, MCP_REVIEW_ACTIONS gate
# ---------------------------------------------------------------------------


def _tool_json(response) -> dict:
    return json.loads(response[0].text)


@pytest.mark.asyncio
async def test_mcp_ignores_caller_supplied_actor(env):
    from app.routes import mcp_server

    _, audit_store, signal_file = env
    result = _tool_json(
        await mcp_server.handle_call_tool(
            "update_signal",
            {"signal_id": SIGNAL_ID, "review_action": "confirm", "actor": "the-ceo"},
        )
    )
    assert result["success"] is True, result
    (row,) = audit_store.read_for_signal(SIGNAL_ID)
    assert row.actor == "mcp"
    assert row.claimed_actor == "the-ceo"
    assert _persisted(signal_file)["can_use_as_instruction"] is True


@pytest.mark.asyncio
async def test_mcp_actor_includes_session_client_name(env):
    from app.routes import mcp_server

    _, audit_store, _ = env
    ctx = SimpleNamespace(
        session=SimpleNamespace(
            client_params=SimpleNamespace(clientInfo=SimpleNamespace(name="claude-code"))
        )
    )
    with patch.object(
        type(mcp_server.server), "request_context", new_callable=PropertyMock, return_value=ctx
    ):
        await mcp_server.handle_call_tool(
            "update_signal", {"signal_id": SIGNAL_ID, "review_action": "confirm"}
        )
    (row,) = audit_store.read_for_signal(SIGNAL_ID)
    assert row.actor == "mcp:claude-code"
    assert row.claimed_actor is None


def test_mcp_actor_sanitizes_client_name():
    from app.services.review_authority import mcp_actor

    assert mcp_actor(None) == "mcp"
    assert mcp_actor("   ") == "mcp"
    assert mcp_actor("evil\nname;rm") == "mcp:evil_name_rm"
    assert len(mcp_actor("x" * 500)) == len("mcp:") + 64


@pytest.mark.asyncio
async def test_review_actions_disabled_rejects_mcp_but_rest_still_works(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.routes.decisions as decisions_route
    from app.config import settings
    from app.routes import mcp_server

    _, audit_store, signal_file = env
    monkeypatch.setattr(settings, "MCP_REVIEW_ACTIONS", False)

    result = _tool_json(
        await mcp_server.handle_call_tool(
            "update_signal", {"signal_id": SIGNAL_ID, "review_action": "confirm"}
        )
    )
    assert result["success"] is False
    assert "MCP_REVIEW_ACTIONS" in result["error"]
    assert _persisted(signal_file)["review_status"] == "pending"
    assert audit_store.read_for_signal(SIGNAL_ID) == []

    # Plain field edits over MCP still work with the flag off.
    with patch("app.services.chat_tools.get_knowledge_graph", side_effect=Exception("no graph")):
        plain = _tool_json(
            await mcp_server.handle_call_tool(
                "update_signal", {"signal_id": SIGNAL_ID, "status": "done"}
            )
        )
    assert plain["success"] is True, plain

    # The REST review endpoint is unaffected by the MCP flag.
    monkeypatch.setattr(
        decisions_route, "get_decision", lambda decision_id, **kw: {"id": decision_id, "state": "active"}
    )
    test_app = FastAPI()
    test_app.include_router(decisions_route.router)
    resp = TestClient(test_app).post(
        f"/api/decisions/{SIGNAL_ID}/review", json={"action": "confirm"}
    )
    assert resp.status_code == 200, resp.text
    assert _persisted(signal_file)["can_use_as_instruction"] is True


@pytest.mark.asyncio
async def test_review_actions_disabled_also_gates_ask_kb_sub_agent(monkeypatch):
    from app.config import settings
    from app.services.ask_kb import _make_tool_dispatcher

    monkeypatch.setattr(settings, "MCP_REVIEW_ACTIONS", False)
    with patch("app.services.chat_tools.update_signal", new_callable=AsyncMock) as fn:
        result = await _make_tool_dispatcher("mcp:x")["update_signal"](
            {"signal_id": SIGNAL_ID, "review_action": "confirm", "actor": "someone"}
        )
    assert result["success"] is False
    fn.assert_not_awaited()


@pytest.mark.asyncio
async def test_ask_kb_sub_agent_stamps_actor(monkeypatch):
    from app.config import settings
    from app.services.ask_kb import _make_tool_dispatcher

    monkeypatch.setattr(settings, "MCP_REVIEW_ACTIONS", True)
    with patch(
        "app.services.chat_tools.update_signal",
        new_callable=AsyncMock,
        return_value={"success": True},
    ) as fn:
        await _make_tool_dispatcher("mcp:x")["update_signal"](
            {"signal_id": SIGNAL_ID, "review_action": "confirm", "actor": "someone"}
        )
    assert fn.call_args.kwargs["actor"] == "mcp:x"
    assert fn.call_args.kwargs["claimed_actor"] == "someone"


# ---------------------------------------------------------------------------
# Recall: a demoted signal is never instruction-grade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_excludes_demoted_signal_from_instruction_grade():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.services.memory_recall import RecallRequest, recall

    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    demoted = demote_for_content_change(_confirmed(), EDITED)
    # The vector was indexed while the signal was confirmed (stale metadata).
    stale_vector = {
        "score": 0.95,
        "metadata": {
            "content_type": "signal",
            "id": SIGNAL_ID,
            "content": ORIGINAL,
            "created_at": "2026-07-01T00:00:00+00:00",
            "provenance_status": "user_confirmed",
            "review_status": "confirmed",
            "can_use_as_evidence": True,
            "can_use_as_instruction": True,
        },
    }

    class _Store:
        def search_vectors(self, embedding, k=10, **kwargs):
            return [stale_vector]

    class _Embedder:
        def generate_embeddings(self, text, data_type="text"):
            return [0.1, 0.2, 0.3]

    resolvers = {
        "signal": lambda rid: demoted if rid == SIGNAL_ID else None,
        "capture": lambda rid: None,
        "agent_memory": lambda rid: None,
    }
    try:
        result = await recall(
            RecallRequest(query="billing migration", authority="instruction"),
            vector_store=_Store(),
            embedder=_Embedder(),
            resolvers=resolvers,
            session_factory=maker,
        )
        assert result["memories"] == []

        evidence = await recall(
            RecallRequest(query="billing migration", authority="evidence"),
            vector_store=_Store(),
            embedder=_Embedder(),
            resolvers=resolvers,
            session_factory=maker,
        )
        assert [m["record_id"] for m in evidence["memories"]] == [SIGNAL_ID]
        assert evidence["memories"][0]["use_policy"]["can_use_as_instruction"] is False
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Captures and agent memories: audit-first review persistence
# ---------------------------------------------------------------------------


def _capture_env(tmp_path):
    from app.services.memory_capture import CaptureStore, capture_memory

    store = CaptureStore(capture_dir=tmp_path / "memory" / "captures", repo_root=tmp_path)
    cap = capture_memory("We standardized on FastAPI.", source="manual")
    store.update(cap)
    return store, cap


@pytest.mark.asyncio
async def test_capture_review_audit_failure_persists_nothing(tmp_path, monkeypatch):
    from app.services import capture_service, signal_indexing

    store, cap = _capture_env(tmp_path)
    before = store._file_path(cap.id).read_bytes()
    monkeypatch.setattr(signal_indexing, "index_capture_one", lambda c: None)

    with (
        patch("app.services.capture_service.git_ops", MagicMock(commit_and_push=AsyncMock())),
        patch.object(SignalAuditStore, "append", side_effect=OSError("disk full")),
    ):
        result = await capture_service.review_capture(
            cap.id, "confirm", store=store, repo_root=tmp_path
        )

    assert result["success"] is False
    assert result["review_applied"] is False
    assert "disk full" in result["error"]
    assert store._file_path(cap.id).read_bytes() == before


@pytest.mark.asyncio
async def test_capture_review_persist_failure_appends_compensating_row(tmp_path, monkeypatch):
    from app.services import capture_service, signal_indexing
    from app.services.memory_governance import capture_audit_store

    store, cap = _capture_env(tmp_path)
    monkeypatch.setattr(signal_indexing, "index_capture_one", lambda c: None)

    with (
        patch("app.services.capture_service.git_ops", MagicMock(commit_and_push=AsyncMock())),
        patch.object(store, "update", side_effect=OSError("read-only filesystem")),
    ):
        result = await capture_service.review_capture(
            cap.id, "confirm", store=store, repo_root=tmp_path
        )

    assert result["success"] is False
    assert store.get(cap.id).can_use_as_instruction is False
    rows = capture_audit_store(repo_root=tmp_path).read_for_signal(cap.id)
    assert [r.action for r in rows] == ["confirm", "confirm_failed"]
    assert rows[1].after == rows[0].before
    assert result["compensating_audit_row_id"] == rows[1].id


@pytest.mark.asyncio
async def test_capture_review_appends_audit_before_persist(tmp_path, monkeypatch):
    from app.services import capture_service, signal_indexing

    store, cap = _capture_env(tmp_path)
    monkeypatch.setattr(signal_indexing, "index_capture_one", lambda c: None)
    order: list[str] = []
    real_update = store.update
    real_append = SignalAuditStore.append

    def tracking_append(self, record):
        order.append("audit")
        return real_append(self, record)

    def tracking_update(memory):
        order.append("persist")
        return real_update(memory)

    with (
        patch("app.services.capture_service.git_ops", MagicMock(commit_and_push=AsyncMock())),
        patch.object(SignalAuditStore, "append", tracking_append),
        patch.object(store, "update", side_effect=tracking_update),
    ):
        result = await capture_service.review_capture(
            cap.id, "confirm", store=store, repo_root=tmp_path
        )

    assert result["success"] is True, result
    assert order == ["audit", "persist"]
    assert store.get(cap.id).reviewed_content_hash == content_hash(cap.content)


def _memory_env(tmp_path):
    from app.models.agent_memory import AgentMemory
    from app.services.agent_memory_store import AgentMemoryStore

    store = AgentMemoryStore(agent_dir=tmp_path / "memory" / "agent", repo_root=tmp_path)
    mem = AgentMemory(memory_type="lesson", content="Batch embedding calls.")
    store.save(mem)
    return store, mem


@pytest.mark.asyncio
async def test_agent_memory_review_audit_failure_persists_nothing(tmp_path, monkeypatch):
    from app.services import memory_writeback as mw

    store, mem = _memory_env(tmp_path)
    path = store._file_path(mem)
    before = path.read_bytes()
    monkeypatch.setattr(mw, "_index_memory", lambda m: None)

    with (
        patch("app.services.memory_writeback.git_ops", MagicMock(commit_and_push=AsyncMock())),
        patch.object(SignalAuditStore, "append", side_effect=OSError("disk full")),
    ):
        result = await mw.review_agent_memory(mem.id, "confirm", store=store, repo_root=tmp_path)

    assert result["success"] is False
    assert result["review_applied"] is False
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_agent_memory_review_persist_failure_appends_compensating_row(
    tmp_path, monkeypatch
):
    from app.services import memory_writeback as mw
    from app.services.memory_governance import capture_audit_store

    store, mem = _memory_env(tmp_path)
    monkeypatch.setattr(mw, "_index_memory", lambda m: None)

    with (
        patch("app.services.memory_writeback.git_ops", MagicMock(commit_and_push=AsyncMock())),
        patch.object(store, "update", side_effect=OSError("read-only filesystem")),
    ):
        result = await mw.review_agent_memory(mem.id, "confirm", store=store, repo_root=tmp_path)

    assert result["success"] is False
    assert store.get(mem.id).can_use_as_instruction is False
    rows = capture_audit_store(repo_root=tmp_path).read_for_signal(mem.id)
    assert [r.action for r in rows] == ["confirm", "confirm_failed"]


@pytest.mark.asyncio
async def test_agent_memory_review_happy_path_binds_revision(tmp_path, monkeypatch):
    from app.services import memory_writeback as mw
    from app.services.memory_governance import capture_audit_store

    store, mem = _memory_env(tmp_path)
    monkeypatch.setattr(mw, "_index_memory", lambda m: None)
    with patch("app.services.memory_writeback.git_ops", MagicMock(commit_and_push=AsyncMock())):
        result = await mw.review_agent_memory(mem.id, "confirm", store=store, repo_root=tmp_path)

    assert result["success"] is True, result
    persisted = store.get(mem.id)
    assert persisted.can_use_as_instruction is True
    assert persisted.reviewed_content_hash == content_hash(mem.content)
    assert [r.action for r in capture_audit_store(repo_root=tmp_path).read_for_signal(mem.id)] == [
        "confirm"
    ]
