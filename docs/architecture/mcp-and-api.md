# MCP Server & API Surface

> **Audience:** agent integrators and developers building on imi's query/mutation surface ·
> **Source of truth:** `app/services/mcp_tool_definitions.py` (`TOOL_DEFS`), `app/routes/mcp_server.py`, `docs/mcp_tool_conventions.md` ·
> **See also:** [Agent operating guide](../agents/README.md) · [Signals & Governance](signals-and-governance.md)

imi exposes its knowledge graph three ways: an **MCP server** (for external agents — Claude
Code, Claude Desktop, Cursor, any MCP client), an **internal chat agent** that uses the same
tools in-process, and a **REST API** (which the frontend consumes and which you can script
against).

## MCP server

- Implementation: `app/routes/mcp_server.py` — one MCP `Server("kb-graph")` served over two
  transports, mounted at `/api/mcp` by `mount_mcp()` (called from `_configure()` in
  `app/main.py`), which also wires the Streamable HTTP session manager into the app lifespan.
- Connect: **Streamable HTTP** at `/api/mcp/http` (GET/POST/DELETE), or legacy **SSE** at
  `GET /api/mcp/sse` (stream) + `POST /api/mcp/messages/`. Access tiers (local / relayed /
  remote) are in [MCP access tiers](../mcp_access_tiers.md). Copy `.mcp.json.example`
  to `.mcp.json`:

```json
{ "mcpServers": { "imi": { "type": "sse", "url": "http://localhost:8080/api/mcp/sse" } } }
```

