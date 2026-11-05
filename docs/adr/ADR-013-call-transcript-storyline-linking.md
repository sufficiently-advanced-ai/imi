# ADR-013: Linking Call Transcripts to Storylines, with Who Said It and to Whom

## Status

Accepted (2026-11-05), implemented in `app/services/meeting_linker.py`,
`meeting_transcript.py`, `meeting_link_store.py` and `graph/meeting_graph_writer.py`. Extends
ADR-011 (the LINK phase) and ADR-012 (record items linked to seeded items only). Changes no
invariant of ADR-002, ADR-003 or ADR-004.

Corpus check (§8), 2026-11-05, 36 calls: passed. A script flagged 16 places where an owner
line ends mid-sentence and the next, unlabeled line starts in lower case. All 16 were read,
5 of them in full context; none reads as the owner continuing unlabeled. They are the other
side breaking in. Categories on that day: 13 calls with every line labeled, 8 owner-labeled
with one other participant, 4 with 2+, 10 listing only the owner, 1 with no labels.

## Context

Storylines (`docs/architecture/storylines.md`) get evidence from library captures (ADR-011)
and the owner's work log (ADR-012). Calls are the third body of first-hand evidence. They
come in through meeting ingest (`add_call_transcript`, the Littlebird import), not captures,
so the LINK phase never sees them. One seeded storyline, *What small firms actually adopt*,
has no other source: what small firms actually do mostly comes up in calls (client
check-ins, calls about automating a firm's processes), and it has no evidence.

A call is not like a capture. One call bears on several storylines, in several voices, with
different stances. Say a client describes the narrow tool they actually pay for (supports),
and the owner makes a different point to the same client. For the owner, two questions
matter as much as the stance:

- **Who said it.** A client describing what their firm did is evidence about the field. The
  owner saying it is the owner's own view (ADR-012's WORK LOG rule).
- **Who was there when the owner said it.** Which people were on calls where the owner made
  a point about a storyline, and from whom the owner got each piece of evidence.

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

Today a meeting is one `Document` (`doc:meetings/<file>.md`) written by meeting ingest
(`MERGE … SET d += $props` in `app/services/graph/neo4j_graph.py`), with `MENTIONED_IN`
edges from entities named in it, `participants` in its front matter, and signals
(`FROM_DOCUMENT`) that paraphrase it ("Scott and Jenny agreed…"). Nothing records who said
what.

## Decision

### 1. The unit of evidence is an excerpt

The linker judges a call against the open storylines (seeded and proposed; not closed, not
`seed_status: retired`), the same set ADR-011 offers captures. No topics, essays or entities
are linked from calls. It returns **excerpts**: one speaker turn (§2), at most 8 lines, with
a one-sentence claim, a stance (`supports | challenges | context`) and a confidence.

Each excerpt becomes one `ADVANCES` edge from the meeting's `Document` to the storyline,
identified by its excerpt id (CLAUDE.md invariant 5: one edge per assertion):

```
excerpt_id = uuid5(NAMESPACE_URL, f"meeting-excerpt|{document_id}|{storyline_id}|{quote_sha256}")
quote_sha256 = sha256 of the quote with whitespace collapsed to single spaces and casefolded
```

The id depends on the words, not on line numbers, so inserting lines elsewhere in the
transcript does not change it, and the same passage returned twice gets the same id.

Limits per call: at most `max_excerpts_per_storyline` (default 3) excerpts on one storyline,
and at most `max_storylines_per_meeting` (default 5) storylines.

A call with several excerpts on one storyline is one piece of evidence. Storyline counts and
lifecycle promotion already use `count(DISTINCT d)` (`app/services/storylines.py`), and stay
that way.

### 2. Speakers: the server attributes, the model never names anyone

**Numbering.** The transcript section of the meeting file is numbered from 1, one number per
non-blank line. Front matter, summaries, key points and every other generated section are
excluded; the parser finds the transcript section in the format meeting ingest writes.
`transcript_sha256` is the sha256 of the numbered lines joined with `\n`. The model is sent
the numbered lines and returns line ranges; it never returns quotes or names. The server
cuts the quote from the lines verbatim.

