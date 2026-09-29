# ADR-004: Event Time on Evidence — Graph-Native Point-in-Time Queries

## Status

Accepted (2026-09-29). Implemented on `feat/event-time-evidence`. Supersedes Phase 1
("Temporal substrate — already implemented") of
`docs/prd/decision-state-and-world-model-prd.md`.

## Context

imi is meant to answer "what did the graph look like at time T" and "what have we learned
since". The query side exists: `get_state_at` and `get_active_relationships`
(`app/services/semantica_knowledge.py`) filter on `valid_from` / `valid_to`, and
`TemporalQueryService` (`app/services/temporal_queries.py`) composes them into `what_changed`,
`graph_as_of` and `temporal_blast_radius`.

The write side never supplies what those queries read:

- The main graph write path (`app/services/graph/neo4j_graph.py`,
  `app/services/graph/batch_writer.py`) writes `updated_at` only. Nothing writes `valid_from` or
  `valid_to` on entity nodes or entity relationships.
- Entity updates are `SET n += $props`: the previous value is overwritten.
- Relationships are one edge per `(source, type, target)`, merged on every ingest, stamped with
  wall-clock time.
- A `NULL` validity window passes the filter for every timestamp, so `graph_as_of` returns the
  current graph for any date and `what_changed` returns no changes.

The temporal queries are imi's own Cypher. Semantica's temporal module
(`semantica.kg.temporal_query`) is not used; it operates on in-memory dicts, and the
`GraphBuilder(enable_temporal=True)` constructed in `app/services/semantica_init.py` has no
callers.

Signals are the exception. They carry `source_timestamp`, `valid_from` and `valid_to`, link to
their source with `FROM_DOCUMENT`, and close their window on supersession.

**Record time is not event time.** Content is routinely backfilled: transcripts are added weeks
after the meeting, imports arrive in bulk. Any design that reads history from when imi recorded
something (node `created_at`, file mtime, git commit time) reports the backfill date. Ingest
already resolves event time — `observed_at = request.timestamp or content_ts or now`
(`ingest_orchestrator.py`) — but only signals and meeting files keep it.

### Measured state of the production corpus (2026-09-29)

| Item | Count | Event time available |
|---|---:|---|
| Meeting files | 24 | 24 with timezone-aware `start_time`. Events span four months; all were ingested on two consecutive days, so event time survived the backfill |
| Signals | 306 | 306 with timezone-aware `source_timestamp`, all equal to their meeting's `start_time`; 298 carry entity references by id |
| Captures | 3,805 | 3,791 with `source_date` (148 without a timezone); `valid_from` is empty on all of them; 2,066 have a `source_date` more than a day before `created_at` |
| Entity files | 70 | `created_at` is ingest time; people carry a `last_seen` date |
| Typed relationships in frontmatter | 8 asserted (16 edges with inverses) | None. Values are bare id strings with no source and no date |

Graph edges (1,603 total):

| Edge | Count | Properties | Datable from existing data |
|---|---:|---|---|
| `Signal -[MENTIONS / ASSIGNED_TO / FOR_CLIENT]-> Entity` | 675 | none | Yes, from the signal |
| `Signal -[FROM_DOCUMENT]-> Document` | 306 | none | Yes |
| `Entity -[MENTIONED_IN]-> Document` | 240 | none | Yes, for meeting documents |
| `Entity -[CO_OCCURRENCE]-> Entity` | 367 | `strength`, `shared_documents`, `context` | Yes — every pair shares at least one document |
| Typed entity relationships | 16 | `source`, `file_path`, `updated_at` | Inferred only — every pair shares at least one signal and one document |

`Document` nodes (94) carry no event time: 24 have `updated_at`, none has a date property. All
time values in the graph are strings; signal `valid_from` has value type `STRING`. Meeting times
are stored with a local offset (`-04:00`) while queries pass UTC ISO strings, so the comparison
is lexicographic across different offsets and can return the wrong answer.

## Decision

### 1. Time belongs to evidence; entities are timeless

Documents (meetings, conversations, library documents) and signals are events and carry event
time. An entity is an identity and has no validity window of its own. Every edge that asserts
something about an entity is attributable to the evidence that asserted it.

"Graph as of T" is the subgraph supported by evidence with `occurred_at <= T`. It is computed by
traversal, not by versioning nodes.

### 2. Two timestamps on every piece of evidence

