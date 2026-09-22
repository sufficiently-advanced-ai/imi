"""Decision-model client (TypeSafe "System One" contract: Jev).

A System One model is not a chat model. It takes a ``state`` (text or JSON)
plus a set of typed ``questions`` and returns calibrated probabilities — no
generated text. That shape cannot ride ``ClaudeClient.generate_message`` or
the chat-completions endpoint types in ``registry.py``, so it gets a small
sibling client here.

Wire contract (identical on TypeSafe direct and DigitalOcean serverless):

    POST {base_url}/v1/systemone
    {"model": ..., "state": ..., "questions": {name: {type, instructions, criteria?}}}
    -> {"model": ..., "answers": {name: {...}}, "usage": {"input_tokens", "output_tokens"}}

Config lives under a ``decisions:`` key in ``config/inference.yaml`` (ignored by
``InferenceRegistry``, so existing routing is untouched):

    decisions:
      endpoints:
        do-jev:
          type: digitalocean            # or: typesafe
          model: typesafe-jev-1.13.0
          api_key_env: DIGITALOCEAN_MODEL_ACCESS_KEY
          pricing: { input: 0.042 }     # USD per 1M input tokens; output is free
      operations:
        entity_resolution_tiebreak: do-jev
      default: do-jev

Design rules, mirroring the registry:
- Fail closed. A missing/misconfigured endpoint raises ``InferenceConfigError``
  at construction; a failed call raises ``DecisionUnavailable``. Callers that
  wrap a heuristic decide for themselves whether to fall back — this client
  never silently does.
- Every call carries an ``operation=`` label and emits the same structured
  stderr JSON line ``ClaudeClient`` does, so the log-joining done for cost
  audits works unchanged (``component: decision_client``).
- The model's own documented weak spot is "large state full of irrelevant
  detail" (context rot) and a hard 32k-token state budget. ``max_state_chars``
  rejects oversized states up front so callers filter instead of truncating
  blindly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from .base import InferenceConfigError
from .registry import InferenceRegistry

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URLS = {
    "typesafe": "https://api.typesafe.ai",
    "digitalocean": "https://inference.do-ai.run",
}
_DEFAULT_MODELS = {
    "typesafe": "jev-latest",
    "digitalocean": "typesafe-jev-1.13.0",
}
# TypeSafe's published state budget is 32k tokens; ~4 chars/token, with
# headroom for the questions block.
_DEFAULT_MAX_STATE_CHARS = 100_000
_RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}


class DecisionUnavailable(RuntimeError):
    """The decision endpoint could not produce an answer (network, 5xx, 429
    after retries, malformed response). Carries the last HTTP status if any."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


# ---- questions --------------------------------------------------------------


@dataclass(frozen=True)
class Noul:
    """Yes/no question. Answer is P(yes)."""

    instructions: str
    criteria: dict[str, str] | None = None  # optional {"true": ..., "false": ...}

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "noul", "instructions": self.instructions}
        if self.criteria:
            d["criteria"] = self.criteria
        return d


@dataclass(frozen=True)
class Choice:
    """Pick one of up to 255 options. ``criteria`` maps option id -> description."""

    instructions: str
    criteria: dict[str, str]

    def __post_init__(self) -> None:
        if not 2 <= len(self.criteria) <= 255:
            raise ValueError(f"Choice needs 2..255 options, got {len(self.criteria)}")

    def to_dict(self) -> dict[str, Any]:
        return {"type": "choice", "instructions": self.instructions, "criteria": dict(self.criteria)}


@dataclass(frozen=True)
class Score:
    """Rate against 2..10 ordered levels (index 0 = lowest)."""

    instructions: str
    criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 2 <= len(self.criteria) <= 10:
            raise ValueError(f"Score needs 2..10 levels, got {len(self.criteria)}")

    def to_dict(self) -> dict[str, Any]:
        return {"type": "score", "instructions": self.instructions, "criteria": list(self.criteria)}


Question = Noul | Choice | Score


# ---- answers ----------------------------------------------------------------


@dataclass(frozen=True)
class NoulAnswer:
    noul: float  # P(yes)


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float]
    confidence: float


@dataclass(frozen=True)
class ScoreAnswer:
    score: float  # continuous, in [0, levels-1]
    probabilities: dict[str, float]  # keyed by level index as string
    confidence: float
    legend: dict[str, str] = field(default_factory=dict)


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


@dataclass(frozen=True)
class DecisionResult:
    answers: dict[str, Answer]
    model: str
    endpoint: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    raw: dict[str, Any]

    def noul(self, name: str) -> float:
        a = self.answers[name]
        if not isinstance(a, NoulAnswer):
            raise TypeError(f"{name!r} is a {type(a).__name__}, not a Noul")
        return a.noul

    def choice(self, name: str) -> ChoiceAnswer:
        a = self.answers[name]
        if not isinstance(a, ChoiceAnswer):
            raise TypeError(f"{name!r} is a {type(a).__name__}, not a Choice")
        return a

    def score(self, name: str) -> ScoreAnswer:
        a = self.answers[name]
        if not isinstance(a, ScoreAnswer):
            raise TypeError(f"{name!r} is a {type(a).__name__}, not a Score")
        return a