**Labels.** A line is labeled when it starts with a speaker label: one to four words, each
starting with a capital letter, then `:` and a space
(`^([A-Z][\w.'’-]*(?: [A-Z][\w.'’-]*){0,3}): \S`). The label is the speaker of that line
only. Each line is one utterance, as in the Littlebird format measured above; the
implementation checks this on the corpus (§8) before building attribution.

**Owner.** The owner's name is `owner_name()` (`app/services/lane_admission.py`: lanes.yaml
`owner`, else `KB_OWNER_NAME`). The owner's names are that name, its first word, and the
aliases of the `Person` entity with that name, if there is one. A label is the owner's when
its normalized form (casefolded, punctuation removed, whitespace collapsed) equals one of
the owner's normalized names.

**Turns.** A turn is a maximal run of consecutive numbered lines that all carry the same
label, or that are all unlabeled. An excerpt's lines must lie inside one turn; an excerpt
the model returns across turns is dropped, with a `dropped: cross_turn` count in the run log.

**Attribution** of a turn:

| `speaker_basis` | When | `speaker` | `speaker_is_owner` |
|---|---|---|---|
| `label` | the turn is labeled | the label | `true` if the label is the owner's, else `false` |
| `sole_other` | the turn is unlabeled, the turns immediately before and after it are both owner-labeled, and `participants` minus the owner is exactly one person | that person | `false` |
| `unknown` | the turn is unlabeled, the turns immediately before and after it are both owner-labeled, and `participants` minus the owner is zero or 2+ people | none | `false` |
| `unknown` | any other unlabeled turn (at the start or end of the transcript, or next to a non-owner label) | none | `null` |

`null` means not known. Reads never treat it as `false`.

**Who was present** is the call's `participants` minus the owner (`with`,
`with_basis: participants`). It records attendance on the call, not that a person heard a
given line. When the call records no one besides the owner, `with` is empty and
`with_basis: unknown`. No audience is inferred from the transcript.

**Resolution.** Speaker and participant names resolve to `Person` entities among those with
a `MENTIONED_IN` edge to the meeting's `Document` (which meeting ingest already resolved),
by normalized name or alias. A name that does not resolve keeps `id: null` and stays a name.
This path creates no entity (ADR-003).

**Re-attribution.** Attribution is a pure function of the numbered lines, `participants`,
the owner's names and the resolution set. The link file stores
`attribution_sha256`, the sha256 of those inputs other than the lines. Each LINK run
recomputes it for every non-orphaned meeting link file. When it differs, the run
re-attributes the stored excerpts without a model call, rewrites the file, re-projects the
edges, and writes an audit row `action: reattribute`. A fixed import (more participants
recorded) therefore updates `with` and `sole_other` attribution on the next run.

### 3. Selecting excerpts

The model is called once per call with operation `meeting_linking`
(`ClaudeClient.generate_message`, routable in `config/inference.yaml`), prompt
`app/prompts/meeting_linking.xml` loaded through `app/services/prompt_loader.py`. A call with
more than `max_lines_per_part` (default 400) numbered lines is split into parts of that size
overlapping by `overlap_lines` (default 40); every part uses the call's global line numbers.

The server then, for the whole call:

1. drops excerpts below `storyline_min_confidence`, longer than 8 lines, or across turns;
2. merges excerpts with the same excerpt id, and excerpts on the same storyline whose line
   ranges overlap, keeping the one with the higher confidence;
3. per storyline, keeps at most `max_excerpts_per_storyline`: first the highest-confidence
   excerpt of each stance present, then the rest by confidence, preferring a speaker not
   yet kept on that storyline when confidences tie within 0.05;
4. keeps the `max_storylines_per_meeting` storylines with the highest best-excerpt
   confidence.

A meeting is linked all or nothing. If any part's call fails or returns invalid output, the
run writes no link file for it, logs the error, and the meeting stays eligible for the next
run.

### 4. Stance

The prompt carries ADR-012's rule on whose development it is, in this form. An excerpt
first has to bear on the storyline's thesis, not just share its subject. Then:

| The excerpt is | Stance |
|---|---|
| a report of what a firm or person did or found (observed behavior, a result, a purchase, an abandoned tool), by anyone | `supports` or `challenges`, per the thesis |
| an opinion or prediction from someone other than the owner | `supports` or `challenges`, per the thesis |
| the owner's opinion, argument or prediction | `context` |
| a question, a plan or a commitment | `context` |
| secondhand ("a friend said…") and not a report of what someone did | `context` |

