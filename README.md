# imi

**Turn everything your team says and writes into a knowledge graph your AI agents can query.**

[![Tests](https://github.com/sufficiently-advanced-ai/imi/actions/workflows/tests.yml/badge.svg)](https://github.com/sufficiently-advanced-ai/imi/actions/workflows/tests.yml)
[![Smoke Test](https://github.com/sufficiently-advanced-ai/imi/actions/workflows/smoke-test.yml/badge.svg)](https://github.com/sufficiently-advanced-ai/imi/actions/workflows/smoke-test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

imi is a self-hosted knowledge engine. Feed it documents, call transcripts, and commits — it
classifies them with Claude, extracts entities, decisions, and action items into a Neo4j
knowledge graph, and serves the result to humans (web UI) and AI agents (MCP) in real time.

![System overview](docs/diagrams/system-overview.svg)

## What it does

- **Ingest anything** — direct upload, Zapier, GitHub webhooks, or drop in a call transcript
  from any recorder (Otter, Fathom, Grain, Fireflies, Zoom).
- **Classify + extract** — Claude classifies content, extracts entities and relationships, and
  enriches them with domain-aware context.
- **Knowledge graph** — every entity, signal, decision, and action item lands in a Neo4j graph
  (with an in-memory fallback) and a git-backed markdown corpus. Files are the source of truth.
- **Governed memory** — observations are promoted into typed signals with a trust/authority
  axis and a full audit trail. Agents write evidence; only humans grant instruction grade
  ([ADR-002](docs/adr/ADR-002-evidence-instruction-authority-gate.md)).
- **MCP server** — ~30 tools over the Model Context Protocol, so Claude Code, Claude Desktop,
  Cursor, or any MCP client can search, recall, and write back to your knowledge base.
- **Domain-agnostic** — one YAML schema defines your entity types, extraction steering, and UI
  labels. Six ready-made domains ship in `config/domains/`; write your own to fit your business.

## 1 · Install

Docker-only — no local Python or Node needed.

```bash
docker --version           # Docker 24+
docker compose version     # Compose v2 — note the space: `docker compose`, not `docker-compose`
```

You also need an [Anthropic API key](https://console.anthropic.com), ~2 GB free RAM, and free
host ports `8080` (app), `7474` / `7687` (Neo4j).

```bash
# 1. Clone
git clone https://github.com/sufficiently-advanced-ai/imi.git
cd imi

# 2. Configure — two required values, everything else has working defaults
cp .env.example .env
#   ANTHROPIC_API_KEY=sk-ant-...
#   NEO4J_PASSWORD=<choose-any-password>

# 3. Build and start (first run pulls images and builds the frontend — allow 5–12 minutes)
docker compose up -d --build
```

**Verify it's up.** The app waits for Neo4j's healthcheck, so startup is not instant — poll
rather than assume:

```bash
docker compose ps                                      # both containers -> "healthy"
curl -fsS http://localhost:8080/health && echo " OK"   # 200 = up; connection error = still booting
```

The web UI is at http://localhost:8080. Step-by-step setup with verification after every step:
[Onboarding guide](docs/getting-started/onboarding.md). Setting up with Claude Code? The
bundled `imi-onboarding` skill (`.claude/skills/imi-onboarding/`) walks an agent through the
whole install.

## 2 · Configure

The one decision that matters on day one is your **domain** — the YAML schema that shapes what
entity types exist, what extraction looks for, and what the UI calls things:

| `ACTIVE_DOMAIN` | Built around |
|---|---|
| `consulting_firm` (default) | accounts, projects, people, teams |
| `b2b_saas` | accounts, projects, people |
| `agency` | clients, campaigns, deliverables |
| `solo_consulting` | clients, engagements, stakeholders |
| `member_network` | members, groups, events |
| `personal_crm` | contacts, companies, activities |

Pick the closest and set it in `.env` — switching later is an env change + restart. When none
fit, the [domain schema guide](docs/customization/domain-schemas.md) walks through writing your
own, and the `domain-config-advisor` skill can draft one from a description of your business.

Everything else — models, vector backend, auth, git corpus, telemetry — is covered in the
[configuration reference](docs/getting-started/configuration.md). The knobs most people touch:

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | **required** | Claude auth; nothing extracts without it |
| `NEO4J_PASSWORD` | **required** | Neo4j password (applied on first init of the data volume) |
| `ACTIVE_DOMAIN` | `consulting_firm` | which domain schema shapes the instance |
| `AUTH_MODE` | `none` | `none` (open) or `demo` (cookie login) — bind to loopback or proxy before exposing |
| `VECTOR_BACKEND` | `sqlite` | semantic-memory store: `sqlite` (persistent) or `pgvector` |
| `GIT_REPO_URL` | — | GitHub repo to back the knowledge corpus ([setup guide](docs/getting-started/git-corpus.md)) |

## 3 · Test it

Ingest something and watch the pipeline run:

```bash
curl -X POST http://localhost:8080/api/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Meeting with Jordan Chen from Acme Corp. Decided to move the rollout to Q3. Casey will draft the migration plan by Friday.",
    "title": "Acme sync",
    "source_id": "first-test-1",
    "participants": ["Jordan Chen", "Casey Park"]
  }'
```

The response is a `202` with a `job_id`. Check what happened:

```bash
curl -s http://localhost:8080/api/ingest/<job_id>/status   # status: "completed"
curl -s http://localhost:8080/api/ingest/<job_id>/delta    # human-readable "what changed" report
```

You should see entities (Jordan Chen, Casey Park, Acme Corp), a `decision` signal ("rollout to
Q3"), and an `action_item` (Casey's plan) — in the delta report, at
http://localhost:8080/explorer, or via `curl -s http://localhost:8080/api/signals/feed`.

> Always send a stable `source_id` — it's the idempotency key. Re-posting the same one updates
> instead of duplicating.

For a full end-to-end check (build, boot, import sweep, real ingestion):

```bash
./scripts/smoke_test.sh            # both phases; pipeline phase needs ANTHROPIC_API_KEY
./scripts/smoke_test.sh --quick    # import sweep only, no API key required
```

## 4 · Use it

The day-to-day rhythm is **feed it → review what it learned → query it**. The full guide is
[Day-to-day usage](docs/getting-started/daily-usage.md); the short version:

**Connect your AI tools over MCP.** Copy `.mcp.json.example` to `.mcp.json` (or add the
equivalent to Claude Desktop / Cursor):

```json
{ "mcpServers": { "imi": { "type": "sse", "url": "http://localhost:8080/api/mcp/sse" } } }
```

Your agent can now call `ask_kb`, `search_knowledge_graph`, `memory_recall`,
`add_call_transcript`, and ~30 other tools — catalog in
[MCP & API](docs/architecture/mcp-and-api.md), agent etiquette in the
[agent operating guide](docs/agents/README.md).

**Wire up real inputs:**

- **Call recorders** — point a Zapier zap (or the recorder's webhook) at `POST /api/ingest/zapier`
- **A git knowledge repo** — back the corpus with a GitHub repo for offsite history and human edits ([setup guide](docs/getting-started/git-corpus.md))
- **Ad-hoc thoughts** — `POST /api/captures` or the `capture_thought` MCP tool

**Review and govern.** The Overview page surfaces what needs review; confirming a signal is
what promotes it from *evidence* to *instruction* — the grade agents are allowed to treat as
policy. The Decisions page tracks every decision's lifecycle (`active`, `stale`, `superseded`,
`conflicting`) so you always know what the current ruling is.

## Documentation

The full suite — with reading paths for operators, developers, and AI agents — starts at
[`docs/README.md`](docs/README.md). Highlights:

| You want to… | Read |
|---|---|
| Set up an instance, step by verified step | [Onboarding](docs/getting-started/onboarding.md) |
| See every configuration knob | [Configuration reference](docs/getting-started/configuration.md) |
| Back the corpus with a GitHub repo | [Git corpus guide](docs/getting-started/git-corpus.md) |
| Run it day to day | [Day-to-day usage](docs/getting-started/daily-usage.md) |
| Understand how the machine works | [System overview](docs/architecture/overview.md) + the [architecture docs](docs/architecture/) |
| Customize it — config to code | [Customization map](docs/customization/README.md) |
| Fit the schema to your business | [Domain schemas](docs/customization/domain-schemas.md) |
| Operate it as an AI agent | [Agent operating guide](docs/agents/README.md) |
| Change the source code | [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md) |

Design records live in [`docs/adr/`](docs/adr/) (architecture decisions),
[`docs/prd/`](docs/prd/) (the memory-governance and decision-state systems), and
[`docs/world-model-concept.md`](docs/world-model-concept.md) (the concept behind the graph).

## Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `imi-neo4j` stays `unhealthy`, `imi-app` never starts | Neo4j applies `NEO4J_PASSWORD` only on the **first** init of its data volume. Reset: `docker compose down -v && docker compose up -d --build` (wipes data). |
| `curl .../health` connection refused | Build/boot still in progress (first build is 5–12 min). Watch `docker compose logs -f app` for `Application startup complete`. |
| Port already in use (`8080`/`7474`/`7687`) | Free the port, or change `PORT` in `.env` (app); Neo4j ports live in `docker-compose.yml`. |
| Ingest job returns `"status": "failed"` | Check `docker compose logs app`; a missing or invalid `ANTHROPIC_API_KEY` is the most common cause. |
| Startup crash mentioning domain/validation | Malformed domain YAML — schema errors fail fast at boot by design; the error names the field. |
| Semantic search / memory recall returns nothing | The vector index is empty (backend switch, or records from before one). Rebuild once: `curl -X POST localhost:8080/api/admin/backfill-memory-index`. |

More in the [onboarding guide's troubleshooting section](docs/getting-started/onboarding.md#troubleshooting).

## Contributing

Issues and PRs welcome — [CONTRIBUTING.md](CONTRIBUTING.md) covers the dev environment, test
suites, and the extraction-quality evals. Design decisions are recorded in
[`docs/adr/`](docs/adr/).

## License

MIT — see [LICENSE](LICENSE). Dependencies are overwhelmingly permissively licensed
(MIT/BSD/Apache-2.0/ISC), with a few weak-copyleft libraries used unmodified: the backend
uses [PyGithub](https://github.com/PyGithub/PyGithub) (LGPL-3.0), the frontend bundles
libvips native binaries via [sharp](https://sharp.pixelplumbing.com/) (LGPL-3.0), and some
build tooling is MPL-2.0. None of these conflict with MIT distribution, but include their
license texts if you redistribute built images.
