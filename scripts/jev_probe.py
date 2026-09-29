#!/usr/bin/env python3
"""Smoke-test the decision client against a live System One endpoint.

Sends TypeSafe's quickstart support-ticket example (one Noul, one Choice, one
Score) and prints the parsed answers, token usage, cost and latency.

    # DigitalOcean (default): needs DIGITALOCEAN_MODEL_ACCESS_KEY in the env/.env
    python scripts/jev_probe.py
    # TypeSafe direct: needs TYPESAFE_API_KEY
    python scripts/jev_probe.py --provider typesafe
    # Any state from a file, default questions
    python scripts/jev_probe.py --state-file some.txt

Exits non-zero on any failure so it can gate CI once the route is live.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional here; the env may already be set
    pass

from app.services.inference.base import InferenceConfigError  # noqa: E402
from app.services.inference.decisions import (  # noqa: E402
    Choice,
    DecisionClient,
    DecisionUnavailable,
    Noul,
    Score,
)

STATE = (
    "Hi, I've been trying to connect my Stripe account for 3 days and the "
    "integration keeps failing. I'm losing sales. Please help ASAP."
)
QUESTIONS = {
    "department": Choice(
        instructions="Which team should handle this",
        criteria={
            "billing": "Payment or subscription issues",
            "technical": "Bugs or integration problems",
            "sales": "Pricing or account questions",
        },
    ),
    "frustration": Score(
        instructions="How frustrated the customer appears",
        criteria=("Calm, just stating facts", "Frustrated but civil", "Very angry, strong language"),
    ),
    "is_urgent": Noul(instructions="The message conveys urgency or time-sensitivity"),
}


def _config(provider: str, model: str | None) -> dict:
    key_env = {"digitalocean": "DIGITALOCEAN_MODEL_ACCESS_KEY", "typesafe": "TYPESAFE_API_KEY"}[provider]
    spec = {"type": provider, "api_key_env": key_env, "pricing": {"input": 0.042}}
    if model:
        spec["model"] = model
    return {"endpoints": {"probe": spec}, "default": "probe"}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["digitalocean", "typesafe"], default="digitalocean")
    ap.add_argument("--model", help="override the endpoint's default model id")
    ap.add_argument("--state-file", type=Path, help="read the state from this file instead of the sample")
    args = ap.parse_args()

    try:
        client = DecisionClient(_config(args.provider, args.model))
    except InferenceConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    ep = client.resolve(None)
    state = args.state_file.read_text() if args.state_file else STATE
    print(f"endpoint  {ep.base_url}  model={ep.model}")
    print(f"state     {len(state)} chars")
    try:
        r = await client.decide(state, QUESTIONS, operation="probe")
    except DecisionUnavailable as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    finally:
        await client.aclose()

    print(f"model     {r.model}   latency {r.latency_ms} ms   tokens in/out {r.input_tokens}/{r.output_tokens}   cost ${r.cost_usd:.7f}")
    for name, a in r.answers.items():
        print(f"  {name:12s} {a}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
