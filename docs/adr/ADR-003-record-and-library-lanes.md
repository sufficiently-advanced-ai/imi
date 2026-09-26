# ADR-003: Record and Library Lanes — Stance-Based Admission

## Status

Accepted (2026-09-26). Implemented on `feat/memory-classifier` (2026-09-26): lane field and
lane-aware recall (§1, §6); admission for captures and the ingest `ADMIT` phase (§2);
library gates in the ingest pipeline and rebuild (§3); library decay via `stale_after`
(§5); backfill of migration steps 1, 2 and the file side of 4 (`scripts/stamp_lanes.py`).
Not yet built: `cites` / `about` pinning edges (§4, §5), splitting blended note+article
captures (migration step 5), and cleanup of entity nodes that library documents already
minted (graph-integrity work).

## Context

imi ingests two kinds of content through one pipeline:

- **What we were party to** — meetings, calls, business mail, our own notes and decisions, the
  communities we take part in.
- **What we watch** — articles, newsletters, videos, feeds and blogs from sources we follow.

The pipeline does not distinguish them. `IngestClassifier` (`app/services/ingest_classifier.py`)
classifies *format* (`call_transcript`, `email_thread`, `document`, …) and the result does not
change routing: every ingested item runs `BUILD_MEETING` → entity extraction → signal extraction
with the full `decision` / `action_item` taxonomy. Captures (`POST /api/captures`,
`capture_thought`, the openbrain import) bypass that pipeline entirely, so the split that does
exist is by *API*, not by what the content is.

A full classification of the production corpus with the decision model (Jev, 2026-09-26;
`scripts/classify_memories.py`, report in `~/Data/agent-context/memory-lanes-20260926/`) measured
the result:

| | memory | library | junk |
|---|---:|---:|---:|
| 7,445 captures | 571 (8%) | 6,213 (83%) | 661 (9%) |
| 11,335 signals | 3,791 (33%) | 7,461 (66%) | 83 (1%) |

Consequences observed:

- 1,418 of 2,090 signal-bearing "meetings" are third-party documents. They carry participants
  such as `Blogwatcher` and create person nodes like "Got Re" and "Quick Hits" — the source of
  much of the graph-integrity work.
