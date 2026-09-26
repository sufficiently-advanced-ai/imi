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
  same      (Choice) — for links to an EXISTING entity: that entity, or a
                       different one with a similar name? Evidence: the
                       candidate's profile context and co-mentions. Catches
                       exact-name collisions ("Brian" the cohort peer vs
                       "Brian Vigilani" the customer) that never reach the
                       fuzzy-zone tiebreak.
  name      (Choice) — when the transcript uses several forms of the name:
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
_WINDOW = 160
_MAX_WINDOWS = 3
_MAX_FORMS = 6
_DIFFERENT = "different"


@dataclass(frozen=True)
class LinkVerdict:
    action: str  # "keep" | "unlink" | "rename" | "split"
    name: str | None = None  # fuller name for rename/split
    p_mentioned: float = 1.0
    p_same: float | None = None
    name_probability: float | None = None


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


def build_link_questions(mention: dict, candidate: dict | None, forms: list[str]):
    from app.services.inference.decisions import Choice, Noul

    etype = mention["type"]
    questions: dict[str, Any] = {
        "mentioned": Noul(
            instructions=(
                f"Does this meeting actually talk about the {etype} named in the mention? "
                "Judge from the evidence quote and the transcript excerpts. Speech-to-text "
                "misspellings of the name count as mentions. If no excerpt contains the name "
                "and the evidence does not support it, answer no."
            ),
        ),
    }
    name_options: dict[str, str] = {}
    if candidate is not None:
        questions["same"] = Choice(
            instructions=(
                f"The mention was matched to an existing {etype} in the knowledge base. Is it "
                f"the same real-world {etype}? A shared first name or similar name is not "
                "enough: compare who they are, what they do and who they appear with. If the "
                "evidence cannot tell them apart, choose different."
            ),
            criteria={
                "existing": _describe_candidate(candidate),
                _DIFFERENT: f"A different {etype} who merely has a similar name",
            },
        )
    if len(forms) > 1:
        name_options = {f"n{i}": f for i, f in enumerate(forms, start=1)}
        questions["name"] = Choice(
            instructions=(
                f"The transcript uses several forms of this {etype}'s name. Which is the "
                "fullest form that refers to this same entity?"
            ),
            criteria=dict(name_options),
        )
    return questions, name_options


def _describe_candidate(candidate: dict) -> str:
    parts = [candidate.get("name", "")]
    ctx = candidate.get("context") or {}
    for key in ("title", "role", "company", "description"):
        if ctx.get(key):
            parts.append(f"{key}: {ctx[key]}")
    if ctx.get("co_mentioned_with"):
        parts.append("usually mentioned with " + ", ".join(ctx["co_mentioned_with"]))
    return "; ".join(p for p in parts if p)[:500]


def build_link_state(mention: dict, windows: list[str], meeting: dict | None) -> dict:
    m = {"name": mention["name"], "type": mention["type"]}
    for key in ("evidence", "role", "aliases_heard"):
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
) -> LinkVerdict:
    if 1.0 - p_mentioned >= UNLINK_MIN_PROBABILITY:
        return LinkVerdict("unlink", None, p_mentioned, p_same)
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
        questions, name_options = build_link_questions(link, candidate, forms)
        try:
            result = await client.decide(
                build_link_state(link, windows, meeting), questions, operation=LINK_OPERATION
            )
            p_mentioned = result.noul("mentioned")
            p_same = None
            if "same" in questions:
                same = result.choice("same")
                p_same = same.probabilities.get("existing", 1.0 - same.probabilities.get(_DIFFERENT, 0.0))
            name_choice, name_p = None, 0.0
            if "name" in questions:
                nm = result.choice("name")
                name_choice, name_p = nm.choice, nm.probabilities.get(nm.choice, 0.0)
        except (DecisionUnavailable, ValueError, KeyError, TypeError) as e:
            logger.warning("[LINK] Judgment failed for %s, keeping: %s", link["id"], e)
            return link["id"], KEEP
        verdict = apply_link(link["name"], p_mentioned, p_same, name_choice, name_p, name_options)
        logger.info(
            "[LINK] %s %s %r: mentioned=%.2f%s%s -> %s%s",
            mode, link["id"], link["name"], p_mentioned,
            f" same={p_same:.2f}" if p_same is not None else "",
            f" name={name_options.get(name_choice, '')!r}@{name_p:.2f}" if name_choice else "",
            verdict.action, f" ({verdict.name})" if verdict.name else "",
        )
        return link["id"], verdict

    unique = {link["id"]: link for link in links if link.get("id") and link.get("name")}
    results = await asyncio.gather(*(one(link) for link in unique.values()))
    if mode != "on":
        return {}
    return {eid: v for eid, v in results if v.action != "keep"}
