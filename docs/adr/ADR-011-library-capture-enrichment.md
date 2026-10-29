# ADR-011: Library Capture Enrichment — Captures in the Graph, Seeded and Corroborated Entities

## Status

Accepted (2026-10-29, revised the same day after a code review of the draft). Applies ADR-006
to captures, which ADR-003 and ADR-006 leave outside the graph. Changes no invariant of ADR-002,
ADR-003 §1–3 or ADR-006 §2. Implements ADR-003 §5 pinning for captures.

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

### 1. Linked captures get a graph node; their links live in the corpus

A linked capture is represented as a `Document` node with `kind: capture`, its `lane`,
`capture_id`, `url`, `title`, `source`, `occurred_at` from `source_date` and `recorded_at` from
the capture's `created_at` (ADR-004). It is a document, never a meeting (ADR-003 §3). The node
id follows the corpus convention, `doc:<path of the capture file>`.

The LINK phase (§2) covers library-lane captures. A record capture gets a node only when the
owner attaches it to something (§5).

**Files stay the source of truth (invariant 2).** A capture's links are stored in a link file
beside it, `memory/capture_links/<capture_id>.json`: the link version, run id, mode, every edge
with its provenance, and the names that did not resolve. The governed capture record itself is
not rewritten by linking. The graph is a projection of these files: a clean rebuild re-projects
every link file, so nothing the linker wrote lives only in Neo4j.

### 2. A `LINK` phase, run in batches outside the request

Linking does not run inside `POST /api/captures`. The 2026-10-29 forwarder incident showed
that a slow, in-request step turns client timeouts into silent data loss.

- `POST /api/captures/link` processes up to `max_results` unlinked library captures and returns
  what it wrote. One run at a time; a second concurrent call is refused.
- Unlinked means no link file, a link file older than the linker's version (`link_version`),
  or a shadow-mode link file when the linker is `on`. Improving the linker and bumping the
  version re-runs it.
- imi owns the logic. A deployment's scheduler only calls the endpoint, which sets both the
  daily cadence and the backfill pace. Nothing outside imi writes Cypher to the graph.
- Entities are created through the graph mutation layer with file write-through
  (`add_node`). Links are MERGEd on (document, target, edge type), so re-running a batch is
  idempotent (invariant 3).
- The mode is `library.linking.mode` in `config/lanes.yaml`: `off` (default), `shadow` (link
  files and audit records are written, nothing is projected into the graph and the linker
  creates no entity; seed sync still runs, since seeds are the owner's) or `on`.

### 3. Three ways a library entity comes to exist

Entity types are deployment configuration (`config/domains/<domain>.yaml`, ADR-005). Which
domain type plays which role is set in `lanes.yaml` `library.linking`; a role whose type the
domain lacks is skipped. Each type is created in exactly one of three ways:

| Tier | Roles (personal deployment) | How a node is created | How library links |
|---|---|---|---|
| **Deterministic** | `source` | By rule from `source` / `source_id` / content: web host, YouTube channel, newsletter sender. No model. | `FROM_SOURCE`, always |
| **Seeded** | `topic`, `storyline`, `essay` | Only by the owner, or by sync from seed files the owner maintains (`seeds/<role>.yaml` in the corpus) | Link-only (ADR-006 §3): the model *selects* from the seeded set by id and never invents |
| **Corroborated** | `library.entities.create_types` (personal: `organization`, `technology`, `person`) | Entity admission (ADR-006 §4) **plus** corroboration: mentioned in ≥3 captures from ≥2 distinct sources within 30 days of event time (`occurred_at`) | Link to existing nodes; below the bar a name stays as text in the link file, as under ADR-003 |

When a corroborated node is created, the earlier captures that mention the name are back-linked
in the same run. The mention ledger is not a separate store: it is read from the link files'
unresolved names, so it is rebuildable and auditable.

A corroborated `person` is created with `relationship: public`. A library mention links to an
existing person only on an exact or alias match of a full name (two or more name tokens); a
first name alone, or a fuzzy match, stays as text and blocks creation, so an author never
merges into the owner's contacts.

`account`, `project` and `team` are unchanged: library never creates them. A library capture
may link to an existing `account` or contact `person`. That link is how a new article about a
client reaches its entity without anyone being pinged.

**Topic growth.** When the model reports an unlisted theme and the same theme recurs under the
corroboration bar above, the linker creates a `topic` entity with `seed_status: provisional`
and reports it in the run's changelog. (The type registry tracks types, not instances, so the
status lives on the entity.) A provisional topic links like a seeded one. Only the owner makes it
canonical, by adding it to the topic seed file, or removes it; it never becomes canonical on its
own. A seed removed from its file becomes `seed_status: retired`: it is no longer offered to the
model, and its existing links stay.

### 4. Edge vocabulary

| Edge | Direction | Notes |
|---|---|---|
| `MENTIONED_IN` | entity → Document | Substantive mention, not a passing name. The existing mention edge, so `get_entity_at_time`, `find_changes` and co-mention reads see library mentions without change |
| `FROM_SOURCE` | Document → source | Deterministic |
| `ABOUT` | Document → topic (or, from the owner, any entity or capture) | At most 3 per document from the linker |
| `ADVANCES` | Document → storyline | The item moves the storyline |
| `EVIDENCE_FOR` | Document → essay | `stance: supports \| challenges \| context` |

