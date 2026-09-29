"""Signal relation — how does a new decision relate to a standing one?

``find_supersession_candidates`` proposes pairs on shared non-person entities
alone. On a small or single-account corpus nearly every decision shares an
entity with every other, so the review queue fills with pairs that are
unrelated ("remove a service-account role" vs "build on Snowflake + React"),
refinements (a scoping or UX decision made inside an earlier one) or
restatements (the same decision from a later meeting). Confirming any of those
closes a live decision's validity window and drops it from the constitution.

Each candidate pair is put to the decision model (TypeSafe Jev, operation
``signal_relation``) as one Choice:

  supersedes — the new decision replaces or reverses the old one
  refines    — adds detail/scope/next step within the old one; both stand
  restates   — the same decision stated again (reworded or reaffirmed)
  conflicts  — incompatible, but the new one does not clearly replace the old
  unrelated  — different questions that happen to share entities

Same contract as the other decision operations: batched, never raises,
``off``/``shadow``/``on``. In ``shadow`` every candidate is annotated with the
model's answer but stays pending, so the queue is unchanged. In ``on`` only
pairs the model calls ``supersedes`` with p >= SUPERSEDE_MIN_PROBABILITY stay
pending (with that probability as their confidence); the rest are recorded as
dismissed by the model, never deleted, so a reviewer can still see them. The
bar is asymmetric on purpose: a missed supersession leaves two decisions
standing (visible, recoverable), a wrong one silently retires a live decision.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

RELATION_OPERATION = "signal_relation"
# Set from scripts/eval_signal_relation.py (3 runs, 2026-09-29): true
# supersessions scored >= 0.85; a conflicting pair Jev mislabels as
# `supersedes` scored 0.62-0.70. The bar sits between them.
SUPERSEDE_MIN_PROBABILITY = 0.75
RELATIONS = ("supersedes", "refines", "restates", "conflicts", "unrelated")
_DISMISSED_BY = "decision_model"


def build_relation_question():
    from app.services.inference.decisions import Choice

    return Choice(
        instructions=(
            "Two decisions recorded from meetings. How does the NEW decision relate to "
            "the OLD decision? Judge what each decision commits to, not whether they "
            "mention the same organizations or people. A decision made later about a "
            "narrower detail of the same project does not replace the earlier one."
        ),
        criteria={
            "supersedes": "The NEW decision replaces, reverses, or cancels the OLD one: "
                          "people should stop following the OLD decision.",
            "refines": "The NEW decision adds detail, scope, a constraint, or a next step "
                       "within the OLD decision; both remain in force.",
            "restates": "The NEW decision is the same decision as the OLD one, reworded, "
                        "reaffirmed, or reported again from another meeting.",
            "conflicts": "The two decisions cannot both be followed, but the NEW one does "
                         "not clearly replace the OLD one; someone must reconcile them.",
            "unrelated": "The decisions answer different questions, even though they share "
                         "organizations, people, or a project.",
        },
    )


def _side(signal: Any) -> dict:
    return {
        "decision": signal.content,
        "meeting": signal.source_meeting_title or signal.source_meeting_id,
        "date": (signal.source_timestamp or "")[:10],
    }


def build_relation_state(new_signal: Any, old_signal: Any, shared_entities: list[str]) -> dict:
    return {
        "old": _side(old_signal),
        "new": _side(new_signal),
        "shared_entities": shared_entities,
    }


def apply_relation(candidate: dict, relation: str, probabilities: dict[str, float], mode: str) -> dict:
    """Return the candidate annotated with the model's answer (a new dict).

    ``on`` mode also decides the queue: only a confident ``supersedes`` stays
    pending, with the model's probability replacing the entity-overlap ratio.
    Candidates a reviewer already actioned are annotated but never re-decided.
    """
    # Gate on the raw probability; round only what is stored for display, so
    # 0.7496 never rounds up past the bar.
    raw_supersedes = float(probabilities.get("supersedes", 0.0))
    p_supersedes = round(raw_supersedes, 3)
    out = {
        **candidate,
        "relation": relation,
        "relation_probability": round(float(probabilities.get(relation, 0.0)), 3),
        "relation_probabilities": {k: round(float(v), 3) for k, v in probabilities.items()},
    }
    if mode != "on" or candidate.get("status", "pending") != "pending":
        return out
    out.setdefault("entity_overlap", candidate.get("confidence"))
    out["confidence"] = p_supersedes
    if relation == "supersedes" and raw_supersedes >= SUPERSEDE_MIN_PROBABILITY:
        return out
    out["status"] = "dismissed"
    out["dismissed_by"] = _DISMISSED_BY
    return out


def _default_client():
    try:
        from app.services.inference.decisions import get_decision_client

        client = get_decision_client()
        return client if client.mode(RELATION_OPERATION) != "off" else None
    except Exception as e:  # config errors must never break ingest
        logger.warning("[SIG_RELATION] Decision model unavailable: %s", e)
        return None


async def judge_candidates(
    new_signal: Any,
    candidates: list[dict],
    standing_by_id: dict[str, Any],
    client: Any = None,
) -> list[dict]:
    """Annotate (and in ``on`` mode, gate) one decision's supersession candidates.

    Returns a new list in the same order. Candidates whose old signal is
    unknown, or whose judgment fails, come back unchanged.
    """
    if client is None:
        client = _default_client()
    if client is None or not candidates:
        return candidates
    mode = client.mode(RELATION_OPERATION)
    if mode == "off":
        return candidates

    from app.services.inference.decisions import DecisionUnavailable

    question = build_relation_question()
    new_names = {e.id: e.name for e in new_signal.entities}

    async def one(candidate: dict) -> dict:
        old = standing_by_id.get(candidate.get("old_signal_id", ""))
        if old is None:
            return candidate
        names = {**{e.id: e.name for e in old.entities}, **new_names}
        shared = sorted(names.get(eid, eid) for eid in candidate.get("matched_entities", []))
        try:
            result = await client.decide(
                build_relation_state(new_signal, old, shared),
                {"relation": question},
                operation=RELATION_OPERATION,
            )
            answer = result.choice("relation")
        except (DecisionUnavailable, ValueError, KeyError, TypeError) as e:
            logger.warning(
                "[SIG_RELATION] Judgment failed for %s -> %s, leaving candidate as is: %s",
                new_signal.id, candidate.get("old_signal_id"), e,
            )
            return candidate
        out = apply_relation(candidate, answer.choice, answer.probabilities, mode)
        logger.info(
            "[SIG_RELATION] %s %s -> %s: %s p=%.2f p_supersedes=%.2f overlap=%s -> %s",
            mode, new_signal.id[:8], old.id[:8], answer.choice,
            out["relation_probability"], answer.probabilities.get("supersedes", 0.0),
            candidate.get("confidence"), out.get("status", "pending"),
        )
        return out

    return list(await asyncio.gather(*(one(c) for c in candidates)))
