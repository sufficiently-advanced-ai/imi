"""
Shared MCP tool definitions — the single source of truth for tool contracts
exposed via both `app/routes/mcp_server.py` (external Claude Code clients) and
`app/agents/chat_tools_mcp.py` (internal chat agent).

Phase D of the MCP surface cleanup. Before this module existed, each surface
maintained its own copy of tool descriptions and JSON schemas, and the two
copies drifted independently. Now both surfaces register from `TOOL_DEFS`,
each adding their own runtime wrapper concerns (SSE emission and ContextVar
on the chat-agent side; raw dispatcher on the external side; the
`mcp__chat__` prefix on the chat-agent side).

Conventions for adding a new tool to this module:
- Use canonical parameter names from `docs/mcp_tool_conventions.md`
  (`max_results`, `entity_id`, `entity_ids`, `timestamp`, `date_from`,
  `date_to`, `max_depth`).
- Descriptions follow the Phase B sharpening rules: lead with what the tool
  does in caller-facing terms, name the contrasting tool when there's
  ambiguity, document non-obvious side effects.
- Schemas are JSON Schema (not Python-type schemas) so the same definition
  works for both the raw `Tool(...)` registration in mcp_server.py and the
  `@tool` decorator in chat_tools_mcp.py (which accepts JSON Schema when
  `type` and `properties` keys are present).

Migration status (as of Phase D):
- Migrated: the consolidated query/signal tools from Phase C1 — see
  `MIGRATED_TOOLS` below.
- Not yet migrated: graph mutation tools, meeting tools, temporal/decision
  tools. Those will move into TOOL_DEFS as part of follow-up phases.
"""

from typing import Any

# ---------------------------------------------------------------------------
# Tool definition schema
# ---------------------------------------------------------------------------

# A tool definition is a dict with three keys:
#   name        — canonical tool name (no surface-specific prefix)
#   description — caller-facing description string
#   inputSchema — JSON Schema for the tool's input parameters
ToolDef = dict[str, Any]


# ---------------------------------------------------------------------------
# Migrated tool definitions (Phase D — initial set)
#
# These are the consolidated query/signal tools from Phase C1, which have
# the highest cross-surface drift risk because they are dual-exposed.
# ---------------------------------------------------------------------------

