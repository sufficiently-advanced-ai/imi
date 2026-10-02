"""ADR-007 agent-mediated intake: connector source defaults, the server-stamped
MCP channel, mcp_trusted_sources, the absent capture_thought source, and the
no-judge fallback (§6)."""

import importlib
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import lane_admission as la
from app.services.inference.decisions import ChoiceAnswer, DecisionResult, NoulAnswer
from app.services.memory_capture import CaptureStore

# Imported before the autouse fixture chdirs away from the repo (the
# orchestrator resolves the domain config from the working directory).
from app.services.orchestrators.ingest_orchestrator import IngestOrchestrator

DMARC = "# Report\n\nFrom: noreply-dmarc-support@google.com\nDate: Thu\n\n(empty body)"


class _Fake:
    def __init__(self, mode="on", memory=0.0, library=0.0, junk=0.0):
        self._mode, self.calls = mode, []
        self.probs = {"memory": memory, "library": library, "junk": junk}

    def mode(self, operation):
        return self._mode

    async def decide(self, state, questions, *, operation):
        self.calls.append(state)
        choice = max(self.probs, key=self.probs.get)
        return DecisionResult(
            answers={
                "lane": ChoiceAnswer(choice=choice, probabilities=self.probs, confidence=0.9),
                "durable": NoulAnswer(noul=0.5),
            },
            model="fake", endpoint="fake", input_tokens=1, output_tokens=1,
            cost_usd=0.0, latency_ms=1, raw={},
        )


@pytest.fixture(autouse=True)
def _no_config(monkeypatch, tmp_path):
    monkeypatch.setenv("LANES_CONFIG_PATH", str(tmp_path / "absent.yaml"))
    monkeypatch.chdir(tmp_path)
    la.reset_lanes_config()
    yield
    la.reset_lanes_config()


def _trust(monkeypatch, tmp_path, body: str):
    cfg = tmp_path / "lanes.yaml"
    cfg.write_text(body)
    monkeypatch.setenv("LANES_CONFIG_PATH", str(cfg))
    la.reset_lanes_config()


# ---- §1 connector source defaults --------------------------------------------


def test_connector_source_defaults():
    assert la.source_default("gmail") == la.PER_ITEM
    assert la.source_default("gdrive") == la.PER_ITEM
    assert la.source_default("gcal") == "record"
    # gmail joins mail/email rather than replacing them
    assert la.source_default("mail") == la.PER_ITEM
    assert la.source_default("email") == la.PER_ITEM
    assert la.source_default(la.UNKNOWN_SOURCE) == la.PER_ITEM


# ---- §2 record-default sources are not trusted on the MCP channel ------------


def test_effective_default_demotes_record_sources_only_on_mcp():
    assert la.effective_default("manual") == "record"
    assert la.effective_default("manual", "rest") == "record"
    assert la.effective_default("manual", la.MCP_CHANNEL) == la.PER_ITEM
    assert la.effective_default("fireflies", la.MCP_CHANNEL) == la.PER_ITEM
    # non-record defaults are unchanged by the channel
    assert la.effective_default("web", la.MCP_CHANNEL) == "library"
    assert la.effective_default("gmail", la.MCP_CHANNEL) == la.PER_ITEM


def test_mcp_trusted_sources_default_empty_and_configurable(monkeypatch, tmp_path):
    assert la.mcp_trusted_sources() == frozenset()
    _trust(monkeypatch, tmp_path, "mcp_trusted_sources: [Manual]\n")
    assert la.mcp_trusted_sources() == frozenset({"manual"})
    assert la.effective_default("manual", la.MCP_CHANNEL) == "record"
    assert la.effective_default("slack", la.MCP_CHANNEL) == la.PER_ITEM


def test_malformed_mcp_trusted_sources_trusts_nothing(monkeypatch, tmp_path):
    _trust(monkeypatch, tmp_path, "mcp_trusted_sources: {manual: true}\n")
    assert la.mcp_trusted_sources() == frozenset()
    assert la.effective_default("manual", la.MCP_CHANNEL) == la.PER_ITEM


