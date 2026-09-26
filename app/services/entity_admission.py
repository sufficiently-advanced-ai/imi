"""Entity admission — should a newly extracted entity enter the graph, and as
which type?

Salient extraction, signal refs and signal owners propose entity names. The
unambiguous junk ("Speaker 2", "Unknown") is caught by the deterministic
``is_placeholder_entity_name`` prefilter; everything else that would become a
NEW node is judged here by the decision model (TypeSafe Jev, operation
``entity_admission``), in one ``decide()`` call per entity with two questions:

  named  (Noul)   — is this a specific, named real-world thing, not a role or
                    job title ("Recruiter"), a placeholder ("Unnamed
                    facilitator"), a generic group ("Partners")?
  type   (Choice) — which domain type is it (with the type descriptions), or
                    none of them?

Same contract as the resolver tiebreak (entity_resolver.py): batched,
concurrent, never raises; ``off`` skips, ``shadow`` logs verdicts without
acting, ``on`` acts only past asymmetric bars — dropping a real entity is worse
than keeping junk, so the drop bar is high.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

ADMISSION_OPERATION = "entity_admission"
DROP_MIN_PROBABILITY = 0.80  # P(not a named entity) needed to drop
RETYPE_MIN_PROBABILITY = 0.85  # P(other type) needed to retype ...
RETYPE_MAX_EXTRACTED_TYPE = 0.20  # ... and P(it IS the extracted type) at most this
_NONE_TYPE = "none"


@dataclass(frozen=True)
class AdmissionVerdict:
    action: str  # "keep" | "drop" | "retype"
    new_type: str | None = None
    p_named: float = 1.0
    type_choice: str | None = None
    type_probability: float = 0.0


KEEP = AdmissionVerdict(action="keep")


def build_admission_questions(entity_types: dict[str, str], extracted_type: str):
    """(questions, option_id -> type) for one mention."""
    from app.services.inference.decisions import Choice, Noul

    options = {f"t{i}": t for i, t in enumerate(sorted(entity_types), start=1)}
    criteria = {
        key: f"{etype}: {entity_types[etype]}" if entity_types[etype] else etype
        for key, etype in options.items()
    }
    criteria[_NONE_TYPE] = "None of these types fits"
    questions = {
        "named": Noul(
            instructions=(
                f"The mention was extracted from a meeting transcript as a {extracted_type}. "
                "Is it a specific, named real-world entity that belongs in a knowledge graph? "
                "Answer no for a role or job title ('Recruiter', 'the facilitator'), a "
                "placeholder for someone unnamed ('Unnamed facilitator', 'Speaker 2'), a "
                "generic group or category ('Partners', 'the team', 'leadership'), or a "
                "feature/concept rather than a named thing. A person's first name alone, or "
                "a named company, product, project or team, is a yes."
            ),
        ),
        "type": Choice(
            instructions=(
                "Which entity type is this mention? An external organisation (a company, "
                "vendor, client, partner firm) is never a team: a team is a group inside the "
                "speaker's own organisation. A person is a human individual."
            ),
            criteria=criteria,
        ),
        # Direct confirmation of the extractor's type: a retype needs BOTH a
        # confident different type above AND a clear "no" here, so a close
        # call in the multi-way choice alone (a project named after a place)
        # never flips a correctly typed entity.
        "is_extracted_type": Noul(
            instructions=(
                f"Is this mention a {extracted_type}"
                + (f" ({entity_types[extracted_type]})" if entity_types.get(extracted_type) else "")
                + "? Judge from the evidence of how it is talked about."
            ),
        ),
    }
    return questions, options


def build_admission_state(mention: dict, meeting: dict | None) -> dict:
    m = {"name": mention["name"], "extracted_type": mention["type"]}
    for key in ("evidence", "role", "aliases_heard"):
        if mention.get(key):
            m[key] = mention[key]
    state: dict[str, Any] = {"mention": m}
    if meeting:
        state["meeting"] = {k: v for k, v in meeting.items() if v}
    return state


def apply_admission(
    extracted_type: str,
    p_named: float,
    type_choice: str | None,
    type_probability: float,
    options: dict[str, str],
    drop_min: float = DROP_MIN_PROBABILITY,
    retype_min: float = RETYPE_MIN_PROBABILITY,
    p_is_extracted_type: float = 0.0,
) -> AdmissionVerdict:
    if (1.0 - p_named) >= drop_min:
        return AdmissionVerdict("drop", None, p_named, type_choice, type_probability)
    new_type = options.get(type_choice or "")
    if (
        new_type
        and new_type != extracted_type
        and type_probability >= retype_min
        and p_is_extracted_type <= RETYPE_MAX_EXTRACTED_TYPE
    ):
        return AdmissionVerdict("retype", new_type, p_named, type_choice, type_probability)
    return AdmissionVerdict("keep", None, p_named, type_choice, type_probability)


def _default_client():
    try:
        from app.services.inference.decisions import get_decision_client

        client = get_decision_client()
        return client if client.mode(ADMISSION_OPERATION) != "off" else None
    except Exception as e:  # config errors must never break ingest
        logger.warning("[ADMISSION] Decision model unavailable: %s", e)
        return None


async def judge_entities(
    mentions: list[dict],
    entity_types: dict[str, str],
    meeting: dict | None = None,
    client: Any = None,
) -> dict[tuple[str, str], AdmissionVerdict]:
    """Verdicts keyed by (type, name) for mentions the model says to drop or
    retype. Empty in ``off``/``shadow`` mode (shadow logs what it would do),
    when no client is configured, or on any failure. Never raises."""
    if client is None:
        client = _default_client()
    if client is None or not mentions:
        return {}
    mode = client.mode(ADMISSION_OPERATION)
    if mode == "off":
        return {}

    from app.services.inference.decisions import DecisionUnavailable

    async def one(mention: dict) -> tuple[tuple[str, str], AdmissionVerdict]:
        key = (mention["type"], mention["name"])
        questions, options = build_admission_questions(entity_types, mention["type"])
        try:
            result = await client.decide(
                build_admission_state(mention, meeting), questions, operation=ADMISSION_OPERATION
            )
            p_named = result.noul("named")
            answer = result.choice("type")
            probability = answer.probabilities.get(answer.choice, 0.0)
            p_is_type = result.noul("is_extracted_type")
        except (DecisionUnavailable, ValueError, KeyError, TypeError) as e:
            logger.warning("[ADMISSION] Judgment failed for %s/%r, keeping: %s", *key, e)
            return key, KEEP
        verdict = apply_admission(
            mention["type"], p_named, answer.choice, probability, options,
            p_is_extracted_type=p_is_type,
        )
        logger.info(
            "[ADMISSION] %s %s/%r: p_named=%.2f type=%s p=%.2f is_%s=%.2f -> %s%s",
            mode, key[0], key[1], p_named,
            options.get(answer.choice, answer.choice), probability, key[0], p_is_type, verdict.action,
            f" ({verdict.new_type})" if verdict.new_type else "",
        )
        return key, verdict

    seen: set[tuple[str, str]] = set()
    unique = []
    for m in mentions:
        key = (m.get("type") or "", (m.get("name") or "").strip())
        if key[0] and key[1] and key not in seen:
            seen.add(key)
            unique.append({**m, "type": key[0], "name": key[1]})
    results = await asyncio.gather(*(one(m) for m in unique))
    if mode != "on":
        return {}
    return {key: v for key, v in results if v.action != "keep"}
