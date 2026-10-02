"""ADR-008 × ADR-002 — reaching the MCP server never confers instruction authority.

ADR-008 makes MCP reachable beyond loopback (remote tier, unauthenticated).
ADR-002 must still hold for every caller: nothing on the MCP surface sets
``can_use_as_instruction`` (or moves provenance / review status) except an
explicit review action. These tests pin that across the WHOLE tool surface,
complementing the per-tool checks in test_capture_mcp_tool.py and
test_writeback_mcp_tool.py and the service-level checks in
test_capture_service.py / test_memory_writeback.py.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routes import mcp_server
from app.services.mcp_tool_definitions import TOOL_DEFS

GOVERNANCE_FIELDS = {
    "provenance_status",
    "review_status",
    "can_use_as_evidence",
    "can_use_as_instruction",
}
INSTRUCTION_ELIGIBLE_PROVENANCE = {"user_confirmed", "imported"}

# The single, audited governance entry point on the MCP surface (ADR-002).
# Adding another tool here is a governance decision — it needs review, not
# just a test update.
REVIEW_ACTION_TOOLS = {"update_signal"}

SMUGGLED = {
    "provenance_status": "user_confirmed",
    "review_status": "confirmed",
    "can_use_as_evidence": True,
    "can_use_as_instruction": True,
}


def _all_schemas():
    """Every input schema served over MCP or the chat-agent tool surface."""
    for tool in mcp_server.TOOLS:
        yield tool.name, tool.inputSchema
    for name, td in TOOL_DEFS.items():
        yield f"chat:{name}", td["inputSchema"]


def _walk(schema, path=""):
    """Yield (dotted_name, subschema) for every property at any depth."""
    if not isinstance(schema, dict):
        return
    for key, sub in (schema.get("properties") or {}).items():
        yield f"{path}{key}", sub
        yield from _walk(sub, f"{path}{key}.")
    if isinstance(schema.get("items"), dict):
        yield from _walk(schema["items"], f"{path}[].")
    for combinator in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(combinator) or []:
            yield from _walk(sub, path)


def _tool_json(response) -> dict:
    return json.loads(response[0].text)


# ---------------------------------------------------------------------------
# Schemas: no tool accepts governance fields
# ---------------------------------------------------------------------------


def test_no_mcp_tool_schema_accepts_governance_fields():
    offenders = [
        f"{tool}:{prop}"
        for tool, schema in _all_schemas()
        for prop, _ in _walk(schema)
        if prop.rsplit(".", 1)[-1] in GOVERNANCE_FIELDS
    ]
    assert offenders == []


def test_no_mcp_tool_offers_instruction_eligible_provenance():
    offenders = [
        f"{tool}:{prop}"
        for tool, schema in _all_schemas()
        for prop, sub in _walk(schema)
        if INSTRUCTION_ELIGIBLE_PROVENANCE & set(sub.get("enum") or [])
    ]
    assert offenders == []


def test_review_action_is_the_only_governance_entry_point():
    with_review = {
        tool.name
        for tool in mcp_server.TOOLS
        for prop, _ in _walk(tool.inputSchema)
        if "review" in prop
    }
    assert with_review == REVIEW_ACTION_TOOLS


# ---------------------------------------------------------------------------
# Dispatcher: smuggled governance args never reach the services
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, target, args",
    [
        ("capture_thought", "capture_thought", {"content": "A thought."}),
        ("memory_writeback", "memory_writeback", {"memory_payload": {"lessons": ["x"]}}),
        ("update_signal", "update_signal", {"signal_id": "sig-1", "content": "edit"}),
    ],
)
async def test_dispatcher_drops_smuggled_governance_args(tool, target, args):
    with patch(
        f"app.services.chat_tools.{target}",
        new_callable=AsyncMock,
        return_value={"success": True},
    ) as fn:
        await mcp_server.handle_call_tool(tool, {**args, **SMUGGLED})

    fn.assert_awaited_once()
    forwarded = set(fn.call_args.kwargs) | {
        k for a in fn.call_args.args if isinstance(a, dict) for k in a
    }
    assert forwarded.isdisjoint(GOVERNANCE_FIELDS)


# ---------------------------------------------------------------------------
# End to end through the MCP dispatcher: writes are evidence-grade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_thought_via_mcp_is_evidence_grade(tmp_path, monkeypatch):
    from app.services import capture_service, signal_indexing
    from app.services.memory_capture import CaptureStore

    store = CaptureStore(capture_dir=tmp_path / "memory" / "captures", repo_root=tmp_path)
    real = capture_service.capture_and_persist

    async def _scoped(*a, **kw):
        kw.update(store=store, repo_root=tmp_path)
        return await real(*a, **kw)

    async def _no_enrich(content, claude_client=None):
        return {}

    monkeypatch.setattr(capture_service, "capture_and_persist", _scoped)
    monkeypatch.setattr(capture_service, "enrich_capture", _no_enrich)
    monkeypatch.setattr(signal_indexing, "index_capture_one", lambda c: None)
    git = MagicMock()
    git.commit_and_push = AsyncMock()

    with patch("app.services.capture_service.git_ops", git):
        result = _tool_json(
            await mcp_server.handle_call_tool(
                "capture_thought", {"content": "We agreed to ship Friday.", **SMUGGLED}
            )
        )

    assert result["success"] is True, result
    persisted = store.get(result["id"])
    assert persisted.can_use_as_instruction is False
    assert persisted.review_status == "pending"
    assert persisted.provenance_status != "user_confirmed"


@pytest.mark.asyncio
async def test_memory_writeback_via_mcp_is_evidence_grade(tmp_path, monkeypatch):
    from app.services import memory_writeback as mw
    from app.services.agent_memory_store import AgentMemoryStore

    store = AgentMemoryStore(agent_dir=tmp_path / "memory" / "agent", repo_root=tmp_path)
    real = mw.writeback

    async def _scoped(request, **kw):
        kw.update(store=store, repo_root=tmp_path)
        return await real(request, **kw)

    monkeypatch.setattr(mw, "writeback", _scoped)
    monkeypatch.setattr(mw, "_index_memory", lambda m: None)
    git = MagicMock()
    git.commit_and_push = AsyncMock()

    with patch("app.services.memory_writeback.git_ops", git):
        result = _tool_json(
            await mcp_server.handle_call_tool(
                "memory_writeback",
                {
                    "memory_payload": {"decisions": ["Use A."], "lessons": ["Batch calls."]},
                    **SMUGGLED,
                },
            )
        )
        # Asking for instruction-eligible provenance is refused outright.
        refused = _tool_json(
            await mcp_server.handle_call_tool(
                "memory_writeback",
                {
                    "memory_payload": {"decisions": ["Use B."]},
                    "provenance_default_status": "user_confirmed",
                },
            )
        )

    assert result["success"] is True, result
    assert len(result["created"]) == 2
    for row in result["created"]:
        mem = store.get(row["id"])
        assert mem.can_use_as_instruction is False
        assert mem.review_status == "pending"
        assert mem.provenance_status not in INSTRUCTION_ELIGIBLE_PROVENANCE

    assert refused.get("success") is False
    assert len(store.list()) == 2
