# ADR-012: Linking the Owner's Work Log to Seeded Items

## Status

Accepted (2026-11-02). Extends ADR-011, which linked library captures and left record-lane
captures to owner attachment ("Settled during review: record-lane captures"). Changes no
invariant of ADR-002, ADR-003 or ADR-011.

## Context

Measured on the personal deployment (holodeck), 2026-11-02:

| | Count |
|---|---:|
| Captures with `source: changes` (record lane) | 1,224, since 2026-10-29 |
| … of which `thread:lego` | 261 |
| … with `Supersedes:` lines | 393 lines, all matching an earlier item's claim exactly |
| Storyline evidence from the library backfill | 84 links, every one `stance: supports` |

`changes` is the owner's work log. An LCARS collector writes it 6 times a day from Claude Code
sessions, meetings and other first-party work, one evidence item per capture:

```
[progress · lego · done] Slope-brick roof ends are now checked …
Who: Scott
Entities: brickmy, roofplan
Evidence: session:…
Quote: "…"
Supersedes: <the claim text of an earlier item this one replaces>
```

Storylines (`docs/architecture/storylines.md`) track arcs from the library. Some arcs are the
owner's own builds, such as turning photos of a house into a Lego kit. Their evidence is in the
work log, not in anything the owner read, so under ADR-011 they get none. The work log is also
where setbacks are recorded (`kind: risk`, failed attempts), which the library evidence lacks.

ADR-003 keeps record content out of library-style unattended linking for a reason: record items
are first-party and feed entities, profiles and signals. The work log is first-party, but the
collector already filters and structures it, and linking it only to seeded items cannot create
or alter an entity.

## Decision

### 1. Record sources are opted in by name

`library.linking.record_sources` in `config/lanes.yaml` lists the capture `source` values whose
record-lane captures the LINK phase links (default: none). The personal deployment sets
`[changes]`. Lane assignment is unchanged (ADR-003, ADR-007): this setting reads the lane, it
never sets it.

### 2. Seeded items only

A record item gets `ABOUT` (topics), `ADVANCES` (storylines, with stance) and `EVIDENCE_FOR`
(essays), selected from the seeded lists by id as in ADR-011 §3. It gets no `FROM_SOURCE`, no
`MENTIONED_IN`, no new topics and no unresolved names. So it creates no entity, and it never
feeds the corroboration ledger. The work log's `entity:` tags are not resolved by this path;
first-party entity extraction stays the ingest orchestrator's job (ADR-003).

### 3. Work-log calls

Record items go to `capture_linking` in their own batches, marked `"work_log": true`, with only
the seeded lists. Each is sent as its own text, the claim line and its Who / Entities / Quote
lines (the enrichment summary of these items is often empty), without the Evidence and
Supersedes lines. The prompt's WORK LOG rule:

- an item advances a storyline only when it bears on the thesis, not because it shares the
  subject;
- progress, a measured result or a decision that bears the thesis out is `supports`; a risk, a
  blocker, a failed attempt or a limitation found is `challenges`; a question, a commitment or
  a step that does not move the thesis is `context`;
- a routine operational step (a deploy, a migration) is not a development unless it says what
  it showed.

`record_storyline_min_confidence` sets the storyline bar for these items (default: the
library's `storyline_min_confidence`). A work item is first-hand, so the model's confidence
measures relevance only. A single item also rarely bears on a thesis as strongly as a news
report does: in a 30-item sample with a matching thesis, relevant items scored 0.5–0.65.

### 4. Superseded claims stop counting

A `Supersedes: <claim>` line names an earlier item by its claim text. The match is on the
normalized claim of a record-source capture that did not occur after the superseding one.

- An item that is already superseded when a run sees it is not linked.
- When a run links the superseding item, the earlier item's link file gets
  `superseded_by: {superseding capture id: run id}`. Its links stay in the file, for the audit
  trail, but are no longer projected into the graph. So storyline evidence counts and reads
  show the current state, not the replaced one. An audit row (`action: supersede`) records it.
- `revert_run` drops the run's `superseded_by` entries, and the earlier links project again.

### 5. Everything else as ADR-011

The same runs, lock, modes, audit, revert and backfill (`POST /api/captures/link/backfill`
offers record items the storylines they have not been judged against). `Document` nodes and
edges carry `lane: record`. Decay, recall lanes and pinning are unchanged: linker edges never
pin (ADR-011 §5).

## Alternatives considered

- **Run the work log through the ingest orchestrator.** Rejected for now: it extracts claims,
  signals and entities per item, which is heavy for 6 batches a day of one-line items, and it
  is not needed to give a storyline its evidence.
- **Resolve `entity:` tags to entities.** Deferred: the tags are free text ("migration 0017",
  "worker v27"). Resolving them would create first-party entities from unreviewed text, which is
  the ADR-003 risk.
- **Delete superseded items' links.** Rejected: a revert would then have to re-create them,
  and the file would lose the record of what the item was linked to.
- **Weight evidence by supersession at read time.** Rejected: every read (evidence, counts,
  activity, lifecycle) would need the rule. Hiding the edges in the projection keeps one place.

## Consequences

**Easier:**
- Storylines about the owner's own builds get evidence, including setbacks (`challenges`).
- A storyline's evidence stays current when the work log supersedes a claim.

**Harder:**
- The linker has two payload shapes, and two storyline bars.
- Supersession depends on the collector repeating the earlier claim text exactly. A reworded
  `Supersedes:` line matches nothing, and the old item keeps counting.
- A storyline that mixes library and work-log evidence should be read with the `lane` column
  in mind: a news report and a work item are different kinds of evidence.

## Related

- ADR-003 (record and library lanes): the record lane, which this ADR leaves unchanged.
- ADR-004 (event time on evidence): work items carry `occurred_at` from `source_date`.
- ADR-011 (library capture enrichment): the LINK phase this extends.
