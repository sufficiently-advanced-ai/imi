"""ask_kb must follow inference routing, never a raw client's API key."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.ask_kb as ask_kb_module
from app.services import claude_client as claude_client_module
from app.services.ask_kb import ask_kb


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(name, tool_input, tool_id="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=tool_id)


def _client(*, is_agent_sdk=False, responses=None, error=None):
    client = MagicMock()
    client.registry.resolve.return_value = SimpleNamespace(
        is_agent_sdk=is_agent_sdk, model="routed-model"
    )
    client.generate_message = AsyncMock(side_effect=error or responses)
    return client


@pytest.fixture
def use_client(monkeypatch):
    def _use(client):
        monkeypatch.setattr(claude_client_module, "get_claude_client", lambda: client)
        return client

    return _use


@pytest.fixture
def stub_tool(monkeypatch):
    calls = []

    async def _list_meetings(args):
        calls.append(args)
        return {"meetings": [{"id": "m1"}], "count": 1}

    monkeypatch.setattr(
        ask_kb_module,
        "_make_tool_dispatcher",
        lambda actor=None: {"list_meetings": _list_meetings},
    )
    return calls


@pytest.mark.asyncio
async def test_message_loop_goes_through_generate_message(use_client, stub_tool):
    client = use_client(_client(responses=[
        SimpleNamespace(content=[_tool_use("list_meetings", {"max_results": 1})]),
        SimpleNamespace(content=[_text("One meeting.")]),
    ]))

    result = await ask_kb("what meetings?")

    assert result["answer"] == "One meeting."
    assert result["stopped_reason"] == "answered"
    assert result["steps_used"] == 2
    assert result["trace"] == [{
        "tool": "list_meetings",
        "args_summary": "max_results=1",
        "result_summary": "1 meetings",
    }]
    assert stub_tool == [{"max_results": 1}]
    assert client.generate_message.await_args.kwargs["operation"] == "ask_kb"
    assert client.client.messages.create.call_count == 0


@pytest.mark.asyncio
async def test_message_loop_stops_at_max_steps(use_client, stub_tool):
    use_client(_client(responses=[
        SimpleNamespace(content=[_tool_use("list_meetings", {})]),
        SimpleNamespace(content=[_tool_use("list_meetings", {})]),
    ]))

    result = await ask_kb("loop forever", max_steps=2)

    assert result["stopped_reason"] == "max_steps"
    assert result["steps_used"] == 2


@pytest.mark.asyncio
async def test_refused_endpoint_is_reported_as_an_error(use_client):
    use_client(_client(error=ValueError("Tool use is not permitted")))

    result = await ask_kb("anything")

    assert result["stopped_reason"] == "error"
    assert "Tool use is not permitted" in result["error"]


@pytest.mark.asyncio
async def test_agent_sdk_endpoint_uses_the_agent_sdk_loop(use_client, monkeypatch):
    client = use_client(_client(is_agent_sdk=True))
    loop = AsyncMock(return_value={"stopped_reason": "answered"})
    monkeypatch.setattr(ask_kb_module, "_run_agent_sdk_loop", loop)

    await ask_kb("anything")

    assert loop.await_count == 1
    assert loop.await_args.args[3] == "routed-model"
    assert client.generate_message.await_count == 0


@pytest.mark.asyncio
async def test_agent_sdk_loop_is_locked_to_the_given_tools(monkeypatch):
    claude_agent_sdk = pytest.importorskip("claude_agent_sdk")
    captured = {}

    class FakeSDKClient:
        def __init__(self, options):
            captured["options"] = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def query(self, prompt):
            captured["prompt"] = prompt

        async def receive_response(self):
            yield claude_agent_sdk.AssistantMessage(
                content=[claude_agent_sdk.TextBlock(text="Done.")], model="m"
            )
            yield SimpleNamespace(is_error=False)

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeSDKClient)

    spec = {
        "name": "list_meetings",
        "description": "d",
        "input_schema": {"type": "object", "properties": {}},
    }
    result = await ask_kb_module._run_agent_sdk_loop("q", [spec], {}, "m", 3)

    options = captured["options"]
    assert options.tools == []
    assert options.allowed_tools == ["mcp__kb__list_meetings"]
    assert list(options.mcp_servers) == ["kb"]
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.permission_mode == "dontAsk"
    assert options.max_turns == 3
    assert options.env["ANTHROPIC_API_KEY"] == ""
    # No ResultMessage arrived, so this is not an answer.
    assert result["stopped_reason"] == "error"
    assert result["answer"] == "Done."


@pytest.mark.asyncio
@pytest.mark.parametrize("max_steps", [0, -1])
@pytest.mark.parametrize("is_agent_sdk", [True, False])
async def test_nonpositive_budget_runs_nothing(use_client, monkeypatch, max_steps, is_agent_sdk):
    client = use_client(_client(is_agent_sdk=is_agent_sdk))
    sdk_loop = AsyncMock()
    monkeypatch.setattr(ask_kb_module, "_run_agent_sdk_loop", sdk_loop)

    result = await ask_kb("anything", max_steps=max_steps)

    assert result["stopped_reason"] == "error"
    assert result["steps_used"] == 0
    assert "max_steps" in result["error"]
    assert sdk_loop.await_count == 0
    assert client.generate_message.await_count == 0


async def _run_sdk_loop_with_result(monkeypatch, **result_fields):
    """Run the SDK loop against a fake client ending in a real ResultMessage."""
    claude_agent_sdk = pytest.importorskip("claude_agent_sdk")
    final = claude_agent_sdk.ResultMessage(
        duration_ms=1, duration_api_ms=1, session_id="s", **result_fields
    )

    class FakeSDKClient:
        def __init__(self, options):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def query(self, prompt):
            pass

        async def receive_response(self):
            yield claude_agent_sdk.AssistantMessage(
                content=[claude_agent_sdk.TextBlock(text="Narration.")], model="m"
            )
            yield final

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeSDKClient)
    return await ask_kb_module._run_agent_sdk_loop("q", [], {}, "m", 3)


@pytest.mark.asyncio
async def test_agent_sdk_loop_reports_the_final_result(monkeypatch):
    result = await _run_sdk_loop_with_result(
        monkeypatch, subtype="success", is_error=False, num_turns=2, result="Final answer."
    )

    assert result == {
        "answer": "Final answer.",
        "trace": [],
        "steps_used": 2,
        "stopped_reason": "answered",
    }


@pytest.mark.asyncio
async def test_agent_sdk_loop_reports_max_turns_as_max_steps(monkeypatch):
    # The CLI can count past the cap, and flags this result as an error.
    result = await _run_sdk_loop_with_result(
        monkeypatch, subtype="error_max_turns", is_error=True, num_turns=4
    )

    assert result["stopped_reason"] == "max_steps"
    assert result["steps_used"] == 3
    assert result["answer"] == "Narration."


@pytest.mark.asyncio
async def test_agent_sdk_loop_reports_an_error_result(monkeypatch):
    result = await _run_sdk_loop_with_result(
        monkeypatch, subtype="success", is_error=True, num_turns=1, result="Invalid API key"
    )

    assert result["stopped_reason"] == "error"
    assert "Invalid API key" in result["error"]


def test_short_result_passes_through_unmarked():
    budget = ask_kb_module._ResultBudget()

    assert budget.fit("short") == "short"
    assert budget.remaining == ask_kb_module._MAX_RUN_RESULT_CHARS - 5


def test_long_result_is_cut_with_a_marker():
    budget = ask_kb_module._ResultBudget()

    text = budget.fit("x" * 50_000)

    assert text.startswith("x" * ask_kb_module._MAX_TOOL_RESULT_CHARS + "\n[truncated: ")
    assert "showing 40,000 of 50,000 chars; narrow the query" in text


@pytest.mark.asyncio
async def test_run_budget_caps_total_results(use_client, monkeypatch):
    async def _big(args):
        return "x" * 45_000

    monkeypatch.setattr(
        ask_kb_module, "_make_tool_dispatcher", lambda actor=None: {"list_meetings": _big}
    )
    client = use_client(_client(responses=[
        SimpleNamespace(content=[_tool_use("list_meetings", {})]) for _ in range(5)
    ] + [SimpleNamespace(content=[_text("Done.")])]))

    result = await ask_kb("everything", max_steps=6)

    sent = [
        message["content"][0]["content"]
        for message in client.generate_message.await_args.kwargs["messages"]
        if message["role"] == "user" and isinstance(message["content"], list)
    ]
    assert result["stopped_reason"] == "answered"
    shown = [text.split("showing ")[1].split(" of ")[0] for text in sent]
    assert shown == ["40,000", "40,000", "40,000", "30,000", "0"]
    assert "narrow the query" in sent[0]
    assert "budget for this run is used up" in sent[3]
