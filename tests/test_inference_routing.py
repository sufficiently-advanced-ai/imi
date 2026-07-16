"""Tests for the configurable inference-endpoint layer (app/services/inference).

Covers registry resolution/precedence, fail-closed config validation, the
tool-use guard, and the non-Anthropic dispatch path (LiteLLM mocked).
"""

import pytest

from app.services.inference import (
    InferenceConfigError,
    InferenceRegistry,
    to_anthropic_response,
    to_openai_messages,
)


# --- registry resolution ----------------------------------------------------

def test_no_config_routes_everything_to_anthropic():
    reg = InferenceRegistry(config={})
    ep = reg.resolve("claude-haiku-4-5-20251001", "metadata_extraction")
    assert ep.is_anthropic is True
    assert ep.name == "anthropic-default"
    assert ep.model == "claude-haiku-4-5-20251001"  # tier string used verbatim
    assert ep.allow_tools is True


def test_alias_routes_to_named_endpoint():
    cfg = {
        "endpoints": {
            "tailnet": {
                "type": "openai",
                "litellm_model": "hosted_vllm/qwen",
                "base_url": "http://llm.tailnet:8000/v1",
            }
        },
        "aliases": {"claude-haiku-4-5-20251001": "tailnet"},
    }
    reg = InferenceRegistry(config=cfg)
    ep = reg.resolve("claude-haiku-4-5-20251001", "chat")
    assert ep.is_anthropic is False
    assert ep.model == "hosted_vllm/qwen"
    assert ep.api_base == "http://llm.tailnet:8000/v1"
    assert ep.allow_tools is False  # openai endpoints default to no tools


def test_operation_override_wins_over_alias():
    cfg = {
        "endpoints": {
            "tailnet": {"type": "openai", "litellm_model": "hosted_vllm/qwen"},
            "anthropic-default": {"type": "anthropic"},
        },
        "aliases": {"claude-haiku-4-5-20251001": "anthropic-default"},
        "operations": {"metadata_extraction": "tailnet"},
    }
    reg = InferenceRegistry(config=cfg)
    # operation override -> tailnet
    assert reg.resolve("claude-haiku-4-5-20251001", "metadata_extraction").name == "tailnet"
    # different operation falls back to the alias -> anthropic-default
    assert reg.resolve("claude-haiku-4-5-20251001", "chat").name == "anthropic-default"


def test_extra_body_flows_into_endpoint_extra():
    cfg = {
        "endpoints": {
            "local-mlx": {
                "type": "openai",
                "litellm_model": "hosted_vllm/mlx-community/Qwen3.6-27B-4bit",
                "base_url": "http://host.docker.internal:8000/v1",
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            }
        },
        "operations": {"metadata_extraction": "local-mlx"},
    }
    reg = InferenceRegistry(config=cfg)
    ep = reg.resolve("claude-haiku-4-5-20251001", "metadata_extraction")
    assert ep.extra == {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
    }


def test_no_extra_body_leaves_extra_empty():
    cfg = {
        "endpoints": {
            "tailnet": {"type": "openai", "litellm_model": "hosted_vllm/qwen"}
        },
        "operations": {"metadata_extraction": "tailnet"},
    }
    reg = InferenceRegistry(config=cfg)
    ep = reg.resolve("claude-haiku-4-5-20251001", "metadata_extraction")
    assert ep.extra == {}


def test_unknown_endpoint_reference_is_config_error():
    cfg = {"endpoints": {}, "aliases": {"some-model": "does-not-exist"}}
    with pytest.raises(InferenceConfigError):
        InferenceRegistry(config=cfg)


def test_openai_endpoint_requires_litellm_model():
    cfg = {"endpoints": {"bad": {"type": "openai"}}, "aliases": {"m": "bad"}}
    reg = InferenceRegistry(config=cfg)
    with pytest.raises(InferenceConfigError):
        reg.resolve("m", "chat")


# --- digitalocean endpoint type --------------------------------------------
# DigitalOcean serverless inference is OpenAI-compatible behind a single base
# URL. The convenience `type: digitalocean` defaults that base URL and wraps the
# bare model id in LiteLLM's generic `openai/` route, so an endpoint is just a
# model id + key + pricing. Tools stay rejected (plain-generation only).

