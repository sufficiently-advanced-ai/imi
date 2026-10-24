# Startup and persistent state

imi was first deployed on a stateless container host, so the original startup
sequence assumed nothing survived a restart: wipe and rebuild the Neo4j graph
from the corpus, re-embed every entity into an in-memory FAISS index, re-clone
the corpus repo. Self-hosted with docker-compose, all of that state *is*
persistent (`imi-neo4j-data`, `./data`, `./repo`), and the old sequence cost a
12.5k-file corpus **~12 minutes per restart** during which the API refused
connections while the container reported "healthy".

This page describes the two startup modes, the readiness contract, and the
knobs.

## Modes (`NEO4J_REBUILD_ON_STARTUP`)

| | `false` — **stateful** (compose default) | `true` — **rebuild** (stateless hosts) |
|---|---|---|
| Graph | Trusted from Neo4j; in-memory caches synced (~1 s) | Full build from the corpus before serving (batched UNWIND writes) |
| Corpus changes while down | Reconciled after bind from a filesystem manifest (`STARTUP_RECONCILE`) | n/a — everything is rebuilt |
| Entity vectors | Persist in `SqliteVectorStore` (`VECTOR_BACKEND=sqlite`); rebuilt from Neo4j if empty (`STARTUP_VECTOR_BOOTSTRAP`) | Rebuilt after bind, off-loop |
| Explicit rebuild | `POST /api/admin/rebuild-graph` (+ `/status`) | same |
| Time to serving | seconds | seconds–tens of seconds |

Code: `app/main.py::startup_event`, `app/services/graph_rebuild.py`.

## Startup sequence

```text
lifecycle.startup()            state=RUNNING, NOT ready
telemetry, metrics
neo4j client + schema
git_ops.initialize()           local-only: init once, preserve; remote: fetch + ff-only, never rm -rf
domain + Semantica objects
graph strategy                 rebuild: run_full_rebuild(include_semantica=False)
                               stateful: kg.build_graph() → sync-from-Neo4j
database
lifecycle.mark_ready()         /health/startup → 200; uvicorn binds :8000 on return
── background, in order ──     rebuild:  semantica-build
                               stateful: corpus-reconcile, vector-bootstrap
```

### Why the port used to open 12 minutes late

Three things compounded (all fixed):

1. The legacy rebuild issued one bolt round trip per node/edge (~130k on LCARS,
   129 s). `Neo4jBatchWriter` now collects writes and flushes `UNWIND $rows`
   statements in chunks of `GRAPH_BUILD_BATCH_SIZE`.
2. The Semantica "background" build awaited coroutines whose bodies were
   synchronous (sync Neo4j driver, FastEmbed, FAISS), so it never yielded; and
   because `startup_event` had already queued it, the lifespan handler was
   parked behind it for 9.5 minutes. All of those calls now go through
   `app/services/blocking.py` (a single-worker executor), so the loop stays
   responsive and deferred work genuinely runs in the background.
3. The container healthcheck hit an nginx location that returned a static
   `"healthy"`. It now hits `/health/startup` on the app (503 until
   `mark_ready()`), and nginx proxies `/health` there too.

## Reconcile: filesystem manifest, not git

`app/services/corpus_manifest.py` records `(mtime_ns, size)` for every
markdown file at the end of a full build, alongside a `build_id` that is also
stored on a `:_CorpusState` node in Neo4j. On a stateful boot:

* graph empty → full rebuild (background);
* graph present but no `build_id` / manifest mismatch → adopt the current disk
  state as baseline. This is logged at WARNING with the file and entity counts,
  because it is the moment files the graph has never ingested become canonical
  without being read — run `POST /api/admin/rebuild-graph` if that is wrong;
* otherwise diff manifest vs disk and call `Neo4jKnowledgeGraph.ingest_files`
  / `remove_files` and `SemanticaKnowledge.ingest_file` for the delta only.

It keys off the **filesystem** deliberately: the corpus working tree regularly
contains files git has not committed yet (on one deployment, ~3.8k untracked
`memory/` records), and "files are the source of truth" means the disk.

Deleted entity-profile files demote their node to a stub (other documents may
still reference it); deleted documents are removed with their `MENTIONED_IN`
edges.

### Corpus sync status

