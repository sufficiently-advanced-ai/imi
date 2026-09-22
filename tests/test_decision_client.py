"""Unit tests for the System One decision client (app/services/inference/decisions.py).

No network: every test injects an ``httpx.MockTransport`` that plays the role
of the TypeSafe / DigitalOcean gateway. Fixture responses mirror the documented
wire contract (docs.typesafe.ai/api) verbatim.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.inference.base import InferenceConfigError
from app.services.inference.decisions import (
    Choice,
    ChoiceAnswer,
    DecisionClient,
    DecisionUnavailable,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
)

KEY_ENV = "TEST_DECISION_KEY"

# Documented quickstart response, verbatim.
QUICKSTART_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "department": {
            "type": "choice",
            "choice": "technical",
            "confidence": 0.78,
            "probabilities": {"technical": 0.85, "sales": 0.0, "billing": 0.15},
        },
        "frustration": {
            "type": "score",
            "score": 1.0,
            "confidence": 1.0,
            "legend": {"0": "Calm", "1": "Frustrated but civil", "2": "Very angry"},
            "probabilities": {"0": 0.0, "1": 1.0, "2": 0.0},
        },
        "is_urgent": {"type": "noul", "noul": 1.0},
    },
    "usage": {"input_tokens": 392, "output_tokens": 65},
}

QUESTIONS = {
    "department": Choice(
        instructions="Which team should handle this",
        criteria={"billing": "Payment", "technical": "Bugs", "sales": "Pricing"},
    ),
    "frustration": Score(instructions="How frustrated", criteria=("Calm", "Frustrated but civil", "Very angry")),
    "is_urgent": Noul(instructions="Conveys urgency"),
}


def _config(**overrides):
    spec = {"type": "digitalocean", "api_key_env": KEY_ENV, "pricing": {"input": 0.042}, **overrides}
    return {"endpoints": {"do-jev": spec}, "operations": {"tiebreak": "do-jev"}, "default": "do-jev"}


def _client(handler, monkeypatch, **overrides) -> DecisionClient:
    monkeypatch.setenv(KEY_ENV, "doo_v1_test")
    return DecisionClient(_config(**overrides), transport=httpx.MockTransport(handler), max_attempts=3)


# ---- config -------------------------------------------------------------------


def test_missing_key_fails_closed(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(InferenceConfigError, match="api_key_env"):
        DecisionClient(_config())


def test_unknown_type_rejected(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "x")
    with pytest.raises(InferenceConfigError, match="type must be one of"):
        DecisionClient(_config(type="openai"))


def test_dangling_operation_reference_rejected(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "x")
    cfg = _config()
    cfg["operations"]["other"] = "nope"
    with pytest.raises(InferenceConfigError, match="unknown endpoint"):
        DecisionClient(cfg)


def test_absent_section_is_unconfigured_not_error():
    c = DecisionClient({})
    assert c.configured is False
    with pytest.raises(InferenceConfigError, match="no default"):
        c.resolve("anything")


def test_provider_defaults(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "x")
    do = DecisionClient(_config()).resolve("tiebreak")
    assert do.base_url == "https://inference.do-ai.run"
    assert do.model == "typesafe-jev-1.13.0"
    ts = DecisionClient(_config(type="typesafe")).resolve(None)
    assert ts.base_url == "https://api.typesafe.ai"
    assert ts.model == "jev-latest"


# ---- question validation -------------------------------------------------------


def test_choice_and_score_cardinality_limits():
    with pytest.raises(ValueError):
        Choice(instructions="x", criteria={"only": "one"})
    with pytest.raises(ValueError):
        Score(instructions="x", criteria=tuple(str(i) for i in range(11)))
    assert Noul(instructions="x", criteria={"true": "a", "false": "b"}).to_dict()["criteria"] == {"true": "a", "false": "b"}


# ---- happy path ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_sends_contract_and_parses_answers(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=QUICKSTART_RESPONSE)

    c = _client(handler, monkeypatch)
    r = await c.decide("Stripe has failed for 3 days, losing sales, help ASAP", QUESTIONS, operation="tiebreak")

    assert seen["url"] == "https://inference.do-ai.run/v1/systemone"
    assert seen["auth"] == "Bearer doo_v1_test"
    assert seen["body"]["model"] == "typesafe-jev-1.13.0"
    assert seen["body"]["questions"]["department"] == {
        "type": "choice",
        "instructions": "Which team should handle this",
        "criteria": {"billing": "Payment", "technical": "Bugs", "sales": "Pricing"},
    }
    assert seen["body"]["questions"]["frustration"]["criteria"] == ["Calm", "Frustrated but civil", "Very angry"]
    assert seen["body"]["questions"]["is_urgent"] == {"type": "noul", "instructions": "Conveys urgency"}

    assert r.model == "jev-1.13.0" and r.endpoint == "do-jev"
    assert r.noul("is_urgent") == 1.0
    dep = r.choice("department")
    assert isinstance(dep, ChoiceAnswer) and dep.choice == "technical" and dep.confidence == 0.78
    assert dep.probabilities["billing"] == 0.15
    fr = r.score("frustration")
    assert isinstance(fr, ScoreAnswer) and fr.score == 1.0 and fr.legend["1"] == "Frustrated but civil"
    assert isinstance(r.answers["is_urgent"], NoulAnswer)
    assert (r.input_tokens, r.output_tokens) == (392, 65)
    assert r.cost_usd == pytest.approx(392 / 1e6 * 0.042)
    assert r.latency_ms >= 0
    await c.aclose()


@pytest.mark.asyncio
async def test_typed_accessors_reject_wrong_kind(monkeypatch):
    c = _client(lambda req: httpx.Response(200, json=QUICKSTART_RESPONSE), monkeypatch)
    r = await c.decide("x", QUESTIONS, operation="tiebreak")
    with pytest.raises(TypeError):
        r.noul("department")
    with pytest.raises(TypeError):
        r.choice("is_urgent")
    await c.aclose()


@pytest.mark.asyncio
async def test_json_state_is_sent_as_object(monkeypatch):
    seen: dict = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.2}}, "usage": {}})

    c = _client(handler, monkeypatch)
    r = await c.decide({"a": {"name": "Acme"}, "b": {"name": "ACME Inc"}}, {"q": Noul(instructions="same?")}, operation="tiebreak")
    assert seen["body"]["state"] == {"a": {"name": "Acme"}, "b": {"name": "ACME Inc"}}
    assert r.noul("q") == 0.2 and r.cost_usd == 0.0
    await c.aclose()


# ---- failure modes -------------------------------------------------------------


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": "slow down"}, headers={"retry-after": "0"})
        return httpx.Response(200, json=QUICKSTART_RESPONSE)

    c = _client(handler, monkeypatch)
    r = await c.decide("x", QUESTIONS, operation="tiebreak")
    assert calls["n"] == 3 and r.noul("is_urgent") == 1.0
    await c.aclose()


@pytest.mark.asyncio
async def test_exhausted_retries_raise_unavailable_with_status(monkeypatch):
    c = _client(lambda req: httpx.Response(529, json={"error": "overloaded"}, headers={"retry-after": "0"}), monkeypatch)
    with pytest.raises(DecisionUnavailable) as ei:
        await c.decide("x", QUESTIONS, operation="tiebreak")
    assert ei.value.status == 529
    await c.aclose()


@pytest.mark.asyncio
async def test_non_retryable_4xx_raises_immediately(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "route not allowed"}})

    c = _client(handler, monkeypatch)
    with pytest.raises(DecisionUnavailable, match="route not allowed") as ei:
        await c.decide("x", QUESTIONS, operation="tiebreak")
    assert calls["n"] == 1 and ei.value.status == 401
    await c.aclose()


@pytest.mark.asyncio
async def test_network_error_after_retries_raises_unavailable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("boom")

    c = _client(handler, monkeypatch)
    with pytest.raises(DecisionUnavailable, match="ConnectError"):
        await c.decide("x", QUESTIONS, operation="tiebreak")
    await c.aclose()


@pytest.mark.asyncio
async def test_missing_or_malformed_answers_raise(monkeypatch):
    c = _client(lambda req: httpx.Response(200, json={"answers": {"is_urgent": {"type": "noul", "noul": 0.5}}}), monkeypatch)
    with pytest.raises(DecisionUnavailable, match="no answer for"):
        await c.decide("x", QUESTIONS, operation="tiebreak")
    await c.aclose()

    c = _client(lambda req: httpx.Response(200, json={"answers": {"q": {"type": "choice"}}}), monkeypatch)
    with pytest.raises(DecisionUnavailable, match="malformed choice"):
        await c.decide("x", {"q": Choice(instructions="x", criteria={"a": "1", "b": "2"})}, operation="tiebreak")
    await c.aclose()


@pytest.mark.asyncio
async def test_oversized_state_rejected_before_sending(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=QUICKSTART_RESPONSE)

    c = _client(handler, monkeypatch, max_state_chars=50)
    with pytest.raises(ValueError, match="over the 50 budget"):
        await c.decide("x" * 51, QUESTIONS, operation="tiebreak")
    assert calls["n"] == 0
    await c.aclose()


@pytest.mark.asyncio
async def test_structured_log_lines(monkeypatch, capsys):
    c = _client(lambda req: httpx.Response(200, json=QUICKSTART_RESPONSE), monkeypatch)
    await c.decide("x", QUESTIONS, operation="tiebreak")
    await c.aclose()
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
    statuses = [e["status"] for e in lines if e["component"] == "decision_client"]
    assert statuses == ["sending", "success"]
    success = lines[-1]["details"]
    assert success["operation"] == "tiebreak" and success["endpoint"] == "do-jev"
    assert success["input_tokens"] == 392 and "cost_usd" in success and "duration_ms" in success
