"""Inference endpoint registry.

Loads ``config/inference.yaml`` and resolves a (model-alias, operation) pair to a
concrete ``ResolvedEndpoint`` — which provider to call, with which credentials
and base URL, and how to price it.

Config shape (all sections optional)::

    endpoints:
      anthropic-default: { type: anthropic, api_key_env: ANTHROPIC_API_KEY, base_url: null }
      tailnet-vllm:
        type: openai                 # generic OpenAI-compatible (vLLM/Ollama/TGI)
        litellm_model: hosted_vllm/qwen2.5-7b-instruct
        base_url: http://llm.tailnet:8000/v1
        api_key_env: TAILNET_LLM_KEY
        pricing: { input: 0.0, output: 0.0 }
        allow_tools: false
      client-dmz-bedrock:
        type: bedrock
        litellm_model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
        aws_region: us-east-1
    aliases:                         # model-tier string -> endpoint name
      "claude-haiku-4-5-20251001": tailnet-vllm
    operations:                      # operation label -> endpoint name (wins over alias)
      metadata_extraction: tailnet-vllm
    default: anthropic-default       # endpoint for everything else

Backward compatibility: if the file is absent, every call resolves to an
implicit Anthropic endpoint built from ``settings.ANTHROPIC_API_KEY`` (+ optional
``ANTHROPIC_BASE_URL``) — byte-for-byte today's behaviour.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .base import InferenceConfigError

logger = logging.getLogger(__name__)

# Endpoint config search roots (first existing wins). Mirrors the dual host/
# container layout used by app/config.py and the domain-config loader.
_CONFIG_CANDIDATES = (
    Path(os.getenv("INFERENCE_CONFIG_PATH", "")) if os.getenv("INFERENCE_CONFIG_PATH") else None,
    Path("config/inference.yaml"),
    Path("/app/config/inference.yaml"),
)

_DEFAULT_ENDPOINT_NAME = "anthropic-default"


@dataclass
class ResolvedEndpoint:
    """A fully resolved target for one inference call."""

    name: str
    is_anthropic: bool
    # For anthropic: the bare model id (e.g. "claude-haiku-4-5-...").
    # For litellm: the provider-prefixed string (e.g. "hosted_vllm/qwen...").
    model: str
    api_base: str | None = None
    api_key: str | None = None
    aws_region: str | None = None
    # Per-million-token pricing for self-hosted models LiteLLM can't price.
    pricing: dict[str, float] | None = None
    allow_tools: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class InferenceRegistry:
    """Resolves model-tier/operation -> endpoint from ``config/inference.yaml``.

    Loaded once and cached. The registry is intentionally tiny and synchronous;
    it is consulted on every ``generate_message`` call.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config if config is not None else self._load_config()
        self._endpoints: dict[str, dict[str, Any]] = self._config.get("endpoints", {}) or {}
        self._aliases: dict[str, str] = self._config.get("aliases", {}) or {}
        self._operations: dict[str, str] = self._config.get("operations", {}) or {}
        self._default_name: str = self._config.get("default", _DEFAULT_ENDPOINT_NAME)
        self._validate()

    # ---- loading -----------------------------------------------------------

    @staticmethod
    def _load_config() -> dict[str, Any]:
        for candidate in _CONFIG_CANDIDATES:
            if candidate is None:
                continue
            try:
                if candidate.exists():
                    data = yaml.safe_load(candidate.read_text()) or {}
                    if not isinstance(data, dict):
                        raise InferenceConfigError(
                            f"{candidate}: top-level YAML must be a mapping"
                        )
                    logger.info("Loaded inference config from %s", candidate)
                    return data
            except InferenceConfigError:
                raise
            except (OSError, yaml.YAMLError) as e:
                # Fail closed on a malformed config rather than silently routing
                # everything to Anthropic.
                raise InferenceConfigError(f"Failed to read {candidate}: {e}") from e
        logger.info("No config/inference.yaml found — defaulting all calls to Anthropic")
        return {}

    def _validate(self) -> None:
        """Catch dangling endpoint references at load time, not call time."""
        referenced = set(self._aliases.values()) | set(self._operations.values())
        if self._default_name != _DEFAULT_ENDPOINT_NAME:
            referenced.add(self._default_name)
        unknown = {n for n in referenced if n not in self._endpoints}
        if unknown:
            raise InferenceConfigError(
                f"inference config references unknown endpoint(s): {sorted(unknown)}; "
                f"defined endpoints: {sorted(self._endpoints)}"
            )

    # ---- resolution --------------------------------------------------------

    def resolve(self, model: str, operation: str | None = None) -> ResolvedEndpoint:
        """Resolve a model-tier string + operation label to an endpoint.

        Precedence: operation override > model alias > configured default >
        implicit Anthropic. ``model`` is the concrete tier string the call site
        passed (e.g. ``settings.CLAUDE_HAIKU_MODEL``); for the default Anthropic
        endpoint it is used verbatim as the model id.
        """
        endpoint_name = (
            (operation and self._operations.get(operation))
            or self._aliases.get(model)
            or self._default_name
        )

        spec = self._endpoints.get(endpoint_name)
        if spec is None:
            if endpoint_name == _DEFAULT_ENDPOINT_NAME:
                return self._implicit_anthropic(model)
            # _validate() should have caught this; belt-and-suspenders.
            raise InferenceConfigError(f"Unknown inference endpoint: {endpoint_name!r}")

        return self._build(endpoint_name, spec, model)

    def _build(self, name: str, spec: dict[str, Any], requested_model: str) -> ResolvedEndpoint:
        etype = (spec.get("type") or "anthropic").lower()
        api_key_env = spec.get("api_key_env")
        api_key = os.getenv(api_key_env) if api_key_env else None

        if etype == "anthropic":
            return ResolvedEndpoint(
                name=name,
                is_anthropic=True,
                model=spec.get("model") or requested_model,
                api_base=spec.get("base_url") or None,
                api_key=api_key or self._anthropic_key(),
                pricing=spec.get("pricing"),
                allow_tools=spec.get("allow_tools", True),
            )

        if etype == "digitalocean":
            # DigitalOcean serverless inference is OpenAI-compatible behind a
            # single base URL. Author writes a bare `model` id; we default the
            # base URL and wrap it in LiteLLM's generic `openai/` route. Tools
            # stay rejected (plain-generation only), like every non-Anthropic
            # endpoint. The key is required up front so a missing/empty env var
            # fails closed here, not as a 401 buried in the call-time retry loop.
            do_model = spec.get("model")
            if not isinstance(do_model, str) or not do_model.strip():
                raise InferenceConfigError(
                    f"endpoint {name!r} (type=digitalocean) requires a 'model' string"
                )
            do_model = do_model.strip()
            # os.getenv returns a str or None; treat a whitespace-only value as
            # absent so it fails closed here instead of as a 401 at call time.
            resolved_api_key = api_key.strip() if isinstance(api_key, str) else ""
            if not resolved_api_key:
                raise InferenceConfigError(
                    f"endpoint {name!r} (type=digitalocean) requires a non-empty key "
                    f"via 'api_key_env' (got {spec.get('api_key_env')!r})"
                )
            return ResolvedEndpoint(
                name=name,
                is_anthropic=False,
                model=f"openai/{do_model}",
                api_base=spec.get("base_url") or "https://inference.do-ai.run/v1",
                api_key=resolved_api_key,
                pricing=spec.get("pricing"),
                # Hard-off, not config-overridable: this convenience type is
                # plain-generation only by contract. Use type: openai for an
                # endpoint where tool use should be configurable.
                allow_tools=False,
            )

        # Non-Anthropic -> LiteLLM. litellm_model carries the provider prefix.
        litellm_model = spec.get("litellm_model")
        if not litellm_model:
            raise InferenceConfigError(
                f"endpoint {name!r} (type={etype}) requires a 'litellm_model' string"
            )
        extra: dict[str, Any] = {}
        if spec.get("aws_region"):
            extra["aws_region_name"] = spec["aws_region"]
        return ResolvedEndpoint(
            name=name,
            is_anthropic=False,
            model=litellm_model,
            api_base=spec.get("base_url") or None,
            api_key=api_key,
            aws_region=spec.get("aws_region"),
            pricing=spec.get("pricing"),
            allow_tools=spec.get("allow_tools", False),
            extra=extra,
        )

    def _implicit_anthropic(self, requested_model: str) -> ResolvedEndpoint:
        return ResolvedEndpoint(
            name=_DEFAULT_ENDPOINT_NAME,
            is_anthropic=True,
            model=requested_model,
            api_base=self._anthropic_base_url(),
            api_key=self._anthropic_key(),
            allow_tools=True,
        )

    @staticmethod
    def _anthropic_key() -> str | None:
        # Imported lazily to avoid a circular import at module load.
        from ...config import settings

        return settings.ANTHROPIC_API_KEY or None

    @staticmethod
    def _anthropic_base_url() -> str | None:
        from ...config import settings

        return getattr(settings, "ANTHROPIC_BASE_URL", "") or None


# ---- singleton -------------------------------------------------------------

_registry_instance: InferenceRegistry | None = None


def get_inference_registry() -> InferenceRegistry:
    """Return the process-global inference registry, creating it on first use."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = InferenceRegistry()
    return _registry_instance


def reset_inference_registry() -> None:
    """Drop the cached registry (tests reload config between cases)."""
    global _registry_instance
    _registry_instance = None
