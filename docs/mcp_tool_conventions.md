# MCP Tool Conventions

Shared conventions for tools exposed via any of the project's tool-registration surfaces. The goal is a consistent contract for callers (external Claude Code clients, the chat agent, REST API consumers) regardless of which surface the tool lives on.

The four surfaces are:

| Surface | File | Audience |
|---|---|---|
| `mcp_server` | `app/routes/mcp_server.py` | External Claude Code CLI via SSE |
| `chat_tools_mcp` | `app/agents/chat_tools_mcp.py` | Internal chat agent (Agent SDK) |
| `agent_tools` (registry) | `app/services/agent_tools.py` | Internal extraction/analysis tools |
| `agent_tools` (REST) | `app/routes/agent_tools.py` | External HTTP clients |

## Verb taxonomy

Tool names should follow this verb pattern. Pick the verb that matches what the tool actually does — don't invent new verbs for one-off cases.

| Verb | Meaning | Examples |
|---|---|---|
| `search_*` | Fuzzy / keyword / semantic queries returning ranked results | `search_knowledge_graph`, `search_meeting_transcripts`, `search_signals` |
| `list_*` | Bulk retrieval by deterministic criteria (type, ID set) | `list_entities`, `list_meetings`, `list_meeting_documents`, `list_entity_profiles` |
| `get_*` | Single-item exact lookup | `get_entity_by_name`, `get_meeting_transcript`, `get_constitution` |
| `find_*` | Graph traversal — neighbors, relationship inventories | `find_related_entities`, `find_decision_precedents`, `find_contradictions` |
| `read_*` | Content I/O on a known file path | `read_document` |
| `extract_*` | AI-driven extraction from raw text | `extract_entities`, `extract_decisions`, `extract_patterns`, `extract_risks` |
| `query_*` | Arbitrary expression languages (Cypher) | `query_graph_cypher` |
| `graph_add_*` / `graph_update_*` / `graph_delete_*` / `graph_merge_*` | Graph node/edge mutations | `graph_add_node`, `graph_delete_edge`, etc. |
| `update_*` / `delete_*` | Domain-object mutations (signals) | `update_signal`, `delete_signal` |

**Avoid**: noun-prefixed names (`decision_influence`, `decision_stats`) and one-off verbs (`trace_*`, `decision_*`). `temporal_blast_radius` is retained as an approved exception — the metaphor is load-bearing for callers (it describes a specific impact-propagation analysis rather than a graph traversal), so renaming it would lose meaning.

## Parameter conventions

| Parameter | Convention |
|---|---|
| `entity_type` | Singular string, when filtering by one type |
| `entity_types` | Plural array, when multiple types may be passed |
| `entity_id` | Singular, for a single entity reference |
| `entity_ids` | Plural array, for batch operations |
| `relationship_type` | Singular string (one type to traverse) |
| `relationship_types` | Plural array (multiple types) |
| `max_results` | Integer cap on returned items. Use this name everywhere — not `limit`, `count`, etc. |
| `max_depth` | Integer cap on graph-traversal depth. Use everywhere — not `depth`. |
| `timestamp` | Single absolute time, ISO-8601 |
| `date_from` / `date_to` | Inclusive date range, YYYY-MM-DD |

## Description guidance

Tool descriptions are the *only* contract a calling LLM sees. Treat them as load-bearing.

**Do**:
- Lead with what the tool does, in caller-facing terms
- Explain when to use this tool vs. an adjacent tool (where ambiguity exists)
- Document any non-obvious side effects (file writes, git commits, Neo4j syncs)
- Include parameter semantics that aren't obvious from the schema (e.g. "inclusive end date")

**Don't**:
- Use jargon without defining it: "one-hop traversal", "BFS", "blast radius", "provenance chain", "N+1 collapse"
- Reference internal implementations callers can't see: "uses Semantica", "via Neo4j", "via SignalStore" — unless the caller needs to know
- Mutate descriptions at runtime (a legacy `query_graph_cypher` tool used to do this; it was removed)

## Surface choice

When adding a new tool, ask:

1. **Who calls it?** External MCP clients → `mcp_server`. Chat agent only → `chat_tools_mcp`. Extraction/analysis pipeline → `agent_tools` registry.
2. **Is it CRUD or analysis?** CRUD on the graph → `graph_*` tools. Analysis on transcripts/signals → `agent_tools` registry.
3. **Does it need request context?** SSE event emission, ContextVar for `execution_id` → `chat_tools_mcp`. Stateless → `mcp_server`.
4. **Does it need to be reachable over HTTP/REST?** External HTTP/web clients (UI, curl, third-party services) → *also* register in `app/routes/agent_tools.py`. This applies on top of the runtime surface chosen in (1)–(3). For example, an `agent_tools` registry tool that the frontend calls directly needs both the registry entry *and* a REST handler in `app/routes/agent_tools.py`. Tools used only by Claude Code clients (`mcp_server`) or in-process agents (`chat_tools_mcp`) do **not** need a REST entry.

The shared tool-definition module is in `app/services/mcp_tool_definitions.py`. New dual-surface tools should register via `build_mcp_tool("<name>")` and `chat_tool_args("<name>")` from that module instead of duplicating descriptions and schemas across `mcp_server.py` and `chat_tools_mcp.py`. A handful of legacy tools still live inline on each surface — those migrations are tracked as follow-ups, but new work should not add to that backlog. Use this doc as the source of truth for any new tool.