@pytest.mark.asyncio
async def test_mcp_manual_capture_is_judged_and_first_party_lands_in_record():
    fake = _Fake(memory=0.9)
    d = await la.admit("I decided to move the launch to May.", "manual",
                       channel=la.MCP_CHANNEL, client=fake)
    assert len(fake.calls) == 1
    assert d.lane == "record" and not d.drop
    assert d.channel == la.MCP_CHANNEL and d.source_default == la.PER_ITEM


@pytest.mark.asyncio
async def test_mcp_manual_newsletter_is_filed_as_library():
    d = await la.admit("Issue 12 of a newsletter", "manual",
                       channel=la.MCP_CHANNEL, client=_Fake(library=0.9))
    assert d.lane == "library" and d.stale_after


@pytest.mark.asyncio
async def test_untrusted_mcp_record_source_is_subject_to_drop_rules():
    fake = _Fake()
    d = await la.admit(DMARC, "manual", channel=la.MCP_CHANNEL, client=fake)
    assert d.drop and d.reason.startswith("rule:")
    # Off the MCP channel a manual note is never dropped (ADR-003)
    d = await la.admit(DMARC, "manual", client=fake)
    assert not d.drop and d.lane == "record"


@pytest.mark.asyncio
async def test_trusted_mcp_source_keeps_record(monkeypatch, tmp_path):
    _trust(monkeypatch, tmp_path, "mcp_trusted_sources: [manual]\n")
    fake = _Fake(library=0.9)
    d = await la.admit("a note", "manual", channel=la.MCP_CHANNEL, client=fake)
    assert d.lane == "record" and d.source_default == "record"


@pytest.mark.asyncio
async def test_no_channel_keeps_pre_adr007_behaviour():
    fake = _Fake(library=0.9)
    d = await la.admit("a note", "manual", client=fake)
    assert d.lane == "record" and d.channel is None and d.source_default == "record"


# ---- §6 the judge is optional -----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["manual", la.UNKNOWN_SOURCE, "fireflies", "gmail"])
async def test_without_a_judge_mcp_intake_falls_back_to_record(source):
    for client in (_Fake(mode="off"), None):
        with patch.object(la, "_default_client", return_value=None):
            d = await la.admit("Remember: ship on Friday.", source,
                               channel=la.MCP_CHANNEL, client=client)
        assert d.lane == "record" and not d.drop and d.mode == "off"


# ---- capture path: channel persisted on the record --------------------------


@pytest.mark.asyncio
async def test_capture_persists_channel_and_judges_mcp_manual(tmp_path):
    from app.services import capture_service, signal_indexing

    store = CaptureStore(capture_dir=tmp_path / "memory" / "captures", repo_root=tmp_path)
    git = MagicMock()
    git.commit_and_push = AsyncMock()
    fake = _Fake(library=0.9)
    with (
        patch.object(signal_indexing, "index_capture_one", lambda m: "v1"),
        patch.object(capture_service, "enrich_capture", AsyncMock(return_value={})),
        patch("app.services.capture_service.git_ops", git),
    ):
        result = await capture_service.capture_and_persist(
            "Forwarded newsletter", source="manual", channel=la.MCP_CHANNEL,
            store=store, repo_root=tmp_path, decision_client=fake,
        )
    assert result["success"] and result["lane"] == "library"
    saved = store.get(result["id"])
    assert saved.channel == la.MCP_CHANNEL
    on_disk = json.loads(next((tmp_path / "memory" / "captures").glob("*.json")).read_text())
    assert on_disk["channel"] == la.MCP_CHANNEL
    assert len(fake.calls) == 1


# ---- §3 capture_thought: absent source, server-stamped channel --------------


