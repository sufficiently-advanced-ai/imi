"""Signal triage — what did the promoter actually extract?

The promoter's LLM pass proposes signals; hand-coded rules then decide what
they mean:

  type      whatever label the extractor gave (four allowed, the rest dropped)
  firmness  the extractor's self-reported confidence: < 0.7 tags a decision
            ``tier: candidate``, and a missing value defaults to 0.8
  owner     a case-insensitive name match where a bare first name matches any
            person with that first name, else a new person is minted
  client    if exactly one client appears across the meeting's signals, every
            signal gets it

Each of those is a judgment call. This module puts them to the decision model
(TypeSafe Jev, operation ``signal_promotion_triage``) as typed questions over
options the code gathered — the meeting's participants and people, its client
entities, the signal types — so the model can pick among them or answer
``none`` but never invent an owner or a client.

Same contract as the other decision operations: batched, never raises,
``off``/``shadow``/``on``. ``shadow`` (the default whenever an endpoint serves
the operation) records the verdicts in ``signal.metadata["triage"]`` next to
what the heuristics chose, and changes nothing else. ``on`` acts on confident
answers (bars below). Promoting a proposal to a firm decision (it then feeds
the constitution and supersession) needs more confidence than demoting one.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

TRIAGE_OPERATION = "signal_promotion_triage"
SIGNAL_TYPES = ("decision", "action_item", "key_point", "insight")

# Bars for ``on`` mode. Firmness is set from scripts/eval_signal_triage.py
# (3 runs, 2026-09-30): clear firm/proposed calls scored 1.00, while a stated
# fact recorded as a decision ("the budget is $40k") drew `proposed` at
# 0.78-0.81. Both firmness bars sit above that, promotion the higher of the
# two. The rest are provisional: the synthetic set has no near-bar misses for
# them. Recalibrate from shadow verdicts on a real KB before turning `on`.
RETYPE_MIN_PROBABILITY = 0.85
DROP_MIN_PROBABILITY = 0.85
FIRM_MIN_PROBABILITY = 0.95  # a proposal becomes a firm decision
TENTATIVE_MIN_PROBABILITY = 0.90  # a firm decision becomes a candidate
OWNER_MIN_PROBABILITY = 0.75
# Clearing an owner loses an assignment: in the eval a true "no owner" scored
# 1.00, while on a real KB the model cleared a plausible owner at 0.79.
OWNER_CLEAR_MIN_PROBABILITY = 0.90
CLIENT_MIN_PROBABILITY = 0.75

# One decide() per signal: the state is the meeting plus that one signal, so
# every question unambiguously refers to it. (Several signals in one state
# with per-signal question names scored near chance in the eval: the model
# cannot tell which signal "the SIGNAL" means.)
# A meeting up to this size is sent whole (the model's state budget is ~32k
# tokens). A longer transcript is cut into segments and each signal gets the
# segments that share the most of its words and entity names, in order.
MAX_BODY_CHARS = 60_000
SEGMENT_CHARS = 3_000
MAX_SEGMENTS = 8
_STOPWORDS = frozenset(
    "about after again also because been before being between could does doing during "
    "every from have having into just like make more most much other over should since "
    "some such than that their them then there these they this those through under until "
    "very what when where which while will with would your".split()
)

_TYPE_CRITERIA = {
    "decision": "A choice the group made or proposed about what to do or how to do it.",
    "action_item": "A task someone is to carry out: a concrete next step with an owner or implied owner.",
    "key_point": "An important fact, status, or piece of information stated in the meeting.",
    "insight": "An interpretation, lesson, risk, or observation drawn from what was discussed.",
    "none": "Not a substantive signal: small talk, logistics of this meeting, a restated "
            "agenda item, or something the meeting does not support.",
}


def _type_question():
    from app.services.inference.decisions import Choice

    return Choice(
        instructions=(
            "Judge the SIGNAL against the meeting text. What kind of signal is it? "
            "Judge what the meeting says, not how the signal is worded."
        ),
        criteria=dict(_TYPE_CRITERIA),
    )


def _firmness_question():
    from app.services.inference.decisions import Choice

    return Choice(
        instructions=(
            "Treat the SIGNAL as a possible decision. In the meeting text, was it decided, "
            "or only raised? Count it as firm only if the people who could decide agreed "
            "to it or stated it as settled; a suggestion, an option under discussion, or a "
            "plan pending someone else's approval is proposed."
        ),
        criteria={
            "firm": "Decided: the group committed to it and is expected to act on it.",
            "proposed": "Raised, suggested, or leaning toward, but not committed.",
        },
    )


def _owner_question(options: dict[str, str]):
    from app.services.inference.decisions import Choice

    return Choice(
        instructions=(
            "Treat the SIGNAL as a task. Who in the meeting took it on or was assigned "
            "it? Pick the person the meeting text names or clearly implies. Pick none if "
            "no one is named, the owner is an unnamed role, or it is someone not listed."
        ),
        criteria={**options, "none": "No listed person owns it."},
    )


def _client_question(options: dict[str, str]):
    from app.services.inference.decisions import Choice

    return Choice(
        instructions=(
            "Which client or account is the SIGNAL about? Pick the one it concerns, "
            "even if the meeting also discusses others. Pick none if it is internal or "
            "concerns no listed client."
        ),
        criteria={**options, "none": "It concerns no listed client."},
    )


def _meeting_state(observation: Any, participants: list[str]) -> dict:
    return {
        "title": observation.title,
        "date": observation.observed_at.date().isoformat() if observation.observed_at else None,
        "participants": participants,
    }


def _terms(signal: Any) -> set[str]:
    words = re.findall(r"[a-z][a-z'\-]{3,}", (signal.content or "").lower())
    terms = {w for w in words if w not in _STOPWORDS}
    for ref in getattr(signal, "entities", None) or []:
        terms.update(w for w in ref.name.lower().split() if len(w) > 2)
    return terms


def meeting_text(body: str, signal: Any) -> str:
    """What the model reads for ``signal``: the whole meeting when it fits,
    else the transcript segments that best match the signal's words and
    entity names, kept in meeting order. Evidence gathering only."""
    if len(body) <= MAX_BODY_CHARS:
        return body
    segments = [body[i:i + SEGMENT_CHARS] for i in range(0, len(body), SEGMENT_CHARS)]
    terms = _terms(signal)
    scored = []
    for idx, seg in enumerate(segments):
        low = seg.lower()
        hits = sum(1 for t in terms if t in low)
        if hits:
            scored.append((hits, idx))
    best = sorted(idx for _, idx in sorted(scored, reverse=True)[:MAX_SEGMENTS])
    if not best:
        best = list(range(min(MAX_SEGMENTS, len(segments))))
    return "\n[...]\n".join(segments[i] for i in best) + "\n[excerpts of a longer meeting]"


def _person_options(
    entity_refs: list[Any], participants: list[str], signals: list[Any], body: str
) -> list[str]:
    """Every name an owner could be: participants, the people the meeting
    mentions and the people its signals reference. A name the heuristic
    resolved as an owner joins only if the meeting text says it verbatim
    ("Sam" does; "Initech IT team", minted from "Initech's IT team", does not)."""
    candidates = [*participants, *(r.name for r in entity_refs if r.type == "person")]
    for sig in signals:
        candidates += [r.name for r in getattr(sig, "entities", None) or [] if r.type == "person"]
    text = (body or "").lower()
    for sig in signals:
        owner = (sig.owner.name if sig.owner else "") or ""
        if owner.strip() and re.search(rf"\b{re.escape(owner.strip().lower())}\b", text):
            candidates.append(owner)
    names: list[str] = []
    seen: set[str] = set()
    for name in candidates:
        key = (name or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            names.append(name.strip())
    return names


def _key(prefix: str, i: int) -> str:
    return f"{prefix}{i}"


def build_questions(people: list[str], clients: list[Any]) -> dict:
    """Every question for one signal. Firmness and owner are asked whatever
    the extracted type, so a retyped signal still gets them; they are applied
    only to the type the signal ends up with. Owner needs people and client
    needs client entities to choose from."""
    questions: dict = {"type": _type_question(), "firmness": _firmness_question()}
    if people:
        questions["owner"] = _owner_question({_key("p", i): name for i, name in enumerate(people)})
    if clients:
        questions["client"] = _client_question({_key("c", i): ref.name for i, ref in enumerate(clients)})
    return questions


def build_state(meeting: dict, body: str, signal: Any) -> dict:
    return {
        "meeting": {**meeting, "text": meeting_text(body, signal)},
        "signal": {"type": signal.type, "content": signal.content},
    }


def _answer(result: Any, name: str) -> dict | None:
    if name not in result.answers:
        return None
    a = result.choice(name)
    return {
        "choice": a.choice,
        "p": round(float(a.probabilities.get(a.choice, 0.0)), 3),
        "probabilities": {k: round(float(v), 3) for k, v in a.probabilities.items()},
    }


def verdict_for(result: Any, people: list[str], clients: list[Any]) -> dict:
    """The model's answers for one signal with option ids mapped back to
    names and entity ids."""
    verdict: dict = {}
    if t := _answer(result, "type"):
        verdict["type"] = t
    if f := _answer(result, "firmness"):
        verdict["firmness"] = f
    if o := _answer(result, "owner"):
        choice = o["choice"]
        o["name"] = people[int(choice[1:])] if choice.startswith("p") else None
        verdict["owner"] = o
    if c := _answer(result, "client"):
        choice = c["choice"]
        c["client_id"] = clients[int(choice[1:])].id if choice.startswith("c") else None
        verdict["client"] = c
    return verdict


def _raw(verdict: dict, field: str, option: str) -> float:
    return float(verdict.get(field, {}).get("probabilities", {}).get(option, 0.0))


def apply_verdict(
    signal: Any,
    verdict: dict,
    mode: str,
    resolve_person: Callable[[str], Any],
) -> bool:
    """Record the verdict on ``signal.metadata["triage"]`` and, in ``on``
    mode, act on confident answers. Returns False when ``on`` mode drops the
    signal as not substantive.

    Signals a reviewer has already actioned are annotated, never re-decided.
    Gates compare raw probabilities; only the stored copy is rounded.
    """
    heuristic = {
        "type": signal.type,
        "tier": signal.metadata.get("tier", "confirmed") if signal.type == "decision" else None,
        "owner": signal.owner.id if signal.owner else None,
        "client_id": signal.client_id,
    }
    signal.metadata["triage"] = {"mode": mode, "heuristic": heuristic, **verdict}
    if mode != "on" or signal.review_status != "pending":
        return True

    applied: list[str] = []
    t = verdict.get("type", {}).get("choice")
    if t == "none" and _raw(verdict, "type", "none") >= DROP_MIN_PROBABILITY:
        return False
    if t in SIGNAL_TYPES and t != signal.type and _raw(verdict, "type", t) >= RETYPE_MIN_PROBABILITY:
        signal.type = t
        applied.append("type")
        if t != "decision":
            signal.metadata.pop("tier", None)
        if t == "action_item" and signal.status is None:
            signal.status = "open"
        elif t != "action_item":
            signal.status = None
            signal.owner = None

    if signal.type == "decision" and "firmness" in verdict:
        if _raw(verdict, "firmness", "proposed") >= TENTATIVE_MIN_PROBABILITY:
            if signal.metadata.get("tier") != "candidate":
                signal.metadata["tier"] = "candidate"
                applied.append("firmness")
        elif _raw(verdict, "firmness", "firm") >= FIRM_MIN_PROBABILITY:
            if signal.metadata.pop("tier", None) is not None:
                applied.append("firmness")

    if signal.type == "action_item" and (owner := verdict.get("owner")):
        choice = owner["choice"]
        bar = OWNER_CLEAR_MIN_PROBABILITY if choice == "none" else OWNER_MIN_PROBABILITY
        if _raw(verdict, "owner", choice) >= bar:
            new_owner = resolve_person(owner["name"]) if owner.get("name") else None
            if (new_owner.id if new_owner else None) != heuristic["owner"]:
                signal.owner = new_owner
                applied.append("owner")

    if client := verdict.get("client"):
        if _raw(verdict, "client", client["choice"]) >= CLIENT_MIN_PROBABILITY:
            if client["client_id"] != signal.client_id:
                signal.client_id = client["client_id"]
                applied.append("client")

    signal.metadata["triage"]["applied"] = applied
    return True


def _default_client():
    try:
        from app.services.inference.decisions import get_decision_client

        client = get_decision_client()
        return client if client.mode(TRIAGE_OPERATION) != "off" else None
    except Exception as e:  # config errors must never break ingest
        logger.warning("[TRIAGE] Decision model unavailable: %s", e)
        return None


async def triage_signals(
    signals: list[Any],
    observation: Any,
    entity_refs: list[Any],
    client_type_ids: set[str],
    resolve_person: Callable[[str], Any],
    client: Any = None,
) -> list[Any]:
    """Judge a meeting's freshly promoted signals. Returns the signals to
    keep (all of them unless ``on`` mode drops some), annotated in place.

    Never raises: a failed call leaves its signal exactly as the heuristics
    left them.
    """
    if not signals:
        return signals
    if client is None:
        client = _default_client()
    if client is None:
        return signals
    mode = client.mode(TRIAGE_OPERATION)
    if mode == "off":
        return signals

    from app.services.inference.decisions import DecisionUnavailable

    participants = [p for p in (observation.participants or []) if isinstance(p, str) and p.strip()]
    people = _person_options(entity_refs, participants, signals, observation.content or "")[:254]
    clients = [r for r in entity_refs if r.type in client_type_ids][:254]
    meeting = _meeting_state(observation, participants)
    questions = build_questions(people, clients)
    dropped: set[int] = set()

    async def one(i: int, sig: Any) -> None:
        try:
            result = await client.decide(
                build_state(meeting, observation.content or "", sig), questions,
                operation=TRIAGE_OPERATION,
            )
            verdict = verdict_for(result, people, clients)
        except (DecisionUnavailable, ValueError, KeyError, TypeError, IndexError) as e:
            logger.warning(
                "[TRIAGE] Judgment failed for %s of %s, keeping heuristics: %s",
                sig.id[:8], observation.external_id, e,
            )
            return
        # apply_verdict edits in place; a failure part-way must not leave a
        # half-applied signal or abort the rest of the batch.
        snapshot = sig.model_copy(deep=True)
        try:
            keep = apply_verdict(sig, verdict, mode, resolve_person)
        except Exception as e:
            for field in type(sig).model_fields:
                setattr(sig, field, getattr(snapshot, field))
            logger.warning(
                "[TRIAGE] Applying verdict failed for %s of %s, keeping heuristics: %s",
                sig.id[:8], observation.external_id, e,
            )
            return
        if not keep:
            dropped.add(i)
        h = sig.metadata["triage"]["heuristic"]
        logger.info(
            "[TRIAGE] %s %s type %s->%s firm=%s owner %s->%s client %s->%s%s",
            mode, sig.id[:8], h["type"], verdict.get("type", {}).get("choice"),
            verdict.get("firmness", {}).get("choice"),
            h["owner"], verdict.get("owner", {}).get("name"),
            h["client_id"], verdict.get("client", {}).get("client_id"),
            " DROP" if not keep else "",
        )

    await asyncio.gather(*(one(i, sig) for i, sig in enumerate(signals)))
    return [sig for i, sig in enumerate(signals) if i not in dropped]
