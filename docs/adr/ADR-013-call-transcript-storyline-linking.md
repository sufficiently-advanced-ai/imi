# ADR-013: Linking Call Transcripts to Storylines, with Who Said It and to Whom

## Status

Proposed (2026-11-05). Extends ADR-011 (the LINK phase) and ADR-012 (record items linked to
seeded items only). Changes no invariant of ADR-002, ADR-003 or ADR-004.

## Context

Storylines (`docs/architecture/storylines.md`) get evidence from library captures (ADR-011)
and the owner's work log (ADR-012). Calls are the third body of first-hand evidence. They
come in through meeting ingest (`add_call_transcript`, the Littlebird import), not captures,
so the LINK phase never sees them. One seeded storyline, *What small firms actually adopt*,
has no other source: what small firms actually do mostly comes up in calls (client
check-ins, calls about automating a firm's processes), and it has no evidence.

A call is not like a capture. One call bears on several storylines, in several voices, with
different stances. Say a client describes the narrow tool they actually pay for (supports),
and the owner makes a different point to the same client. For the owner, two questions matter as
much as the stance:

- **Who is talking about it.** A client saying it is evidence about the field. The owner
  saying it is the owner's own view (ADR-012's WORK LOG rule).
- **Who the owner is talking about it with.** Which people have heard the owner's thesis, and
  from whom the owner heard each piece of evidence.

Measured on the personal deployment, 2026-11-05 (36 calls in `meetings/`):

| Calls | Count | What the transcript tells us about speakers |
|---|---:|---|
| Every line labeled `Name:` | 12 | speaker of every line (all are 1:1 calls) |
| Owner's lines labeled, the rest not, exactly one other participant | 9 | the rest is that participant |
| Owner's lines labeled, the rest not, 2+ other participants | 4 | the rest is "one of them" (the Foley calls) |
| `participants` lists only the owner, unlabeled lines present | 11 | the rest is "someone else"; who is unknown (cohorts, webinars, some 1:1s) |

The owner's lines carry a label in 35 of the 36 (one webinar has none). Littlebird does not
label the other side in most calls, and the import sometimes records only the owner as a
participant.

Today a meeting is one `Document` (`doc:meetings/<file>.md`) with `MENTIONED_IN` edges from
entities named in it, `participants` in its front matter, and signals (`FROM_DOCUMENT`) that
paraphrase it ("Scott and Jenny agreed…"). Nothing records who said what.

## Decision

### 1. The unit of evidence is an excerpt

The linker judges a call against the seeded storylines and returns **excerpts**: a span of
transcript lines (at most 8, one speaker turn), a one-sentence claim, a stance
(`supports | challenges | context`, as ADR-012 §3) and a confidence. Each excerpt becomes one
`ADVANCES` edge from the meeting's `Document` to the storyline, MERGEd on a deterministic
excerpt id, `uuid5(document id, storyline id, first line)` (CLAUDE.md invariant 5: one edge
per assertion). At most `max_excerpts_per_storyline` (default 3) per call and storyline.

A call with three excerpts on one storyline still counts as one piece of evidence:
storyline counts use `count(DISTINCT d)`. The evidence read returns one row per excerpt.

### 2. The model never names a speaker

The transcript is sent with numbered lines, and the model returns line numbers, not quotes
and not names. The server takes the quote from the lines verbatim and attributes it:

| `speaker_basis` | When | `speaker` |
|---|---|---|
| `label` | the lines carry a `Name:` label | that name |
| `sole_other` | unlabeled lines, the owner's lines in this call are labeled, and `participants` has exactly one person besides the owner | that person |
| `unknown` | anything else | none; `speaker_is_owner: false` when the owner's lines are labeled, else unknown |

An excerpt whose lines carry two different labels is rejected. The owner is
`lanes.yaml owner` / `KB_OWNER_NAME`, as in lane admission.

**Who the owner was talking with** is the call's `participants` minus the owner
(`with`, `with_basis: participants`). When the call records no one else, `with` is empty
and `with_basis: unknown`; we do not infer an audience from the transcript.

Names resolve to existing `Person` entities through the meeting's own `entity_ids`, which
meeting ingest already resolved. A name that does not resolve stays a name. This path
creates no entity (ADR-003).

### 3. What is stored

The meeting's links live in `memory/meeting_links/<meeting file stem>.json`, written by the
run, like `memory/capture_links/` (CLAUDE.md invariant 2; the graph is a projection). Each
excerpt:

