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
answers; its bars are provisional until ``scripts/eval_signal_triage.py`` and
shadow verdicts from a real KB calibrate them. They are asymmetric like the
other ops: promoting a proposal to a firm decision (it then feeds the
constitution and supersession) needs more confidence than demoting one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

TRIAGE_OPERATION = "signal_promotion_triage"
SIGNAL_TYPES = ("decision", "action_item", "key_point", "insight")

# Provisional bars (uncalibrated) — used only in ``on`` mode.
RETYPE_MIN_PROBABILITY = 0.85
DROP_MIN_PROBABILITY = 0.85
FIRM_MIN_PROBABILITY = 0.85  # a proposal becomes a firm decision
TENTATIVE_MIN_PROBABILITY = 0.70  # a firm decision becomes a candidate
OWNER_MIN_PROBABILITY = 0.75
CLIENT_MIN_PROBABILITY = 0.75

# Signals per decide() call. Each signal asks up to four questions, and every
# call carries the meeting text, so chunks keep a busy meeting's request small.
SIGNALS_PER_CALL = 8
MAX_BODY_CHARS = 40_000

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
            "The SIGNAL was recorded as a decision. In the meeting text, was it decided, "
            "or only raised? Count it as firm only if the people who could decide agreed "
            "to it; a suggestion, an option under discussion, or a plan pending someone "
            "else's approval is proposed."
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
            "The SIGNAL is an action item. Who in the meeting took it on or was assigned "
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
    body = observation.content or ""
    clipped = len(body) > MAX_BODY_CHARS
    return {
        "title": observation.title,
        "date": observation.observed_at.date().isoformat() if observation.observed_at else None,
        "participants": participants,
        "text": body[:MAX_BODY_CHARS] + ("\n[... clipped]" if clipped else ""),
    }


def _person_options(signals: list[Any], entity_refs: list[Any], participants: list[str]) -> list[str]:
    """Every name an owner could be: participants, people the meeting
    mentions, and the owners the extractor already named. Deduped, ordered."""
    names: list[str] = []
    seen: set[str] = set()
    candidates = list(participants)
    candidates += [r.name for r in entity_refs if r.type == "person"]
    candidates += [s.owner.name for s in signals if s.owner is not None]
    for name in candidates:
        key = (name or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            names.append(name.strip())
    return names


def _key(prefix: str, i: int) -> str:
    return f"{prefix}{i}"


def build_questions(
    signals: list[tuple[int, Any]],
    people: list[str],
    clients: list[Any],
) -> dict:
    """Questions for one chunk: ``s<i>_type`` for every signal, ``s<i>_firm``
    for decisions, ``s<i>_owner`` for action items (when there are people to
    choose from), ``s<i>_client`` when the meeting has client entities."""
    person_opts = {_key("p", i): name for i, name in enumerate(people)}
    client_opts = {_key("c", i): ref.name for i, ref in enumerate(clients)}
    questions: dict = {}
    for i, sig in signals:
        questions[f"s{i}_type"] = _type_question()
        if sig.type == "decision":
            questions[f"s{i}_firm"] = _firmness_question()
        if sig.type == "action_item" and person_opts:
            questions[f"s{i}_owner"] = _owner_question(person_opts)
        if client_opts:
            questions[f"s{i}_client"] = _client_question(client_opts)
    return questions


def build_state(meeting: dict, signals: list[tuple[int, Any]]) -> dict:
    return {
        "meeting": meeting,
        "signals": {f"s{i}": {"type": sig.type, "content": sig.content} for i, sig in signals},
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


def verdict_for(result: Any, i: int, people: list[str], clients: list[Any]) -> dict:
    """The model's answers for signal ``i`` with option ids mapped back to
    names and entity ids."""
    verdict: dict = {}
    if t := _answer(result, f"s{i}_type"):
        verdict["type"] = t
    if f := _answer(result, f"s{i}_firm"):
        verdict["firmness"] = f
    if o := _answer(result, f"s{i}_owner"):
        choice = o["choice"]
        o["name"] = people[int(choice[1:])] if choice.startswith("p") else None
        verdict["owner"] = o
    if c := _answer(result, f"s{i}_client"):
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
        if _raw(verdict, "owner", choice) >= OWNER_MIN_PROBABILITY:
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

    Never raises: a failed chunk leaves its signals exactly as the heuristics
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
    people = _person_options(signals, entity_refs, participants)[:254]
    clients = [r for r in entity_refs if r.type in client_type_ids][:254]
    meeting = _meeting_state(observation, participants)
    indexed = list(enumerate(signals))
    chunks = [indexed[i:i + SIGNALS_PER_CALL] for i in range(0, len(indexed), SIGNALS_PER_CALL)]
    dropped: set[int] = set()

    async def one(chunk: list[tuple[int, Any]]) -> None:
        try:
            result = await client.decide(
                build_state(meeting, chunk),
                build_questions(chunk, people, clients),
                operation=TRIAGE_OPERATION,
            )
            verdicts = [(i, sig, verdict_for(result, i, people, clients)) for i, sig in chunk]
        except (DecisionUnavailable, ValueError, KeyError, TypeError, IndexError) as e:
            logger.warning(
                "[TRIAGE] Judgment failed for %d signals of %s, keeping heuristics: %s",
                len(chunk), observation.external_id, e,
            )
            return
        for i, sig, verdict in verdicts:
            keep = apply_verdict(sig, verdict, mode, resolve_person)
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

    await asyncio.gather(*(one(c) for c in chunks))
    return [sig for i, sig in indexed if i not in dropped]
