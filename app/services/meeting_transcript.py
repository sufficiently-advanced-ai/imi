"""Call transcripts as numbered lines with speaker attribution (ADR-013 §2).

Pure functions, shared by the meeting linker and its tests:

- ``parse_meeting``: a meeting file -> its transcript section as numbered
  lines (one number per non-blank line, from 1), ``participants`` and the
  Document id meeting ingest gives it;
- ``speaker_label`` / ``turns``: a line's ``Name:`` label and the maximal runs
  of lines with the same label (or none);
- ``attribute``: who said a turn (``label`` | ``sole_other`` | ``unknown``),
  and whether that was the owner (``True`` / ``False`` / ``None`` = not known);
- ``excerpt_id`` / ``quote_sha256``: deterministic ids from the words, not the
  line numbers, so an edit elsewhere in the transcript keeps them.

The model never names a speaker: it returns line ranges, and everything here
is computed from the lines, the call's ``participants``, the owner's names and
the people meeting ingest already resolved for the call.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

TRANSCRIPT_HEADING = "\n## Full Transcript\n"
MAX_EXCERPT_LINES = 8

# One to four capitalized words, then ": " and text (ADR-013 §2).
SPEAKER_LABEL = re.compile(r"^([A-Z][\w.'’-]*(?: [A-Z][\w.'’-]*){0,3}): \S")

LABEL = "label"
SOLE_OTHER = "sole_other"
UNKNOWN = "unknown"
PARTICIPANTS = "participants"


def normalize_name(name: str | None) -> str:
    """Casefolded, punctuation removed, whitespace collapsed."""
    return " ".join(re.sub(r"[^\w\s]", " ", (name or "").casefold()).split())


def meeting_document_id(meeting_path: str) -> str:
    """The Document id meeting ingest writes: ``doc:<repo-relative path>``."""
    return f"doc:{meeting_path}"


def speaker_label(line: str) -> str | None:
    match = SPEAKER_LABEL.match(line)
    return match.group(1) if match else None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def quote_sha256(quote: str) -> str:
    """sha256 of the quote with whitespace collapsed and casefolded."""
    return _sha256(" ".join(quote.split()).casefold())


def excerpt_id(document_id: str, storyline_id: str, quote: str) -> str:
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL, f"meeting-excerpt|{document_id}|{storyline_id}|{quote_sha256(quote)}"
    ))


@dataclass
class Turn:
    start: int  # 1-based, inclusive
    end: int
    label: str | None


@dataclass
class MeetingTranscript:
    meeting_path: str  # repo-relative, e.g. meetings/meeting-x.md
    title: str
    occurred_at: str | None
    participants: list[str]
    lines: list[str]  # lines[0] is line 1
    turns: list[Turn] = field(default_factory=list)

    @property
    def stem(self) -> str:
        return self.meeting_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    @property
    def document_id(self) -> str:
        return meeting_document_id(self.meeting_path)

    @property
    def transcript_sha256(self) -> str:
        return _sha256("\n".join(self.lines))

    def quote(self, start: int, end: int) -> str:
        return "\n".join(self.lines[start - 1 : end])

    def turn_index(self, start: int, end: int) -> int | None:
        """The index of the turn holding lines start..end, or None when they
        cross a turn boundary (or fall outside the transcript)."""
        for i, turn in enumerate(self.turns):
            if turn.start <= start <= turn.end:
                return i if end <= turn.end else None
        return None


def compute_turns(lines: list[str]) -> list[Turn]:
    out: list[Turn] = []
    for n, line in enumerate(lines, start=1):
        label = speaker_label(line)
        if out and out[-1].label == label:
            out[-1].end = n
        else:
            out.append(Turn(start=n, end=n, label=label))
    return out


def _front_matter(text: str) -> tuple[dict[str, Any], str]:
    import yaml

    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    return (data if isinstance(data, dict) else {}), parts[2]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def parse_meeting(text: str, meeting_path: str) -> MeetingTranscript | None:
    """The transcript section of a meeting file, in the format meeting ingest
    writes (``## Full Transcript`` after the summary). None when the file has
    no transcript. Front matter and generated sections are never numbered."""
    meta, body = _front_matter(text)
    if TRANSCRIPT_HEADING not in "\n" + body:
        return None
    transcript = ("\n" + body).split(TRANSCRIPT_HEADING, 1)[1]
    lines = [line.rstrip() for line in transcript.splitlines() if line.strip()]
    if not lines:
        return None
    participants = [str(p).strip() for p in (meta.get("participants") or []) if str(p or "").strip()]
    occurred = meta.get("start_time") or meta.get("occurred_at") or meta.get("updated_at")
    return MeetingTranscript(
        meeting_path=meeting_path,
        title=str(meta.get("title") or meeting_path.rsplit("/", 1)[-1]),
        occurred_at=_iso(occurred),
        participants=participants,
        lines=lines,
        turns=compute_turns(lines),
    )


# ---------------------------------------------------------------------------
# Owner, people, attribution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonRef:
    id: str
    name: str
    aliases: tuple[str, ...] = ()


def owner_names(owner: str, people: list[PersonRef] | tuple[PersonRef, ...] = ()) -> frozenset[str]:
    """The owner's normalized names: the name, its first word, and the
    aliases of the Person entity with that name."""
    full = normalize_name(owner)
    names = {full} if full else set()
    if full:
        names.add(full.split()[0])
    for person in people:
        if normalize_name(person.name) == full:
            names.update(normalize_name(a) for a in person.aliases if normalize_name(a))
    return frozenset(names)


def resolve_person(name: str | None, people: list[PersonRef] | tuple[PersonRef, ...]) -> str | None:
    """A Person id among ``people`` by normalized name or alias; None when no
    single person matches. Never creates anything (ADR-003)."""
    wanted = normalize_name(name)
    if not wanted:
        return None
    hits = {p.id for p in people
            if wanted == normalize_name(p.name) or wanted in {normalize_name(a) for a in p.aliases}}
    return hits.pop() if len(hits) == 1 else None


@dataclass(frozen=True)
class Attribution:
    speaker_name: str | None
    speaker_id: str | None
    basis: str  # label | sole_other | unknown
    is_owner: bool | None
    with_people: tuple[tuple[str, str | None], ...]  # (name, id or None)
    with_basis: str  # participants | unknown


def others(participants: list[str], owner_norms: frozenset[str]) -> list[str]:
    """``participants`` minus the owner, first spelling of each kept."""
    seen: dict[str, str] = {}
    for p in participants:
        key = normalize_name(p)
        if key and key not in owner_norms and key not in seen:
            seen[key] = p
    return list(seen.values())


def attribute(meeting: MeetingTranscript, turn_index: int, owner_norms: frozenset[str],
              people: list[PersonRef] | tuple[PersonRef, ...]) -> Attribution:
    """Who said one turn, by the table in ADR-013 §2, and who was present."""
    rest = others(meeting.participants, owner_norms)
    with_people = tuple((name, resolve_person(name, people)) for name in rest)
    with_basis = PARTICIPANTS if rest else UNKNOWN

    def is_owner_label(label: str | None) -> bool:
        return label is not None and normalize_name(label) in owner_norms

    turn = meeting.turns[turn_index]
    if turn.label is not None:
        owner = is_owner_label(turn.label)
        return Attribution(turn.label, resolve_person(turn.label, people), LABEL, owner,
                           with_people, with_basis)
    before = meeting.turns[turn_index - 1] if turn_index > 0 else None
    after = meeting.turns[turn_index + 1] if turn_index + 1 < len(meeting.turns) else None
    between_owner = (before is not None and after is not None
                     and is_owner_label(before.label) and is_owner_label(after.label))
    if between_owner and len(rest) == 1:
        return Attribution(rest[0], with_people[0][1], SOLE_OTHER, False, with_people, with_basis)
    if between_owner:
        return Attribution(None, None, UNKNOWN, False, with_people, with_basis)
    return Attribution(None, None, UNKNOWN, None, with_people, with_basis)


def attribution_sha256(participants: list[str], owner_norms: frozenset[str],
                       people: list[PersonRef] | tuple[PersonRef, ...]) -> str:
    """sha256 of the attribution inputs other than the lines (§2
    re-attribution): a change here re-attributes stored excerpts."""
    payload = {
        "participants": list(participants),
        "owner": sorted(owner_norms),
        "people": sorted([p.id, normalize_name(p.name), sorted(normalize_name(a) for a in p.aliases)]
                         for p in people),
    }
    return _sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def number_lines(meeting: MeetingTranscript, start: int, end: int) -> str:
    """Lines start..end as the model sees them: ``<n>\\t<text>``."""
    return "\n".join(f"{n}\t{meeting.lines[n - 1]}" for n in range(start, end + 1))


def parts(total: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """Line ranges (1-based, inclusive) covering 1..total, ``size`` lines each,
    overlapping by ``overlap``."""
    if total <= 0:
        return []
    size = max(1, size)
    overlap = max(0, min(overlap, size - 1))
    out, start = [], 1
    while True:
        end = min(total, start + size - 1)
        out.append((start, end))
        if end == total:
            return out
        start = end - overlap + 1