```json
{"id": "…", "storyline_id": "storyline-small-firm-adoption", "stance": "supports",
 "confidence": 0.7, "claim": "<one sentence: what was said>",
 "lines": [212, 215], "quote": "<lines 212–215, verbatim>",
 "speaker": {"name": "<client>", "id": "person-<client>", "basis": "label"},
 "speaker_is_owner": false,
 "with": [{"name": "<client>", "id": "person-<client>"}], "with_basis": "participants",
 "run_id": "link-…", "linked_at": "…"}
```

The edge carries `stance`, `confidence`, `claim`, `quote`, `lines`, `speaker_id`,
`speaker_name`, `speaker_basis`, `speaker_is_owner`, `with_ids`, `with_basis`, `lane: record`,
`created_by: linker`, `run_id`, `linked_at`. Event time is the meeting's `occurred_at`
(ADR-004); there is no per-line time.

### 4. Reads

- **Evidence** (`GET /api/storylines/{id}/evidence`, MCP `get_storyline`) returns the excerpt
  fields on meeting rows and gains `speaker=<person id | owner | others>` and
  `with=<person id>` filters.
- **Voices** (new: `GET /api/storylines/{id}/voices?window=`, MCP `list_storyline_voices`):
  per person, excerpts they said, by stance, and excerpts the owner said to them. With
  `person_id` instead of a storyline id, the same counts per storyline: what this person has
  said about each storyline, and what the owner has raised with them.

### 5. Running it

`library.linking.meetings` in `config/lanes.yaml` (default off):

```yaml
library:
  linking:
    meetings:
      mode: shadow          # off | shadow | on, as ADR-011
      storyline_min_confidence: 0.6
      max_excerpts_per_storyline: 3
```

A LINK run takes meetings without a link file, one `meeting_linking` call per call (a call
longer than the model's window is split into overlapping parts), with the seeded and
proposed storylines only: no topics, essays or entities in this ADR. The same runs, lock,
audit, revert and backfill as ADR-011 apply; a storyline backfill or re-check covers
meetings too. Shadow mode writes the link file with `projected: false` so the owner can read
a run before anything reaches the graph.

The prompt carries ADR-012's rule on whose development it is: the owner saying what a
small firm does is context unless it reports what the firm did; the firm's own people
saying it supports or challenges.

## Alternatives considered

- **Link the whole meeting `Document`.** Rejected: one stance per call loses the point, and
  there is no speaker.
- **Link the meeting's existing signals.** Rejected: signals paraphrase and merge voices
  ("Scott and Jenny agreed…"), so they cannot say who said it, and they are governed records
  (ADR-002) whose extraction has its own purpose.
- **Excerpt nodes** (`(:Excerpt)-[:SPOKEN_BY]->(:Person)`). Deferred: a new node label
  outside the domain schema, and every storyline read would need another hop. Edge
  properties are enough at this scale (36 calls, tens of excerpts each). Revisit if person
  pages need excerpts as first-class nodes.
- **Let the model attribute unlabeled lines** ("that was Michelle, she's doing the demo").
  Rejected for now: a confident wrong speaker is worse than `unknown`, and nothing could
  check it. If intake starts labeling every line, `basis: label` covers it with no change
  here.
- **Run calls through the capture path.** Rejected: calls are already ingested, with
  resolved entities and signals; a second copy would double them.

## Consequences

**Easier:**
- Storylines that live in conversations (small firms) get evidence, and every storyline
  can show who holds which side.
- "What have I told Dan about agent memory?" and "who disagrees with me on local models?"
  become one read.

**Harder:**
- Attribution is only as good as the transcript: 15 of 36 calls today cannot name the
  other speaker. Reads must show `speaker_basis` so `unknown` is not read as the owner.
- The call import should record every participant. 11 of 36 calls list only the owner, and
  for those `with` is empty. That is a fix in the import (imi-upkeep), outside this ADR.
- Excerpts quote other people's words into storyline reads, which reviewers (the weekly
  LCARS review) see. They are the same words the transcript already holds, at the same
  visibility; they remain evidence, never instruction (ADR-002).
- Three payload shapes in the linker, each with its own bar.

## Related

- ADR-003 (record and library lanes): meetings stay record; this path reads them and
  creates nothing.
- ADR-004 (event time on evidence): excerpts take the meeting's `occurred_at`.
- ADR-011 (library capture enrichment): the LINK phase, modes, audit and revert.
- ADR-012 (work-log linking): seeded-only linking of record items and the "whose
  development it is" rule.