- **Tool source of truth:** most tools are defined once in
  `app/services/mcp_tool_definitions.py` (`TOOL_DEFS`) and registered on both surfaces —
  external MCP via `build_mcp_tool()`, internal chat agent via `chat_tool_args()` (prefixed
  `mcp__chat__`). A few graph-mutation/cypher tools are still inline in `mcp_server.py`
  (noted as migration backlog in that file's header).
- **Conventions:** `docs/mcp_tool_conventions.md` defines the verb taxonomy
  (`search_` / `list_` / `get_` / `find_` / `read_` / `extract_` / `query_` / `graph_*`) and
  parameter conventions (`max_results` not `limit`, `entity_id(s)`, `date_from`/`date_to`).
  Follow it when adding tools.

### Tool catalog

**Query — graph & documents**

| Tool | Purpose |
|---|---|
| `search_knowledge_graph` | Fuzzy/keyword search over entity names + attributes, ranked |
| `list_entities` | Bulk list entities of a type, optional inline relationships |
| `get_entity_by_name` | Exact single-entity lookup |
| `find_related_entities` | Graph traversal from an entity (`mode=neighbors` or `types_only`) |
| `list_entity_profiles` | Bulk-fetch full profiles for up to 50 entity IDs |
| `query_graph_cypher` | Read-only Cypher (mutations rejected, max 100 rows) |
| `read_document` | Read a KB markdown file + parsed frontmatter |
| `extract_entities` | AI entity extraction from raw text |
| `ask_kb` | Natural-language intent → internal sub-agent runs a tool-use loop and returns a synthesized answer + trace |

**Signals, decisions & memory**

| Tool | Purpose |
|---|---|
| `search_signals` | Filter persisted signals (decision / action_item / key_point / insight) |
| `search_signals_semantic` | Governance-aware vector search; `authority=evidence\|instruction` |
| `list_decisions` / `get_decision` | Decision signals with lifecycle state, supersession lineage, audit trail |
| `get_constitution` | The full decision "constitution" rendered as markdown |
| `update_signal` | Update signal fields **or** run a governance transition (`review_action`) — the only governance entry point |
| `delete_signal` | Permanently remove a signal (JSON + git + Neo4j) |
| `capture_thought` | Persist a free-form thought into the memory layer (dedup, enrich, embed). `source` is the connector it came from; omitted = `unknown`, judged per item; the handler stamps `channel: mcp` (ADR-007) |
| `memory_writeback` | Batch write typed operational memories after a task (idempotent, safety-gated) |
| `memory_recall` | Unified governed recall across signals + captures + agent memories |
| `record_memory_usage` | Close the recall feedback loop (which memories were used/ignored) |
| `inspect_memory` | Audit one memory record: provenance, usage, lineage, judge decisions |

**Meetings & ingestion**

| Tool | Purpose |
|---|---|
| `list_meetings` / `get_meeting_transcript` | Discover meetings; fetch full transcript |
| `add_call_transcript` | Ingest a transcript through the full pipeline; blocks (or returns `job_id`) |

**Graph mutations** (each performs a three-layer write: Neo4j + markdown source file + git commit)

| Tool | Purpose |
|---|---|
| `graph_add_node` / `graph_update_node` / `graph_delete_node` | Entity CRUD (delete is soft + archives the source file) |
| `graph_merge_nodes` | Merge a duplicate entity into a primary |
| `graph_add_edge` / `graph_update_edge` / `graph_delete_edge` | Relationship mutations, validated against the active domain schema |

**Point in time** ([ADR-004](../adr/ADR-004-event-time-on-evidence.md)) — answers are dated by
when things happened, not by when they were recorded, so backfilled content lands in the right
place

| Tool | Purpose |
|---|---|
| `get_entity_at_time` | What was known about an entity at a past time: evidence counts, first/last seen, signals current then |
| `find_relationships_at_time` | Relationships stated by then, with how many sources stated each; co-mentions marked `derived` |
| `find_changes` | What was learned between two dates, plus `recorded_late` (material about an earlier time added during the window) |
| `get_graph_at_time` | The entities and relationships around an entity as known at a past time |
| `get_entity_provenance` | Every source that mentions an entity, in event order, with `recorded_at` and `time_source` |
| `list_claims` | Library claims about (or attributed to) an entity, oldest first, within an optional `date_from`/`date_to` window: attribution (resolved entities + source text), `as_of`, supersession, decay state; `include_stale` to see decayed claims ([ADR-006](../adr/ADR-006-library-primary-deployments.md) §6) |

Full parameter schemas: `app/services/mcp_tool_definitions.py`.

**Chat-surface-only tools:** the internal chat agent has the same point-in-time queries under
its older names — `graph_as_of`, `entity_at_time`, `active_relationships_at_time`,
`what_changed` — plus `find_contradictions` and `temporal_blast_radius` (registered in
`app/agents/chat_tools_mcp.py`). Both surfaces are backed by
`app/services/temporal_queries.py`.

### Governance invariant (ADR-002)

Load-bearing and enforced server-side across the memory/signal tools: **agents can only write
evidence-grade memory**. Instruction-grade authority requires a human review transition via
`update_signal(review_action="confirm")`. No tool parameter combination lets an agent mint
instruction-grade memory directly. See [Signals & Governance](signals-and-governance.md).

## In-repo agents (`app/agents/`)

| Component | Role |
|---|---|
| `chat.py` — `ChatAgent` | The chat interface. Uses the Claude Agent SDK with the in-process `mcp__chat__*` toolset (`chat_tools_mcp.py`). Invoked from `POST /api/query` with `prompt_type="search"` |
| `base.py` | `AgentBase` ABC + `AgentRegistry` (register/execute/chain agents) |
| `memory_agent.py` | Organizational-memory queries over the graph and signal store |

The **judge extender** lives in `app/services/judge_service.py` + `app/routes/judge.py`:
`judge_recall` returns evidence plus `policy_hits` (instruction-grade memory and confirmed
decisions, each with a `required_behavior` of allow/block/revise/escalate), and `judge_decide`
writes an idempotent `JudgeDecisionEvent`. This is the seam for building policy-aware agents on
top of imi.

## REST API surface

~55 routers under `app/routes/`, registered in `app/main.py`. Grouped:

| Area | Routers | Notable endpoints |
|---|---|---|
| Ingestion | `ingest.py`, `ingest_zapier.py`, `upload.py`, `webhook.py`, `captures.py` | `POST /api/ingest`, `POST /api/ingest/zapier`, `POST /api/webhook/github`, `POST /upload`, `POST /api/captures` |
| Entities | `entity_crud.py`, `entity_search.py`, `entity_profile.py`, `entity_management.py`, `entity_bulk.py`, `entity_enrichment.py`, `entity_suggestions.py`, … | `/api/entities*`, `GET /api/entities/{id}/profile` |
| Graph & domain | `domain_graph.py`, `domain_config.py`, `type_registry.py`, `knowledge_explorer.py` | `/api/domain-graph`, `GET /api/domain/config`, `/api/type-registry`, `POST /api/query` |
| Signals & governance | `signal_feed.py`, `signal_mutations.py`, `decisions.py`, `judge.py`, `supersession.py`, `conflicts.py` | `/api/signals`, `/api/decisions`, `POST /api/judge/recall`, `POST /api/judge/decisions` |
| Memory | `captures.py`, `agent_memory.py`, `memories_review.py`, `memory.py` | `/api/captures`, `/api/agent-memory`, `/api/memories` (review queue) |
| Agent/tool execution | `agent_tools.py`, `command.py`, `workflows.py`, `analysis.py`, `streaming_chat.py` | tool discovery + `POST` execute over `AgentToolRegistry` |
| Ops | `health.py`, `admin.py`, `metrics.py`, `production_monitoring.py`, `sse_status.py` | `GET /health`, `GET /health/ready`, `POST /api/admin/backfill-memory-index` |

## Auth

`AUTH_MODE` (env, handled in `app/services/auth.py`):

- `none` (default) — every request is the canonical demo user; no cookie.
- `demo` — requires a `session` cookie set by `/auth/login`; no external IdP.

Health, auth, docs, static, and the GitHub webhook are on a public allowlist
(`auth.py:172-190`). The **MCP endpoint has no bearer auth in community edition** — access
control is network-level (bind to loopback / nginx allowlist) plus a DNS-rebinding Host-header
allowlist seeded from `MCP_ALLOWED_HOSTS` and the host of `MCP_PUBLIC_URL`
(`_build_allowed_hosts()` in `mcp_server.py`), shared by both transports. Do not expose port
8080 to untrusted networks without a proxy in front; see
[MCP access tiers](../mcp_access_tiers.md) (ADR-008).

The hosted edition layers SSO/multi-tenant auth on the `create_app(extra_routers=...)` seam
(`app/main.py:1116`) and the tenant-context middleware.

## Customization points

| You want to… | Do this |
|---|---|
| Add an MCP tool | Add a `TOOL_DEFS` entry in `app/services/mcp_tool_definitions.py` (it will appear on both the external MCP and chat surfaces); follow `docs/mcp_tool_conventions.md` |
| Add a REST endpoint family | New router in `app/routes/`, include it in the relevant module (`app/modules/*/__init__.py`) or `app/main.py` |
| Build a policy-aware agent | Use `POST /api/judge/recall` → act → `POST /api/judge/decisions`; see `app/services/judge_service.py` |
| Add an in-process agent | Subclass `AgentBase` (`app/agents/base.py`); register with `AgentRegistry` |
| Restrict MCP access | Set `MCP_ALLOWED_HOSTS`; keep 8080 behind nginx/Tailscale |
| Reach MCP from other machines on a private network | Set `MCP_PUBLIC_URL` (+ `MCP_ALLOWED_HOSTS`, `BIND_ADDRESS`); see [MCP access tiers](../mcp_access_tiers.md) |
