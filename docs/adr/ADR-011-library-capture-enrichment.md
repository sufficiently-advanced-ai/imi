# ADR-011: Library Capture Enrichment — Captures in the Graph, Seeded and Corroborated Entities

## Status

Proposed (2026-10-29). Applies ADR-006 to captures, which ADR-003 and ADR-006 leave outside the
graph. Changes no invariant of ADR-002, ADR-003 §1–3 or ADR-006 §2.

## Context

Measured on the personal deployment (holodeck), 2026-10-29:

| | Count |
|---|---:|
| Captures in the corpus | ~3,130 (+~2,500 being backfilled from the intake buffer) |
| Captures linked to anything in the graph | 0 |
| Graph `Document` nodes | 113, all meetings |
| Graph entities | 79: 37 person, 33 account, 8 project, 1 team |
| `topic` and `source` entities | 0, although `personal.yaml` declares both types |

Captures are most of what the owner reads: feeds, newsletters, videos, articles. They bypass the
ingest orchestrator (ADR-003, Consequences), so none of its entity phases run on them. What they
get instead is `capture_enrichment`, which writes free-text `topics` and `people` lists that are
never resolved. The vocabulary fragments: in the last two weeks' captures, `ai agents` (37) /
`ai-agents` (16), `enterprise ai` (17) / `enterprise-ai` (15) and `ai safety` (21) /
`ai-safety` (5) are each one topic.

As a result, the graph cannot answer the questions the library exists for:

- What do I know about this organization, technology or person, across everything I've read?
- What has happened on this topic or storyline recently, and from which sources?
- Which sources cover which topics, and what does the library not cover at all?
- What new evidence bears on an essay I am writing?

The owner also wants enrichment to run as continuous maintenance, with no approval queue: agents
act and report what changed. So unattended writes have to be safe by construction: gated where
pollution is possible, and audited and reversible everywhere.

ADR-003 measured what goes wrong when third-party text creates entities from a single document:
person nodes named "Got Re" and "Quick Hits". ADR-006 reopened creation for allowlisted types
behind entity admission. This ADR adds one more condition for unattended library creation:
corroboration.

## Decision

### 1. Library captures get a graph node

Each library-lane capture is represented as a `Document` node with `kind: capture`,
`lane: library`, `capture_id`, `url` and `occurred_at` from `source_date` (ADR-004). It is
a document, never a meeting (ADR-003 §3). Record-lane captures are out of scope here.

### 2. A `LINK` phase, run in batches outside the request

Linking does not run inside `POST /api/captures`. The 2026-10-29 forwarder incident showed
that a slow, in-request step turns client timeouts into silent data loss.

- `POST /api/captures/link` processes up to N unlinked captures and returns what it wrote.
- Captures carry `linked_at`. Unlinked means `linked_at` is null or older than the linker's
  version (`link_version`), so improving the linker can re-run it.
- imi owns the logic. A deployment's scheduler only calls the endpoint, which sets both the
  daily cadence and the backfill pace. Nothing outside imi writes Cypher to the graph.
- All writes go through the graph mutation layer with file write-through (invariant 2):
  capture documents and created entities exist in the corpus, not only in Neo4j.
  `occurred_at` comes from `source_date`, and `recorded_at` from the capture's ingest time
  (invariant 5). Links are MERGEd on (document, target, edge type), so re-running a batch is
  idempotent (invariant 3).

### 3. Three ways a library entity comes to exist

Entity types are deployment configuration (`config/domains/<domain>.yaml`, ADR-005). Each type
is created in exactly one of three ways:

| Tier | Types (personal deployment) | How a node is created | How library links |
|---|---|---|---|
| **Deterministic** | `source` | By rule from `source` / `source_id`: web host, YouTube channel, newsletter sender. No model. | `FROM_SOURCE`, always |
| **Seeded** | `topic`, `storyline`, `essay` | Only by the owner, or by sync from files the owner maintains (a topic vocabulary file, the essay map, the storyline file) | Link-only (ADR-006 §3): the model *selects* from the seeded set and never invents |
| **Corroborated** | `organization`, `technology`, `person` (public figures, as attribution targets per ADR-006 §5) | Entity admission (ADR-006 §4) **plus** corroboration: mentioned in ≥3 captures from ≥2 distinct sources within 30 days | Link to existing nodes; below the bar a name stays as text on the document, as under ADR-003 |

When a corroborated node is created, earlier captures that mention the name are back-linked in
the same run.

`account`, `project` and `team` are unchanged: library never creates them. A library capture
may link to an existing `account` or contact `person`. That link is how a new article about a
client reaches its entity without anyone being pinged.

**Topic growth.** When an unlisted theme recurs across sources (the domain's `emerging_theme`
pattern), the linker adds it to the type registry with status `provisional` and reports it in
the run's changelog. A provisional topic links like a seeded one. Only the owner makes it
`canonical` or removes it, and it never becomes canonical on its own.

### 4. Edge vocabulary

| Edge | From → to | Notes |
|---|---|---|
| `FROM_SOURCE` | Document → source | Deterministic |
| `ABOUT` | Document → topic | At most 3 per document |
| `MENTIONS` | Document → organization / technology / person / account | Substantive mention, not a passing name |
| `ADVANCES` | Document → storyline | The item moves the storyline |
| `EVIDENCE_FOR` | Document → essay | `stance: supports \| challenges \| context` |

