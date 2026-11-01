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