| Property | Meaning | Source |
|---|---|---|
| `occurred_at` | When it happened (event time) | `request.timestamp`, else a date recovered from the content, else fallback |
| `recorded_at` | When imi ingested it | Server clock |
| `time_source` | How `occurred_at` was obtained | `explicit`, `content_header`, `inferred`, `fallback_now`, `unrecorded` |

`unrecorded` is what content written before this ADR reads as: it has an event time, but
how that time was obtained was not kept.

Point-in-time queries filter on `occurred_at`. `recorded_at` answers a different question ("what
arrived late", "what did we learn this week about last quarter") and is never used as a proxy
for event time. Evidence with `time_source = fallback_now` is reported by an audit and can be
excluded from point-in-time queries.

Like governance fields (ADR-002) and lanes (ADR-003), `recorded_at` and `time_source` are
server-assigned. `occurred_at` may come from the client only through the existing
`request.timestamp`.

### 3. Native temporal types

`occurred_at`, `recorded_at`, `valid_from` and `valid_to` are stored as Neo4j `DATETIME` values,
normalised to UTC, with range indexes on `Document.occurred_at`, `Signal.valid_from` and
`Signal.occurred_at`. Files keep ISO-8601 strings with an explicit offset; conversion happens at
the graph write (`app/utils/event_time.py`).

Readers are unaffected by the type change: both graph read boundaries
(`Neo4jClient.execute_read` / `execute_write` and the patched Semantica `execute_query`) return
temporal values as UTC ISO-8601 strings. `Signal.source_timestamp` and `created_at` stay strings;
they mirror the signal file.

### 4. One relationship edge per assertion

Typed entity relationships are keyed on their source evidence, so parallel edges accumulate
instead of overwriting:

```cypher
MATCH (a:Entity {id: $a}), (b:Entity {id: $b})
MERGE (a)-[r:WORKS_ON_PROJECTS {source_id: $source_id}]->(b)
SET r.occurred_at = datetime($occurred_at), r.recorded_at = datetime(),
    r.time_source = $time_source
```

First and last assertion are aggregates over those edges. Re-ingesting the same source merges
onto the same edge, so idempotency (invariant 3) holds. `source_id` is the id of the evidence's
`Document` node. A relationship nothing attributes (a bare id in a file written before this ADR,
or an edge added by hand without a source) has `source_id = ''` and no `occurred_at`: it is part
of the current graph and is left out of every point-in-time answer.

`MENTIONED_IN` edges are dated through their `Document`. `CO_OCCURRENCE` is derived data: a
point-in-time query computes it from `MENTIONED_IN` filtered by `occurred_at`. The materialised
`CO_OCCURRENCE` edge remains as a current-state cache and is not used for point-in-time queries.

### 5. Change over time is expressed as signals

Entity attribute history is not versioned. A change of role, status or ownership is a signal,
governed and superseded like any other. A relationship ends when later evidence says so, using
the same supersession mechanism; there is no second mechanism for closing relationship windows.

### 6. Supersession orders by event time

Supersession candidates were proposed for a newly ingested signal against standing signals
without comparing event time (`app/services/supersession_candidates.py`). A backfilled older
signal must never be proposed as superseding a newer one. Candidate direction is decided by
event time (`valid_from`, else `source_timestamp`); when the incoming signal is older, the
proposal is reversed: the candidate is attached to the standing, newer signal and names the
incoming one as the old signal, so the review queue reads the same in both directions. Reversed
candidates pass through the same `signal_relation` gate, with the roles swapped, and are written
in `PERSIST`, once the signal they name is stored. Signals with no readable event time keep
the previous behaviour.

### 7. Files carry the time data

Files remain the source of truth (invariant 2), so a rebuild must reproduce every dated edge:

- Meeting and document frontmatter: `start_time` remains the file key for `occurred_at`, so
  existing knowledge repos stay valid; `recorded_at` and `time_source` are added. Both are
  omitted when unknown, so a file written before this ADR is unchanged on disk.
- Entity frontmatter gains a `relationship_assertions` list, one entry per assertion:
  `{type, target, source_id, occurred_at, time_source, recorded_at}`. The typed keys
  (`works_on_projects: [project-x]`) stay as they are and remain the relationship list: every
  existing reader of those keys parses plain ids, and an object in that list would break them.
  An assertion dates a relationship; it does not create one.
- Captures default `valid_from` to `source_date`, as signals default it to `source_timestamp`.

### 8. Query surface

`TemporalQueryService` (`app/services/temporal_queries.py`) is rewritten over this model and
uses the async Neo4j client; it no longer needs Semantica. `SemanticaKnowledge.get_state_at`,
`get_active_relationships` and `get_provenance` delegate to it.

Five tools are registered in `TOOL_DEFS` and served on the external MCP surface:
`get_entity_at_time`, `find_relationships_at_time`, `find_changes`, `get_graph_at_time` and
`get_entity_provenance`. The chat agent keeps its existing tool names over the same service.

`find_changes` also returns `recorded_late`: evidence about a time at or before the start of the
window that was only ingested during it. That is the one place `recorded_at` is used.

## Regenerating the production graph

The existing corpus is sufficient to regenerate the graph under this model, with one class of
inferred dates.

| Element | Regenerable | Notes |
|---|---|---|
| Meeting documents (24) | Yes | `start_time` becomes `occurred_at`. `time_source` is not recorded today; none matches its ingest date, so none used the fallback |
| Signals and their edges (306 / 981) | Yes | No data change; string to `DATETIME` conversion only |
| `MENTIONED_IN` (240) | Yes | From meeting `entity_ids` |
| Co-occurrence (367) | Yes | Derived at query time |
| Typed relationships (16 edges) | Partly | No source or date in the files. The migration dates each from the earliest document that names both entities and marks it `time_source = inferred`. Re-running relationship inference per meeting would give stated dates (all 24 meeting files retain their transcript) but could also change which relationships exist, so it is kept out of the migration |
| Entity first and last seen | Yes | Derived from `MENTIONED_IN` |
| Captures (3,805) | Mostly | 14 lack `source_date`; 148 need a timezone assumption. Captures are not graph nodes today, so this affects recall, not graph rewind |
| Supersession history | Not applicable | No signal is currently superseded |

## Alternatives considered

- **Validity windows on entity nodes and edges** (the design the PRD assumed). Rejected. It
  needs a node version per change, records history only from the day it ships, and gives an
  entity a lifetime it does not have.
- **Rewind from git history.** Rejected. Commit time is record time, and backfill makes it
  unrelated to event time.
- **Adopt Semantica's temporal module.** Rejected. It queries in-memory dicts, not the stored
  graph, and uses a different field name (`valid_until`).
- **Version chains of entity state nodes.** Rejected. Signals already carry change over time
  with governance and supersession attached.
- **Reified assertion nodes** (`(:Assertion)-[:SUBJECT]->`, `-[:OBJECT]->`, `-[:EVIDENCED_BY]->`).
  Deferred. Parallel edges keyed on `source_id` cover the need with simpler traversals; reify
  if an assertion ever needs its own governance state.

## Consequences

**Easier:**
- Backfill is correct by construction: late content lands at its event time and appears in
  every point-in-time query from then on.
- Point-in-time queries are indexed range filters on typed values.
- Every relationship can be traced to the evidence that asserted it.
- The graph no longer depends on Semantica for temporal behaviour.

**Harder:**
- Edge counts grow with the number of assertions, not the number of pairs. Current-state
  queries must aggregate parallel edges or read a current-state cache.
- Relationship frontmatter changes shape; readers must accept both shapes during migration.
- Existing typed relationships get inferred dates unless re-extracted.
- Point-in-time traversals that derive co-occurrence cost more than reading a materialised edge.
- `neo4j_graph.py` and `ingest_orchestrator.py`, the two most frequently changed files, both
  change.

## Migration

For an existing knowledge base:

1. Deploy the code.
2. `python scripts/event_time.py audit --corpus <repo>` — report what the corpus is missing.
3. `python scripts/event_time.py migrate --corpus <repo> --apply` — add `recorded_at` to
   observation documents and attribute undated typed relationships (`time_source: inferred`).
   Idempotent; the default is a dry run. Commit the changed files.
4. Rebuild the graph from files (`scripts/rebuild_kb.py` or `POST /api/admin/rebuild-graph`
   with `clean`). A clean rebuild is required: edges written before this ADR have no
   `source_id`, so merging onto them would leave a second, unkeyed copy.
5. Verify: parity check, the audit, and a point-in-time query at each month boundary returning
   a graph that only grows.

## Related

- ADR-002 (evidence/instruction authority gate) — server-assigned fields follow the same rule.
- ADR-003 (record and library lanes) — library claims use `as_of` and `valid_from`; this ADR
  makes those values typed and indexed.
- `docs/prd/decision-state-and-world-model-prd.md` — R1.1 (validity-window coverage), R1.3
  (temporal queries over MCP) and R1.4 (bi-temporal need) are resolved here.
- `docs/architecture/entities-and-graph.md`, `docs/architecture/signals-and-governance.md` — to
  be updated when implemented.
