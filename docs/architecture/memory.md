# Memory & Vector Layer

> **Audience:** developers/agents using recall, writeback, or swapping vector backends ·
> **Source of truth:** `app/services/memory_recall.py`, `app/services/signal_indexing.py`,
> `app/core/tenancy/backends/sqlite_vector_store.py` ·
> **See also:** [Signals & Governance](signals-and-governance.md) · [MCP & API](mcp-and-api.md)

The memory layer gives agents governed, semantic recall over everything the system knows —
and a safe way to write their own operational memory back.

## Three governed record kinds, one trust axis

| Kind | Model | Origin | Default posture |
|---|---|---|---|
| **Signal** | `app/models/signal.py` | extracted from meetings/documents during ingestion | `inferred` / `pending` / evidence-only |
| **CapturedMemory** | `app/models/captured_memory.py` | captured thoughts and imported evidence (`POST /api/captures`, `capture_thought` MCP tool); sha256 `content_fingerprint` dedup | `imported` / evidence-grade |
| **AgentMemory** | `app/models/agent_memory.py` | written by agents after tasks via `memory_writeback`. Types: `decision`, `output`, `lesson`, `constraint`, `open_question`, `failure`, `artifact_reference`, `work_log` | `generated` / `pending` / evidence-only |

All three carry the identical governance surface and the ADR-002 invariant
(instruction grade requires `user_confirmed`/`imported` provenance — see
[Signals & Governance](signals-and-governance.md)). Agent writebacks are *clamped*: the
writeback schema (`imi.memory.writeback.v1`, `app/services/memory_writeback.py`) restricts
provenance to observed/inferred/generated, so an agent cannot self-promote.

## Lanes: record vs library

Orthogonal to authority, every record carries a **lane**
([ADR-003](../adr/ADR-003-record-and-library-lanes.md), `app/models/lane.py`):

| Lane | Meaning |
|---|---|
| `record` | We were party to it: meetings, business mail, own notes, communities we take part in |
| `library` | Third-party content we watch: articles, newsletters, videos, feeds |

The lane is **server-assigned** at admission (`app/services/lane_admission.py`), never
accepted from clients. Captures are admitted in `capture_and_persist`; ingested content in
the `ADMIT` phase. Records without a stored lane (written before lanes existed) read as
`record`. Library records carry `stale_after`; once past it they drop out of recall (the
file stays: decay is not deletion). `scripts/stamp_lanes.py` backfills lanes from
`scripts/classify_memories.py` verdicts. Decay is per-deployment policy
([ADR-006](../adr/ADR-006-library-primary-deployments.md)): `library.decay.enabled: false` in
`config/lanes.yaml` stamps no new horizon and makes recall ignore existing ones (re-enabling
restores them); `library.decay.horizons_days` sets the three durability horizons.

## Recall

`recall(RecallRequest)` (`app/services/memory_recall.py`) is the single unified recall
surface (schema `imi.memory.recall.v1`), exposed as the `memory_recall` MCP tool and
`POST /api/agent-memory/recall`. `lanes` defaults to `recall.default_lanes` in
`config/lanes.yaml` (`["record"]` unless configured; an explicit `lanes` always wins); each requested lane is a
separate store-side-filtered search (library outnumbers record ~3:1, so a post-hoc filter
would starve record hits), and library hits come back under `background`, never merged
into `memories`:

1. Embed the query once; `search_vectors` across the shared store.
2. Deduplicate by record id.
3. **Re-hydrate governance fields from the authoritative git-corpus record** — a stale
   instruction-grade vector must never leak through (`_GOVERNANCE_FIELDS`,
   `memory_recall.py:42`).
4. Filter by requested `authority` (`evidence` or `instruction`) and by lane.
5. Rank by similarity + recency + authority bonus.
6. Write a recall trace with per-item snapshots.

The feedback loop: agents report which memories they actually used via
`record_memory_usage` / `POST /recall/{id}/usage`; traces are inspectable at
`GET /recall-traces` and per-record via `inspect_memory` (provenance, usage, lineage, judge
decisions — `app/services/memory_inspector.py`).

## Vector backends

Selected by `VECTOR_BACKEND` (`app/config.py:152`), resolved per-tenant in
`app/services/signal_indexing.py:76`:

| Backend | When | Notes |
|---|---|---|
| `sqlite` (**default**) | community/self-hosted | `SqliteVectorStore` (`app/core/tenancy/backends/sqlite_vector_store.py`): single WAL-mode `vectors.db` sidecar beside `DATABASE_PATH`; upsert-by-id; exact brute-force cosine search. Survives restarts, no extra services |
| `pgvector` | hosted / large corpora | needs `DATABASE_URL` |
| `faiss` | legacy | in-memory only — loses vectors on restart and drops metadata, which breaks governed recall. Avoid |

Indexing is **on-write** (`index_one` / `index_capture_one` / `index_agent_memory_one`), with
idempotent **backfill** for restarts, backend switches, or cloned corpora:

```bash
curl -X POST localhost:8080/api/admin/backfill-memory-index
```

(`backfill_signals` / `backfill_captures` / `backfill_agent_memories`,
`signal_indexing.py:343-423`.)

## Customization points

| You want to… | Do this |
|---|---|
| Switch backends | Set `VECTOR_BACKEND`, restart, run the backfill endpoint |
| Add a vector backend | Implement `store_vectors` / `search_vectors` / `delete` (contract: `SqliteVectorStore`), add a branch in `resolve_vector_store` (`signal_indexing.py:76`) |
| Add a governed record kind | Model with the shared invariant, `index_*_one` + `backfill_*` in `signal_indexing.py`, resolver in `memory_recall.default_resolvers` (`:110`), store with `iter_all`/`get`. Audit generalizes via duck typing |
| Tune recall ranking | recency/authority weighting in `memory_recall.py`; callers can pass `recency_weight` per request |
| Search library by default, or stop library decay | `recall.default_lanes` and `library.decay` in `config/lanes.yaml` (ADR-006; see `config/lanes.yaml.example`) |