Confidence measures how directly and clearly the excerpt bears on the thesis. It does not
measure how credible the speaker is; who spoke is recorded in the attribution, not in the
confidence.

### 5. What is stored

**Files are the source of truth** (CLAUDE.md invariant 2). A meeting's links live in
`memory/meeting_links/<meeting file stem>.json` (`app/services/meeting_link_store.py`):

```json
{
  "meeting_path": "meetings/meeting-<id>.md",
  "document_id": "doc:meetings/meeting-<id>.md",
  "link_version": 1,
  "mode": "on",
  "run_id": "link-…",
  "linked_at": "…",
  "occurred_at": "…",
  "transcript_sha256": "…",
  "attribution_sha256": "…",
  "orphaned_at": null,
  "storylines_checked": {"storyline-small-firm-adoption": "link-…"},
  "excerpts": [
    {"id": "…", "storyline_id": "storyline-small-firm-adoption", "stance": "supports",
     "confidence": 0.8, "claim": "<one sentence: what was said>",
     "lines": [212, 215], "quote": "<lines 212–215, verbatim>",
     "speaker": {"name": "<client>", "id": "person-<client>", "basis": "label"},
     "speaker_is_owner": false,
     "with": [{"name": "<client>", "id": "person-<client>"}], "with_basis": "participants",
     "run_id": "link-…", "linked_at": "…"}
  ],
  "undo": {"link-…": {"excerpts": [], "storylines_checked": {}}},
  "reverted_runs": []
}
```

- `mode` is `shadow` or `on`: the effective mode of the run that wrote it (§6). A shadow file
  is never projected.
- `storylines_checked` lists every storyline the call was judged against, whether or not an
  excerpt qualified, so a backfill can tell "examined, no match" from "never examined".
- `undo` holds, per run, the excerpts and `storylines_checked` entries that run replaced.
- `MEETING_LINK_VERSION` (in `meeting_link_store.py`, starting at 1) is bumped when the
  linker changes enough that existing files should be redone.

**The graph is a projection.** Each projected excerpt is an edge
`(d:Document {id: document_id})-[r:ADVANCES {excerpt_id}]->(s:Entity {id: storyline_id})`
with properties `stance`, `confidence`, `claim`, `quote`, `lines`, `speaker_id`,
`speaker_name`, `speaker_basis`, `speaker_is_owner` (absent when `null`), `with_ids`,
`with_names`, `with_basis`, `lane: record`, `created_by: link_phase`, `run_id`, `linked_at`
(DATETIME), and `meeting_link: true`. It never carries `capture_link`. Event time is the
meeting Document's `occurred_at` (ADR-004); there is no per-line time.

`app/services/graph/meeting_graph_writer.py` projects one meeting file:

1. `MATCH (d:Document {id: $doc})-[r:ADVANCES]->() WHERE r.meeting_link = true DELETE r`;
2. `MATCH (d:Document {id: $doc})`, `MATCH (s:Entity {id: row.storyline_id})`,
   `MERGE (d)-[r:ADVANCES {excerpt_id: row.id}]->(s) SET r += row.props` for each excerpt.

It never creates, merges or sets properties on a `Document`. If the Document does not exist,
the meeting is skipped and the run log records `missing_document`. The capture writer's
deletes stay scoped to `capture_link = true`, so neither writer removes the other's edges.

Every existing query or delete that assumes one `ADVANCES` edge per Document and storyline
is checked against this. That includes storyline re-check unlinks in
`app/services/capture_linker.py` and the owner link tools (`graph_delete_capture_link`),
which only act on capture link edges and leave `meeting_link` edges alone. Removing a single
excerpt by hand is out of scope; a run is undone with revert (§6).

**Rebuild.** `_reingest_meeting_links()` in `app/services/graph/neo4j_graph.py` runs right
after `_reingest_captures()` in the graph build. Meeting Documents and `Person` entities
already exist by then. It projects every meeting link file with `mode: on` and no
`orphaned_at`. It is non-fatal, like capture projection.