TOOL_DEFS: dict[str, ToolDef] = {
    "search_knowledge_graph": {
        "name": "search_knowledge_graph",
        "description": (
            "Fuzzy/keyword search across entity names and string attributes. "
            "Returns ranked matches (highest score first). "
            "Use this when you don't know the exact entity name. "
            "For exact-name lookup use get_entity_by_name; for type-scoped retrieval "
            "(all entities of a given type) use list_entities."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search text matched against names and string attribute values",
                },
                "entity_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of entity types to restrict the search (e.g. ['person', 'project'])",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    "list_entities": {
        "name": "list_entities",
        "description": (
            "List all entities of a given type, optionally with their relationships inline. "
            "Use this for bulk retrieval scoped to one type (e.g. 'all members'). "
            "When include_relationships=true, you usually do NOT need a follow-up "
            "find_related_entities call — the data is already on each entity. "
            "Use attribute_filter to pre-filter by attribute values "
            "(e.g. attribute_filter='west coast' returns only entities whose attributes "
            "contain that substring)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": "Entity type to list (e.g. 'member', 'focus_area', 'cohort')",
                },
                "include_relationships": {
                    "type": "boolean",
                    "description": "Include relationship data inline on each entity (default true)",
                    "default": True,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum entities to return (default 50)",
                    "default": 50,
                },
                "attribute_filter": {
                    "type": "string",
                    "description": "Optional substring filter applied to attribute values (e.g. 'west coast')",
                },
            },
            "required": ["entity_type"],
        },
    },
    "get_entity_by_name": {
        "name": "get_entity_by_name",
        "description": (
            "Look up a single entity by its display name (case-insensitive, with partial-match fallback). "
            "Use this when you have a precise name and want the canonical entity ID and metadata. "
            "For fuzzy or keyword queries use search_knowledge_graph instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity name to look up"},
                "entity_type": {
                    "type": "string",
                    "description": "Optional type filter (e.g. 'person', 'project')",
                },
            },
            "required": ["name"],
        },
    },
    "find_related_entities": {
        "name": "find_related_entities",
        "description": (
            "Traverse the graph from one entity to its directly connected entities. "
            "Two modes (set via the 'mode' parameter):\n"
            "  - mode='neighbors' (default): returns the list of neighboring entities, "
            "each with the relationship type connecting them. Optionally filter by relationship_type.\n"
            "  - mode='types_only': returns just an inventory of which relationship types this entity "
            "has (outgoing + incoming) and how many of each, without the full neighbor list. "
            "Useful as a quick discovery step before deciding which relationship_type to traverse.\n"
            "'Outgoing' = edges where this entity is the source; 'incoming' = edges where this entity is the target."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Starting entity ID (e.g. 'person-sarah-chen')",
                },
                "relationship_type": {
                    "type": "string",
                    "description": "Only used in mode='neighbors'. Restrict to one relationship type (e.g. 'has_projects').",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum neighbor entities to return in mode='neighbors' (default 20)",
                    "default": 20,
                },
                "mode": {
                    "type": "string",
                    "enum": ["neighbors", "types_only"],
                    "description": "What to return: 'neighbors' for connected entities, 'types_only' for a relationship-type inventory (default 'neighbors')",
                    "default": "neighbors",
                },
            },
            "required": ["entity_id"],
        },
    },
    "search_signals": {
        "name": "search_signals",
        "description": (
            "Search persisted meeting signals (decisions, action items, key points, insights). "
            "Source of truth is the SignalStore JSON files; results are filtered in-memory. "
            "When entity_id is provided, prefers a Neo4j graph-relationship lookup (signals "
            "explicitly linked to the entity in the graph) and falls back to JSON content-mention "
            "filtering if the graph is unavailable. Without entity_id, filters JSON directly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "Filter to signals linked to this entity slug ID (e.g. 'person-chris-fernandes'). Triggers graph-first lookup.",
                },
                "signal_type": {
                    "type": "string",
                    "description": "Filter by type: decision, action_item, key_point, insight",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by status: open, in_progress, done",
                },
                "client_id": {
                    "type": "string",
                    "description": "Filter to signals scoped to this client slug ID (e.g. 'client-acme-corp'). Omit to sweep across all clients.",
                },
                "date_from": {
                    "type": "string",
                    "description": "Inclusive start date (YYYY-MM-DD)",
                },
                "date_to": {
                    "type": "string",
                    "description": "Inclusive end date (YYYY-MM-DD)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Cap on returned signals (default 20)",
                    "default": 20,
                },
            },
        },
    },
    "search_signals_semantic": {
        "name": "search_signals_semantic",
        "description": (
            "Governance-aware semantic (vector similarity) search over indexed signals. "
            "Returns signals ranked by embedding similarity to the query, with optional "
            "recency blending. "
            'Use authority="instruction" to return only signals that satisfy the '
            "ADR-002 invariant (human-confirmed, instruction-grade records); "
            'the default authority="evidence" returns all evidence-grade-or-better signals. '
            "Rejected and superseded signals are excluded by default; set include_rejected=True "
            "to include them. "
            "Complements search_signals (exact-field filter) — use this tool when a "
            "natural-language query is more appropriate than a structured filter."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query matched via vector similarity",
                },
                "signal_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of signal types to restrict results "
                        "(e.g. ['decision', 'action_item', 'key_point', 'insight'])"
                    ),
                },
                "status": {
                    "type": "string",
                    "description": "Optional status filter: open, in_progress, done",
                },
                "authority": {
                    "type": "string",
                    "enum": ["evidence", "instruction"],
                    "description": (
                        "Trust-axis filter. "
                        '"evidence" (default) returns evidence-grade-or-better signals. '
                        '"instruction" returns only instruction-grade signals satisfying '
                        "the ADR-002 invariant (human-confirmed)."
                    ),
                    "default": "evidence",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 10)",
                    "default": 10,
                },
                "recency_weight": {
                    "type": "number",
                    "description": (
                        "Blend factor for recency: 0 = pure similarity (default), "
                        "1 = pure recency. Values in between blend exponential half-life "
                        "decay into the score."
                    ),
                    "default": 0,
                },
                "include_rejected": {
                    "type": "boolean",
                    "description": (
                        "When true, include rejected/superseded/disputed signals in results. "
                        "Default false."
                    ),
                    "default": False,
                },
            },
            "required": ["query"],
        },
    },
    "capture_thought": {
        "name": "capture_thought",
        "description": (
            "Capture a thought, note, or fact into the knowledge base's general "
            "memory layer (distinct from meeting ingestion). The text is persisted "
            "immediately, then enriched with extracted metadata (type, topics, "
            "people, action items), embedded for semantic search, and committed to "
            "the corpus. Duplicate content (same fingerprint or same source+source_id) "
            "returns the existing record with deduped=true instead of creating a copy. "
            "Captures enter the governance ladder as imported, evidence-grade memory — "
            "they only become instruction-grade after human review (ADR-002); "
            "provenance and authority are server-injected and never parameters. "
            "Use add_call_transcript for meeting transcripts — this tool is for "
            "everything else (quick notes, web content, decisions worth remembering)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The thought/text to capture",
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Capture source: manual (default), web, mail, or rss"
                    ),
                    "default": "manual",
                },
                "source_id": {
                    "type": "string",
                    "description": (
                        "External id (URL, message id) for idempotent re-capture"
                    ),
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional caller-supplied tags",
                },
                "source_date": {
                    "type": "string",
                    "description": "Original publish/sent date (ISO), if known",
                },
            },
            "required": ["content"],
        },
    },
    "memory_writeback": {
        "name": "memory_writeback",
        "description": (
            "Persist a batch of typed operational memories (decisions, outputs, "
            "lessons, constraints, unresolved_questions, next_steps, failures, "
            "artifacts) after completing a task. Rows are deduplicated by "
            "idempotency_key — replaying the same key returns the same records. "
            "Content is safety-gated: secrets, credentials, large code blocks, and "
            "raw transcripts reject the whole batch. Everything written enters the "
            "review queue as evidence-grade memory pending human review — this tool "
            "can NEVER produce instruction-grade memory (ADR-002); "
            "provenance_default_status may only be observed, inferred, or generated. "
            "Use capture_thought for a single free-form thought; this tool is for "
            "structured end-of-task write-back."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_payload": {
                    "type": "object",
                    "description": (
                        "Typed memory lists: decisions[], outputs[], lessons[], "
                        "constraints[], unresolved_questions[], next_steps[], "
                        "failures[], artifacts[{kind, uri, description}]"
                    ),
                    "properties": {
                        "decisions": {"type": "array", "items": {"type": "string"}},
                        "outputs": {"type": "array", "items": {"type": "string"}},
                        "lessons": {"type": "array", "items": {"type": "string"}},
                        "constraints": {"type": "array", "items": {"type": "string"}},
                        "unresolved_questions": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "next_steps": {"type": "array", "items": {"type": "string"}},
                        "failures": {"type": "array", "items": {"type": "string"}},
                        "artifacts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {"type": "string"},
                                    "uri": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["kind", "uri"],
                            },
                        },
                    },
                },
                "task_id": {"type": "string", "description": "Originating task id"},
                "flow_id": {"type": "string", "description": "Originating flow id"},
                "runtime_name": {
                    "type": "string",
                    "description": "Agent runtime writing the memory",
                },
                "runtime_version": {"type": "string"},
                "confidence": {
                    "type": "number",
                    "description": "0-1 confidence in the batch",
                    "default": 0.5,
                },
                "provenance_default_status": {
                    "type": "string",
                    "enum": ["observed", "inferred", "generated"],
                    "description": (
                        "How the memories came to exist. user_confirmed/imported are "
                        "review outcomes and cannot be claimed here (ADR-002)."
                    ),
                    "default": "generated",
                },
                "stale_after": {
                    "type": "string",
                    "description": "Freshness horizon (ISO timestamp), optional",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": "Replay key — same key returns the same records",
                },
            },
            "required": ["memory_payload"],
        },
    },
    "memory_recall": {
        "name": "memory_recall",
        "description": (
            "Unified governed recall over ALL memory record kinds — meeting signals, "
            "captured thoughts, and agent-written memories — ranked by semantic "
            "similarity plus the trust axis (confirmed instruction-grade memory "
            "rises; unreviewed generated memory sinks). "
            'authority="instruction" returns only records satisfying the ADR-002 '
            "invariant (human-confirmed); governance is re-checked against the "
            "authoritative store at recall time. Every call returns a request_id — "
            "report which memories you actually used via record_memory_usage. "
            "Use search_signals_semantic for signals-only search; this tool is the "
            "cross-kind recall surface."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language recall query",
                },
                "authority": {
                    "type": "string",
                    "enum": ["evidence", "instruction"],
                    "default": "evidence",
                    "description": (
                        '"instruction" returns only human-confirmed, '
                        "instruction-grade records (ADR-002)."
                    ),
                },
                "record_kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["signal", "capture", "agent_memory"],
                    },
                    "description": "Restrict to specific record kinds (default: all)",
                },
                "limit": {"type": "integer", "default": 10},
                "recency_weight": {
                    "type": "number",
                    "default": 0,
                    "description": "0 = pure similarity; >0 blends recency decay",
                },
                "task_id": {
                    "type": "string",
                    "description": "Originating task id (for the recall trace)",
                },
                "runtime_name": {
                    "type": "string",
                    "description": "Agent runtime making the recall",
                },
            },
            "required": ["query"],
        },
    },
    "record_memory_usage": {
        "name": "record_memory_usage",
        "description": (
            "Report which memories from a memory_recall response were actually used "
            "or deliberately ignored. Closes the recall feedback loop — usage data "
            "drives future ranking quality and the memory inspector. Call this after "
            "acting on recalled memories, passing the request_id from the recall."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "description": "request_id returned by memory_recall",
                },
                "used_memory_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Record ids that informed your output",
                },
                "ignored": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "memory_id": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["memory_id"],
                    },
                    "description": "Records returned but deliberately not used",
                },
            },
            "required": ["request_id"],
        },
    },
    "inspect_memory": {
        "name": "inspect_memory",
        "description": (
            "Inspect a governed memory record (capture or agent memory): why it "
            "exists (provenance/enrichment), its full audit history, how often it "
            "was recalled/used/ignored, judge decisions that relied on it, its "
            "supersession lineage, and what it may influence (evidence vs "
            "instruction grade). Works for deleted records too — the audit trail "
            "survives deletion. Use this to understand why a memory was handed to "
            "you, or before trusting a recalled record."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "Capture or agent-memory record id",
                },
            },
            "required": ["record_id"],
        },
    },
    "list_entity_profiles": {
        "name": "list_entity_profiles",
        "description": (
            "Bulk-fetch full profiles (attributes + relationships) for a specific set of entity IDs. "
            "Sibling of list_entities, but selected by ID rather than by type. "
            "Use this when you already have a set of entity IDs (from search_knowledge_graph or "
            "find_related_entities) and want their full data in one call instead of calling "
            "find_related_entities once per entity. Capped at 50 entities per call. "
            "Missing entities appear as {id, error: 'not_found'}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of entity IDs to fetch profiles for (capped at 50)",
                },
                "include_relationships": {
                    "type": "boolean",
                    "description": "Include relationship data per entity (default true)",
                    "default": True,
                },
                "max_relationships_per_entity": {
                    "type": "integer",
                    "description": "Cap on relationships per entity (default 20)",
                    "default": 20,
                },
            },
            "required": ["entity_ids"],
        },
    },
    "update_signal": {
        "name": "update_signal",
        "description": (
            "Update a persisted meeting signal (decision, action item, key point, insight). "
            "Two mutually exclusive paths:\n\n"
            "**Plain field update** (status / content / owner_id / due_date): writes to "
            "the SignalStore JSON file (source of truth), git-commits the change, then "
            "syncs to Neo4j (best-effort). At least one field required.\n\n"
            "**Governance transition** (review_action): routes through the audited review "
            "boundary — applies the trust-axis state machine (ADR-002), appends an "
            "immutable audit row to signals/audit/{signal_id}.jsonl, git-commits the audit "
            "file, and mirrors the governance change to Neo4j (all best-effort after the "
            "primary write). Use this to confirm, reject, mark evidence-only, dispute, or "
            "supersede a signal.\n\n"
            "Authority / governance fields (can_use_as_evidence, can_use_as_instruction, "
            "provenance_status, review_status) are NOT settable directly — review_action "
            "is the ONLY governance entry point (ADR-002 server-injected)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "signal_id": {
                    "type": "string",
                    "description": "The signal's UUID5 identifier",
                },
                "status": {
                    "type": "string",
                    "description": "New status: open, in_progress, or done (plain field update)",
                },
                "content": {
                    "type": "string",
                    "description": "Updated content text (plain field update)",
                },
                "owner_id": {
                    "type": "string",
                    "description": "Entity slug ID of the new owner (e.g. 'person-sarah-chen'). Resolved to a readable name from the graph. (plain field update)",
                },
                "due_date": {
                    "type": "string",
                    "description": "New due date (YYYY-MM-DD) (plain field update)",
                },
                "revisit_date": {
                    "type": "string",
                    "description": (
                        "Marks a decision temporary until this date — after the date passes "
                        "it surfaces as zombie; empty string clears. Accepts ISO date "
                        "(YYYY-MM-DD) or ISO datetime. Mutually exclusive with review_action."
                    ),
                },
                "review_action": {
                    "type": "string",
                    "enum": [
                        "confirm",
                        "reject",
                        "evidence_only",
                        "dispute",
                        "supersede",
                    ],
                    "description": (
                        "Governance transition — the ONLY way to change trust-axis fields. "
                        "confirm: promotes to instruction-grade (user_confirmed provenance). "
                        "reject: blocks signal from evidence and instruction use. "
                        "evidence_only: safe for context but not for agent instructions. "
                        "dispute: flags provenance as disputed, clears instruction-grade. "
                        "supersede: marks this signal as replaced by superseded_by (required)."
                    ),
                },
                "actor": {
                    "type": "string",
                    "description": "Who is performing the review action (recorded in the audit row). Omit to leave anonymous.",
                },
                "superseded_by": {
                    "type": "string",
                    "description": "Required when review_action='supersede': the ID of the successor signal.",
                },
            },
            "required": ["signal_id"],
        },
    },
    "delete_signal": {
        "name": "delete_signal",
        "description": (
            "Permanently remove a signal. Three-layer persistence: deletes from the SignalStore "
            "JSON file (source of truth), git-commits the deletion, then removes from Neo4j "
            "(best-effort — Neo4j removal failure is non-fatal and reported via neo4j_synced). "
            "The git commit preserves an audit trail; use git revert to undo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "signal_id": {
                    "type": "string",
                    "description": "The signal's UUID5 identifier",
                },
            },
            "required": ["signal_id"],
        },
    },
    "read_document": {
        "name": "read_document",
        "description": (
            "Read a markdown document from the knowledge base by file path. "
            "Returns content plus parsed YAML frontmatter as metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Document path relative to repo root (e.g. 'members/john-doe.md')",
                },
            },
            "required": ["path"],
        },
    },
    "extract_entities": {
        "name": "extract_entities",
        "description": (
            "Extract structured entities (people, projects, teams, and any other configured types) "
            "from unstructured text using AI. Returns a dict keyed by entity type."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to extract entities from",
                },
            },
            "required": ["text"],
        },
    },
    "list_meetings": {
        "name": "list_meetings",
        "description": (
            "List meetings with titles, dates, participants, and bot_ids. Use to discover "
            "available meetings before calling get_meeting_transcript. Newest first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "Max meetings to return (default 20, capped at 100)",
                    "default": 20,
                },
                "status": {
                    "type": "string",
                    "enum": ["finalized", "in_progress", "all"],
                    "description": "Filter by meeting status (default 'all')",
                    "default": "all",
                },
            },
        },
    },
    "get_meeting_transcript": {
        "name": "get_meeting_transcript",
        "description": (
            "Get the full transcript plus metadata for one meeting, identified by its bot_id "
            "(from list_meetings). Truncates the transcript at max_length characters; pass "
            "max_length=0 for the full transcript."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bot_id": {
                    "type": "string",
                    "description": "The bot ID of the meeting (from list_meetings results)",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Max transcript character length (default 50000, 0 = no truncation)",
                    "default": 50000,
                },
            },
            "required": ["bot_id"],
        },
    },
    "add_call_transcript": {
        "name": "add_call_transcript",
        "description": (
            "Ingest a call/meeting transcript and run the full enrichment pipeline "
            "(classify → build meeting → promote signals → write to the graph → "
            "persist), making the call a first-class meeting alongside live ones.\n\n"
            "ALWAYS include participants and timing — both are REQUIRED and the call "
            "is rejected without them:\n"
            "  - start_time: ISO 8601 timestamp of when the call started "
            "(e.g. '2026-06-04T14:30:00Z'); it places the meeting on the timeline.\n"
            "  - participants: the list of people on the call; they become linked "
            "Person entities and action-item owners.\n"
            "Strongly prefer a transcript with inline '[mm:ss] Speaker:' markers — "
            "speaker attribution and timing markedly improve extraction quality.\n\n"
            "Blocks until enrichment finishes (usually a few seconds) and returns a "
            "summary (bot_id + extracted signal/entity counts). If it exceeds "
            "wait_timeout_seconds it returns {status:'processing', job_id, poll_url} "
            "instead — poll that job, then use list_meetings / get_meeting_transcript. "
            "Pass source_id (a stable external ID) to make re-ingestion idempotent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "transcript": {
                    "type": "string",
                    "description": "Full transcript text. Inline '[mm:ss] Speaker:' markers are preserved and improve attribution.",
                },
                "start_time": {
                    "type": "string",
                    "description": "REQUIRED. ISO 8601 timestamp when the call started (e.g. '2026-06-04T14:30:00Z').",
                },
                "participants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "REQUIRED. Non-empty list of attendee names.",
                },
                "title": {
                    "type": "string",
                    "description": "Call subject/title (optional but recommended).",
                },
                "source": {
                    "type": "string",
                    "enum": [
                        "local_recording",
                        "plaud",
                        "grain",
                        "otter",
                        "fathom",
                        "fireflies",
                    ],
                    "description": "Recorder source (default 'local_recording'). All map to the call_transcript pipeline.",
                    "default": "local_recording",
                },
                "duration_minutes": {
                    "type": "number",
                    "description": "Call length in minutes (optional).",
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Optional stable key linking multiple captures of the SAME call (e.g. a Plaud recording and a Grain transcript), for future reconciliation.",
                },
                "source_id": {
                    "type": "string",
                    "description": "Optional external ID for idempotency — re-ingesting the same source_id is suppressed as a duplicate.",
                },
                "wait_timeout_seconds": {
                    "type": "integer",
                    "description": "Max seconds to block for enrichment before returning a job_id to poll (default 30, clamped 1..60).",
                    "default": 30,
                },
            },
            "required": ["transcript", "start_time", "participants"],
        },
    },
    # --- Decision lifecycle tools ---
    "list_decisions": {
        "name": "list_decisions",
        "description": (
            "List decision signals with computed lifecycle states "
            "(candidate/active/stale/superseded/rejected). "
            "States are computed from governance review status plus age — there is no "
            "separate decision store; signals with type=='decision' are the source of truth. "
            "Use search_signals for raw signal-field filtering across all signal types; "
            "use this tool for lifecycle-aware decision queries. "
            "States temporary/zombie/conflicting are reserved and currently never emitted."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": [
                        "candidate",
                        "active",
                        "stale",
                        "superseded",
                        "rejected",
                        "temporary",
                        "zombie",
                        "conflicting",
                    ],
                    "description": (
                        "Filter by computed lifecycle state. "
                        "candidate: unreviewed/default. "
                        "active: confirmed and not stale. "
                        "stale: confirmed but older than 90 days or manually staled. "
                        "superseded: replaced by another decision. "
                        "rejected: explicitly rejected. "
                        "temporary/zombie/conflicting: reserved, never emitted."
                    ),
                },
                "owner_id": {
                    "type": "string",
                    "description": "Filter to decisions owned by this entity slug ID (e.g. 'person-alice')",
                },
                "client_id": {
                    "type": "string",
                    "description": "Filter to decisions scoped to this client slug ID (e.g. 'client-acme-corp'). Omit to sweep across all clients.",
                },
                "date_from": {
                    "type": "string",
                    "description": "Inclusive start date (YYYY-MM-DD) compared against source_timestamp",
                },
                "date_to": {
                    "type": "string",
                    "description": "Inclusive end date (YYYY-MM-DD) compared against source_timestamp",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Cap on returned decisions (default 50). total and counts_by_state always reflect the full matching set before truncation.",
                    "default": 50,
                },
            },
        },
    },
    "get_decision": {
        "name": "get_decision",
        "description": (
            "Get one decision by signal ID with its supersession lineage chain, "
            "governance audit history, and governance-ladder position. "
            "Returns the full decision view extended with: "
            "lineage (predecessors → self → successors via superseded_by hops), "
            "audit_history (immutable audit rows from signals/audit/{id}.jsonl), and "
            "governance_ladder (position: instruction|evidence|blocked). "
            "Read-only; to change governance state use update_signal with review_action. "
            "Returns an error when the decision_id does not exist."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "decision_id": {
                    "type": "string",
                    "description": "The signal UUID5 identifier of the decision to retrieve",
                },
            },
            "required": ["decision_id"],
        },
    },
    "get_constitution": {
        "name": "get_constitution",
        "description": (
            "Get the full current decision constitution as Markdown: the organization's "
            "active, conflicting, stale, and superseded decisions with their owners, "
            "rationale, decided date, and governance authority "
            "(instruction-grade | evidence-grade | blocked). "
            "Built fresh from the current decision signals on every call, so it is always "
            "up to date and never missing. Returns the complete document; takes no parameters. "
            "Use this to load the organization's governing decisions into context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    "ask_kb": {
        "name": "ask_kb",
        "description": (
            "Natural-language intent tool. Give a free-form question or instruction; "
            "an internal sub-agent runs a tool-use loop over the read-only CRUD tools "
            "(search_knowledge_graph, list_entities, find_related_entities, search_signals, "
            "list_entity_profiles, read_document, etc.) and returns a synthesized answer "
            "plus a structured trace of which tools were called.\n\n"
            "Use this when you have a fuzzy or multi-step question and don't already know "
            "which CRUD tool to reach for. If you DO know the specific tool to call, prefer "
            "calling it directly — it's cheaper and deterministic.\n\n"
            "Read-only by default. Pass allow_mutations=true to give the sub-agent access "
            "to update_signal / delete_signal as well."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "description": "Free-form natural-language question or instruction.",
                },
                "entity_context": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of entity IDs to ground the sub-agent (surfaced in the system prompt).",
                },
                "allow_mutations": {
                    "type": "boolean",
                    "description": "When true, gives the sub-agent access to mutation tools (update_signal, delete_signal). Default false.",
                    "default": False,
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Hard cap on tool-call iterations (default 8).",
                    "default": 8,
                },
            },
            "required": ["intent"],
        },
    },
}


