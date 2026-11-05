"""Call transcripts linked to storylines, with who said it and who was there (ADR-013).

Runs inside the LINK phase (``capture_linker.run_link``), after the captures,
sharing the run id, lock, audit log and lifecycle step:

  1. orphan link files whose meeting file is gone; restore those whose meeting
     came back;
  2. re-attribute stored excerpts when ``participants``, the owner's names or
     the call's resolved people changed (no model call);
  3. link each eligible meeting: the model (operation ``meeting_linking``)
     returns line ranges per open storyline; the server drops, merges and caps
     them (§3), cuts the quote verbatim and attributes the speaker (§2);
  4. write ``memory/meeting_links/<stem>.json`` and project it (mode on).

``backfill_meetings`` judges linked meetings against storylines they have not
been judged against (or re-checks them); ``revert_meetings`` undoes one run.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.capture_link_store import STANCES, now_iso
from app.services.meeting_link_store import (
    MEETING_LINK_VERSION,
    Excerpt,
    MeetingLinkFile,
    MeetingLinkStore,
    MeetingUndo,
    PresentPerson,
    Speaker,
)
from app.services.meeting_transcript import (
    MAX_EXCERPT_LINES,
    Attribution,
    MeetingTranscript,
    PersonRef,
    attribute,
    attribution_sha256,
    excerpt_id,
    normalize_name,
    number_lines,
    owner_names,
    parse_meeting,
    parts,
)
from app.utils.event_time import to_utc

logger = logging.getLogger(__name__)

MEETING_OPERATION = "meeting_linking"
PROMPT_NAME = "meeting_linking"
MEETINGS_DIR = "meetings"
KIND = "meeting"
_CLAIM_CHARS = 300
_TIE = 0.05  # confidences this close count as tied (§3 step 3)
_PERSON_LABELS = ("person", "contact")

PeopleLookup = Callable[[list[str]], Awaitable[dict[str, list[PersonRef]]]]


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------


@dataclass
class MeetingRunLog:
    mode: str
    processed: int = 0
    remaining: int = 0
    excerpts: int = 0
    excerpts_by_stance: Counter = field(default_factory=Counter)
    storylines: Counter = field(default_factory=Counter)
    dropped: Counter = field(default_factory=Counter)
    reattributed: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    missing_document: list[str] = field(default_factory=list)
    unlinked: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "link_version": MEETING_LINK_VERSION,
            "processed": self.processed,
            "remaining": self.remaining,
            "excerpts": self.excerpts,
            "excerpts_by_stance": dict(self.excerpts_by_stance),
            "storylines": dict(self.storylines),
            "dropped": dict(self.dropped),
            "reattributed": self.reattributed,
            "orphaned": self.orphaned,
            "restored": self.restored,
            "failed": self.failed,
            "missing_document": self.missing_document,
            "unlinked": self.unlinked,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


def load_meetings(repo_root: Path) -> dict[str, MeetingTranscript]:
    """Every meeting file with a transcript, by file stem."""
    directory = Path(repo_root) / MEETINGS_DIR
    out: dict[str, MeetingTranscript] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.md")):
        try:
            meeting = parse_meeting(path.read_text(encoding="utf-8"), f"{MEETINGS_DIR}/{path.name}")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("[LINK] Unreadable meeting %s: %s", path.name, e)
            continue
        if meeting is not None:
            out[meeting.stem] = meeting
    return out


def _aliases(metadata: dict[str, Any]) -> tuple[str, ...]:
    aliases = metadata.get("aliases") or []
    if isinstance(aliases, str):
        try:
            aliases = json.loads(aliases)
        except json.JSONDecodeError:
            aliases = [aliases]
    return tuple(a for a in aliases if isinstance(a, str) and a.strip())


def _person_ref(kg: Any, entity_id: str, name: str | None) -> PersonRef:
    node = (getattr(kg, "nodes", None) or {}).get(entity_id)
    metadata = (getattr(node, "metadata", None) or {}) if node is not None else {}
    return PersonRef(id=entity_id, name=(getattr(node, "name", None) or name or entity_id),
                     aliases=_aliases(metadata))


def owner_people(kg: Any, owner: str) -> list[PersonRef]:
    """Person entities named as the owner (for their aliases)."""
    wanted = normalize_name(owner)
    out = []
    for node in list((getattr(kg, "nodes", None) or {}).values()):
        if str(getattr(node, "type", "")).lower() in _PERSON_LABELS and normalize_name(node.name) == wanted:
            out.append(PersonRef(id=node.id, name=node.name,
                                 aliases=_aliases(getattr(node, "metadata", None) or {})))
    return out


def neo4j_people(kg: Any) -> PeopleLookup:
    """People meeting ingest resolved for each call: Person entities with a
    MENTIONED_IN edge to the meeting's Document (§2 resolution)."""

    async def lookup(document_ids: list[str]) -> dict[str, list[PersonRef]]:
        out: dict[str, list[PersonRef]] = defaultdict(list)
        neo4j = getattr(kg, "neo4j", None)
        if neo4j is None or not document_ids:
            return out
        rows = await neo4j.execute_read(
            "UNWIND $ids AS doc MATCH (e:Entity)-[:MENTIONED_IN]->(d:Document {id: doc}) "
            "WHERE any(l IN labels(e) WHERE toLower(l) IN $labels) "
            "RETURN doc, e.id AS id, e.name AS name",
            {"ids": document_ids, "labels": list(_PERSON_LABELS)},
        )
        for row in rows or []:
            out[row["doc"]].append(_person_ref(kg, row["id"], row.get("name")))
        return out

    return lookup


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------