def _parse_answer(name: str, raw: Any) -> Answer:
    if not isinstance(raw, dict) or "type" not in raw:
        raise DecisionUnavailable(f"malformed answer for {name!r}: {raw!r}")
    t = raw["type"]
    try:
        if t == "noul":
            return NoulAnswer(noul=float(raw["noul"]))
        if t == "choice":
            return ChoiceAnswer(
                choice=str(raw["choice"]),
                probabilities={str(k): float(v) for k, v in raw.get("probabilities", {}).items()},
                confidence=float(raw.get("confidence", 0.0)),
            )
        if t == "score":
            return ScoreAnswer(
                score=float(raw["score"]),
                probabilities={str(k): float(v) for k, v in raw.get("probabilities", {}).items()},
                confidence=float(raw.get("confidence", 0.0)),
                legend={str(k): str(v) for k, v in raw.get("legend", {}).items()},
            )
    except (KeyError, TypeError, ValueError) as e:
        raise DecisionUnavailable(f"malformed {t} answer for {name!r}: {e}") from e
    raise DecisionUnavailable(f"unknown answer type {t!r} for {name!r}")


# ---- endpoint config --------------------------------------------------------


@dataclass(frozen=True)
class DecisionEndpoint:
    name: str
    base_url: str
    model: str
    api_key: str
    input_price_per_mtok: float
    timeout: float = 30.0
    max_concurrency: int = 8
    max_state_chars: int = _DEFAULT_MAX_STATE_CHARS


def _build_endpoint(name: str, spec: dict[str, Any]) -> DecisionEndpoint:
    etype = spec.get("type")
    if etype not in _DEFAULT_BASE_URLS:
        raise InferenceConfigError(
            f"decisions endpoint {name!r}: type must be one of {sorted(_DEFAULT_BASE_URLS)}, got {etype!r}"
        )
    key_env = spec.get("api_key_env")
    api_key = (os.getenv(key_env) or "").strip() if isinstance(key_env, str) else ""
    if not api_key:
        # Same fail-closed posture as the `digitalocean` chat endpoint type.
        raise InferenceConfigError(
            f"decisions endpoint {name!r} requires a non-empty key via 'api_key_env' (got {key_env!r})"
        )
    pricing = spec.get("pricing") or {}
    try:
        price = float(pricing.get("input", 0.0))
    except (TypeError, ValueError) as e:
        raise InferenceConfigError(f"decisions endpoint {name!r}: pricing.input must be a number") from e
    return DecisionEndpoint(
        name=name,
        base_url=str(spec.get("base_url") or _DEFAULT_BASE_URLS[etype]).rstrip("/"),
        model=str(spec.get("model") or _DEFAULT_MODELS[etype]),
        api_key=api_key,
        input_price_per_mtok=price,
        timeout=float(spec.get("timeout", 30.0)),
        max_concurrency=int(spec.get("max_concurrency", 8)),
        max_state_chars=int(spec.get("max_state_chars", _DEFAULT_MAX_STATE_CHARS)),
    )


# ---- client -----------------------------------------------------------------


