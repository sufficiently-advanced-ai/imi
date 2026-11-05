# Storylines

> **Audience:** developers and agents tracking long-running arcs ·
> **Source of truth:** `app/services/storylines.py`, `app/services/library_seeds.py`,
> `app/routes/storylines.py` ·
> **See also:** [Capture Linking](capture-linking.md) · [ADR-011](../adr/ADR-011-library-capture-enrichment.md)

A storyline is a long-running, falsifiable arc the owner is tracking ("local open models are
closing on frontier"). Unlike a topic, which is a bucket, it has a thesis, a current state, a
direction, and evidence that supports it or cuts against it. It is the `storyline` role of
the LINK phase (`library.linking.roles.storyline` in `config/lanes.yaml`): a seeded type
whose evidence is capture Documents linked to it by `ADVANCES` edges. Evidence time is the
Document's `occurred_at` (ADR-004).

## Fields and who writes them

Each field has exactly one writer.

| Field | Writer |
|---|---|
| `name`, `aliases`, `description`, `thesis`, `stakes` | the seed file, synced every run |
| `would_change`, `open_question` | the seed file **while empty** (on create, or after being cleared); the reviewer after that |
| `state`, `direction` (strengthening / steady / fading / resolved), `confidence` (low / med / high) | the reviewer: `update_storyline` / `PATCH` |
| `status` (proposed / active / dormant / closed), `seed_status`, `status_changed_*`, `closed_by`, `last_reviewed`, `proposed_by` | the server only |

Server-owned fields are refused from every client write: the storyline endpoints, MCP
`graph_add_node` / `graph_update_node`, and the REST entity editor (an edit form that echoes
an unchanged value is not refused).

## Status

- **Seeded** (listed in `seeds/storyline.yaml`): created `active`. `status: closed` in the file
  closes it; removing that line reopens it. A closed storyline is not offered to the linker
  and refuses review writes.
- **Proposed** (`propose_storyline`, `POST /api/storylines`, or `graph_add_node`): created
  `seed_status: provisional`, `status: proposed`, and offered to the linker so it can gather
  evidence. Listing it in the seed file adopts it: canonical and `active`.
- **Lifecycle** (server side, at the end of every LINK run and backfill in mode `on`;
  `app/services/storylines.py`):
  - proposed → active at 5 or more `ADVANCES` with `occurred_at` in the last 14 days
  - active → dormant with none in the last 30 days, and no status change in them (a new
    storyline gets a grace period)
  - dormant → active on any evidence in the last 30 days
  - Each transition is a `storyline_status` audit row of the run, so `POST
    /api/captures/link/revert` with the run id restores the previous status.
- Every transition is appended to the storyline's history.

## Evidence and stance

The LINK phase offers every open storyline (with its thesis) to `capture_linking` and writes
`ADVANCES` with a `stance` judged against the thesis: `supports`, `challenges` or `context`.
Storylines take a higher bar than topics: `library.linking.storyline_min_confidence`
(default 0.75) and at most `max_advances` (default 2) per capture.

Each link file records the storylines its capture was judged against
(`storylines_checked`, storyline id → run id). `POST /api/captures/link/backfill
{storyline_ids?, since?, max_results}` offers already-linked captures only the storylines they
have not been judged against, newest first by `occurred_at`: how a new or proposed storyline
gets its history. It shares the LINK run lock (409 while one runs), and its run id reverts
like a LINK run.

### Call transcripts (ADR-013)

With `library.linking.meetings.mode` on, the same LINK run also judges each call in
`meetings/` against the open storylines. The unit is an **excerpt**: one speaker turn of at
most 8 transcript lines, with a claim, a stance and a confidence, stored in
`memory/meeting_links/<meeting stem>.json` and projected as one `ADVANCES` edge per excerpt
(`meeting_link: true`, MERGEd on `excerpt_id`). A call with several excerpts is still one
piece of evidence: counts and the lifecycle use `count(DISTINCT d)`.

The model returns line numbers only. The server cuts the quote and attributes it:

| `speaker_basis` | When | `speaker_is_owner` |
|---|---|---|
| `label` | the turn has a `Name:` label | `true` for the owner's names, else `false` |
| `sole_other` | unlabeled, between two owner turns, exactly one other participant | `false` |
| `unknown` | unlabeled, between two owner turns, zero or 2+ other participants | `false` |
| `unknown` | any other unlabeled turn | `null` (not known) |

`with` is the call's `participants` minus the owner: who was on the call, not who heard a
given line. Names resolve to `Person` entities meeting ingest already linked to the call;
nothing is created. When the participants (or the owner's aliases) change, the next run
re-attributes the stored excerpts without a model call.

`POST /api/captures/link/backfill` takes `kinds: ["capture", "meeting"]` (default both);
`/link/revert` undoes meeting files too. Meeting ingest never triggers linking.

## History

`memory/storyline_history/<id>.jsonl` in the corpus, append-only, one row per proposal, review
write and status transition (`at`, `actor`, `event`, `fields`, `status_from`, `status_to`).
`get_storyline(..., as_of=)` folds the rows up to that time to give the state as it stood then;
`GET /api/storylines/{id}/history` returns the rows newest first.

## Seed file

```yaml
# seeds/storyline.yaml
- name: Local open models closing on frontier
  id: storyline-local-open-models          # optional; default storyline-<slug of name>
  description: Open-weight releases, evals comparing them to frontier models.   # shown to the linker
  thesis: Open-weight models reach frontier parity on everyday work within a year.
  stakes: [build, writing]                 # practice | build | writing | learning
  open_question: Which workloads cross first?            # seeded while empty
  would_change: Frontier lead widening on agentic evals. # seeded while empty
  # status: closed
```

Seeds sync at the start of every LINK run (`POST /api/captures/link`).

Rewording a thesis changes what future captures are judged against, not what was already
judged. To re-judge the existing captures against the new thesis, run a re-check
([Capture Linking](capture-linking.md#re-check)): the storyline-only backfill with
`storyline_ids` and `recheck_before`.

## Reads and writes

| REST | MCP | |
|---|---|---|
| `GET /api/storylines?status=&window=` | `list_storylines` | each storyline with evidence counts: last `window` days vs the window before |
| `GET /api/storylines/{id}?as_of=&evidence=` | `get_storyline` | one storyline, recent evidence, past state |
| `GET /api/storylines/{id}/evidence?since=&until=&stance=&linked_since=&speaker=&with=` | `get_storyline` (`date_from`, `date_to`, `linked_from`, `stance`, `speaker`, `with`) | evidence newest first by `occurred_at`, one row per Document; capture rows have `linked_at` and a stance, call rows (`kind: meeting`) have `excerpts` |
| `GET /api/storylines/{id}/voices?window_days=` | `list_storyline_voices(storyline_id)` | per person: excerpts they `said` (by stance and `speaker_basis`), and the owner's excerpts on calls they were `present_when_owner_said`; plus `unattributed` |
| `GET /api/people/{id}/storyline-voices?window_days=` | `list_storyline_voices(person_id)` | the same two counts for one person, per storyline |
| `GET /api/storylines/{id}/history` | | versions and transitions |
| `PATCH /api/storylines/{id}` | `update_storyline` | reviewer fields only |
| `POST /api/storylines` | `propose_storyline` | idempotent on the name |
| `GET /api/activity?type=&window=` | `list_entity_activity` | per-entity counts for any type: topics by `ABOUT`, storylines by `ADVANCES`, others by `MENTIONED_IN` |

`since` / `until` filter on when the evidence happened (`occurred_at`); `linked_since` filters
on when it was linked to the storyline (the `ADVANCES` edge's `linked_at`). A reviewer that
reads "what is new since my last review" should use `linked_since`: a backfill links items that
happened long ago, and `since` would hide them.

`stance` filters call excerpts. `speaker` takes a person id, `owner` (`speaker_is_owner:
true`), `others` (`false`) or `unknown` (`null`); `with` takes a person id. Both keep only
matching excerpts, so they drop capture rows. `max_results` counts Documents; the response
adds `excerpt_count`.

Each read is one aggregate Cypher query over the `Document.occurred_at` index; counts use
typed datetime parameters.

## Not yet

A timeline view and an `ask_kb` storyline hint. Record-lane work-log captures are linked
since ADR-012 ([Capture Linking](capture-linking.md#record-lane-sources)); their evidence rows
carry `lane: record`.