async def call_part(client: Any, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    """One meeting_linking call -> raw excerpts; None on any failure."""
    from app.config import settings
    from app.services.capture_enrichment import _extract_response_text, _parse_json_object
    from app.services.prompt_loader import load_prompt

    try:
        response = await client.generate_message(
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            model=settings.CLAUDE_SONNET_MODEL,
            max_tokens=3000,
            temperature=0.0,
            system=load_prompt(PROMPT_NAME),
            operation=MEETING_OPERATION,
        )
    except Exception as e:
        logger.warning("[LINK] meeting_linking call failed: %s", e)
        return None
    text = _extract_response_text(response)
    parsed = _parse_json_object(text) if text else None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("excerpts"), list):
        logger.warning("[LINK] Unparseable meeting_linking response")
        return None
    return [item for item in parsed["excerpts"] if isinstance(item, dict)]


async def judge(client: Any, meeting: MeetingTranscript, storylines: list[dict[str, Any]],
                owner: str, size: int, overlap: int,
                semaphore: asyncio.Semaphore) -> list[dict[str, Any]] | None:
    """Every part of one call; None when any part failed (all or nothing)."""
    total = len(meeting.lines)

    async def one(start: int, end: int) -> list[dict[str, Any]] | None:
        payload = {
            "owner": owner,
            "title": meeting.title,
            "date": (meeting.occurred_at or "")[:10] or None,
            "storylines": storylines,
            "part": {"first": start, "last": end, "total": total},
            "lines": number_lines(meeting, start, end),
        }
        async with semaphore:
            answer = await call_part(client, payload)
        if answer is None:
            return None
        for item in answer:
            item["_part"] = (start, end)
        return answer

    answers = await asyncio.gather(*(one(s, e) for s, e in parts(total, size, overlap)))
    if any(a is None for a in answers):
        return None
    return [item for answer in answers for item in answer]


# ---------------------------------------------------------------------------
# Selection (§3)
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    id: str
    storyline_id: str
    start: int
    end: int
    turn: int
    stance: str
    confidence: float
    claim: str
    quote: str


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _line_range(item: dict[str, Any]) -> tuple[int, int] | None:
    lines = item.get("lines")
    if isinstance(lines, int) and not isinstance(lines, bool):
        lines = [lines]
    if not isinstance(lines, list) or not 1 <= len(lines) <= 2:
        return None
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in lines):
        return None
    return lines[0], lines[-1]