**Deleted meetings.** When a meeting file no longer exists, the stale-file cleanup deletes
its Document, and the edges with it. The link file is kept: the next LINK run sets
`orphaned_at`, lists it in the run log, and skips it. If a meeting file with the same stem
appears again, the run clears `orphaned_at`. It projects the stored excerpts if
`transcript_sha256` matches, and re-links the meeting otherwise.

### 6. Running it

```yaml
# config/lanes.yaml
library:
  linking:
    meetings:
      mode: shadow                   # off | shadow | on (default off)
      storyline_min_confidence: 0.75
      max_excerpts_per_storyline: 3
      max_storylines_per_meeting: 5
      max_lines_per_part: 400
      overlap_lines: 40
```

Meetings are record-lane content. The settings sit under `library.linking` because they
configure the LINK phase, as ADR-012's `record_sources` does. They read the lane and never
set it.

**Effective mode** is the lower of `library.linking.mode` and
`library.linking.meetings.mode`, ordered `off < shadow < on`.

**Trigger.** Meetings are linked by the existing LINK run (`POST /api/captures/link`): after
the captures, when the effective meeting mode is not `off`. They share its run id, lock,
audit log (`memory/audit/links-YYYY-MM.jsonl`, with `kind: meeting` on each row) and
lifecycle step. Meeting ingest does not trigger linking.

**Eligibility.** A meeting is linked when any of these holds:

- it has no link file;
- its file's `link_version` is below `MEETING_LINK_VERSION`;
- its current `transcript_sha256` differs from the file's;
- its file has `mode: shadow` and the effective mode is `on`.

Linking an eligible meeting judges it against every open storyline and replaces the whole
file's excerpts and `storylines_checked`, saving what it replaced in `undo[run_id]`.

**Backfill and re-check.** `POST /api/captures/link/backfill` gains
`kinds: ["capture", "meeting"]` (default both). For meetings, it judges each linked meeting
against the requested open storylines that are not in its `storylines_checked`, or against
all requested storylines when `recheck_before` applies. For each storyline judged, it
replaces that storyline's excerpts in the file, including with none. It saves what it
replaced in `undo[run_id]` and records the storyline in `storylines_checked`. It requires the
effective meeting mode to be `on`, as capture backfill requires `on`.

**Revert.** `POST /api/captures/link/revert {run_id}` also reverts meeting files. For each
file the run touched, it restores `undo[run_id]`, drops the run's excerpts and checks, adds
the run to `reverted_runs`, and re-projects. Status transitions revert as today.

### 7. Reads

**Evidence** (`GET /api/storylines/{id}/evidence`, MCP `get_storyline`) returns one row per
Document, so `max_results` / `evidence=N` and `count` count Documents, as storyline counts
do. Meeting rows carry `kind: meeting`, `document_id`, `title`, `occurred_at`, `lane`,
`source`, and `excerpts` (the stored excerpt objects of §5 for that storyline, in line
order). They have no row-level `stance` or `confidence`, and no `capture_id`,
`url` or `summary`. The response adds `excerpt_count`.

The `stance` filter applies to excerpts. New filters:

- `speaker=<person id | owner | others | unknown>`: `owner` is `speaker_is_owner: true`,
  `others` is `false`, `unknown` is `null`.
- `with=<person id>`.

Filters keep only matching excerpts and drop meeting rows left with none. Capture rows are
unchanged and are dropped by `speaker` and `with` filters.

**Voices** report what the files support and nothing more:

- `GET /api/storylines/{id}/voices?window_days=` returns, per speaker (resolved `Person`
  id, or normalized name with `id: null`):
  - `said`: counts by stance and by `speaker_basis`, for excerpts with
    `speaker_is_owner: false`;
  - `present_when_owner_said`: counts by stance of the owner's excerpts on calls where the
    person was in `with`.

  It also returns an `unattributed` bucket: counts by stance for excerpts whose speaker is
  unknown.
- `GET /api/people/{id}/storyline-voices?window_days=` returns the same two counts for one
  person, per storyline.
- MCP `list_storyline_voices(storyline_id | person_id, window_days, max_results)` takes
  exactly one of the two ids and returns the matching shape. It is defined in `TOOL_DEFS`
  and follows `docs/mcp_tool_conventions.md`.