The reconcile outcome is not only logged: `app/services/corpus_sync.py` records
it and exposes it as `corpus_sync` on `GET /health/ready`, `GET /health/startup`
and `GET /api/admin/corpus-sync` (the overview page shows a warning banner from
the last one).

| `corpus_sync.status` | Meaning |
|---|---|
| `pending` | stateful boot queued the deferred reconcile; it has not started |
| `reconciling` | a reconcile or full rebuild is running |
| `synced` | graph matches the corpus: unchanged, delta reconciled, or a full rebuild completed |
| `adopted` | the disk state was adopted as baseline **without being ingested** — files changed before `baseline_at` may be missing from the graph |
| `rebuild_required` | a full rebuild was needed (empty graph) or attempted, and did not complete |
| `unknown` | no reconcile has run (`STARTUP_RECONCILE=false`, graph/Neo4j unavailable, reconcile errored — see `error`) |

`adopted` and `rebuild_required` set `degraded: true` and add an operator-facing
line to `warnings` (so do files queued for retry after a partial reconcile).
Readiness is unaffected: `ready` stays `true` and `/health/ready` stays 200,
because the instance serves correctly from the graph it has — it is the graph's
completeness that is in question.

`adopted` survives restarts. The adoption reason is written as `source` on the
`:_CorpusState` node, so the next boot's reconcile (which sees an unchanged
manifest) still reports `baseline_source: manifest_missing` and the status
stays `adopted`. Only a completed full rebuild (`POST /api/admin/rebuild-graph`,
or the empty-graph startup build) writes a non-adoption source and returns the
status to `synced`.

### Restore drill

`scripts/restore_drill.py` (nightly in `.github/workflows/restore-drill.yml`) proves
the restore path end to end: synthetic KB + one decision confirmed through the review
API → snapshot (graph nodes/edges, audit rows, vector counts) → stop app → tar
`repo/` + `data/` → wipe both and the graph → restore → boot → clean rebuild +
memory-index backfill → assert snapshot parity, unchanged audit rows, the decision
still `can_use_as_instruction`, a graph search hit, and `corpus_sync.status ==
synced`. The pure parts (tar/restore/compare) are unit-tested in
`tests/test_restore_drill.py`. Procedure for operators:
[Backup and restore](../getting-started/git-corpus.md#backup-and-restore).

## Readiness contract

| Endpoint | Meaning |
|---|---|
| `GET /health` (app) | liveness — process is up |
| `GET /health/startup` | **200 only after `startup_event` completed**; what the container healthcheck and nginx `/health` use |
| `GET /health/ready` | dependency readiness; 503 `starting` until startup completed |
| `GET /health/nginx` | static nginx liveness |

`LifecycleManager.mark_ready()` is called at the *end* of `startup_event`
(it used to be set on the first line) — and only if no readiness-critical
dependency (Neo4j client, domain config, database, or the startup sequence
itself) failed. A degraded instance keeps serving `/health` (liveness) and
whatever still works, but `/health/startup` stays 503 and lists `failures`.

## Logging

`app/core/logging_setup.py` configures the root logger once at import of
`app.main` (`LOG_LEVEL`, default INFO). Before this, every `logger.info` in
`app/` was discarded — only `print()`/`sys.stderr.write` lines reached docker
logs. Per-file corpus-read progress from `git_ops` is DEBUG-only now (it was
~50k lines per boot).

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `NEO4J_REBUILD_ON_STARTUP` | code `true`, compose `false` | mode, see above |
| `STARTUP_RECONCILE` | `true` | stateful: diff corpus vs manifest after bind |
| `STARTUP_VECTOR_BOOTSTRAP` | `true` | stateful: reindex entities from Neo4j if the vector index is empty |
| `GRAPH_BUILD_BATCH_SIZE` | `500` | rows per UNWIND during full builds |
| `VECTOR_BOOTSTRAP_THROTTLE_MS` | `0` | pause between entities during the boot-time vector reindex (memory/CPU-constrained hosts; set `STARTUP_VECTOR_BOOTSTRAP=false` to skip entirely) |
| `LOG_LEVEL` | `INFO` | root log level |
| `VECTOR_BACKEND` | `sqlite` | persistent, metadata-carrying entity/memory vectors |