def test_digitalocean_type_wraps_model_and_defaults_base_url(monkeypatch):
    monkeypatch.setenv("DO_KEY", "doo_v1_test")
    cfg = {
        "endpoints": {
            "do-deepseek": {
                "type": "digitalocean",
                "model": "deepseek-4-flash",
                "api_key_env": "DO_KEY",
                "pricing": {"input": 0.5, "output": 1.5},
            }
        },
        "operations": {"metadata_extraction": "do-deepseek"},
    }
    reg = InferenceRegistry(config=cfg)
    ep = reg.resolve("claude-haiku-4-5-20251001", "metadata_extraction")
    assert ep.is_anthropic is False
    assert ep.model == "openai/deepseek-4-flash"  # LiteLLM generic OpenAI route
    assert ep.api_base == "https://inference.do-ai.run/v1"  # DO default
    assert ep.api_key == "doo_v1_test"
    assert ep.allow_tools is False  # fail-closed: DO is plain-generation only
    assert ep.pricing == {"input": 0.5, "output": 1.5}


def test_digitalocean_custom_base_url_override(monkeypatch):
    monkeypatch.setenv("DO_KEY", "doo_v1_test")
    cfg = {
        "endpoints": {
            "do": {
                "type": "digitalocean",
                "model": "llama3.3-70b-instruct",
                "base_url": "https://inference.do-ai.run/v2",
                "api_key_env": "DO_KEY",
            }
        },
        "aliases": {"m": "do"},
    }
    ep = InferenceRegistry(config=cfg).resolve("m", "chat")
    assert ep.api_base == "https://inference.do-ai.run/v2"
    assert ep.model == "openai/llama3.3-70b-instruct"


def test_digitalocean_requires_model(monkeypatch):
    monkeypatch.setenv("DO_KEY", "doo_v1_test")
    cfg = {
        "endpoints": {"do": {"type": "digitalocean", "api_key_env": "DO_KEY"}},
        "aliases": {"m": "do"},
    }
    reg = InferenceRegistry(config=cfg)
    with pytest.raises(InferenceConfigError):
        reg.resolve("m", "chat")


def test_digitalocean_requires_api_key(monkeypatch):
    # A DO endpoint whose key env var is unset must fail closed at resolve time,
    # not surface as a 401 buried in the call-time retry loop.
    monkeypatch.delenv("DO_KEY_MISSING", raising=False)
    cfg = {
        "endpoints": {
            "do": {
                "type": "digitalocean",
                "model": "deepseek-4-flash",
                "api_key_env": "DO_KEY_MISSING",
            }
        },
        "aliases": {"m": "do"},
    }
    reg = InferenceRegistry(config=cfg)
    with pytest.raises(InferenceConfigError):
        reg.resolve("m", "chat")


def test_digitalocean_rejects_whitespace_api_key(monkeypatch):
    # A whitespace-only key is truthy but useless — must fail closed, not pass
    # through to surface as a 401 at call time.
    monkeypatch.setenv("DO_KEY_BLANK", "   ")
    cfg = {
        "endpoints": {
            "do": {"type": "digitalocean", "model": "gemma-4-31B-it", "api_key_env": "DO_KEY_BLANK"}
        },
        "aliases": {"m": "do"},
    }
    reg = InferenceRegistry(config=cfg)
    with pytest.raises(InferenceConfigError):
        reg.resolve("m", "chat")


@pytest.mark.parametrize(
    "bad_model",
    [
        123,        # non-string scalar: must not coerce into "openai/123"
        "   ",      # whitespace-only string: truthy but useless
        "",         # empty string
    ],
)
def test_digitalocean_rejects_invalid_model(monkeypatch, bad_model):
    # `model` must be a non-empty string after stripping; anything else fails
    # closed at resolve time rather than producing a bogus "openai/<x>" route.
    monkeypatch.setenv("DO_KEY", "doo_v1_test")
    cfg = {
        "endpoints": {
            "do": {"type": "digitalocean", "model": bad_model, "api_key_env": "DO_KEY"}
        },
        "aliases": {"m": "do"},
    }
    reg = InferenceRegistry(config=cfg)
    with pytest.raises(InferenceConfigError):
        reg.resolve("m", "chat")


def test_digitalocean_allow_tools_is_hard_off(monkeypatch):
    # The convenience type is plain-generation only by contract: config must not
    # be able to re-enable tools (use type: openai for a configurable endpoint).
    monkeypatch.setenv("DO_KEY", "doo_v1_test")
    cfg = {
        "endpoints": {
            "do": {
                "type": "digitalocean",
                "model": "gemma-4-31B-it",
                "api_key_env": "DO_KEY",
                "allow_tools": True,  # should be ignored
            }
        },
        "aliases": {"m": "do"},
    }
    ep = InferenceRegistry(config=cfg).resolve("m", "chat")
    assert ep.allow_tools is False
    assert ep.api_key == "doo_v1_test"  # stripped/validated key flows through