@pytest.mark.asyncio
async def test_capture_thought_omitted_source_is_unknown():
    seen: dict = {}

    async def fake(content, **kw):
        seen.update(kw)
        return {"success": True, "id": "cap-1"}

    with patch("app.services.capture_service.capture_and_persist", side_effect=fake):
        from app.services.chat_tools import capture_thought

        await capture_thought("A thought.", channel=la.MCP_CHANNEL)
    assert seen["source"] == la.UNKNOWN_SOURCE
    assert seen["channel"] == la.MCP_CHANNEL


def test_capture_thought_schema_has_no_source_default_and_no_channel():
    from app.services.mcp_tool_definitions import TOOL_DEFS

    for name in ("capture_thought", "add_call_transcript"):
        props = TOOL_DEFS[name]["inputSchema"]["properties"]
        assert "channel" not in props and "lane" not in props
    assert "default" not in TOOL_DEFS["capture_thought"]["inputSchema"]["properties"]["source"]
    for name in ("capture_thought", "add_call_transcript"):
        assert "<connector>:<native id>" in TOOL_DEFS[name]["description"]


def _mcp_mod():
    mod = sys.modules.get("app.routes.mcp_server")
    return mod or importlib.import_module("app.routes.mcp_server")


@pytest.mark.asyncio
async def test_mcp_handler_stamps_channel_and_ignores_client_channel():
    with patch("app.services.chat_tools.capture_thought", new_callable=AsyncMock,
               return_value={"success": True}) as fn:
        await _mcp_mod().handle_call_tool(
            "capture_thought", {"content": "x", "channel": "rest"}
        )
    kwargs = fn.await_args.kwargs
    assert kwargs["channel"] == la.MCP_CHANNEL
    assert kwargs["source"] is None


@pytest.mark.asyncio
async def test_mcp_handler_stamps_channel_on_add_call_transcript():
    with patch("app.services.chat_tools.add_call_transcript", new_callable=AsyncMock,
               return_value={"status": "completed"}) as fn:
        await _mcp_mod().handle_call_tool(
            "add_call_transcript",
            {"transcript": "t", "start_time": "2026-06-04T14:30:00Z",
             "participants": ["A"], "channel": "rest"},
        )
    assert fn.await_args.kwargs["channel"] == la.MCP_CHANNEL


# ---- ingest path: channel cannot come from a body, reaches admission --------


def test_ingest_request_channel_is_not_settable_from_input():
    from app.models.ingestion.models import IngestRequest

    req = IngestRequest.model_validate({"content": "x", "_channel": "mcp", "channel": "mcp"})
    assert req._channel is None
    assert "channel" not in req.model_dump() and "_channel" not in req.model_dump()


@pytest.mark.asyncio
async def test_add_call_transcript_stamps_channel_on_request():
    seen = {}

    async def fake_submit(request, *, timeout_s):
        seen["channel"] = request._channel
        return {"state": "pending", "job_id": "j", "poll_url": "/p"}

    from app.services.chat_tools import add_call_transcript

    with patch("app.routes.ingest.submit_and_wait", fake_submit):
        await add_call_transcript(
            transcript="[00:01] A: hi", start_time="2026-06-04T14:30:00Z",
            participants=["A"], channel=la.MCP_CHANNEL,
        )
    assert seen["channel"] == la.MCP_CHANNEL


@pytest.mark.asyncio
async def test_admit_phase_passes_request_channel():
    from app.models.ingestion.models import ContentSource, IngestRequest
    req = IngestRequest(content="[00:01] A: hi", source=ContentSource.FIREFLIES)
    req._channel = la.MCP_CHANNEL
    admit = AsyncMock(return_value=la.LaneDecision(lane="record"))
    orch = IngestOrchestrator.__new__(IngestOrchestrator)
    with patch.object(la, "admit", admit):
        await orch._phase_admit(req)
    assert admit.await_args.kwargs["channel"] == la.MCP_CHANNEL