These edges start or end at a `Document`, which the domain schema does not describe, so they are
a fixed vocabulary in code (as `MENTIONED_IN` already is); their entity endpoints are checked
against the role's domain type. Every edge carries `lane`, `created_by` (`link_phase` or
`owner`), `confidence`, `linked_at` and the `run_id`. Capture documents are excluded from
`CO_OCCURRENCE`: library never infers entity relationships (ADR-003 §3). They are also kept out
of the in-memory graph mirror that feeds entity profiles, since library never feeds profiles as
first-party fact (ADR-006 §2); graph reads see them.

Library edges do **not** pin library items: ADR-003 §5 is unchanged, and only owner engagement
pins (§5 below).

### 5. Owner attachment and pinning

An owner attaching something to an entity is engagement in the ADR-003 §4 sense:

- `capture_thought` gains an optional `about` parameter (entity ids or names, or capture ids).
  The note is captured as usual (the lane stays server-assigned; ADR-007), then gets `ABOUT`
  edges with `created_by: owner` to each entity resolved through the entity resolver, and to
  each named capture. An unresolved name is returned to the caller; it never creates a node.
- Attaching an existing capture uses `graph_add_capture_link` (capture id, entity, edge type);
  `graph_delete_capture_link` removes an owner link. `graph_add_edge` stays entity-to-entity.
- Pinning is implemented here (ADR-003 §5 specified it; nothing implemented it). An owner edge
  to or from a library capture adds the edge to the capture's `pinned_by`. Recall treats a
  pinned capture as fresh while `pinned_by` is non-empty. `stale_after` is kept, so removing
  the last pin restores decay rather than losing the horizon.

### 6. Unattended, audited, reversible

The `LINK` phase writes without approval. To keep that safe:

- Every link and entity a run writes or would write is appended to
  `memory/audit/links-YYYY-MM.jsonl` with its `run_id`, mode, inputs and confidence.
- `POST /api/captures/link/revert {run_id}` removes that run's links from the link files and
  the graph, and archives the entities it created when nothing else references them (an entity
  a later run has linked to stays). Owner links and captures are never removed. Reverted
  captures keep their link file, so they are not re-linked automatically; bumping
  `link_version` re-links them.
- Each run returns a changelog (entities created, links by type, provisional topics, names
  still below the bar) for the deployment to surface. The owner reviews outcomes after the fact
  rather than approving items beforehand.

### 7. Inference

A new operation, `capture_linking`, goes through `ClaudeClient.generate_message` with
`operation="capture_linking"`, so `config/inference.yaml` routes it like the other operations.
The prompt is `app/prompts/capture_linking.xml`. Each call takes a batch of captures as title +
summary + source, not full text, with the seeded lists and the existing entities whose names
appear in the batch. Seeded links are selections by id; anything not offered is discarded.
Mentions come back as names and are resolved by the entity resolver, not by the model.
Confidence is the model's own and is gated by `library.linking.min_confidence`, calibrated in
shadow mode. Corroborated creation still goes through the `entity_admission` decision operation.
A full backfill (~5,600 captures) is paced by the scheduler under the provider's limits and
shares capacity with live intake.

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
  the link files, audit and revert, the `about` parameter, two owner MCP tools and pinning.
- A clean rebuild also re-projects every link file.
- The deployment must maintain seed files: the topic vocabulary, essay map and storylines.
- Corroboration delays genuinely new organizations and technologies by days. A one-off mention
  stays text.
- The graph grows by roughly 5,600 `Document` nodes and an estimated 20–40k edges on the
  personal deployment. Graph-integrity metrics need watching per ADR-006.

## Settled during review (2026-10-29)

- **Decay versus links.** `ADVANCES` and `EVIDENCE_FOR` do not extend `stale_after`; only owner
  engagement pins (§5).
- **Record-lane captures.** Linked only when the owner attaches them (`about`, §5). Broader
  record linking is left for a later ADR; ADR-003 already allows record content richer
  extraction through the ingest orchestrator.
- **Public versus contact people.** `relationship: public` on library-created people, and
  full-name-only linking to existing people (§3).
- **Storage, edge direction, inference shape and provisional topics** are as written in §1, §4,
  §7 and §3 respectively; the first draft put links only in Neo4j, a new `MENTIONS` edge the
  temporal reads would not see, and provisional topics in the type registry.

## Open questions

- **Coverage reads.** The topic × source coverage map is a Cypher read today
  (`query_graph_cypher`). A dedicated tool can follow once the shape of the question settles.

## Migration (personal deployment)

1. Add `source`, `organization`, `technology`, `storyline` and `essay` types to `personal.yaml`
   (`topic` and `source` exist), and set `lanes.yaml` `library.entities` to `allowlist` with
   `create_types: [organization, technology, person]`.
2. Write the seed files `seeds/topic.yaml`, `seeds/storyline.yaml` and `seeds/essay.yaml` in the
   corpus (generated separately; format in `docs/architecture/capture-linking.md`). Each run
   syncs them first.
3. Set `library.linking.mode: shadow`, run `POST /api/captures/link {"max_results": 200}`,
   calibrate `min_confidence` and the corroboration bar on the audit records, then set `on`.
   Shadow link files are re-linked when the mode is `on`.
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