def select_excerpts(meeting: MeetingTranscript, raw: list[dict[str, Any]], storyline_ids: set[str],
                    *, min_confidence: float, max_per_storyline: int, max_storylines: int,
                    speaker_key: Callable[[int], str]) -> tuple[list[Candidate], Counter]:
    """The model's ranges -> the excerpts kept, and drop counts by reason."""
    dropped: Counter = Counter()
    total = len(meeting.lines)
    valid: list[Candidate] = []
    for item in raw:
        storyline_id = item.get("storyline_id")
        if storyline_id not in storyline_ids:
            dropped["not_offered"] += 1
            continue
        span = _line_range(item)
        part = item.get("_part") or (1, total)
        if span is None or not (part[0] <= span[0] <= span[1] <= part[1]) or span[1] > total:
            dropped["bad_lines"] += 1
            continue
        start, end = span
        if end - start + 1 > MAX_EXCERPT_LINES:
            dropped["too_long"] += 1
            continue
        confidence = _confidence(item.get("confidence"))
        if confidence < min_confidence:
            dropped["below_bar"] += 1
            continue
        turn = meeting.turn_index(start, end)
        if turn is None:
            dropped["cross_turn"] += 1
            continue
        quote = meeting.quote(start, end)
        stance = item.get("stance") if item.get("stance") in STANCES else "context"
        claim = " ".join(str(item.get("claim") or "").split())[:_CLAIM_CHARS]
        valid.append(Candidate(id=excerpt_id(meeting.document_id, storyline_id, quote),
                               storyline_id=storyline_id, start=start, end=end, turn=turn,
                               stance=stance, confidence=confidence, claim=claim, quote=quote))

    # Same id, or overlapping ranges on one storyline: keep the higher confidence.
    merged: list[Candidate] = []
    for cand in sorted(valid, key=lambda c: (-c.confidence, c.storyline_id, c.start, c.end)):
        if any(m.id == cand.id or (m.storyline_id == cand.storyline_id
                                   and m.start <= cand.end and cand.start <= m.end) for m in merged):
            dropped["merged"] += 1
            continue
        merged.append(cand)

    by_storyline: dict[str, list[Candidate]] = defaultdict(list)
    for cand in merged:  # already in confidence order
        by_storyline[cand.storyline_id].append(cand)
    kept: dict[str, list[Candidate]] = {}
    for storyline_id, cands in by_storyline.items():
        chosen: list[Candidate] = []
        stances: set[str] = set()
        for cand in cands:  # the best of each stance present first
            if cand.stance not in stances:
                stances.add(cand.stance)
                chosen.append(cand)
        chosen = chosen[:max_per_storyline]
        rest = [c for c in cands if c not in chosen]
        while len(chosen) < max_per_storyline and rest:
            top = rest[0].confidence
            heard = {speaker_key(c.turn) for c in chosen}
            tied = [c for c in rest if top - c.confidence <= _TIE]
            pick = next((c for c in tied if speaker_key(c.turn) not in heard), rest[0])
            chosen.append(pick)
            rest.remove(pick)
        if rest:
            dropped["over_storyline_cap"] += len(rest)
        kept[storyline_id] = chosen

    order = sorted(kept, key=lambda sid: (-max(c.confidence for c in kept[sid]), sid))
    for storyline_id in order[max(0, max_storylines):]:
        dropped["over_meeting_cap"] += len(kept.pop(storyline_id))
    out = [c for sid in sorted(kept) for c in sorted(kept[sid], key=lambda c: c.start)]
    return out, dropped


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


