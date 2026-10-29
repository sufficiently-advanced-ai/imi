# Capture Linking (the LINK phase)

> **Audience:** developers and operators wiring library captures into the graph ·
> **Source of truth:** `app/services/capture_linker.py`, `app/services/capture_link_store.py`,
> `app/services/graph/capture_graph_writer.py` ·
> **Decision record:** [ADR-011](../adr/ADR-011-library-capture-enrichment.md) ·
> **See also:** [Memory](memory.md) · [Entities & Graph](entities-and-graph.md)

Captures (`POST /api/captures`, `capture_thought`) bypass the ingest orchestrator, so on
their own they never reach the graph. The LINK phase links **library** captures to sources,
topics, storylines, essays and the entities they discuss, in batches, outside the request
that created them.

## Running it

```bash
# one run: up to max_results unlinked library captures (default 50, max 500)
curl -X POST localhost:8000/api/captures/link -H 'content-type: application/json' \
     -d '{"max_results": 50}'
# undo one run
curl -X POST localhost:8000/api/captures/link/revert -H 'content-type: application/json' \
     -d '{"run_id": "link-20261029T..."}'
```

A deployment's scheduler only calls the endpoint; its cadence sets the daily pace and the
backfill pace. One run at a time (a concurrent call gets `409`). The run is shielded from
client disconnects, so a proxy timeout does not stop it; its changelog is also the last row
of the run in the audit log. Size `max_results` to your proxy timeout: each
`capture_linking` call takes `batch_size` captures, with `concurrency` calls in flight.

The response is the run's changelog:

| Key | Meaning |
|---|---|
| `run_id`, `mode`, `link_version` | identity of the run |
| `processed`, `remaining` | captures linked this run; unlinked captures still waiting |
| `links_by_type` | count per edge type, including back-links |
| `sources_created` | source entities created by rule |
| `entities_created`, `provisional_topics` | corroborated entities and topics created |
| `would_create` | shadow mode: what `on` would have created |
| `admission_dropped` | corroborated names entity admission rejected |
| `below_bar` | the names closest to the corroboration bar (top 25) |
| `seeds` | seed sync results per role |

## Configuration (`config/lanes.yaml`)

```yaml
library:
  entities:
    mode: allowlist
    create_types: [organization, technology, person]  # the corroborated tier
  linking:
    mode: shadow          # off (default) | shadow | on
    batch_size: 10
    concurrency: 2
    min_confidence: 0.6
    roles:                # which domain type plays each role; null disables a role
      source: source
      topic: topic
      storyline: storyline
      essay: essay
    corroboration:
      min_captures: 3
      min_sources: 2
      window_days: 30
```

