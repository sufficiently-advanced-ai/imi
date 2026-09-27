"""Entity linking — should this meeting be linked to this entity, and is it
the entity we think it is?

Every (meeting, entity) link the pipeline is about to write is put to the
decision model (TypeSafe Jev, operation ``entity_link``), for new AND existing
entities (meeting participants are exempt). Code only gathers evidence and
candidate options; Jev makes each judgment:

  mentioned (Noul)   — does the meeting actually talk about this entity?
                       Evidence: the extractor's quote plus transcript
                       excerpts around each occurrence of its names — or the
                       note that none was found. Catches entities the
                       extractor pulled from the existing-entities context
                       rather than the transcript.
  same      (Choice) — for links to an EXISTING person: that person, or a
                       different one with a similar name? Evidence: the
                       candidate's profile context and co-mentions. Catches
                       exact-name collisions ("Brian" the cohort peer vs
                       "Brian Vigilani" the customer) that never reach the
                       fuzzy-zone tiebreak.
  name      (Choice) — for people, when the transcript uses several forms of the name:
                       which is the fullest form referring to this entity?
                       Recovers names the extractor shortened.

Same contract as the other decision operations: batched, never raises,
``off``/``shadow``/``on``, asymmetric bars (unlinking or splitting is the
cheaper error; a wrong link is not recoverable from the graph alone).
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

LINK_OPERATION = "entity_link"
UNLINK_MIN_PROBABILITY = 0.75  # P(not mentioned) needed to drop a link
DIFFERENT_MIN_PROBABILITY = 0.70  # P(different entity) needed to detach
NAME_MIN_PROBABILITY = 0.70  # P(form) needed to adopt a fuller name
REASSIGN_MIN_PROBABILITY = 0.70  # P(participant/namesake) needed to move a link onto them
COMPAT_MIN_PROBABILITY = 0.50  # P(names compatible) for a participant reassignment
_WINDOW = 160
_MAX_WINDOWS = 3
_MAX_FORMS = 6
_DIFFERENT = "different"


@dataclass(frozen=True)
class LinkVerdict:
    action: str  # "keep" | "unlink" | "rename" | "split" | "reassign"
    name: str | None = None  # fuller name (rename/split) or reassign target's name
    p_mentioned: float = 1.0
    p_same: float | None = None
    name_probability: float | None = None
    target_id: str | None = None  # reassign to an existing entity (namesake)


KEEP = LinkVerdict("keep")


# --- evidence gathering (context only; never decides) ------------------------


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def transcript_windows(transcript: str, names: list[str]) -> list[str]:
    """Excerpts around occurrences of any of ``names`` (case/accent-folded,
    word-bounded), at most a few, non-overlapping."""
    folded = _fold(transcript)
    spans: list[tuple[int, int]] = []
    for name in sorted({n for n in names if n and n.strip()}, key=len, reverse=True):
        pattern = r"\b" + re.escape(_fold(name).strip()) + r"\b"
        for m in re.finditer(pattern, folded):
            start, end = max(0, m.start() - _WINDOW), min(len(transcript), m.end() + _WINDOW)
            if all(end <= s or start >= e for s, e in spans):
                spans.append((start, end))
            if len(spans) >= _MAX_WINDOWS:
                break
        if len(spans) >= _MAX_WINDOWS:
            break
    return ["…" + transcript[s:e].replace("\n", " ") + "…" for s, e in sorted(spans)]


def surface_forms(transcript: str, name: str) -> list[str]:
    """Name forms in the transcript that extend ``name`` with capitalised
    words ("Brian" -> "Brian Vigilani"), plus ``name`` itself if present."""
    tokens = (name or "").split()
    if not tokens:
        return []
    head = re.escape(tokens[0])
    tail = "".join(r"\s+" + re.escape(t) for t in tokens[1:])
    pattern = rf"\b{head}{tail}(?:\s+[A-Z][\w'’\-]+){{0,2}}\b"
    forms: list[str] = []
    for m in re.finditer(pattern, transcript or ""):
        form = m.group(0).strip()
        # Stop at a sentence-leading word glued on ("Brian Um" is not a name).
        words = form.split()
        while len(words) > len(tokens) and words[-1].lower() in _FILLERS:
            words.pop()
        form = " ".join(words)
        if form not in forms:
            forms.append(form)
    return forms[:_MAX_FORMS]


_FILLERS = {"um", "uh", "yeah", "so", "and", "but", "okay", "ok", "right", "well", "i", "the"}


# --- questions / verdicts ------------------------------------------------------


def build_link_questions(
    mention: dict,
    candidate: dict | None,
    forms: list[str],
    participants: list[str] | None = None,
    namesakes: list[dict] | None = None,
):
    from app.services.inference.decisions import Choice, Noul

    etype = mention["type"]
    questions: dict[str, Any] = {
        "mentioned": Noul(
            instructions=(
                f"Does this meeting actually talk about the {etype} named in the mention? "
                "Judge from the evidence quote and the transcript excerpts. The mention may "
                "have been heard under another form (heard_as: initials, a first name, a "
                "speech-to-text misspelling); those count as mentions. If no excerpt contains "
                "the name or its heard form and the evidence does not support it, answer no."
            ),
        ),
    }
    name_options: dict[str, str] = {}
    participant_options: dict[str, str] = {}
    # People share names all the time; organisations rarely do, and for a
    # well-known company an unfamiliar context was read as "a different
    # Anthropic". So the identity question — and any unlink it could cause —
    # is for people only; other types are unlinked only by "not mentioned".
    # The meeting's participants are offered as answers too: a nickname or
    # initials in the room ("ask AD some questions") usually mean one of them,
    # and the extractor mapped "AD" onto an unrelated existing "Aditya".
    namesake_options: dict[str, dict] = {}
    if etype == "person":
        taken = {(candidate or {}).get("name", "").casefold(), mention["name"].casefold()}
        participant_options = {
            f"p{i}": p
            for i, p in enumerate([p for p in (participants or []) if p.casefold() not in taken], start=1)
        }
        # Other people in the knowledge base who share the first name: the
        # resolver picks one; the right one may be another ("Brian" in a Foley
        # call is Brian Vigilani, not the cohort's Brian).
        namesake_options = {
            f"k{i}": c
            for i, c in enumerate(
                [c for c in (namesakes or []) if c.get("id") != (candidate or {}).get("id")][:4],
                start=1,
            )
        }
    if etype == "person" and (candidate is not None or participant_options or namesake_options):
        criteria = {}
        if candidate is not None:
            criteria["existing"] = _describe_candidate(candidate)
        for key, c in namesake_options.items():
            criteria[key] = _describe_candidate(c) + " (another person in the knowledge base)"
        for key, pname in participant_options.items():
            criteria[key] = f"{pname}, a participant in this meeting (nickname, initials or first name)"
            # A reassignment to a participant also needs the names to fit:
            # the identity pick alone moved "Wendy" onto "Scott Jennings",
            # the only listed participant of a group call.
            heard = mention.get("heard_as") or mention["name"]
            questions[f"compat_{key}"] = Noul(
                instructions=(
                    f"Judge only the names: could '{heard}' be a nickname, initials, a first "
                    f"name or another short form of '{pname}'?"
                ),
            )
        criteria[_DIFFERENT] = f"Someone else: a different {etype} who merely has a similar name"
        questions["same"] = Choice(
            instructions=(
                "Who does the mention refer to? A shared first name or similar name is not "
                "enough: compare who they are, what they do and who they appear with. People in "
                "the room are often called by nicknames, initials or first names. If the "
                "evidence cannot tell, choose someone else."
            ),
            criteria=criteria,
        )
    # Only people: an organisation's name extended by more capitalised words
    # is usually a different thing ("Anthropic" vs "Anthropic Academy"), while
    # a person's is their fuller name ("Brian" vs "Brian Vigilani").
    if len(forms) > 1 and etype == "person":
        name_options = {f"n{i}": f for i, f in enumerate(forms, start=1)}
        questions["name"] = Choice(
            instructions=(
                f"The transcript uses several forms of this {etype}'s name. Which is the "
                "fullest form that refers to this same entity?"
            ),
            criteria=dict(name_options),
        )
    return questions, name_options, participant_options, namesake_options


def _describe_candidate(candidate: dict) -> str:
    parts = [candidate.get("name", "")]
    ctx = candidate.get("context") or {}
    for key in ("title", "role", "company", "description", "profile_summary"):
        if ctx.get(key):
            parts.append(f"{key}: {ctx[key]}")
    if ctx.get("co_mentioned_with"):
        parts.append("usually mentioned with " + ", ".join(ctx["co_mentioned_with"]))
    return "; ".join(p for p in parts if p)[:700]


def build_link_state(mention: dict, windows: list[str], meeting: dict | None) -> dict:
    m = {"name": mention["name"], "type": mention["type"]}
    for key in ("heard_as", "evidence", "role", "aliases_heard"):
        if mention.get(key):
            m[key] = mention[key]
    state: dict[str, Any] = {
        "mention": m,
        "transcript_excerpts": windows or ["(no occurrence of the name found in the transcript)"],
    }
    if meeting:
        state["meeting"] = {k: v for k, v in meeting.items() if v}
    return state


def apply_link(
    name: str,
    p_mentioned: float,
    p_same: float | None,
    name_choice: str | None,
    name_probability: float,
    name_options: dict[str, str],
    participant: str | None = None,
    participant_probability: float = 0.0,
    target_id: str | None = None,
) -> LinkVerdict:
    if 1.0 - p_mentioned >= UNLINK_MIN_PROBABILITY:
        return LinkVerdict("unlink", None, p_mentioned, p_same)
    if participant and participant_probability >= REASSIGN_MIN_PROBABILITY:
        return LinkVerdict(
            "reassign", participant, p_mentioned, p_same, participant_probability,
            target_id=target_id,
        )
    fuller = name_options.get(name_choice or "")
    if not (fuller and fuller != name and name_probability >= NAME_MIN_PROBABILITY
            and len(fuller.split()) > len(name.split())):
        fuller = None
    if p_same is not None and 1.0 - p_same >= DIFFERENT_MIN_PROBABILITY:
        # Not the existing entity. With a fuller name it becomes its own
        # entity; without one it is left unlinked rather than misattributed.
        if fuller:
            return LinkVerdict("split", fuller, p_mentioned, p_same, name_probability)
        return LinkVerdict("unlink", None, p_mentioned, p_same)
    if fuller:
        return LinkVerdict("rename", fuller, p_mentioned, p_same, name_probability)
    return LinkVerdict("keep", None, p_mentioned, p_same)


def _default_client():
    try:
        from app.services.inference.decisions import get_decision_client

        client = get_decision_client()
        return client if client.mode(LINK_OPERATION) != "off" else None
    except Exception as e:  # config errors must never break ingest
        logger.warning("[LINK] Decision model unavailable: %s", e)
        return None


async def judge_links(
    links: list[dict],
    transcript: str,
    meeting: dict | None = None,
    client: Any = None,
) -> dict[str, LinkVerdict]:
    """Verdicts keyed by entity id for links to unlink/split/rename.

    links: [{"id", "type", "name", "names": [surface forms/aliases],
             "evidence"?, "role"?, "candidate"?: {name, context} for existing}]
    Empty in off/shadow mode (shadow logs), without a client, or on failure.
    """
    if client is None:
        client = _default_client()
    if client is None or not links:
        return {}
    mode = client.mode(LINK_OPERATION)
    if mode == "off":
        return {}

    from app.services.inference.decisions import DecisionUnavailable

    async def one(link: dict) -> tuple[str, LinkVerdict]:
        names = [link["name"], *(link.get("names") or [])]
        windows = transcript_windows(transcript, names)
        forms = surface_forms(transcript, link["name"])
        candidate = link.get("candidate")
        questions, name_options, participant_options, namesake_options = build_link_questions(
            link, candidate, forms, (meeting or {}).get("participants"), link.get("namesakes")
        )
        try:
            result = await client.decide(
                build_link_state(link, windows, meeting), questions, operation=LINK_OPERATION
            )
            p_mentioned = result.noul("mentioned")
            p_same = None
            participant, participant_p, target_id = None, 0.0, None
            if "same" in questions:
                same = result.choice("same")
                if candidate is not None:
                    p_same = same.probabilities.get("existing", 0.0)
                for key, pname in participant_options.items():
                    pp = same.probabilities.get(key, 0.0)
                    if result.noul(f"compat_{key}") < COMPAT_MIN_PROBABILITY:
                        continue  # the names cannot refer to each other
                    if pp > participant_p:
                        participant, participant_p, target_id = pname, pp, None
                for key, c in namesake_options.items():
                    pp = same.probabilities.get(key, 0.0)
                    if pp > participant_p:
                        participant, participant_p, target_id = c.get("name"), pp, c.get("id")
            name_choice, name_p = None, 0.0
            if "name" in questions:
                nm = result.choice("name")
                name_choice, name_p = nm.choice, nm.probabilities.get(nm.choice, 0.0)
        except (DecisionUnavailable, ValueError, KeyError, TypeError) as e:
            logger.warning("[LINK] Judgment failed for %s, keeping: %s", link["id"], e)
            return link["id"], KEEP
        verdict = apply_link(
            link["name"], p_mentioned, p_same, name_choice, name_p, name_options,
            participant, participant_p, target_id,
        )
        logger.info(
            "[LINK] %s %s %r: mentioned=%.2f%s%s%s -> %s%s",
            mode, link["id"], link["name"], p_mentioned,
            f" same={p_same:.2f}" if p_same is not None else "",
            f" reassign_to={participant!r}@{participant_p:.2f}" if participant else "",
            f" name={name_options.get(name_choice, '')!r}@{name_p:.2f}" if name_choice else "",
            verdict.action, f" ({verdict.name})" if verdict.name else "",
        )
        return link["id"], verdict

    unique = {link["id"]: link for link in links if link.get("id") and link.get("name")}
    results = await asyncio.gather(*(one(link) for link in unique.values()))
    if mode != "on":
        return {}
    return {eid: v for eid, v in results if v.action != "keep"}