Responses never say a person "holds" or "heard" a position. Descriptions and docs use "said"
and "was present when".

### 8. Acceptance

**Corpus check, before building attribution.** On the deployment's `meetings/`, confirm
that each labeled line is one whole utterance, with no unlabeled continuation lines after a
label, in all four categories of the Context table. If any category fails, stop and report
the failing calls with examples. Attribution as specified in §2 assumes the check passes.

**Fixtures** (unit tests, and eval cases run through `scripts/check_evals.sh` for the new
prompt) cover:

- each of the four transcript categories;
- an owner-labeled turn followed by an unlabeled turn;
- an excerpt spanning two turns (dropped);
- unlabeled turns at the start and the end;
- unknown speakers on a 2+ participant call;
- opposing stances on one storyline (both kept);
- an edit that inserts lines before an excerpt (same excerpt id);
- an edit that changes an excerpt's words (re-linked);
- a passage returned by two overlapping parts (one excerpt);
- a failed part (no file, still eligible);
- a participants change (re-attributed, no model call);
- shadow then `on` (re-linked and projected);
- backfill with no qualifying excerpt (`storylines_checked` recorded);
- re-check that removes excerpts, then revert (restored);
- a deleted meeting (orphaned, then restored);
- a clean rebuild reproducing the meeting edges exactly.

**Calibration, before `mode: on`.** Run shadow mode on the deployment over at least 10
calls covering the four categories, label the excerpts by hand, and set
`storyline_min_confidence` for the deployment from that sample (recorded in the PR), as
ADR-012 did for work items.

## Alternatives considered

- **Link the whole meeting `Document`.** Rejected: one stance per call loses the point, and
  there is no speaker.
- **Link the meeting's existing signals.** Rejected: signals paraphrase and merge voices
  ("Scott and Jenny agreed…"), so they cannot say who said it, and they are governed records
  (ADR-002) whose extraction has its own purpose.
- **Excerpt nodes** (`(:Excerpt)-[:SPOKEN_BY]->(:Person)`). Deferred: a new node label
  outside the domain schema, and every storyline read would need another hop. Edge
  properties are enough at this scale (36 calls, at most 15 excerpts each). Revisit if
  person pages need excerpts as first-class nodes.
- **Let the model attribute unlabeled lines** ("that was Michelle, she's doing the demo").
  Rejected: a confident wrong speaker is worse than `unknown`, and nothing could check it.
  If intake starts labeling every line, `basis: label` covers it with no change here.
- **Run calls through the capture path.** Rejected: calls are already ingested, with
  resolved entities and signals; a second copy would double them.
- **Reuse the capture link file and graph writer.** Rejected: a capture link is keyed by
  edge and target, so several excerpts on one storyline would collapse. The capture writer
  also replaces Document properties, which would erase meeting ingest's.

## Consequences

**Easier:**
- Storylines that live in conversations (small firms) get evidence, and each storyline shows
  who said what on which side.
- "What have I said about agent memory on calls Dan was on?" and "who has said something
  that challenges my local-models thesis?" become one read.

**Harder:**
- Attribution is only as good as the transcript: 15 of 36 calls today cannot name the other
  speaker. Reads show `speaker_basis` and `speaker_is_owner: null` so `unknown` is never
  read as the owner.
- The call import should record every participant. 11 of 36 calls list only the owner, and
  for those `with` is empty. That is a fix in the import (imi-upkeep), outside this ADR. Once
  it lands, re-attribution (§2) applies it without re-linking.
- Excerpts quote other people's words into storyline reads, which reviewers (the weekly
  LCARS review) see. They are the same words the transcript already holds, at the same
  visibility; they remain evidence, never instruction (ADR-002).
- The LINK phase gains a second link store, a second graph writer and a third payload
  shape, each with its own bar.

## Related

- ADR-003 (record and library lanes): meetings stay record; this path reads them and
  creates nothing.
- ADR-004 (event time on evidence): excerpts take the meeting's `occurred_at`.
- ADR-011 (library capture enrichment): the LINK phase, modes, audit and revert.
- ADR-012 (work-log linking): seeded-only linking of record items and the "whose
  development it is" rule.