@dataclass
class Attributor:
    meeting: MeetingTranscript
    owner_norms: frozenset[str]
    people: list[PersonRef]
    _cache: dict[int, Attribution] = field(default_factory=dict)

    def __call__(self, turn: int) -> Attribution:
        if turn not in self._cache:
            self._cache[turn] = attribute(self.meeting, turn, self.owner_norms, self.people)
        return self._cache[turn]

    def speaker_key(self, turn: int) -> str:
        att = self(turn)
        return att.speaker_id or normalize_name(att.speaker_name) or "?"

    @property
    def sha(self) -> str:
        return attribution_sha256(self.meeting.participants, self.owner_norms, self.people)

    def apply(self, excerpt: Excerpt) -> Excerpt:
        """The excerpt with its speaker and audience recomputed."""
        turn = self.meeting.turn_index(excerpt.lines[0], excerpt.lines[-1])
        if turn is None:  # cannot happen while transcript_sha256 matches
            return excerpt
        att = self(turn)
        return excerpt.model_copy(update={
            "speaker": Speaker(name=att.speaker_name, id=att.speaker_id, basis=att.basis),
            "speaker_is_owner": att.is_owner,
            "with_": [PresentPerson(name=n, id=i) for n, i in att.with_people],
            "with_basis": att.with_basis,
        })


def build_excerpt(cand: Candidate, attributor: Attributor, run_id: str, linked_at: str) -> Excerpt:
    base = Excerpt(id=cand.id, storyline_id=cand.storyline_id, stance=cand.stance,
                   confidence=cand.confidence, claim=cand.claim, lines=[cand.start, cand.end],
                   quote=cand.quote, run_id=run_id, linked_at=linked_at)
    return attributor.apply(base)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def _owner() -> str:
    from app.services.lane_admission import owner_name

    return owner_name()


@dataclass
class MeetingDeps:
    """What the meeting pass needs beyond the LinkContext."""

    store: MeetingLinkStore
    graph: Any | None  # MeetingGraphWriter; None skips projection
    people: PeopleLookup
    owner: str


def deps_for(ctx: Any) -> MeetingDeps:
    store = getattr(ctx, "meeting_links", None) or MeetingLinkStore(repo_root=ctx.repo_root)
    graph = getattr(ctx, "meeting_graph", None)
    people = getattr(ctx, "meeting_people", None) or neo4j_people(ctx.kg)
    owner = getattr(ctx, "owner", None) or _owner()
    return MeetingDeps(store=store, graph=graph, people=people, owner=owner)


def _audit(run_id: str, mode: str, action: str, meeting: str, **extra: Any) -> dict[str, Any]:
    return {"run_id": run_id, "mode": mode, "kind": KIND, "action": action, "meeting": meeting,
            **{k: v for k, v in extra.items() if v is not None}}


def _occurred(meeting: MeetingTranscript) -> datetime:
    return to_utc(meeting.occurred_at) or datetime.min.replace(tzinfo=UTC)


async def _attributors(deps: MeetingDeps, kg: Any, meetings: list[MeetingTranscript],
                       log: MeetingRunLog) -> dict[str, Attributor]:
    try:
        people = await deps.people([m.document_id for m in meetings])
    except Exception as e:
        logger.warning("[LINK] Meeting people lookup failed: %s", e)
        log.errors.append(f"people lookup: {e}")
        people = {}
    owner_norms = owner_names(deps.owner, owner_people(kg, deps.owner))
    return {m.stem: Attributor(m, owner_norms, list(people.get(m.document_id) or []))
            for m in meetings}


async def _project(deps: MeetingDeps, stems: set[str], log: MeetingRunLog) -> None:
    if deps.graph is None or not stems:
        return
    records = [r for s in sorted(stems) if (r := deps.store.get(s)) is not None]
    try:
        stats = await deps.graph.write(records)
    except Exception as e:
        logger.warning("[LINK] Meeting projection failed (files are written): %s", e, exc_info=True)
        log.errors.append(f"meeting graph projection: {e}")
        return
    log.missing_document.extend((stats or {}).get("missing_document") or [])