class DecisionClient:
    """Async client for a System One decision endpoint.

    Construct once per process (``get_decision_client()``); it holds one
    ``httpx.AsyncClient`` and one semaphore per endpoint.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = 3,
    ):
        if config is None:
            config = InferenceRegistry._load_config().get("decisions") or {}
        if not isinstance(config, dict):
            raise InferenceConfigError("inference.yaml: 'decisions' must be a mapping")
        specs = config.get("endpoints") or {}
        self._endpoints = {n: _build_endpoint(n, s or {}) for n, s in specs.items()}
        self._operations: dict[str, str] = config.get("operations") or {}
        self._default: str | None = config.get("default") or (
            next(iter(self._endpoints)) if len(self._endpoints) == 1 else None
        )
        unknown = {n for n in [*self._operations.values(), *([self._default] if self._default else [])]}
        unknown -= set(self._endpoints)
        if unknown:
            raise InferenceConfigError(
                f"decisions config references unknown endpoint(s): {sorted(unknown)}; "
                f"defined: {sorted(self._endpoints)}"
            )
        self._semaphores = {n: asyncio.Semaphore(e.max_concurrency) for n, e in self._endpoints.items()}
        self._http = httpx.AsyncClient(transport=transport, timeout=None)
        self._max_attempts = max(1, max_attempts)

    @property
    def configured(self) -> bool:
        return bool(self._endpoints)

    @property
    def endpoints(self) -> dict[str, DecisionEndpoint]:
        return dict(self._endpoints)

    def resolve(self, operation: str | None) -> DecisionEndpoint:
        name = (operation and self._operations.get(operation)) or self._default
        if not name:
            raise InferenceConfigError(
                f"no decisions endpoint for operation {operation!r} and no default configured"
            )
        return self._endpoints[name]

    async def aclose(self) -> None:
        await self._http.aclose()

    async def decide(
        self,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, Question],
        *,
        operation: str,
        endpoint: str | None = None,
    ) -> DecisionResult:
        """Ask ``questions`` about ``state``. All questions are evaluated in one
        request (they are scored in parallel server-side, so more questions cost
        only their own tokens)."""
        if not questions:
            raise ValueError("decide() needs at least one question")
        ep = self._endpoints[endpoint] if endpoint else self.resolve(operation)
        state_len = len(state) if isinstance(state, str) else len(json.dumps(state))
        if state_len > ep.max_state_chars:
            raise ValueError(
                f"state is {state_len} chars, over the {ep.max_state_chars} budget for {ep.name!r}; "
                "filter to what the questions need instead of sending the whole document"
            )
        body = {
            "model": ep.model,
            "state": state,
            "questions": {name: q.to_dict() for name, q in questions.items()},
        }
        headers = {"Authorization": f"Bearer {ep.api_key}", "Content-Type": "application/json"}
        url = f"{ep.base_url}/v1/systemone"

        started = time.monotonic()
        last_status: int | None = None
        last_err = ""
        async with self._semaphores[ep.name]:
            for attempt in range(1, self._max_attempts + 1):
                self._log("sending", operation, ep, attempt=attempt, questions=len(questions), state_chars=state_len)
                try:
                    resp = await self._http.post(url, json=body, headers=headers, timeout=ep.timeout)
                except httpx.HTTPError as e:
                    last_err = f"{type(e).__name__}: {e}"
                    self._log("error", operation, ep, attempt=attempt, error=last_err)
                    if attempt < self._max_attempts:
                        await asyncio.sleep(min(2 ** (attempt - 1), 8))
                        continue
                    raise DecisionUnavailable(f"{ep.name}: {last_err}") from e

                last_status = resp.status_code
                if resp.status_code in _RETRYABLE and attempt < self._max_attempts:
                    retry_after = resp.headers.get("retry-after")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** (attempt - 1), 8)
                    self._log("retry", operation, ep, attempt=attempt, status=resp.status_code, delay=delay)
                    await asyncio.sleep(delay)
                    continue
                if resp.status_code != 200:
                    snippet = resp.text[:300]
                    self._log("error", operation, ep, attempt=attempt, status=resp.status_code, body=snippet)
                    raise DecisionUnavailable(
                        f"{ep.name}: HTTP {resp.status_code}: {snippet}", status=resp.status_code
                    )
                break
            else:  # pragma: no cover - loop always breaks or raises
                raise DecisionUnavailable(f"{ep.name}: exhausted retries", status=last_status)

        try:
            data = resp.json()
        except ValueError as e:
            raise DecisionUnavailable(f"{ep.name}: non-JSON response") from e
        raw_answers = data.get("answers")
        if not isinstance(raw_answers, dict):
            raise DecisionUnavailable(f"{ep.name}: response has no 'answers' mapping")
        missing = set(questions) - set(raw_answers)
        if missing:
            raise DecisionUnavailable(f"{ep.name}: no answer for {sorted(missing)}")
        answers = {name: _parse_answer(name, raw_answers[name]) for name in questions}

        usage = data.get("usage") or {}
        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
        cost = in_tok / 1_000_000 * ep.input_price_per_mtok
        latency_ms = int((time.monotonic() - started) * 1000)
        result = DecisionResult(
            answers=answers,
            model=str(data.get("model") or ep.model),
            endpoint=ep.name,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            latency_ms=latency_ms,
            raw=data,
        )
        self._log(
            "success", operation, ep,
            model=result.model, input_tokens=in_tok, output_tokens=out_tok,
            cost_usd=round(cost, 8), duration_ms=latency_ms,
        )
        return result

    @staticmethod
    def _log(event: str, operation: str, ep: DecisionEndpoint, **details: Any) -> None:
        # Same stderr JSON shape as ClaudeClient._log_claude_request so the
        # request logs can be joined and audited with the existing tooling.
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": "decision_client",
            "status": event,
            "details": {"operation": operation, "endpoint": ep.name, **details},
        }
        print(json.dumps(entry), file=sys.stderr)


_client: DecisionClient | None = None


def get_decision_client() -> DecisionClient:
    """Process-wide client. Raises ``InferenceConfigError`` if the ``decisions``
    section is malformed; returns an unconfigured client (``configured`` is
    False) if the section is absent, so callers can gate on it."""
    global _client
    if _client is None:
        _client = DecisionClient()
    return _client


def reset_decision_client() -> None:
    global _client
    _client = None
