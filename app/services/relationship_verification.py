"""Relationship verification — does the meeting support this inferred
entity-to-entity relationship?

The ``infer_relationships`` tool proposes typed edges (``Foley managed_by
Brian Vigilani``) from a transcript. Unchecked, it wrote plausible-sounding
but wrong ones — "Dan Kauppi reports_to Brian Vigilani" for a vendor lead
and his client. Every proposed edge is put to the decision model (TypeSafe
Jev, operation ``relationship_verify``); code only gathers evidence:

  supported (Noul) — does the meeting establish this relationship, in this
                     direction? Evidence: the proposer's quote, transcript
                     excerpts around both names, both entities' profile
                     summaries and the relationship type's meaning.

Same contract as the other decision operations: batched, never raises,
``off``/``shadow``/``on``. ``off`` and ``shadow`` keep every proposal (the
pre-Jev behaviour); ``on`` keeps only confident ones — a missing edge comes
back from the next meeting that states it, a wrong one misleads every query.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.entity_linking import transcript_windows

logger = logging.getLogger(__name__)

VERIFY_OPERATION = "relationship_verify"
SUPPORT_MIN_PROBABILITY = 0.80  # P(supported) needed to write the edge


def describe(rel: dict) -> str:
    """'Foley —managed_by→ Brian Vigilani (Foley is managed by Brian Vigilani)'."""
    src, tgt, rtype = rel["source_name"], rel["target_name"], rel["type"]
    return f"{src} —{rtype}→ {tgt} (read: {src} {rtype.replace('_', ' ')} {tgt})"


def build_verify_question():
    from app.services.inference.decisions import Noul

    return {
        "supported": Noul(
            instructions=(
                "Does this meeting establish the proposed relationship, in exactly this "
                "direction? Answer yes only when the transcript states it or clearly implies "
                "it. Being on the same call, being mentioned together, or working on the same "
                "deal is not enough. Keep organisations straight: a vendor's lead and the "
                "client's contact do not report to or manage each other, and a person manages "
                "an account only if they own that relationship for their own organisation. "
                "Use the profiles to know who works where."
            ),
        ),
    }


def build_verify_state(rel: dict, windows: list[str], meeting: dict | None) -> dict:
    def side(prefix: str) -> dict:
        out = {"name": rel[f"{prefix}_name"], "type": rel[f"{prefix}_type"]}
        if rel.get(f"{prefix}_profile"):
            out["profile_summary"] = rel[f"{prefix}_profile"]
        return out

    proposal: dict[str, Any] = {
        "source": side("source"),
        "relationship": describe(rel),
        "target": side("target"),
    }
    if rel.get("type_description"):
        proposal["relationship_meaning"] = rel["type_description"]
    for key in ("evidence", "description"):
        if rel.get(key):
            proposal[key] = rel[key]
    state: dict[str, Any] = {
        "proposal": proposal,
        "transcript_excerpts": windows or ["(neither name found in the transcript)"],
    }
    if meeting:
        state["meeting"] = {k: v for k, v in meeting.items() if v}
    return state


def _default_client():
    try:
        from app.services.inference.decisions import get_decision_client

        client = get_decision_client()
        return client if client.mode(VERIFY_OPERATION) != "off" else None
    except Exception as e:  # config errors must never break ingest
        logger.warning("[RELATION] Decision model unavailable: %s", e)
        return None


async def verify_relationships(
    relationships: list[dict],
    transcript: str,
    meeting: dict | None = None,
    client: Any = None,
) -> list[dict]:
    """The relationships to write.

    relationships: [{"source_id", "source_name", "source_type", "type",
                     "target_id", "target_name", "target_type", optional
                     "source_profile", "target_profile", "type_description",
                     "evidence", "description"}]
    Returns all of them in ``off``/``shadow`` mode, without a client, or for
    any judgment that failed; in ``on`` mode only those with
    P(supported) >= SUPPORT_MIN_PROBABILITY. Never raises.
    """
    if not relationships:
        return []
    if client is None:
        client = _default_client()
    if client is None:
        return list(relationships)
    mode = client.mode(VERIFY_OPERATION)
    if mode == "off":
        return list(relationships)

    from app.services.inference.decisions import DecisionUnavailable

    async def one(rel: dict) -> tuple[dict, bool]:
        windows = transcript_windows(transcript, [rel["source_name"], rel["target_name"]])
        try:
            result = await client.decide(
                build_verify_state(rel, windows, meeting),
                build_verify_question(),
                operation=VERIFY_OPERATION,
            )
            p = result.noul("supported")
        except (DecisionUnavailable, ValueError, KeyError, TypeError) as e:
            logger.warning("[RELATION] Judgment failed for %s, keeping: %s", describe(rel), e)
            return rel, True
        keep = p >= SUPPORT_MIN_PROBABILITY
        logger.info(
            "[RELATION] %s %s: supported=%.2f -> %s",
            mode, describe(rel), p, "write" if keep else "drop",
        )
        return rel, keep

    results = await asyncio.gather(*(one(r) for r in relationships))
    if mode != "on":
        return list(relationships)
    return [rel for rel, keep in results if keep]