def _storylines(ctx: Any) -> list[dict[str, Any]]:
    from app.services.library_seeds import offered

    storyline_type = ctx.role_type("storyline")
    return offered(ctx.kg, storyline_type) if storyline_type else []


# ---------------------------------------------------------------------------
# Run (§6)
# ---------------------------------------------------------------------------


def eligible(record: MeetingLinkFile | None, meeting: MeetingTranscript, mode: str) -> bool:
    if record is None:
        return True
    if record.link_version < MEETING_LINK_VERSION:
        return True
    if record.transcript_sha256 != meeting.transcript_sha256:
        return True
    return record.mode == "shadow" and mode == "on"


async def link_meetings(ctx: Any, run_id: str, max_results: int,
                        audit_rows: list[dict[str, Any]]) -> tuple[MeetingRunLog, list[str]]:
    """The meeting pass of one LINK run. Returns its log and the repo paths
    it changed (to commit with the run)."""
    mode = ctx.policy.meeting_mode
    log = MeetingRunLog(mode=mode)
    if mode == "off":
        return log, []
    deps = deps_for(ctx)
    policy = ctx.policy.meetings
    meetings = load_meetings(ctx.repo_root)
    touched: set[str] = set()
    now = now_iso()

    # 1. orphans and restored meetings
    for record in list(deps.store.iter_all()):
        stem = record.stem
        if stem not in meetings and record.orphaned_at is None:
            def orphan(rec: MeetingLinkFile | None) -> MeetingLinkFile | None:
                if rec is None:
                    return None
                rec.orphaned_at = now
                return rec

            deps.store.mutate(stem, orphan)
            touched.add(stem)
            log.orphaned.append(stem)
            audit_rows.append(_audit(run_id, mode, "orphan", record.meeting_path))
        elif stem in meetings and record.orphaned_at is not None:
            def restore(rec: MeetingLinkFile | None) -> MeetingLinkFile | None:
                if rec is None:
                    return None
                rec.orphaned_at = None
                return rec

            deps.store.mutate(stem, restore)
            touched.add(stem)
            log.restored.append(stem)
            audit_rows.append(_audit(run_id, mode, "restore", record.meeting_path))

    attributors = await _attributors(deps, ctx.kg, list(meetings.values()), log)

    # 2. re-attribution (no model call)
    for stem, meeting in meetings.items():
        record = deps.store.get(stem)
        if record is None or record.orphaned_at is not None:
            continue
        if record.transcript_sha256 != meeting.transcript_sha256:
            continue  # re-linked below
        attributor = attributors[stem]
        if record.attribution_sha256 == attributor.sha:
            continue

        def reattribute(rec: MeetingLinkFile | None, attributor: Attributor = attributor
                        ) -> MeetingLinkFile | None:
            if rec is None:
                return None
            rec.excerpts = [attributor.apply(e) for e in rec.excerpts]
            rec.attribution_sha256 = attributor.sha
            return rec

        deps.store.mutate(stem, reattribute)
        touched.add(stem)
        log.reattributed.append(stem)
        audit_rows.append(_audit(run_id, mode, "reattribute", meeting.meeting_path,
                                 excerpts=len(record.excerpts)))

    # 3. linking
    pending = [m for stem, m in meetings.items() if eligible(deps.store.get(stem), m, mode)]
    pending.sort(key=_occurred, reverse=True)
    batch = pending[:max(0, max_results)]
    log.remaining = len(pending) - len(batch)
    storylines = _storylines(ctx)
    if batch and not storylines:
        log.errors.append("no open storylines: meetings not linked")
        log.remaining += len(batch)
        batch = []
    if batch and ctx.claude_client is None:
        log.errors.append(f"{len(batch)} meeting(s) not linked: no Claude client")
        log.remaining += len(batch)
        batch = []

    semaphore = asyncio.Semaphore(ctx.policy.concurrency)
    answers = await asyncio.gather(*(
        judge(ctx.claude_client, m, storylines, deps.owner, policy.max_lines_per_part,
              policy.overlap_lines, semaphore) for m in batch
    ))
    linked_at = now_iso()
    storyline_ids = {s["id"] for s in storylines}
    for meeting, raw in zip(batch, answers, strict=True):
        if raw is None:
            log.failed.append(meeting.meeting_path)
            log.remaining += 1
            audit_rows.append(_audit(run_id, mode, "link_failed", meeting.meeting_path))
            continue
        attributor = attributors[meeting.stem]
        chosen, dropped = select_excerpts(
            meeting, raw, storyline_ids, min_confidence=policy.storyline_min_confidence,
            max_per_storyline=policy.max_excerpts_per_storyline,
            max_storylines=policy.max_storylines_per_meeting, speaker_key=attributor.speaker_key,
        )
        log.dropped.update(dropped)
        excerpts = [build_excerpt(c, attributor, run_id, linked_at) for c in chosen]

        def relink(rec: MeetingLinkFile | None, meeting: MeetingTranscript = meeting,
                   excerpts: list[Excerpt] = excerpts, attributor: Attributor = attributor
                   ) -> MeetingLinkFile:
            undo = MeetingUndo(excerpts=rec.excerpts if rec else [],
                               storylines_checked=rec.storylines_checked if rec else {})
            if rec is not None:
                undo = undo.model_copy(update={
                    "mode": rec.mode, "link_version": rec.link_version, "run_id": rec.run_id,
                    "linked_at": rec.linked_at, "transcript_sha256": rec.transcript_sha256,
                    "attribution_sha256": rec.attribution_sha256,
                })
                # A passage already in the graph keeps when it was first
                # linked, so linked_since reads do not show it as new.
                first = ({(e.id, e.storyline_id): e.linked_at for e in rec.excerpts}
                         if rec.mode == "on" else {})
                excerpts = [e.model_copy(update={"linked_at": first.get((e.id, e.storyline_id))
                                                 or e.linked_at}) for e in excerpts]
            return MeetingLinkFile(
                meeting_path=meeting.meeting_path, document_id=meeting.document_id,
                mode="shadow" if mode == "shadow" else "on", run_id=run_id, linked_at=linked_at,
                occurred_at=meeting.occurred_at, transcript_sha256=meeting.transcript_sha256,
                attribution_sha256=attributor.sha,
                storylines_checked={sid: run_id for sid in sorted(storyline_ids)},
                excerpts=excerpts,
                undo={**(rec.undo if rec else {}), run_id: undo},
                reverted_runs=rec.reverted_runs if rec else [],
            )

        deps.store.mutate(meeting.stem, relink)
        touched.add(meeting.stem)
        log.processed += 1
        for e in excerpts:
            log.excerpts += 1
            log.excerpts_by_stance[e.stance] += 1
            log.storylines[e.storyline_id] += 1
            audit_rows.append(_audit(
                run_id, mode, "link", meeting.meeting_path, edge="ADVANCES",
                target_id=e.storyline_id, excerpt_id=e.id, stance=e.stance,
                confidence=e.confidence, lines=e.lines, speaker_basis=e.speaker.basis,
                speaker_is_owner=e.speaker_is_owner,
            ))
    if log.failed:
        log.errors.append(f"{len(log.failed)} meeting(s) left unlinked: a meeting_linking call "
                          "failed or returned invalid output")

    # 4. projection (the writer projects only files with mode on)
    await _project(deps, touched, log)
    return log, [deps.store.relative_path(s) for s in sorted(touched)]


