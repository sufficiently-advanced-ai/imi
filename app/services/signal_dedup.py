"""Signal dedup at ingest — keep the richest statement of a fact, link the rest.

Meetings on an active engagement keep retelling the same facts, each time with
a little more (or different) detail: a team size, what a product does, who
gets which access. Verbatim repeats are rare; what piles up is the same fact
at several levels of completeness, which crowds the feed and recall.

Candidates are cheap and local: for each new signal, the nearest standing
signals of the same type (vector index) at or above ``MIN_SIMILARITY`` whose
meetings fall within ``WINDOW_DAYS``, plus earlier signals of the same batch.
Embeddings cannot tell "same fact, more detail" from "same topic, different
claim" (both land around 0.8), so each pair is put to the decision model
(TypeSafe Jev, operation ``signal_duplicate``) as one Choice between EARLIER
and LATER:

  same            — same information; hide LATER behind EARLIER
  earlier_richer  — same fact, EARLIER says more; hide LATER behind EARLIER
  later_richer    — same fact, LATER says more; hide EARLIER behind LATER
  overlap         — partly the same, each adds something; keep both, link them
  different       — nothing to do

Nothing is deleted. The hidden signal keeps its place in its meeting's file
with ``metadata.duplicate_of`` naming the kept one, so provenance, uuid5 IDs
and idempotent re-ingest are untouched; clearing the key undoes it. An EARLIER
signal is only hidden while unreviewed and not instruction-grade — a confirmed
record is never retired in favour of a fresh extraction (the new one is hidden
behind it instead). Overlaps are recorded as ``metadata.related_signals`` on
the new signal. Read paths hide duplicates by default and report corroborating
meetings on the kept signal (``resolve_hidden`` / ``corroborations``).

Same contract as the other decision operations: batched, never raises,
``off``/``shadow``/``on``. Shadow records each verdict under
``metadata.duplicate_check`` on the new signal and changes nothing else.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.utils.event_time import signal_event_time

logger = logging.getLogger(__name__)

DEDUP_OPERATION = "signal_duplicate"
MIN_SIMILARITY = 0.78
HIDE_MIN_PROBABILITY = 0.50  # p(chosen label) to hide one side of a same-fact pair
LINK_MIN_PROBABILITY = 0.50  # p(overlap) to record a related link
# A "richer" verdict hides the other side only if the kept statement omits no
# claim of it. Jev reads "richer" loosely (a fuller description that drops a
# qualifier still scores later_richer ~0.8), so the omission is asked in the
# same request: truly richer fixture pairs scored 0.06-0.38, a fuller
# description that drops "maintains" 0.57-0.69, real near-duplicates 0.88+
# (eval_signal_dedup.py, 3 runs, 2026-09-29).
OMIT_MAX_PROBABILITY = 0.50
WINDOW_DAYS = 30
MAX_CANDIDATES = 3

# (text, signal_type) -> [(signal_id, cosine similarity)], best first
SimilarFn = Callable[[str, str], list[tuple[str, float]]]


@dataclass(frozen=True)
class DuplicateCandidate:
    new: Any  # the signal being ingested
    old: Any  # a standing signal, or an earlier one of the same batch
    similarity: float

    @property
    def new_is_later(self) -> bool:
        """Whether the ingested signal is the LATER one in event time (ADR-004).

        Backfilled content can be older than what is already standing, so
        EARLIER/LATER follow ``signal_event_time``, not ingest order. Unknown
        or equal times fall back to ingest order.
        """
        t_new, t_old = signal_event_time(self.new), signal_event_time(self.old)
        if t_new is None or t_old is None or t_new == t_old:
            return True
        return t_new > t_old

    @property
    def earlier(self) -> Any:
        return self.old if self.new_is_later else self.new

    @property
    def later(self) -> Any:
        return self.new if self.new_is_later else self.old


@dataclass
class DedupOutcome:
    hidden_new: int = 0
    linked: int = 0
    # EARLIER signals newly hidden behind a richer new one. The caller persists
    # the standing ones; batch-internal ones ride along with the batch.
    hidden_old: list[Any] = field(default_factory=list)


def _within_window(a: Any, b: Any, days: int) -> bool:
    ta, tb = signal_event_time(a), signal_event_time(b)
    if ta is None or tb is None:
        return True  # undated: similarity and the model decide
    return abs((ta - tb).total_seconds()) <= days * 86400


def _eligible_original(sig: Any) -> bool:
    return not (
        (sig.metadata or {}).get("duplicate_of")
        or sig.provenance_status in ("superseded", "disputed")
        or sig.review_status == "rejected"
    )


def _may_hide(sig: Any) -> bool:
    """A standing signal may only be hidden while nobody has vouched for it."""
    return sig.review_status == "pending" and not sig.can_use_as_instruction


def find_duplicate_candidates(
    new_signals: list[Any],
    similar: SimilarFn,
    standing_by_id: dict[str, Any],
    *,
    min_similarity: float = MIN_SIMILARITY,
    window_days: int = WINDOW_DAYS,
    max_candidates: int = MAX_CANDIDATES,
    batch_similarity: Callable[[Any, Any], float] | None = None,
) -> list[DuplicateCandidate]:
    """Pairs worth asking about, best first per new signal.

    ``similar`` searches the standing index; ``batch_similarity`` (optional)
    scores two signals from the new batch, which are not indexed yet — an
    earlier position in the batch counts as EARLIER.
    """
    out: list[DuplicateCandidate] = []
    new_ids = {s.id for s in new_signals}
    for i, new in enumerate(new_signals):
        if not new.content or not new.content.strip():
            continue
        found: list[DuplicateCandidate] = []
        for old_id, score in similar(new.content, new.type):
            old = standing_by_id.get(old_id)
            if (old is None or old_id in new_ids or score < min_similarity
                    or old.type != new.type or not _eligible_original(old)
                    or not _within_window(new, old, window_days)):
                continue
            found.append(DuplicateCandidate(new, old, round(float(score), 4)))
        if batch_similarity is not None:
            for old in new_signals[:i]:
                if old.type != new.type or not old.content:
                    continue
                score = batch_similarity(new, old)
                if score >= min_similarity:
                    found.append(DuplicateCandidate(new, old, round(float(score), 4)))
        found.sort(key=lambda c: c.similarity, reverse=True)
        out.extend(found[:max_candidates])
    return out


def build_duplicate_question():
    from app.services.inference.decisions import Choice

    return Choice(
        instructions=(
            "Two statements extracted from meeting notes, EARLIER and LATER. Compare the "
            "information each one carries."
        ),
        criteria={
            "same": "They carry the same information (reworded or restated); neither adds "
                    "anything material.",
            "later_richer": "Same fact, decision, or commitment, and the LATER statement keeps "
                            "every claim of the EARLIER one (each role, status, number, owner "
                            "and qualifier) and adds more. If the LATER one leaves out any "
                            "claim of the EARLIER one, this is overlap.",
            "earlier_richer": "Same fact, decision, or commitment, and the EARLIER statement "
                              "keeps every claim of the LATER one (each role, status, number, "
                              "owner and qualifier) and adds more. If the EARLIER one leaves "
                              "out any claim of the LATER one, this is overlap.",
            "overlap": "Same topic and partly the same claims, but each says something the "
                       "other does not, including when one merely omits a claim the other "
                       "makes.",
            "different": "Different facts, tasks, or claims, even if the topic or people are "
                         "the same.",
        },
    )


def build_omission_questions() -> dict:
    from app.services.inference.decisions import Noul

    def omits(kept: str, other: str):
        return Noul(instructions=(
            f"Does the {kept} statement leave out any claim made in the {other} statement "
            "(a role, status, number, owner, qualifier, or fact)? Answer yes if at least one "
            f"claim of the {other} statement is missing from the {kept} one."
        ))

    return {"later_omits": omits("LATER", "EARLIER"), "earlier_omits": omits("EARLIER", "LATER")}


def _side(sig: Any) -> dict:
    return {
        "type": sig.type,
        "statement": sig.content,
        "meeting": sig.source_meeting_title or sig.source_meeting_id,
        "date": (sig.source_timestamp or "")[:10],
    }


def build_duplicate_state(candidate: DuplicateCandidate) -> dict:
    return {"earlier": _side(candidate.earlier), "later": _side(candidate.later)}


def decide_action(
    relation: str, p: float, candidate: DuplicateCandidate, p_kept_omits: float = 0.0
) -> str:
    """hide_new | hide_old | link | none for one judged pair.

    ``same`` always hides the incoming signal. A "richer" verdict hides the
    less complete side (by event time) only when the kept side omits none of
    its claims (``p_kept_omits`` < OMIT_MAX_PROBABILITY); otherwise the pair
    is linked. A standing signal someone reviewed or confirmed is never
    retired behind a fresh extraction; the incoming one is hidden instead.
    """
    if p >= HIDE_MIN_PROBABILITY and relation == "same":
        return "hide_new"
    if p >= HIDE_MIN_PROBABILITY and relation in ("earlier_richer", "later_richer"):
        if p_kept_omits >= OMIT_MAX_PROBABILITY:
            return "link"
        hide_later = relation == "earlier_richer"
        hide_new = hide_later == candidate.new_is_later
        if hide_new or not _may_hide(candidate.old):
            return "hide_new"
        return "hide_old"
    if relation == "overlap" and p >= LINK_MIN_PROBABILITY:
        return "link"
    return "none"


def _default_client():
    try:
        from app.services.inference.decisions import get_decision_client

        client = get_decision_client()
        return client if client.mode(DEDUP_OPERATION) != "off" else None
    except Exception as e:  # config errors must never break ingest
        logger.warning("[DEDUP] Decision model unavailable: %s", e)
        return None


_ACTION_RANK = {"hide_new": 3, "hide_old": 2, "link": 1, "none": 0}


async def judge_duplicates(candidates: list[DuplicateCandidate], client: Any = None) -> DedupOutcome:
    """Judge each pair and annotate signals in place.

    Per new signal, at most one hide (the strongest: hiding the new signal
    wins over hiding an old one, then higher p) is applied; every ``overlap``
    becomes a link. Standing signals hidden behind a new one are returned in
    ``hidden_old`` for the caller to persist.
    """
    outcome = DedupOutcome()
    if client is None:
        client = _default_client()
    if client is None or not candidates:
        return outcome
    mode = client.mode(DEDUP_OPERATION)
    if mode == "off":
        return outcome

    from app.services.inference.decisions import DecisionUnavailable

    questions = {"relation": build_duplicate_question(), **build_omission_questions()}

    async def one(c: DuplicateCandidate) -> tuple[DuplicateCandidate, str, float, float] | None:
        try:
            result = await client.decide(
                build_duplicate_state(c), questions, operation=DEDUP_OPERATION
            )
            answer = result.choice("relation")
            # The kept side of a "richer" verdict is the richer one.
            kept = "earlier_omits" if answer.choice == "earlier_richer" else "later_omits"
            return (c, answer.choice, float(answer.probabilities.get(answer.choice, 0.0)),
                    float(result.noul(kept)))
        except (DecisionUnavailable, ValueError, KeyError, TypeError) as e:
            logger.warning("[DEDUP] Judgment failed for %s -> %s: %s", c.new.id, c.old.id, e)
            return None

    judged = [j for j in await asyncio.gather(*(one(c) for c in candidates)) if j]

    by_new: dict[str, list[tuple[DuplicateCandidate, str, float, str]]] = {}
    omits: dict[tuple[str, str], float] = {}
    for c, relation, p, p_omits in judged:
        action = decide_action(relation, p, c, p_omits)
        by_new.setdefault(c.new.id, []).append((c, relation, p, action))
        omits[(c.new.id, c.old.id)] = p_omits
        logger.info(
            "[DEDUP] %s %s %s -> %s: sim=%.2f %s p=%.2f omits=%.2f -> %s",
            mode, c.new.type, c.new.id[:8], c.old.id[:8], c.similarity, relation, p, p_omits,
            action,
        )

    hidden_old_ids: set[str] = set()
    for rows in by_new.values():
        new = rows[0][0].new
        new.metadata["duplicate_check"] = [
            {"of": c.old.id, "relation": r, "p": round(p, 3),
             "p_kept_omits": round(omits[(c.new.id, c.old.id)], 3),
             "similarity": c.similarity, "action": a, "mode": mode}
            for c, r, p, a in rows
        ]
        if mode != "on":
            continue
        links = [{"id": c.old.id, "relation": r, "p": round(p, 3)}
                 for c, r, p, a in rows if a == "link"]
        if links:
            new.metadata["related_signals"] = links
            outcome.linked += len(links)
        hides = [row for row in rows if row[3] in ("hide_new", "hide_old")]
        if not hides:
            continue
        c, relation, p, action = max(hides, key=lambda row: (_ACTION_RANK[row[3]], row[2]))
        if action == "hide_new" and not new.metadata.get("duplicate_of"):
            new.metadata["duplicate_of"] = c.old.id
            new.metadata["duplicate_relation"] = relation
            outcome.hidden_new += 1
        elif (action == "hide_old" and c.old.id not in hidden_old_ids
              # already shown under another signal, or would point back at us
              and not c.old.metadata.get("duplicate_of")
              and new.metadata.get("duplicate_of") != c.old.id):
            c.old.metadata["duplicate_of"] = new.id
            c.old.metadata["duplicate_relation"] = relation
            hidden_old_ids.add(c.old.id)
            outcome.hidden_old.append(c.old)
    return outcome


def resolve_hidden(signals: list[Any]) -> dict[str, str]:
    """hidden signal id -> id of the signal it is shown under.

    Follows chains (A hidden behind B hidden behind C -> C). A pointer to a
    signal that does not exist, or a loop, hides nothing — a dangling
    ``duplicate_of`` must never make a signal disappear.
    """
    ids = {s.id for s in signals}
    pointer = {s.id: (s.metadata or {}).get("duplicate_of") for s in signals}

    def root(sid: str) -> str | None:
        seen = {sid}
        cur = pointer.get(sid)
        while cur in ids and cur not in seen:
            seen.add(cur)
            nxt = pointer.get(cur)
            if nxt not in ids:
                return cur
            cur = nxt
        return None

    return {sid: r for sid in ids if (r := root(sid)) is not None}


def lazy_signal_exists() -> Callable[[str], bool]:
    """Existence check over the signal store, loaded on first use only.

    Read paths that filter on vector metadata use it so a ``duplicate_of``
    pointing at a deleted signal hides nothing (matches ``resolve_hidden``).
    """
    ids: set[str] | None = None

    def exists(signal_id: str) -> bool:
        nonlocal ids
        if ids is None:
            from app.services.signal_store import signal_store

            ids = {s.id for b in signal_store.load_all() for s in b.signals}
        return signal_id in ids

    return exists


def corroborations(signals: list[Any], hidden: dict[str, str] | None = None) -> dict[str, list[dict]]:
    """kept signal id -> the hidden signals shown under it (meeting provenance)."""
    hidden = resolve_hidden(signals) if hidden is None else hidden
    by_id = {s.id: s for s in signals}
    out: dict[str, list[dict]] = {}
    for sid, kept in hidden.items():
        s = by_id[sid]
        out.setdefault(kept, []).append({
            "signal_id": s.id, "content": s.content, "meeting_id": s.source_meeting_id,
            "meeting_title": s.source_meeting_title, "timestamp": s.source_timestamp,
        })
    return out
