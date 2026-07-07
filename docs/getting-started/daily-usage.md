# Day-to-day usage

> **Audience:** operators using a running instance — humans at the web UI and the people
> pointing agents at it ·
> **Prereq:** a working install ([Onboarding](onboarding.md)) ·
> **Agents:** the agent-facing version of this loop is the
> [Agent Operating Guide](../agents/README.md)

The rhythm is a loop: **feed it → review what it learned → query it**, plus a little
maintenance. None of it is mandatory ceremony — the system works if you only ever ingest and
search — but the review step is what makes the knowledge base *trustworthy* rather than just
searchable.

## Feed it

Everything funnels into the same pipeline; pick whichever intake fits the moment:

| Input | How |
|---|---|
| Call transcripts, automatically | Zapier zap (or the recorder's webhook) → `POST /api/ingest/zapier`. Works with Otter, Fathom, Grain, Fireflies, Zoom. |
| A document or transcript, by hand | `POST /api/ingest` with `{content, title, source_id, participants}` — or paste into the Explorer UI |
| A passing thought | `POST /api/captures`, or the `capture_thought` MCP tool from any connected agent |
| A git repo of notes/docs | back the corpus with a GitHub repo + optional webhook — [setup guide](git-corpus.md) |
| Grain recordings, directly | `python -m app.connectors` exports to JSONL for `/api/ingest` |

Two habits worth forming:

- **Always send a stable `source_id`.** It's the idempotency key — re-ingesting the same
  source updates instead of duplicating, which also makes retries safe.
- **Skim the delta report** (`GET /api/ingest/{job_id}/delta`) after anything important. It's
  a human-readable summary of what the pipeline extracted and changed — the fastest way to
  catch a misclassification while the context is still fresh.

## Review what it learned

Extraction is automatic; *trust* is not. Every extracted signal enters as **evidence** —
visible, searchable, but not something an agent may treat as policy. A human review action is
what promotes it to **instruction** grade
([ADR-002](../adr/ADR-002-evidence-instruction-authority-gate.md)). Your review loop:

1. **Overview** (`/overview`) — the dashboard's *Needs review* card is your inbox.
2. **Signal feed** (`/feed`) — everything extracted, newest first. Confirm the signals that
   should carry weight; leave the rest as evidence. Confirming is deliberate friction — it's
   the one gate between "something someone said in a meeting" and "a rule agents follow."
3. **Decisions** (`/decisions`) — decisions get lifecycle states computed at read time
   (`active`, `stale`, `superseded`, `conflicting`). Conflicts surface here; resolve them by
   confirming the winner or marking supersession, and the graph's answer to "what's the
   current ruling?" stays correct.
4. **Memory** (`/memory`) — the same confirm/keep-as-evidence review for agent-written
   memories and captures.

A few minutes after each important meeting beats a weekly slog — the feed is chronological,
so review debt compounds.

## Query it

- **Chat** (`/chat`) — conversational access to the whole graph; the in-process agent runs
  the tool loop for you.
- **Explorer & Entities** (`/explorer`, `/entities`, `/domain-graph-enhanced`) — browse
  documents, entity profiles, and the graph visually.
- **From your own AI tools, over MCP** — the same knowledge from Claude Code, Claude Desktop,
  or Cursor (`.mcp.json.example` has the snippet). The workhorses: `ask_kb` for synthesized
  answers, `search_knowledge_graph` + `find_related_entities` for composing your own,
  `memory_recall` for semantic recall, `get_constitution` for all confirmed active decisions
  as one markdown document. Full catalog: [MCP & API](../architecture/mcp-and-api.md).
- **REST** — everything MCP does and more; schema at `/openapi.json`.

## Keep it healthy

Occasional, not daily:

| Task | How | When |
|---|---|---|
| Merge duplicate entities | Entities UI, or `POST /api/entities/{id}/merge` with `preview=true` first | when search shows twins ("Acme" / "Acme Corp") |
| Stop a recurring junk extraction | add a `ner_exclude` entry in `config/domains/<domain>.yaml` + restart | when you keep deleting the same wrong entity |
| Rebuild the vector index | `curl -X POST localhost:8080/api/admin/backfill-memory-index` | semantic recall comes back empty after a restart or `VECTOR_BACKEND` switch |
| Check system status | Command Center (`/command`), `GET /health/ready` | when anything feels off |
| Back up state | the `imi-neo4j-data` volume, `./data` (SQLite + vectors), `./repo` (git corpus) | on your normal backup cadence — connecting a GitHub remote ([git corpus guide](git-corpus.md)) makes the corpus back itself up on every write |

The graph itself is rebuildable: files are the source of truth, and by default
(`NEO4J_REBUILD_ON_STARTUP=true`) the graph is reconstructed from the corpus at boot.

## Where to go deeper

| You want to… | Read |
|---|---|
| Tune extraction to your business | [Domain Schemas](../customization/domain-schemas.md) |
| Understand the trust model you're gatekeeping | [Signals & Governance](../architecture/signals-and-governance.md) |
| Let agents run this loop for you | [Agent Operating Guide](../agents/README.md) |
| Change any setting | [Configuration](configuration.md) |