# ---------------------------------------------------------------------------
# Backfill and re-check (§6)
# ---------------------------------------------------------------------------


def _run_time(run_id: str | None) -> datetime | None:
    from app.services.capture_linker import _run_time as parse

    return parse(run_id)


async def backfill_meetings(ctx: Any, run_id: str, targets: dict[str, dict[str, Any]],
                            since: datetime | None, recheck_before: datetime | None,
                            max_results: int, audit_rows: list[dict[str, Any]]
                            ) -> tuple[MeetingRunLog, list[str]]:
    """Judge linked meetings against requested storylines they have not been
    judged against (or, with ``recheck_before``, judged before then). Each
    judged storyline's excerpts are replaced, with none if nothing qualifies."""
    mode = ctx.policy.meeting_mode
    log = MeetingRunLog(mode=mode)
    if mode != "on":
        log.errors.append("meeting backfill needs library.linking.meetings.mode: on")
        return log, []
    if not targets:
        return log, []
    deps = deps_for(ctx)
    policy = ctx.policy.meetings
    meetings = load_meetings(ctx.repo_root)
    candidates: list[tuple[MeetingTranscript, set[str]]] = []
    for stem, meeting in meetings.items():
        record = deps.store.get(stem)
        if (record is None or record.mode != "on" or record.orphaned_at is not None
                or record.link_version < MEETING_LINK_VERSION
                or record.transcript_sha256 != meeting.transcript_sha256):
            continue  # unlinked or stale: the next LINK run links it whole
        missing = set(targets) - set(record.storylines_checked)
        if recheck_before is not None:
            missing |= {sid for sid in targets if sid in record.storylines_checked
                        and ((t := _run_time(record.storylines_checked[sid])) is None
                             or t < recheck_before)}
        if not missing:
            continue
        if since is not None and (to_utc(meeting.occurred_at) is None
                                  or to_utc(meeting.occurred_at) < since):
            continue
        candidates.append((meeting, missing))
    candidates.sort(key=lambda c: _occurred(c[0]), reverse=True)
    batch = candidates[:max(0, max_results)]
    log.remaining = len(candidates) - len(batch)
    if batch and ctx.claude_client is None:
        log.errors.append(f"{len(batch)} meeting(s) not judged: no Claude client")
        log.remaining += len(batch)
        batch = []
    if not batch:
        return log, []

    attributors = await _attributors(deps, ctx.kg, [m for m, _ in batch], log)
    semaphore = asyncio.Semaphore(ctx.policy.concurrency)
    answers = await asyncio.gather(*(
        judge(ctx.claude_client, m, [targets[i] for i in sorted(missing)], deps.owner,
              policy.max_lines_per_part, policy.overlap_lines, semaphore)
        for m, missing in batch
    ))
    linked_at = now_iso()
    touched: set[str] = set()
    for (meeting, missing), raw in zip(batch, answers, strict=True):
        if raw is None:
            log.failed.append(meeting.meeting_path)
            log.remaining += 1
            continue
        record = deps.store.get(meeting.stem)
        if record is None:
            continue
        others = {e.storyline_id for e in record.excerpts if e.storyline_id not in missing}
        attributor = attributors[meeting.stem]
        chosen, dropped = select_excerpts(
            meeting, raw, missing, min_confidence=policy.storyline_min_confidence,
            max_per_storyline=policy.max_excerpts_per_storyline,
            max_storylines=policy.max_storylines_per_meeting - len(others),
            speaker_key=attributor.speaker_key,
        )
        log.dropped.update(dropped)
        new = [build_excerpt(c, attributor, run_id, linked_at) for c in chosen]
        replaced: list[Excerpt] = []

        def apply(rec: MeetingLinkFile | None, missing: set[str] = missing, new: list[Excerpt] = new,
                  replaced: list[Excerpt] = replaced, attributor: Attributor = attributor
                  ) -> MeetingLinkFile | None:
            if rec is None:
                return None
            undo = MeetingUndo(
                excerpts=[e for e in rec.excerpts if e.storyline_id in missing],
                storylines_checked={sid: rec.storylines_checked[sid] for sid in missing
                                    if sid in rec.storylines_checked},
            )
            replaced[:] = undo.excerpts
            rec.undo[run_id] = undo
            rec.excerpts = [e for e in rec.excerpts if e.storyline_id not in missing] + new
            rec.excerpts.sort(key=lambda e: (e.storyline_id, e.lines[0]))
            for sid in missing:
                rec.storylines_checked[sid] = run_id
            rec.attribution_sha256 = attributor.sha
            return rec

        deps.store.mutate(meeting.stem, apply)
        touched.add(meeting.stem)
        log.processed += 1
        for old in replaced:
            log.unlinked += 1
            audit_rows.append(_audit(run_id, "on", "unlink", meeting.meeting_path, reason="recheck",
                                     edge="ADVANCES", target_id=old.storyline_id, excerpt_id=old.id,
                                     stance=old.stance, replaced_run_id=old.run_id))
        for e in new:
            log.excerpts += 1
            log.excerpts_by_stance[e.stance] += 1
            log.storylines[e.storyline_id] += 1
            audit_rows.append(_audit(run_id, "on", "link", meeting.meeting_path, edge="ADVANCES",
                                     target_id=e.storyline_id, excerpt_id=e.id, stance=e.stance,
                                     confidence=e.confidence, lines=e.lines,
                                     speaker_basis=e.speaker.basis,
                                     speaker_is_owner=e.speaker_is_owner))
    if log.failed:
        log.errors.append(f"{len(log.failed)} meeting(s) not judged (model call failed)")
    await _project(deps, touched, log)
    return log, [deps.store.relative_path(s) for s in sorted(touched)]


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------