- **off**: the endpoint does nothing.
- **shadow**: link files and audit rows are written, nothing is projected into the graph and
  the linker creates no entity (seed sync still runs: seeds are the owner's). Use it on a
  sample, read the audit rows, tune `min_confidence` and the bar.
- **on**: acts. Shadow link files count as unlinked again, so they are redone.

The model call is operation `capture_linking` (`app/prompts/capture_linking.xml`); route it
in `config/inference.yaml`. Corroborated creation also calls `entity_admission`.

## Three tiers of entity

| Tier | Roles | How a node comes to exist |
|---|---|---|
| Deterministic | `source` | By rule (`app/services/capture_sources.py`): web host, YouTube `**Channel:**` header, mail sender address. Id `source-<slug of the key>`; idempotent |
| Seeded | `topic`, `storyline`, `essay` | Seed files or the owner. The model selects ids from the offered lists; anything not offered is discarded |
| Corroborated | `library.entities.create_types` minus the role types | A name the resolver cannot place is kept as text in the link file. When it appears in `min_captures` captures from `min_sources` distinct sources within `window_days` (by `occurred_at`), it goes to entity admission, is created, and every capture that named it is back-linked |

People are stricter: a library mention links to an existing person only on an exact or
alias match of a full name. A first name or a fuzzy match is never linked and never created.
Library-created people carry `relationship: public`.

An unlisted recurring theme (the model's `new_topics`) crosses the same bar and becomes a
`topic` with `seed_status: provisional`. It links like a seeded topic. Adding it to the topic
seed file makes it canonical; deleting the node removes it.

## Seed files

One YAML file per seeded role in the corpus: `seeds/topic.yaml`, `seeds/storyline.yaml`,
`seeds/essay.yaml`. Each is a list (or a mapping holding one list):

```yaml
- name: AI agents                 # required (title is accepted too)
  id: topic-ai-agents             # optional; default <type>-<slug of name>
  aliases: [agentic AI, ai-agents]
  description: Autonomous systems that plan and act with tools.   # shown to the model
- AI safety                       # a bare string is a name
```

Every run syncs them first (`app/services/library_seeds.py`): new seeds are created
(`seed_status: canonical`), changed names, aliases and descriptions are updated, a seed
removed from its file becomes `seed_status: retired` (no longer offered; its links stay), and
a provisional topic listed in the file becomes canonical. A role with no seed file is left
alone. Nodes of a seeded type that the owner created by hand are offered too.

## Storage: link files are the source of truth

`memory/capture_links/<capture_id>.json`, one per linked capture, beside the governed capture
record (which linking never rewrites):

```json
{"capture_id": "…", "link_version": 1, "mode": "on", "run_id": "link-…",
 "linked_at": "…", "lane": "library", "occurred_at": "2026-10-01T09:00:00Z",
 "source_key": "web:stratechery.com",
 "links": [{"edge": "ABOUT", "target_id": "topic-ai-agents", "target_type": "topic",
            "confidence": 0.9, "created_by": "link_phase", "run_id": "link-…",
            "linked_at": "…"}],
 "unresolved": [{"name": "Acme Robotics", "entity_type": "organization",
                 "kind": "mention", "confidence": 0.9}],
 "reverted_runs": []}
```

The unresolved names across all link files are the corroboration ledger; there is no other
store. Every link, creation and revert is also appended to
`memory/audit/links-YYYY-MM.jsonl`. A run commits its link files and audit file in one commit;
created entities are committed by the graph mutation layer as usual.

Bump `LINK_VERSION` (`capture_link_store.py`) when the linker changes enough that existing
links should be redone: older files count as unlinked.

## The graph projection

`CaptureGraphWriter` turns a link file into a `Document` node
(`id: doc:memory/captures/<id>.json`, `kind: capture`, `lane`, `capture_id`, `url`, `title`,
`source`, `summary`, `occurred_at` from `source_date`, `recorded_at` from `created_at`) and
its edges, each marked `capture_link: true` with `lane`, `created_by`, `confidence`,
`linked_at`, `run_id` and `stance`:

| Edge | Direction |
|---|---|
| `MENTIONED_IN` | entity → Document (the existing mention edge: `get_entity_at_time`, `find_changes` and co-mention reads see library mentions) |
| `FROM_SOURCE` | Document → source |
| `ABOUT` | Document → topic, or (owner) any entity or capture Document |
| `ADVANCES` | Document → storyline |
| `EVIDENCE_FOR` | Document → essay, with `stance` |

Each write replaces the edges the document owns, so the graph always matches the file. A
clean rebuild re-projects every link file (`Neo4jKnowledgeGraph._reingest_captures`).
Capture documents are excluded from `CO_OCCURRENCE` and from the in-memory graph mirror that
feeds entity profiles.

Useful reads (`query_graph_cypher`):

```cypher
// what has been happening on a storyline
MATCH (d:Document {kind: 'capture'})-[:ADVANCES]->(s:Entity {id: $storyline})
RETURN d.title, d.url, d.occurred_at ORDER BY d.occurred_at DESC
// coverage: topic x source
MATCH (src:Entity)<-[:FROM_SOURCE]-(d:Document {kind: 'capture'})-[:ABOUT]->(t:Entity)
RETURN t.name, src.name, count(d) ORDER BY count(d) DESC
```

## Owner links and pinning

- `capture_thought(about=[…])` links the new note `ABOUT` each named entity (id or exact
  name) or capture id. Unresolved names come back in `about.unresolved`; nothing is created.
- `graph_add_capture_link` / `graph_delete_capture_link` (MCP and chat) add or remove an
  owner link from any capture (`about`, `mentions`, `advances`, `evidence_for`).

Owner links survive re-linking and revert. An owner link from or to a library capture adds an
entry to its `pinned_by`; recall keeps a pinned capture even past `stale_after` (ADR-003 §5).
`stale_after` itself is kept, so removing the last pin restores decay.

## Revert

`POST /api/captures/link/revert {run_id}` removes that run's `link_phase` links from every link
file (and the graph), records the run in each file's `reverted_runs`, and archives the
entities the run created when no link file references them and they have no other edge.
Reverted captures keep their `linked_at`, so the next run does not redo them; bump
`LINK_VERSION` after fixing whatever went wrong to re-link them.