Every edge carries `created_by` (`link_phase` or `owner`), `confidence`, `linked_at` and the
`run_id`. Library edges do **not** pin library items: ADR-003 §5 is unchanged, and only owner
engagement pins (§5 below).

### 5. Owner attachment

An owner attaching something to an entity is engagement in the ADR-003 §4 sense:

- `capture_thought` gains an optional `about` parameter (entity names or ids). The note is
  stored as a record capture, with `ABOUT` edges to the named entities resolved through the
  entity resolver. An unresolved name is returned to the caller; it never creates a node.
- Attaching an existing library item uses `graph_add_edge` with `created_by: owner`. That edge
  pins the item, clearing `stale_after`.

### 6. Unattended, audited, reversible

The `LINK` phase writes without approval. To keep that safe:

- Every node and edge it writes is also appended to `repo/memory/audit/links-YYYY-MM.jsonl`
  with its `run_id`, inputs and confidence.
- `POST /api/captures/link/revert {run_id}` removes that run's edges, and the nodes it created
  where nothing else references them. Captures themselves are never deleted.
- Each run returns a changelog (entities created, links by type, provisional topics) for the
  deployment to surface. The owner reviews outcomes after the fact rather than approving items
  beforehand.

### 7. Inference

A new operation, `capture_linking`, goes through `ClaudeClient.generate_message` with
`operation="capture_linking"`, so `config/inference.yaml` routes it like the other operations. Each call takes a batch of captures as title + summary + source, not full text. Like
`entity_link`, it receives candidate entities from the graph and the seeded lists, so most
decisions are selections. Corroborated creation still goes through the `entity_admission`
operation. A full backfill (~5,600 captures) is paced by the scheduler under the provider's
limits and shares capacity with live intake.

## Alternatives considered

- **A sidecar outside imi that writes the graph directly** (e.g. a scheduled script issuing
  Cypher). Rejected: it bypasses the resolver, admission and audit, and creates a second owner
  for the schema.
- **Route captures through the full ingest orchestrator.** Rejected for now: it extracts claims
  and signals per item, which is heavy for ~5,600 items and not needed for linking. Claims for
  library captures can follow later under ADR-006 §6.
- **Create entities on first mention (ADR-006 allowlist without corroboration).** Rejected: it
  reproduces the single-document pollution ADR-003 measured.
- **Keep topics as free tags and normalize strings.** Rejected: normalizing strings cannot merge
  synonyms or keep topics broad (the `personal.yaml` topic definition forbids products and
  one-off news), and tags cannot be traversed.
- **Discover topics by embedding clusters.** Deferred: cluster labels are unstable between runs,
  and the owner's themes already exist as a list.

## Consequences

**Easier:**
- "What do I know about X" and "what's been happening on Y" become graph reads over everything
  the owner has read, not similarity searches.
- A coverage map (topic × source counts) shows what the library covers and what it misses,
  which is the input for choosing new sources.
- A storyline's history is its `ADVANCES` edges, ordered by `occurred_at`. An essay's evidence
  is its `EVIDENCE_FOR` edges.
- A library item about a known client or contact reaches that entity's record, without a
  notification.

**Harder:**
- New surface to build and maintain: the `LINK` phase, two endpoints, an inference operation,
  the link audit and revert, and the `about` parameter.
- The deployment must maintain seed files: the topic vocabulary, essay map and storylines.
- Corroboration delays genuinely new organizations and technologies by days. A one-off mention
  stays text.
- The graph grows by roughly 5,600 `Document` nodes and an estimated 20–40k edges on the
  personal deployment. Graph-integrity metrics need watching per ADR-006.

## Open questions

- **Decay versus links.** Should `ADVANCES` or `EVIDENCE_FOR` edges extend a library item's
  `stale_after`, even though they are not owner engagement?
- **Record-lane captures.** Should `ai-circle-inbox` and owner notes be linked by the same phase?
  They are record, so ADR-003 already allows them richer extraction.
- **Public versus contact people.** Is a `relationship: public | contact` attribute on `person`
  enough to keep authors from mixing with the owner's network in profiles and recall?

## Migration (personal deployment)

1. Add `organization`, `technology`, `storyline` and `essay` types to `personal.yaml`, and set
   `lanes.yaml` `library.entities` to `allowlist` with `create_types: [organization, technology,
   person]`.
2. Seed the topic vocabulary (30–50 themes) and sync essays and storylines from their files.
3. Build `LINK`. Run it in `shadow` (audit records only, no graph writes) on 200 captures and
   calibrate thresholds on that sample, then turn it `on`.
4. Backfill the existing ~5,600 captures through the scheduler.
5. Keep the free-text `enrichment.topics` field for compatibility. Readers move to the links.

## Related

- ADR-003 (record and library lanes): link-only and pinning, which this ADR keeps.
- ADR-004 (event time on evidence): `occurred_at` on capture documents.
- ADR-005 (use-case packs): entity types as deployment configuration.
- ADR-006 (library-primary deployments): allowlist, seeded idea entities and attribution, which
  this ADR applies to captures and extends with corroboration.
- ADR-007 (agent-mediated intake): `capture_thought` remains judged per its channel rules; the
  `about` parameter does not change its lane.