def _strip_run(record: MeetingLinkFile, run_id: str) -> MeetingLinkFile | None:
    """``record`` with ``run_id`` undone, or None when the run never touched it."""
    undo = record.undo.get(run_id)
    keep = [e for e in record.excerpts if e.run_id != run_id]
    checked = {k: v for k, v in record.storylines_checked.items() if v != run_id}
    other_undo = {k: u for k, u in record.undo.items() if k != run_id}
    stale = any(e.run_id == run_id for u in other_undo.values() for e in u.excerpts)
    if (undo is None and len(keep) == len(record.excerpts)
            and len(checked) == len(record.storylines_checked) and not stale):
        return None
    if undo is not None:
        have = {e.id for e in keep}
        keep.extend(e for e in undo.excerpts if e.id not in have and e.run_id != run_id)
        for sid, earlier in undo.storylines_checked.items():
            checked.setdefault(sid, earlier)
        if undo.mode is not None:  # a whole-file re-link: its earlier state
            record.mode = undo.mode
            record.link_version = undo.link_version or record.link_version
            record.run_id = undo.run_id
            record.linked_at = undo.linked_at
            record.transcript_sha256 = undo.transcript_sha256
            record.attribution_sha256 = undo.attribution_sha256
    for u in other_undo.values():
        u.excerpts = [e for e in u.excerpts if e.run_id != run_id]
    keep.sort(key=lambda e: (e.storyline_id, e.lines[0]))
    record.excerpts = keep
    record.storylines_checked = checked
    record.undo = other_undo
    if run_id not in record.reverted_runs:
        record.reverted_runs.append(run_id)
    return record


async def revert_meetings(ctx: Any, run_id: str) -> tuple[dict[str, Any], list[str]]:
    """Undo one run in every meeting link file it touched, and re-project."""
    deps = deps_for(ctx)
    changed: set[str] = set()
    removed = 0
    def strip(rec: MeetingLinkFile | None) -> MeetingLinkFile | None:
        return _strip_run(rec, run_id) if rec is not None else None

    for record in list(deps.store.iter_all()):
        if deps.store.mutate(record.stem, strip) is not None:
            changed.add(record.stem)
            removed += sum(1 for e in record.excerpts if e.run_id == run_id)
    log = MeetingRunLog(mode=ctx.policy.meeting_mode)
    await _project(deps, changed, log)
    summary = {"meetings_changed": len(changed), "excerpts_removed": removed}
    if log.errors:
        summary["errors"] = log.errors
    return summary, [deps.store.relative_path(s) for s in sorted(changed)]
