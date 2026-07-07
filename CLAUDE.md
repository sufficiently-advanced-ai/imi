# CLAUDE.md — working on the imi codebase

imi is a self-hosted knowledge engine: FastAPI backend + Next.js frontend + Neo4j graph.
Content is ingested, classified with Claude, mined for entities and governed signals, and
exposed over MCP and REST. Community edition of a production codebase; downstream
deployments extend it through documented seams.

**This file is for agents changing the code.** If you are *operating* a running instance
(ingesting, querying, configuring), read `docs/agents/README.md` instead.

## Orientation — read in this order

1. `docs/architecture/overview.md` — five-minute mental model + code map
2. `docs/customization/README.md` — extension-point map; find the right tier **before**
   writing code (most requests are Tier 0 config or Tier 1 prompt changes, not code)
3. The architecture doc for the subsystem you're touching (`docs/architecture/*.md`) — each
   has precise file:line citations and a "customization points" table

## Commands

```bash
./dev-hot.sh                              # hot-reload dev env (app/ + imi-frontend/ live)
docker exec imi-dev pytest tests/ -q      # backend tests (needs the dev env's Neo4j)
docker exec imi-dev ruff check app/       # lint
cd imi-frontend && npx jest               # frontend tests
scripts/check_evals.sh                    # extraction-quality evals — REQUIRED after
                                          # changing app/prompts/*.xml or entity_resolver.py
scripts/smoke_test.sh                     # end-to-end smoke
```

Verification for pipeline/schema changes: real ingest beats unit tests —
`POST /api/ingest` a representative doc, check `/api/ingest/{job_id}/delta` and
`/api/signals/feed`.

## Load-bearing invariants — do not weaken

1. **ADR-002 authority gate** (`docs/adr/ADR-002-evidence-instruction-authority-gate.md`): `can_use_as_instruction` requires
   `provenance_status ∈ {user_confirmed, imported}`. Enforced in model validators
   (`app/models/signal.py:132`), the review state machine
   (`app/services/signal_governance.py`), and re-hydrated at recall
   (`app/services/memory_recall.py`). Never accept `provenance_status` / `review_status` /
   `can_use_as_*` from client input — that would be a privilege escalation.
2. **Files are the source of truth.** Entities/meetings/signals are markdown+JSON in the git
   corpus with write-through from graph mutations (`neo4j_graph.py:2144` rolls back Neo4j on
   file-write failure). Don't write Neo4j directly and skip the file layer.
3. **Idempotency**: ingest dedups by `source_id` then content hash; signal IDs are
   deterministic `uuid5`. Preserve both when touching the pipeline.
4. **Domain schema is the type system.** Entity/relationship types come from
   `config/domains/<domain>.yaml`, validated at every graph write. Never hardcode entity
   types; the `EntityType` enum in `app/models/api/core.py` is legacy.

## Known traps (verified against the code — the docs/agents may mislead you)

- **Authoritative domain model** is `app/model_schemas/domain_config.py`.
  `app/models/domain/config.py` is a divergent legacy model (e.g. it disagrees on
  `success_metrics`) — never write schemas, validation, or docs against it.
  `config/domains/DOMAIN_SCHEMA.md` is a maintained quick reference that defers to the
  Pydantic model.
- **Domain switching is restart-only.** `POST /api/domain/switch` and
  `DomainConfigService.set_active_domain()` are intentional no-ops.
- **Two entity-resolution implementations** exist: `app/services/entity_resolver.py` (newer,
  pure, preferred — shared with the eval harness) and `app/services/entity_deduplication.py`
  (older batch path). Change matching behavior in the former.
- **MCP tools** are defined in `app/services/mcp_tool_definitions.py` (`TOOL_DEFS`) and
  registered on two surfaces; a few remain inline in `app/routes/mcp_server.py` (migration
  backlog). Follow `docs/mcp_tool_conventions.md` (`max_results` not `limit`, verb taxonomy).
- The ingest **job store is an in-memory dict** (`app/routes/ingest.py:37`) — job status is
  lost on restart; don't build on it as durable state.
- `app/modules/` is router aggregation only — logic lives in `app/services/` and
  `app/routes/`.

## Conventions

- Prompts live in `app/prompts/*.xml` (`<instructions>` element; loaders vary — grep the
  basename, `app/services/prompt_loader.py` is the preferred one for new code). Several
  older prompts are still inline in services (see `docs/customization/README.md`). Prefer
  editing prompts over adding extraction code, and run the evals after.
- New intake sources: copy the adapter pattern in `app/routes/ingest_zapier.py`; new pull
  connectors: subclass `app/connectors/base.py`.
- New pipeline stages: `PHASES` + `_phase_*` + `_run_phase` in
  `app/services/orchestrators/ingest_orchestrator.py` — status tracking and SSE are free.
- Async everywhere on the request path; LLM calls go through
  `ClaudeClient.generate_message` with an `operation=` label so `config/inference.yaml` can
  route them.
- Commit style in this repo: conventional-commit-ish (`feat(memory): ...`, `fix(judge): ...`).