# --- translation ------------------------------------------------------------

def test_to_openai_messages_flattens_blocks_and_prepends_system():
    msgs = to_openai_messages(
        [{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}],
        system="SYS",
    )
    assert msgs == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "ab"},
    ]


def test_to_openai_messages_rejects_non_list():
    with pytest.raises(TypeError):
        to_openai_messages("not a list", system=None)


def test_to_anthropic_response_maps_shape_and_stop_reason():
    class _Resp:
        class _Choice:
            class message:  # noqa: N801
                content = "hi there"
            finish_reason = "length"

        choices = [_Choice]

        class usage:  # noqa: N801
            prompt_tokens = 7
            completion_tokens = 2

        model = "ollama/llama3"

    r = to_anthropic_response(_Resp())
    assert r.content[0].text == "hi there"
    assert r.content[0].type == "text"
    assert r.usage.input_tokens == 7
    assert r.usage.output_tokens == 2
    assert r.stop_reason == "max_tokens"  # "length" -> "max_tokens"
    assert r.model == "ollama/llama3"


# --- ClaudeClient integration (fail-closed guard + non-anthropic dispatch) ---

@pytest.mark.asyncio
async def test_tools_on_non_anthropic_endpoint_fail_closed():
    from app.services.claude_client import ClaudeClient

    client = ClaudeClient()
    client.registry = InferenceRegistry(
        config={
            "endpoints": {"tailnet": {"type": "openai", "litellm_model": "hosted_vllm/qwen"}},
            "default": "tailnet",
        }
    )
    with pytest.raises(ValueError, match="Tool use is not permitted"):
        await client.generate_message(
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "t", "description": "d", "input_schema": {"type": "object"}}],
            operation="chat",
        )


@pytest.mark.asyncio
async def test_default_anthropic_path_uses_native_sdk_unchanged():
    """Regression guard: with no inference config, generate_message routes
    through the native Anthropic client and returns its response untouched."""
    from unittest.mock import MagicMock

    from app.services.claude_client import ClaudeClient

    native = MagicMock()
    native.usage.input_tokens = 12
    native.usage.output_tokens = 4
    native.content = [MagicMock(text="native answer")]

    client = ClaudeClient()  # no-config registry -> anthropic-default
    client.registry = InferenceRegistry(config={})
    # Replace the resolved endpoint's client so no real network call happens.
    client.client.messages.create = MagicMock(return_value=native)
    client._anthropic_clients = {
        (k[0], k[1]): client.client for k in list(client._anthropic_clients)
    }

    resp = await client.generate_message(
        messages=[{"role": "user", "content": "hi"}], operation="chat"
    )
    assert resp is native  # native Message returned verbatim
    client.client.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_non_anthropic_route_calls_litellm_and_translates(monkeypatch):
    import litellm

    from app.services.claude_client import ClaudeClient

    captured = {}

    class _Resp:
        class _Choice:
            class message:  # noqa: N801
                content = "routed answer"
            finish_reason = "stop"

        choices = [_Choice]

        class usage:  # noqa: N801
            prompt_tokens = 5
            completion_tokens = 4

        model = "hosted_vllm/qwen"

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr(litellm, "completion", fake_completion)

    client = ClaudeClient()
    client.registry = InferenceRegistry(
        config={
            "endpoints": {
                "tailnet": {
                    "type": "openai",
                    "litellm_model": "hosted_vllm/qwen",
                    "base_url": "http://llm.tailnet:8000/v1",
                    "pricing": {"input": 0.0, "output": 0.0},
                }
            },
            "default": "tailnet",
        }
    )

    resp = await client.generate_message(
        messages=[{"role": "user", "content": "hi"}],
        system="be brief",
        operation="chat",
    )

    # routed to litellm with the right model/base_url and translated messages
    assert captured["model"] == "hosted_vllm/qwen"
    assert captured["api_base"] == "http://llm.tailnet:8000/v1"
    assert captured["messages"][0] == {"role": "system", "content": "be brief"}
    # response is Anthropic-shaped for callers
    assert resp.content[0].text == "routed answer"
    assert resp.usage.input_tokens == 5
    assert resp.stop_reason == "end_turn"