# Convenience: tools migrated to this module so callers can audit migration
# progress without having to introspect the dict.
MIGRATED_TOOLS = list(TOOL_DEFS.keys())


# ---------------------------------------------------------------------------
# Helpers for surface-specific registration
# ---------------------------------------------------------------------------


def get_tool_def(name: str) -> ToolDef:
    """Look up a tool definition by canonical name. Raises KeyError if missing."""
    if name not in TOOL_DEFS:
        raise KeyError(
            f"Tool '{name}' is not yet migrated to mcp_tool_definitions. "
            f"Migrated tools: {sorted(MIGRATED_TOOLS)}"
        )
    return TOOL_DEFS[name]


def build_mcp_tool(name: str):
    """Build a `mcp.types.Tool` from the shared definition.

    Used by `app/routes/mcp_server.py`'s TOOLS list. Lazy-imports `Tool` so
    this module is testable without the MCP SDK installed.
    """
    from mcp.types import Tool

    td = get_tool_def(name)
    return Tool(
        name=td["name"],
        description=td["description"],
        inputSchema=td["inputSchema"],
    )


def chat_tool_args(name: str) -> tuple[str, str, dict]:
    """Return (mcp_name, description, schema) for the chat-agent @tool decorator.

    Used by `app/agents/chat_tools_mcp.py`'s `@tool` decorations. The chat
    surface prefixes tool names with `mcp__chat__`. The description is
    forwarded as-is. The schema is the JSON Schema dict (the @tool decorator
    accepts JSON Schema when 'type' and 'properties' keys are present, which
    they always are for definitions in this module).
    """
    td = get_tool_def(name)
    return f"mcp__chat__{td['name']}", td["description"], td["inputSchema"]
