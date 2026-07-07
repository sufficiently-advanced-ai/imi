# Operating imi as an Agent

> **Audience:** AI agents (Claude Code, Hermes, or any MCP-capable framework) that manage,
> feed, or query a running imi instance on a user's behalf ·
> **If you are changing imi's source code instead, read [`CLAUDE.md`](../../CLAUDE.md).**

This guide is written to be actionable without reading the architecture docs first, but links
into them for depth. Everything here is verifiable — run the checks, don't assume.

## Connect

Two surfaces, use both:

- **MCP** (preferred for knowledge operations): SSE endpoint at
  `http://<host>:8080/api/mcp/sse`. Config snippet in `.mcp.json.example`. ~30 tools —
  catalog in [MCP & API](../architecture/mcp-and-api.md).
- **REST** (for operations MCP doesn't cover: health, admin, job status): base
  `http://<host>:8080`. No auth in community mode (`AUTH_MODE=none`).

Health checks before doing anything:

```bash
curl -fsS http://<host>:8080/health           # 200 = app up
curl -fsS http://<host>:8080/health/ready     # readiness incl. dependencies
```

## The rules of the road (non-negotiable)

1. **You write evidence, never instructions.** Whatever you persist via `memory_writeback`,
   `capture_thought`, or ingestion enters as evidence-grade, pending review. You cannot
   promote it — only a human review action (`update_signal` with `review_action="confirm"`,
   or the review UI/endpoints) grants instruction grade. Don't try to work around this; it's
   enforced server-side at write, review, and recall (ADR-002).
2. **When recalling memory to decide how to act**, request `authority="instruction"` for
   policy and `authority="evidence"` for context — and treat them differently. Policy hits
   from `judge_recall` carry a `required_behavior` (allow/block/revise/escalate): honor it.
3. **Always pass a stable `source_id` when ingesting.** It's the idempotency key; without it,
   retries create duplicates.
4. **Mutations are heavyweight.** `graph_*` write tools touch three stores (Neo4j + markdown
   file + git commit). Prefer `graph_merge_nodes` over delete+create; use `preview` on merges
   where offered; never bulk-mutate without checking counts first.

## Task recipes

### Ingest a transcript or document

```text
MCP: add_call_transcript(transcript, start_time, participants, title, source_id, ...)
REST: POST /api/ingest  {content, title, source_id, participants, source}
```

Then **verify**: poll `GET /api/ingest/{job_id}/status` until `completed` (job status is
in-memory — if the app restarted mid-job, re-ingest; idempotency makes this safe). Read
`GET /api/ingest/{job_id}/delta` for a summary of what changed, and surface it to the user.

### Answer a question from the knowledge base

Escalating strategies:
1. `ask_kb(intent)` — one call; an internal sub-agent runs the tool loop and returns a
   synthesized answer with a trace. Good default.
2. Compose it yourself: `search_knowledge_graph` → `find_related_entities` /
   `list_entity_profiles` → `search_signals` for decisions/actions on those entities.
3. `query_graph_cypher` for anything structural (read-only, ≤100 rows).
4. `memory_recall(query, authority="evidence")` for semantic recall across signals +
   captures + agent memories.

### Work with decisions

- Current policy: `get_constitution` (all confirmed active decisions as markdown).
- Lifecycle-aware lists: `list_decisions(state="active")` — states are computed
  (`active`/`stale`/`superseded`/`conflicting`/…), so trust them over dates.
- One decision with lineage + audit: `get_decision(decision_id)`.
- Before acting on a decision, check it isn't `superseded` or `conflicting`.

### Write back what you did (the memory loop)

After completing a meaningful task:

```text
memory_writeback(memory_payload={decisions: [...], lessons: [...], outputs: [...]},
                 idempotency_key=<stable-per-task>)
```

When you used recall results, close the loop: `record_memory_usage(request_id,
used_memory_ids, ignored)` — this feeds ranking. Audit any record with
`inspect_memory(record_id)`.

### Curate entities

Duplicates: `search_knowledge_graph` to find candidates → `graph_merge_nodes(primary_id,
duplicate_id)` (or `POST /api/entities/{id}/merge` with `preview=true` first). Aliases:
entity-management REST. Wrong extractions recurring? Suggest a `ner_exclude` entry in the
domain schema instead of deleting the same entity weekly.

### Configure / reconfigure the instance

Config changes are file edits + restart — see
[Configuration](../getting-started/configuration.md). The high-leverage moves:

| Goal | Change |
|---|---|
| Different entity types / business fit | `config/domains/*.yaml` + `ACTIVE_DOMAIN` — use the `domain-config-advisor` skill |
| Extraction quality | `app/prompts/*.xml`, then `scripts/check_evals.sh` |
| Cost control / local models | `config/inference.yaml` per-operation routing |
| Vector backend switch | `VECTOR_BACKEND` env + `POST /api/admin/backfill-memory-index` |

After any restart or backend switch, if semantic search comes back empty, run the backfill
endpoint — that's the expected fix, not a bug.

### Diagnose problems

| Symptom | Check |
|---|---|
| Ingest stuck | `GET /api/ingest/jobs`; SSE stream `GET /api/ingest/{job_id}/stream`; app logs |
| No entities extracted | is `ANTHROPIC_API_KEY` valid? Did classification fall back to `document`? Check the delta report |
| Semantic recall empty | backfill index (above); check `VECTOR_BACKEND` isn't `faiss` |
| Graph queries failing / merge returns 503 | Neo4j down → app runs on the in-memory fallback; check `GET /health/neo4j` and the neo4j container |
| Startup crash | almost always a malformed domain YAML — the error names the field |

## For agents embedded in workflows (Hermes-style)

If your framework consumes OpenAPI rather than MCP: FastAPI serves the schema at
`/openapi.json`; the REST surface mirrors most MCP capabilities (see the router table in
[MCP & API](../architecture/mcp-and-api.md)). The judge loop
(`POST /api/judge/recall` → act → `POST /api/judge/decisions`) is the sanctioned pattern for
policy-aware autonomous behavior: recall returns `policy_hits` you must respect, decide
idempotently with a stable `action_id`, and your decision events become auditable memory.