- 95% of `decision` signals from third-party documents are news facts ("The Chinese Government
  imposed export controls…"), stored as *our* decisions.
- 378 first-person notes (ICP definition, essay frameworks, coaching calls) sit inside the
  openbrain import alongside ~2,800 article summaries, and rank against them in recall.
- Default recall ranks 73% library against 27% memory; no weighting fixes a ratio that size.

Format and entry point are the wrong axes. The property that determines how content should be
treated is **stance**: were we party to it?

## Decision

### 1. Every record carries a lane

| Lane | Meaning | Examples |
|---|---|---|
| `record` | We were party to it: participated in, authored, or were addressed by it personally | meeting transcripts and recaps, business mail to or from a known person, own notes and decisions, AI Circle discussions |
| `library` | Third-party published content we received or watched | articles, newsletters, blog posts, videos, podcasts, news, announcements, documentation |

Junk (transactional notices, promos, test fixtures, error pages, empty or boilerplate content) is
not a lane; it is dropped at admission (§2).

The lane is a field on signals, captures, agent memories and source-document nodes. One corpus,
one vector store, one graph — lanes are a partition, not separate systems.

**The lane is server-assigned, never client-settable** — the same rule ADR-002 applies to
governance fields. A client that could declare `record` could inject content into the business
graph with full extraction rights.

### 2. One admission gate for every entry point

A new first ingest phase, `ADMIT`, decides lane or drop before anything is built
(`PHASES` / `_phase_*` in `app/services/orchestrators/ingest_orchestrator.py`). Captures route
through the same gate; no entry point bypasses it.

Admission is decided in this order:

1. **Source configuration** assigns a default per source: meeting bots (Littlebird, Fathom,
   Fireflies, …) → `record`; RSS, YouTube, web clipping → `library`; mail, and any source marked
   mixed → per item.
2. **Deterministic rules** drop known junk: automated senders (DMARC, statements, receipts),
   test fixtures, empty content.
3. **The decision model** (Jev, operation `lane_admission`) judges per-item sources, and audits
   fixed-lane sources in `shadow` mode to catch misconfiguration. Same contract as
   `entity_admission`: batched, never raises, `off` / `shadow` / `on`.

**Mail criteria.** Addressed to us by a known person, or a thread we replied to → `record`. Bulk or
list mail (`List-Unsubscribe`, newsletter senders) → `library`. Transactional → drop.

**Asymmetric thresholds.** Filing our own meeting as library hides memory; filing an article as
record pollutes the graph. Both are bad, so:

- Dropping requires high confidence (as with `entity_admission.DROP_MIN_PROBABILITY`).
- Filing a per-item record as library requires high confidence when the sender or author is a
  known entity.
- Otherwise the source default wins.

**Dropped items are audited, not silently discarded.** Each drop is appended to an admission
audit log with its reason and a content hash, so drops are reviewable and repeats are idempotent.
Deduplication by `source_id` and then content hash (invariant 3) still runs before admission.

### 3. What each lane may produce

| | Record | Library |
|---|---|---|
| Graph node | meeting / conversation (participants) | document (author, publication, date) — never a meeting |
| Signal types | `decision`, `action_item`, `key_point`, `insight` | **`claim`** only: attributed (`attributed_to` author or publication) and dated (`as_of`) |
| Entities | may create people, accounts, projects via the resolver and admission gates | **link only**: may attach mention edges to entities that already exist; unresolved names stay as text on the document; never creates a node |
| Entity relationships | may infer | may not infer |
| Profiles | feeds entity profiles | does not feed profiles |
| Retention | permanent | decays unless referenced (§5) |
| Default recall | yes | opt-in (§6) |

A record signal inside a conversation may still report an outside fact. It is then attributed to
the speaker ("Ankit reported that…") and typed `key_point`, never `decision`.

Library claims use the existing temporal fields (`valid_from`, `superseded_by`), so "model X is
state of the art" can be superseded like any other signal. Library content is evidence-grade
only; nothing in this ADR creates an instruction path, and ADR-002 applies unchanged.

### 4. Overlap is by reference, never by copy

A library item never becomes a record. Our *engagement* with it does:

| Situation | Result |
|---|---|
| An article is discussed in a meeting or circle | The record signal gets a `cites` edge to the library document |
| We write a note about something we read | The note is a `record` capture with an `about` edge to the library item; note and article are stored separately, not blended into one text |
| A community discussion is mostly about outside news (AI Circle) | `record` — we are party to it; outside facts are attributed to the speaker |
| A vendor we do business with writes to us personally | `record`; the same vendor's newsletter is `library` |
| Content is pasted into a meeting or note verbatim | It belongs to the record it was pasted into; a separate library item is created only if it was also captured from its source |

A `cites` or `about` edge **pins** the library item (§5).

### 5. Library retention: decay unless referenced

Admission sets `stale_after` on each library item, scaled by the decision model's durability
judgment. A stale library item is excluded from all recall but stays in the corpus: decay is
**not deletion**, and files remain the source of truth (invariant 2). Any `cites` or `about` edge
from a record clears `stale_after`, and the item is kept for as long as that edge exists. Deleting
old library content outright is out of scope for this ADR.

### 6. Recall

`RecallRequest` gains a `lanes` parameter, default `["record"]`. When library is requested, it is
ranked and returned **separately** (a background section), never merged into one score with
record results. `memory_recall`'s re-hydration step re-reads the lane from the authoritative file,
as it already does for governance fields.

## Alternatives considered

- **Separate library system or store.** Rejected. Overlap is the valuable part: citing, pinning,
  and linking to known entities all need one corpus and one graph.
- **Recall weighting only.** Rejected. It leaves the graph pollution and fake decisions in place,
  and a 73/27 ratio swamps any reasonable weight.
- **A separate "world" entity layer for library mentions.** Rejected for now as heavier than
  needed. Link-only captures the useful joins without creating nodes, and can be revisited if
  library-side entity analysis is ever wanted.
- **Classify by format (extend `IngestClassifier`).** Rejected. Format does not determine stance:
  an email can be a newsletter or a client thread, and a transcript can be our call or a
  conference talk.

## Consequences

**Easier:**
- Default recall returns what we did, decided and committed to, without scoring it against
  thousands of articles.
- Graph integrity improves at the source: third-party text can no longer create people,
  participants, or relationships.
- `decision` and `action_item` mean something again: they exist only where we were party to the
  conversation.
- Library content stays useful — searchable on request, citable, and linked to known entities.

**Harder:**
- Every entry point must route through `ADMIT`, including captures, which do not use the ingest
  orchestrator today.
- Every store and index needs the lane field, and recall and re-hydration must filter on it.
- Per-item admission adds one decision-model call per mail item; bulk backfills must pace under
  the provider's rate cap (~1,000 requests/min on DigitalOcean, shared with live ingest).
- Stance is sometimes ambiguous (a forwarded article with our comment, a webinar we attended).
  §4 settles the common cases; the rest fall to thresholds and review.

## Migration

The 2026-09-26 verdicts are the backfill input; the model does not need to be re-run.

1. Add the lane field and a default-record recall filter; stamp lanes from the verdicts.
2. Reject junk captures and signals through the review state machine (auditable, reversible) —
   not by deleting files.
3. Convert library-lane signals to `claim` with attribution; the 446 misfiled library
   "decisions" become claims.
4. Re-home the 1,418 library "meetings" as document nodes and remove their participant and entity
   side effects. Coordinate with the graph-integrity work.
5. Split notes from source text where one capture blends both (the ai-circle-inbox pattern).
6. Turn on `ADMIT` in `shadow`, compare against the verdicts, then `on`.

## Related

- ADR-002 (evidence/instruction authority gate) — lanes are orthogonal to authority; library
  content is evidence-grade only.
- ADR-001 (signals vs decision records) — the `decision` type is now restricted to the record lane.
- `app/services/entity_admission.py` — the admission pattern `ADMIT` reuses.
- `scripts/classify_memories.py` — the classifier that produced the measurements and backfill
  verdicts.
- `docs/architecture/memory.md`, `docs/architecture/ingestion-pipeline.md` — to be updated when
  implemented.
