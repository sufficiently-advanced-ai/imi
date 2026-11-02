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
- Every transition is appended to the storyline's history.

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

## Reads and writes

| REST | MCP | |
|---|---|---|
| `GET /api/storylines?status=&window=` | `list_storylines` | each storyline with evidence counts: last `window` days vs the window before |
| `GET /api/storylines/{id}?as_of=&evidence=` | `get_storyline` | one storyline, recent evidence, past state |
| `GET /api/storylines/{id}/evidence?since=&until=&stance=` | `get_storyline` (`date_from`, `date_to`, `stance`) | evidence newest first by `occurred_at` |
| `GET /api/storylines/{id}/history` | | versions and transitions |
| `PATCH /api/storylines/{id}` | `update_storyline` | reviewer fields only |
| `POST /api/storylines` | `propose_storyline` | idempotent on the name |
| `GET /api/activity?type=&window=` | `list_entity_activity` | per-entity counts for any type: topics by `ABOUT`, storylines by `ADVANCES`, others by `MENTIONED_IN` |

Each read is one aggregate Cypher query over the `Document.occurred_at` index; counts use
typed datetime parameters.

## Not yet

Planned next: a stance on `ADVANCES` links and a higher linker bar for storylines,
server-side lifecycle (proposed → active on evidence, active → dormant after 30 quiet days),
a storyline-only backfill pass, and linking record-lane `changes` captures (needs an ADR,
since ADR-011 left record-lane linking to the owner).
