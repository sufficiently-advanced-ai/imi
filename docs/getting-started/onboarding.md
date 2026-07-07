# Onboarding: from zero to a working knowledge engine

> **Audience:** new operators (human or agent) standing up an imi instance ·
> **Time:** ~30 minutes, most of it the first Docker build ·
> **Agents:** every step here is scriptable; verification commands are provided after each
> step — run them rather than assuming success. See also the
> [agent operating guide](../agents/README.md) and the `imi-onboarding` skill
> (`.claude/skills/imi-onboarding/`).

## Step 0 — Prerequisites

```bash
docker --version           # Docker 24+
docker compose version     # Compose v2 (note: `docker compose`, not `docker-compose`)
```

You need an Anthropic API key, ~2 GB free RAM, and free host ports 8080, 7474, 7687.

## Step 1 — Configure

```bash
git clone https://github.com/sufficiently-advanced-ai/imi.git && cd imi
cp .env.example .env
```

Set the two required values in `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
NEO4J_PASSWORD=<choose-any-password>
```

Everything else has working defaults. The full reference is in
[Configuration](configuration.md).

## Step 2 — Choose a domain (the decision that matters)

The `ACTIVE_DOMAIN` variable selects which schema in `config/domains/` shapes your entire
instance — what entity types exist, what the extraction pipeline looks for, what the UI calls
things. Six ship:

| Domain | Built around |
|---|---|
| `consulting_firm` (default) | accounts, projects, people, teams |
| `b2b_saas` | accounts, projects, people |
| `agency` | clients, campaigns, deliverables |
| `solo_consulting` | clients, engagements, stakeholders |
| `member_network` | members, groups, events |
| `personal_crm` | contacts, companies, activities |

Pick the closest one to start — you can inspect each with
`cat config/domains/<name>.yaml`. Don't over-think it on day one: switching later is just an
env change + restart, though entities already extracted keep their old types. When none fit,
write your own — the [domain schema guide](../customization/domain-schemas.md) walks through
it, and the `domain-config-advisor` skill (`.claude/skills/domain-config-advisor/`) can draft
one from a description of your business.

```bash
# in .env
ACTIVE_DOMAIN=consulting_firm
```

## Step 3 — Build and start

```bash
docker compose up -d --build     # first run: 5–12 minutes
```

**Verify** (poll — startup waits for Neo4j's healthcheck, so it is not instant):

```bash
docker compose ps                                      # both containers -> "healthy"
curl -fsS http://localhost:8080/health && echo " OK"   # 200 = up; connection error = still booting
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/api/mcp/sse   # -> 200
```

The web UI is at http://localhost:8080 (no auth wall by default, `AUTH_MODE=none`).

## Step 4 — First ingest

```bash
curl -X POST http://localhost:8080/api/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Meeting with Jordan Chen from Acme Corp. Decided to move the rollout to Q3. Casey will draft the migration plan by Friday.",
    "title": "Acme sync",
    "source_id": "onboarding-test-1",
    "participants": ["Jordan Chen", "Casey Park"]
  }'
```

The response is a `202` with a `job_id`. **Verify the pipeline ran:**

```bash
curl -s http://localhost:8080/api/ingest/<job_id>/status   # status: "completed", phases_completed: [...]
curl -s http://localhost:8080/api/ingest/<job_id>/delta    # human-readable "what changed" report
```

You should now see: entities (Jordan Chen, Casey Park, Acme Corp), a `decision` signal
("rollout to Q3"), and an `action_item` (Casey's plan) — check the UI at `/explorer` or:

```bash
curl -s "http://localhost:8080/api/signals/feed" | head -50
```

Always send a stable `source_id` — it's the idempotency key; re-posting the same one updates
instead of duplicating.

## Step 5 — Connect an agent over MCP

```bash
cp .mcp.json.example .mcp.json
```

```json
{ "mcpServers": { "imi": { "type": "sse", "url": "http://localhost:8080/api/mcp/sse" } } }
```

Any MCP client (Claude Code, Claude Desktop, Cursor) can now call
`search_knowledge_graph`, `memory_recall`, `add_call_transcript`, and ~30 other tools —
catalog in [MCP & API](../architecture/mcp-and-api.md).

> **Security note:** the MCP endpoint has no auth in the community edition. Ports bind to
> loopback by default; keep it that way or put a proxy in front before exposing anything.

## Step 6 — Wire up real inputs

- **Call recorders** (Otter, Fathom, Grain, Fireflies, Zoom): point a Zapier zap (or the
  recorder's webhook) at `POST /api/ingest/zapier`.
- **A git knowledge repo**: back the corpus with a GitHub repo — walkthrough (repo creation,
  token scopes, webhook) in [Git corpus](git-corpus.md). Careful: once `GIT_REPO_URL` is
  set, the remote is authoritative and `./repo` is re-cloned at startup — if you've already
  built up a local corpus, push it to the new remote first (steps in the guide).
- **Grain direct**: `python -m app.connectors` exports recordings to JSONL for `/api/ingest`.
- **Ad-hoc notes**: `POST /api/captures` or the `capture_thought` MCP tool.

## Where to go next

| You want to… | Read |
|---|---|
| Settle into the daily feed → review → query loop | [Day-to-day usage](daily-usage.md) |
| Understand the machine you just started | [System Overview](../architecture/overview.md) |
| Tune it to your business | [Domain Schemas](../customization/domain-schemas.md) |
| See every knob | [Configuration](configuration.md) |
| Run it with agents managing it | [Agent operating guide](../agents/README.md) |
| Review + govern what gets extracted | [Signals & Governance](../architecture/signals-and-governance.md) |

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `curl /health` connection refused for minutes | first build still running — `docker compose logs -f app` |
| `imi-neo4j` unhealthy | password mismatch between `.env` and the volume from a previous run: `docker compose down -v` (wipes data) or fix `NEO4J_PASSWORD` |
| App starts, ingest jobs fail at CLASSIFY/PROMOTE | bad or missing `ANTHROPIC_API_KEY` — check `docker compose logs app \| grep -i anthropic` |
| Startup crash mentioning domain/validation | malformed domain YAML — schema errors fail fast at boot by design; validate with the steps in the [domain guide](../customization/domain-schemas.md) |
| Semantic search returns nothing after a restart/switch | rebuild the vector index: `curl -X POST localhost:8080/api/admin/backfill-memory-index` |
