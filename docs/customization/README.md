# Customization Map — where to plug in

> **Audience:** anyone deciding *where* to make a change — humans scoping work, agents
> planning an implementation ·
> This is the master index of extension points, ordered from **no-code** (config/prompts) to
> **deep** (new subsystems). Prefer the highest row that solves your problem.

## Tier 0 — Configuration (no code, no restart-risk beyond a reboot)

| Change | Where | Docs |
|---|---|---|
| Entity types, attributes, relationships, NER steering, UI labels, intelligence patterns | `config/domains/<domain>.yaml` — the single highest-leverage file in the system | [Domain Schemas](domain-schemas.md) |
| Which LLM serves which operation (local models, gateways, cost control) | `config/inference.yaml` | [Configuration](../getting-started/configuration.md#models--inference) |
| Per-meeting-type processing (processors, thresholds, agent model) | `config/workflows/<id>.yaml` | [Configuration](../getting-started/configuration.md#workflows-config-optional) |
| Backends, auth mode, models, telemetry | `.env` | [Configuration](../getting-started/configuration.md) |

## Tier 1 — Prompts (no code; changes extraction behavior directly)

Prompts are XML files in `app/prompts/`; edit the `<instructions>` element. (Loading goes
through a few mechanisms for historical reasons — `app/services/prompt_loader.py` is the
preferred one; `app/services/prompts.py` and a couple of per-service loaders cover the
rest. Grep for the file's basename to find its loader.) The eval harness
(`scripts/run_evals.py`, `scripts/check_evals.sh`) reads the same files and tracks prompt
versions by hash — **run the evals after prompt changes**.

| Prompt file | Drives |
|---|---|
| `signal_promote.xml` | what counts as a decision / action item / key point / insight |
| `transcript_entity_extract.xml`, `entity_extract.xml` | entity extraction |
| `extract_relationships.xml` | relationship inference between entities |
| `person_update.xml`, `project_update.xml`, `team_update.xml` | entity profile updates |
| `meeting_finalize.xml` | meeting finalization (summary + title envelope) |
| `metadata.xml`, `metadata_with_entities.xml` | document metadata analysis |
| `digest.xml`, `explain-diff.xml`, `search.xml` | digests, diff explanations, `/api/query` |
| `generate_insights.xml`, `extract_patterns.xml` | analysis & insight generation |

Not everything is in XML yet: several substantial prompts are still inline in Python —
notably the content classifier (`app/services/ingest_classifier.py`), the capture
enrichment taxonomy (`app/services/capture_enrichment.py`), conflict detection
(`app/services/conflict_detector.py`), and the decision/weak-signal/statement tools under
`app/services/tools/`. To customize those today, edit the `_SYSTEM_PROMPT`/prompt string
in the service; migrating them to `app/prompts/` is welcome contribution territory.

## Tier 2 — Bounded code extensions (copy an existing pattern)

| Change | Pattern to copy | Key files |
|---|---|---|
| New push intake source (recorder, Slack, email…) | the Zapier adapter | `app/routes/ingest_zapier.py`; add a `ContentSource` (`app/models/ingestion/models.py:15`) |
| New pull connector | `GrainConnector` | subclass `BaseConnector` (`app/connectors/base.py:7`) |
| New MCP tool | any `TOOL_DEFS` entry | `app/services/mcp_tool_definitions.py` + conventions in `docs/mcp_tool_conventions.md`; registers on both MCP and chat surfaces |
| New REST endpoints | any router | `app/routes/`, include via `app/modules/*/__init__.py` |
| New extraction tool | existing `AgentTool` subclasses | `app/services/agent_tools.py`; wire into `_get_extraction_tools` (`app/routes/ingest.py:406`) |
| Entity-resolution tuning | — | thresholds/nicknames/normalization in `app/services/entity_resolver.py` (pure function; unit-test it) |
| React to entity events | webhooks | `app/routes/entity_webhooks.py` |

## Tier 3 — Pipeline & platform extensions

| Change | How | Docs |
|---|---|---|
| New ingestion pipeline stage | add to `PHASES` + `_phase_*` coroutine + `_run_phase` call in `app/services/orchestrators/ingest_orchestrator.py:72,240` | [Ingestion Pipeline](../architecture/ingestion-pipeline.md) |
| New governed memory kind | Signal/CapturedMemory/AgentMemory pattern: shared invariant + indexer + recall resolver + store | [Memory & Vectors](../architecture/memory.md#customization-points) |
| New vector backend | implement `store_vectors`/`search_vectors`/`delete`; branch in `signal_indexing.resolve_vector_store` | [Memory & Vectors](../architecture/memory.md) |
| Policy-aware agent on top of imi | judge loop: `POST /api/judge/recall` → act → `POST /api/judge/decisions` | [MCP & API](../architecture/mcp-and-api.md) |
| New in-process agent | subclass `AgentBase`, register in `AgentRegistry` | `app/agents/base.py` |
| Hosted/multi-tenant layering | `create_app(extra_routers=...)` + tenancy backends | `app/main.py:1116`, `app/core/tenancy/` |

## Things to NOT customize

- **The ADR-002 authority invariant** (`can_use_as_instruction` ⇒ human-confirmed
  provenance). Everything in recall, judge policy, and review assumes it. Never accept
  governance fields (`provenance_status`, `review_status`, `can_use_as_*`) from client input.
- **The three-store synchrony** (Neo4j + markdown corpus + signal store). The files are the
  source of truth; bypassing the write-through (e.g. writing Neo4j directly) desynchronizes a
  rebuildable system into an unrebuildable one.
- **Signal ID determinism** (`uuid5` content addressing) — re-ingestion idempotency depends
  on it.

## Verifying changes

```bash
docker exec imi-dev pytest tests/ -q        # backend tests (dev env provides Neo4j)
docker exec imi-dev ruff check app/         # lint
scripts/check_evals.sh                      # extraction quality after prompt/resolver changes
scripts/smoke_test.sh                       # end-to-end smoke
```

For prompt or domain-schema changes, the best verification is a real ingest: post a
representative document to `/api/ingest`, then inspect the delta report and
`/api/signals/feed`.
