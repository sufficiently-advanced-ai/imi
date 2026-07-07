"""
ChatAgent - Intelligent context selection agent for chat interface.

Uses Claude Agent SDK with MCP tools for automatic tool execution.
Replaces the manual _call_claude / _execute_tool iteration loop with
a single SDK client that handles tool orchestration automatically.
"""

import asyncio
import json
import logging
import re
import sys
import time
from typing import Any

from app.agents.base import AgentBase, DecisionOutcome

# ---------------------------------------------------------------------------
# Span text sanitizer -- truncates and strips obvious PII patterns
# (emails, API keys) before attaching to OTEL spans.  The heavier
# PIIProtectionSpanProcessor in telemetry_manager.py handles export-time
# scrubbing; this is a lightweight first pass at attribute-set time.
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_API_KEY_RE = re.compile(r"(?:sk|pk|key|token|secret)[-_]?[A-Za-z0-9]{20,}", re.IGNORECASE)


def _sanitize_span_text(text: str, max_length: int = 200) -> str:
    """Truncate and strip obvious PII/secrets from text before attaching to spans."""
    if not text:
        return ""
    truncated = text[:max_length]
    truncated = _EMAIL_RE.sub("[EMAIL]", truncated)
    truncated = _API_KEY_RE.sub("[REDACTED_KEY]", truncated)
    return truncated


# ---------------------------------------------------------------------------
# OpenTelemetry imports with safe fallback
# ---------------------------------------------------------------------------
try:
    from opentelemetry import trace
    from opentelemetry.trace.status import Status, StatusCode

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

# ---------------------------------------------------------------------------
# SDK imports with fallback mocks (for test environments without the SDK)
# ---------------------------------------------------------------------------
try:
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        ClaudeSDKClient,
        PermissionResultAllow,
        PermissionResultDeny,
    )
    from claude_agent_sdk.types import StreamEvent, ToolUseBlock

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    ToolUseBlock = None  # type: ignore[assignment,misc]

    class ClaudeAgentOptions:
        """Mock ClaudeAgentOptions for when SDK is unavailable."""

        def __init__(
            self,
            model=None,
            system_prompt=None,
            mcp_servers=None,
            allowed_tools=None,
            max_turns=1000,
            permission_mode="acceptEdits",
            can_use_tool=None,
            **_,
        ):
            self.model = model
            self.system_prompt = system_prompt
            self.mcp_servers = mcp_servers or {}
            self.allowed_tools = allowed_tools or []
            self.max_turns = max_turns
            self.permission_mode = permission_mode
            self.can_use_tool = can_use_tool

    class PermissionResultAllow:
        """Mock PermissionResultAllow for when SDK is unavailable."""

        def __init__(self, updated_input=None, **_):
            self.updated_input = updated_input or {}

    class PermissionResultDeny:
        """Mock PermissionResultDeny for when SDK is unavailable."""

        def __init__(self, reason: str = "", **_):
            self.reason = reason

    class StreamEvent:
        """Mock StreamEvent for when SDK is unavailable."""

        def __init__(self, **_):
            self.event = {}
            self.uuid = ""
            self.session_id = ""

    class ClaudeSDKClient:
        """Mock ClaudeSDKClient for when SDK is unavailable."""

        def __init__(self, options=None):
            self.options = options
            self.connected = False

        async def connect(self):
            self.connected = True
            logger.info("Mock SDK client connected (SDK not available)")

        async def query(self, prompt):
            logger.info(f"Mock SDK query (SDK not available): {prompt[:100]}...")

        async def receive_response(self):
            yield "I'm currently unable to process your question as the Claude Agent SDK is not available."

        async def disconnect(self):
            self.connected = False
            logger.info("Mock SDK client disconnected (SDK not available)")


from app.agents.chat_tools_mcp import create_chat_tools_server  # noqa: E402 — after SDK-availability guard

logger = logging.getLogger(__name__)

# Log SDK availability once at module load
logger.info(f"[CHAT_AGENT_MODULE] Claude Agent SDK available: {SDK_AVAILABLE}")


class ChatAgent(AgentBase):
    """Intelligent chat agent with context awareness and tool usage.

    Uses the Claude Agent SDK to automatically orchestrate MCP tools
    for knowledge graph querying and graph maintenance.
    """

    def __init__(
        self,
        claude_client=None,
        git_ops=None,
        file_cache=None,
        platform: str | None = None,
        bot_id: str | None = None,
    ):
        """Initialize ChatAgent.

        Args:
            claude_client: Claude client wrapper.
            git_ops: Git operations service.
            file_cache: File cache service.
            platform: Chat platform identifier (e.g., 'google_meet', 'zoom').
            bot_id: Upstream bot identifier (for meeting context tool).
        """
        from app.git_ops import git_ops as default_git_ops
        from app.services.claude_client import ClaudeClient as CC
        from app.services.file_cache import file_cache as default_file_cache

        claude_client = claude_client or CC()
        git_ops = git_ops or default_git_ops
        file_cache = file_cache or default_file_cache

        from app.config import settings

        super().__init__(claude_client, git_ops, file_cache)
        self.model = settings.CLAUDE_SONNET_MODEL

        # Platform and bot context
        self.platform = platform.strip().lower() if isinstance(platform, str) else None
        self.bot_id = bot_id
        self.conversation_history: list[dict[str, Any]] = []
        self.max_history_messages = 50

        # SSE streaming configuration
        self.emit_sse = False
        self.execution_id: str | None = None

        # OpenTelemetry tracer
        self.tracer = trace.get_tracer("chat_agent") if OTEL_AVAILABLE else None

    # ------------------------------------------------------------------
    # Public interface (unchanged from old ChatAgent)
    # ------------------------------------------------------------------

    async def _emit_sse_event(self, event_type: str, event_data: dict[str, Any]):
        """Emit SSE event if streaming is enabled."""
        if not self.emit_sse or not self.execution_id:
            return
        try:
            from app.services.sse_manager import sse_manager

            await sse_manager.send_event(self.execution_id, event_type, event_data)
        except Exception as e:
            logger.warning(f"Failed to emit SSE event {event_type}: {e}")

    def configure_streaming(self, execution_id: str, emit_sse: bool = True):
        """Configure SSE streaming for this agent instance."""
        self.execution_id = execution_id
        self.emit_sse = emit_sse

    @property
    def name(self) -> str:
        return "ChatAgent"

    @property
    def description(self) -> str:
        return "Intelligent chat agent with automatic context selection and tool usage"

    @property
    def capabilities(self) -> list[str]:
        return [
            "conversation",
            "context_aware_responses",
            "agent_routing",
            "memory_integration",
            "entity_awareness",
        ]

    # ------------------------------------------------------------------
    # System prompt construction (preserved from old ChatAgent)
    # ------------------------------------------------------------------

    def _build_domain_schema_section(self) -> str:
        """Build a section describing the domain schema's actual relationship types."""
        try:
            from app.core.domain_config import get_domain_config

            config = get_domain_config()

            if not config.entities:
                return ""

            schema_lines = ["=== DOMAIN SCHEMA (ACTUAL RELATIONSHIP TYPES) ===", ""]

            for entity_name, entity_config in config.entities.items():
                if not entity_config.relationships:
                    continue

                rel_descriptions = []
                for rel in entity_config.relationships:
                    rel_descriptions.append(f"  - {rel.type} (Cypher: {rel.type.upper()}) -> {rel.target}")

                if rel_descriptions:
                    schema_lines.append(f"**{entity_name}** relationships:")
                    schema_lines.extend(rel_descriptions)
                    schema_lines.append("")

            schema_lines.append(
                "IMPORTANT: Use find_related_entities(entity_id=..., mode='types_only') FIRST to see what relationship types exist for a specific entity."
            )
            schema_lines.append(
                "The snake_case names above are the EXACT names you must use with find_related_entities() and graph_add_edge()."
            )
            schema_lines.append(
                "For query_graph_cypher: relationship types MUST be UPPERCASE (e.g. [:FOCUS_AREAS], [:HAS_MEMBERS], not [:focus_areas])."
            )
            schema_lines.append("")

            return "\n".join(schema_lines)

        except Exception as e:
            logger.warning(f"Could not build domain schema section: {e}")
            return ""

    def _build_entity_type_list(self) -> str:
        """Build a human-readable list of entity types from domain config."""
        try:
            from app.core.domain_config import get_domain_config

            config = get_domain_config()

            if not config.entities:
                return "entities"

            labels = []
            for entity_config in config.entities.values():
                label = getattr(entity_config, "plural_label", None) or getattr(entity_config, "plural", None) or entity_config.name
                labels.append(label)
            return ", ".join(labels) if labels else "entities"
        except Exception:
            return "entities"

    def _build_entity_type_names(self) -> list[str]:
        """Return the raw entity type ID strings from domain config."""
        try:
            from app.core.domain_config import get_domain_config

            config = get_domain_config()

            if not config.entities:
                return []
            return list(config.entities.keys())
        except Exception:
            return []

    def _build_relationship_type_names(self) -> list[str]:
        """Return all relationship type strings from domain config."""
        try:
            from app.core.domain_config import get_domain_config

            config = get_domain_config()

            if not config.entities:
                return []

            rel_types = set()
            for entity_config in config.entities.values():
                if not entity_config.relationships:
                    continue
                for rel in entity_config.relationships:
                    rel_types.add(rel.type)
            return sorted(rel_types)
        except Exception:
            return []

    def _build_query_examples(self) -> str:
        """Build query pattern examples dynamically from domain config entity types."""
        entity_types = self._build_entity_type_names()
        rel_types = self._build_relationship_type_names()

        if not entity_types:
            return ""

        # Pick first two entity types for examples
        primary_type = entity_types[0]  # e.g. "member"
        secondary_type = entity_types[1] if len(entity_types) > 1 else primary_type

        # Build example ID prefix from type
        primary_id_example = f"{primary_type}-example-123"
        secondary_id_example = f"{secondary_type}-example-456"

        # Pick relationship types for examples
        rel_example = rel_types[0] if rel_types else "related_to"
        rel_example_2 = rel_types[1] if len(rel_types) > 1 else rel_example

        lines = [
            "=== QUERY PATTERNS ===",
            "",
            "These patterns show MAXIMUM steps. Skip steps when earlier results already contain the answer.",
            "Most queries should complete in 1-3 tool calls.",
            "",
            '**Simple lookup: "Tell me about [name]"**',
            '1. search_knowledge_graph(query="[name]")',
            "2. read_document on the result's file_path",
            "3. Answer based on document content",
            "",
            f'**Bulk listing: "List all {primary_type}s" or "Who are the {primary_type}s?"**',
            f'1. list_entities(entity_type="{primary_type}", include_relationships=true)',
            f"   -> Returns ALL {primary_type}s with their relationships in ONE call",
            "2. Answer directly from the returned data (no need for individual lookups)",
            "",
            f'**Relationship query: "What {secondary_type}s are connected to [name]?"**',
            '1. get_entity_by_name(name="[name]") -> get entity ID',
            f'2. find_related_entities(entity_id="{primary_id_example}", mode="types_only") -> see which relationship types this entity has',
            f'3. find_related_entities(entity_id="{primary_id_example}", relationship_type="{rel_example}") -> get the connected entities of that type',
            "3. read_document on the target entity's document for details",
            "",
            f'**Filtering query: "Which {primary_type}s have [attribute]?"**',
            f'1. list_entities(entity_type="{primary_type}", include_relationships=true)',
            "2. Filter the results by the attribute or relationship in the response",
            "3. Read 2-3 documents for detailed information if needed",
            "",
            f'**Multi-hop: "What {secondary_type}s are shared between [name1] and [name2]?"**',
            '1. get_entity_by_name(name="[name1]") -> get entity ID',
            f'2. find_related_entities(entity_id="{primary_id_example}", relationship_types=["{rel_example}"])',
            '3. get_entity_by_name(name="[name2]") -> get entity ID',
            f'4. find_related_entities(entity_id="{secondary_id_example}", relationship_types=["{rel_example_2}"])',
            "5. Compare the two result sets to find overlap",
            "",
            '**Compound filter via Cypher: "Who on the west coast focuses on tech workforce?"**',
            f"1. query_graph_cypher(cypher=\"MATCH (m:{primary_type.title()}:Entity)-[:{rel_example.upper()}]->(f:Entity) "
            "WHERE m.geography =~ '.*(CA|WA|OR).*' AND f.name =~ '.*Tech.*' "
            'RETURN m.name, m.geography, m.organization, f.name")',
            "2. Answer directly from results (single tool call!)",
            "",
            "=== GEOGRAPHY MATCHING ===",
            "",
            'Geography is stored as "City, ST" (e.g., "Seattle, WA", "Oakland, CA").',
            "When users say regions, translate to state abbreviations in Cypher regex:",
            "- West Coast: CA, WA, OR",
            "- East Coast: NY, NJ, CT, MA, MD, VA, DC",
            "- Southeast: GA, NC, SC, FL, AL",
            "- Midwest: IL, OH, MI, IN, MN, WI",
            "Example: WHERE m.geography =~ '.*(CA|WA|OR).*'",
        ]

        return "\n".join(lines)

    def _build_system_prompt(self) -> str:
        """Build system prompt with rules and workflow."""

        domain_schema_section = self._build_domain_schema_section()
        entity_type_list = self._build_entity_type_list()
        entity_type_names = self._build_entity_type_names()
        rel_type_names = self._build_relationship_type_names()
        query_examples = self._build_query_examples()

        # Build entity type and relationship type strings for prompt injection
        entity_types_str = ", ".join(f'"{t}"' for t in entity_type_names) if entity_type_names else '"entity"'
        rel_types_str = ", ".join(rel_type_names) if rel_type_names else "related_to"
        entity_id_examples = ", ".join(f'"{t}-example-id"' for t in entity_type_names[:3]) if entity_type_names else '"entity-example-id"'

        base_prompt = (
            f"""You are a helpful assistant that answers questions about an organization's knowledge base.

IMPORTANT: You have access to tools that let you search for documents and traverse the knowledge graph. You MUST use these tools to find information before answering.

=== KNOWLEDGE GRAPH STRUCTURE ===

The knowledge base is organized as a graph with:
- ENTITIES: {entity_type_list}
- RELATIONSHIPS: Connections between entities

The valid entity types in this domain are: {entity_types_str}
The valid relationship types are: {rel_types_str}

You can traverse this graph to answer complex queries about relationships between entities.

"""
            + domain_schema_section
            + f"""

=== CORE PRINCIPLE: THINK THEN ACT ===

**ONE tool call per turn.** After EVERY tool call, you MUST:
1. Write 1-2 sentences analyzing what you learned from the result
2. Decide: "I have enough to answer" OR "I need exactly ONE more call because ___"
3. If answering, write your answer. If calling another tool, make exactly ONE call.

NEVER issue multiple tool calls in the same response.

**Tool budget guidance:**
- Simple lookups: 1-3 tool calls (entity by name, read document)
- Standard questions: 3-6 tool calls (entity + relationships + signals)
- Temporal/analytical questions: 6-12 tool calls (entity + temporal tools + signals + documents)
- Complex multi-entity analysis: up to 15 tool calls

You have a budget of 20 tool calls per conversation. Plan your approach:
start with the most informative tool, then add context as needed.
Don't spend calls on redundant lookups — if list_entities gave you relationships, don't also call find_related_entities.

=== TOOL USAGE RULES ===

1. ALWAYS use knowledge graph tools to answer questions — never guess from general knowledge
2. For questions about multiple entities: use list_entities(entity_type=X, include_relationships=true) — this gives you EVERYTHING in ONE call, including all relationships. DO NOT follow up with find_related_entities.
3. For questions about a specific entity: use get_entity_by_name(name=X) then read_document for details. Partial names work (e.g. "Chicago" matches "Chicago 2024 Cohort").
4. For relationship exploration: use find_related_entities ONLY when you need relationships for a single entity that you did NOT get from list_entities. Use mode='types_only' first if you don't yet know which relationship_type to traverse.
5. For batch entity details: After getting entity IDs (from list_entities, find_related_entities, etc.),
   use list_entity_profiles(entity_ids=["id1","id2",...]) to fetch ALL profiles at once.
   NEVER call find_related_entities in a loop for individual entities.
6. Read source documents with read_document when you need details NOT already present in tool results
7. Cite specific documents and quotes when making claims

=== TOOL CATEGORIES ===

**Query Tools:**
- search_knowledge_graph: Find documents and entities by keyword
- read_document: Read the full content of a specific document
- list_entities: List ALL entities of a type with COMPLETE relationships included. Use for any multi-entity query.
- extract_entities: Extract entities from text
- get_entity_by_name: Look up entity by name (partial match supported). Returns ID, name, type, metadata.
- find_related_entities: Graph traversal. mode='neighbors' (default) returns connected entities;
  mode='types_only' returns just the relationship-type inventory for an entity (use first if you
  don't know which relationship_type to traverse). NOT needed after list_entities.
- list_entity_profiles: Bulk-fetch FULL profiles (attributes + relationships) for a SET of entity IDs in ONE call.
  Returns direction-aware relationships (outgoing vs incoming).
  Pass an array of entity IDs. Use this instead of calling find_related_entities in a loop.
- query_graph_cypher: Execute a read-only Cypher query directly against Neo4j.
  PREFERRED for: compound filtering (multiple WHERE conditions), multi-hop traversal, regex matching.
  IMPORTANT: Cypher relationship types MUST be UPPERCASE (e.g. [:FOCUS_AREAS], [:HAS_MEMBERS], not [:focus_areas]).
  Falls back gracefully if Neo4j is unavailable — use list_entities/search_knowledge_graph instead.
- search_meeting_transcripts: Search meeting transcript TEXT for quotes, topics, discussions.
  Use for "what did X say" or "in their own words" questions. Filters by speaker, date range.
- list_meeting_documents: List meeting files with metadata. Use to discover file paths before read_document.
  Filters by meeting_id, date, participant.
- get_entity_context_summary: Context-aware entity summary with recency data.
  Shows last meeting, recent signals, open action items, related people with last interaction dates.
  Use instead of just find_related_entities (mode='types_only') for "who should" / "who is best positioned" questions.
- search_signals: Search decisions, action items, key points, and insights from meetings.
  Filter by entity_id (graph-first lookup with file fallback), signal_type, status, or date range.

**Temporal Intelligence Tools** — Use for time-aware queries about how things have evolved:
- entity_at_time: Get an entity's state at a PAST point in time. Use when asked "what did we know about X at time T?"
  Returns the entity as it existed then, or null if it didn't exist yet. Key for showing entity evolution.
- active_relationships_at_time: Get relationships that were active at a specific time.
  Use to compare "who was connected to X then vs now?"
- get_entity_provenance: Trace WHERE information about an entity came from — which meetings, documents, and signals.
  Use for "where did this information come from?" or "when was X first mentioned?"
- what_changed: Diff an entity's state between two timestamps. Required: date_from.
  Optional: date_to (defaults to now). Shows added/removed/modified fields.
  Use for "what changed about X since date?" (pass only date_from) or
  "how did X evolve between date A and date B?" (pass both date_from and date_to).
  Replaces the former what_changed_between tool.
- graph_as_of: Reconstruct the subgraph around an entity at a past point in time.
  Use for "what was the graph around X at time T?" Shows historical connections.
- find_contradictions: Detect conflicting signals for an entity (e.g., "on track" followed by "delayed").
  Use for "are there inconsistencies?" or "has the story changed?"
- temporal_blast_radius: BFS traversal showing all entities connected to X at a specific time.
  Use for "what's the impact if X changes?" or "what depends on X?"

**Decision Intelligence Tools:**
- find_decision_precedents: Find similar past decisions
- decision_chain: Walk the causal chain around a decision (direction='upstream' for what led
  TO it, direction='downstream' for what flowed FROM it). Replaces trace_decision_chain +
  decision_influence.
- decision_stats: Aggregate decision statistics

**Signal Mutation Tools** — Use when user asks to update or delete signals:
- update_signal: Update a signal's status, content, owner, or due date. Always query first to get the ID.
- delete_signal: Permanently remove a signal. Always confirm with the user before deleting.

8. When a tool returns data (e.g. list_entities returns a JSON array of members), analyze and filter that data DIRECTLY in your response. You have all the data in context — do NOT use file system tools, shell commands, or sub-agents to process it. Just read the JSON and reason about it.
9. You do NOT have access to Read, Bash, Grep, Agent, or any file system tools. You ONLY have the mcp__chat__* knowledge graph tools listed above.
**Graph Maintenance Tools** — Use ONLY when user explicitly asks to modify graph data:
- graph_add_node, graph_update_node, graph_delete_node, graph_merge_nodes
- graph_add_edge, graph_update_edge, graph_delete_edge

=== TOOL STRATEGY GUIDANCE ===

1. For "who should" / "who is best positioned" / "who has context" questions:
   - Do NOT just look up org chart relationships (find_related_entities mode='types_only')
   - Use get_entity_context_summary to see who has recent meeting attendance,
     open action items, and recent signals — recency matters more than role title
   - Consider communication quality: someone who missed meetings or has broken
     commitments may not be the best person despite their org chart role

2. For direct quote / "in their own words" / "what did X say" questions:
   - Use search_meeting_transcripts to find actual transcript text
   - Signals and entity profiles summarize — transcripts contain exact words
   - Filter by speaker name for targeted quote retrieval

3. For finding meeting documents:
   - Use list_meeting_documents to discover file paths before read_document
   - Meeting files are nested in year/month directories — don't guess paths
   - Meeting IDs (like "demo-006") can be used with list_meeting_documents

4. For entity profiles without meeting evidence:
   - Always note when information comes from profile data vs meeting observations
   - Say "based on their profile" vs "observed in meetings" to be transparent
   - If an entity has zero meeting attendance, explicitly note this

5. Efficiency:
   - For simple questions, 3-5 tool calls should be sufficient
   - For temporal/analytical questions, 8-12 calls is expected — chain temporal tools with signals and documents
   - If you're repeating the same tool with slight variations, stop and use what you have

6. Temporal analysis strategy:
   - Start with get_entity_by_name to resolve the entity ID
   - Use the appropriate temporal tool (entity_at_time, what_changed, graph_as_of, etc.)
   - Enrich with search_signals(entity_id=...) for decisions and action items
   - Read source documents (read_document) for full context when needed
   - For "what should we do" questions, combine temporal data with current state to form recommendations

{query_examples}

=== GRAPH MAINTENANCE ===

When the user explicitly asks to modify the graph (e.g., "Add a new entity",
"Delete this entity", "Merge these duplicates"):
- Use the graph maintenance tools (graph_add_node, graph_update_node, etc.)
- Always confirm what you're about to change before executing destructive operations
- After modifications, use query tools to verify the changes

**Adding nodes (graph_add_node):**
- Required: entity_type (must match domain config types: {entity_types_str})
- Required: name (display name)
- Optional: properties — if used, must be a JSON object like {{"role": "advisor"}}, NOT a string. Omit entirely if not needed.

**Adding relationships (graph_add_edge):**
- Required: source_id, target_id, relationship_type
- Optional: properties — omit if not needed
- CRITICAL: relationship_type must EXACTLY match a valid type from the domain schema above
- Valid relationship types: {rel_types_str}
- Do NOT invent relationship types — they will be rejected

**Merging nodes (graph_merge_nodes):**
- Required: primary_id (entity to keep) and duplicate_id (entity to merge away)
- Optional: strategy — "primary_wins" (default), "duplicate_wins", or "merge_all"

=== SIGNAL QUERY WORKFLOW ===

When users ask about decisions, action items, key points, or insights:
1. Use search_signals to find relevant signals
2. If they ask about a specific entity, use search_signals with entity_id=<id> — when an entity_id
   is provided, the tool prefers Neo4j graph-relationship lookup with a file-based fallback.
3. For "open action items", use search_signals with signal_type="action_item" and status="open"
4. For "recent decisions", use search_signals with signal_type="decision"
5. Combine with read_document to provide full context from entity profiles

=== SIGNAL MUTATION WORKFLOW ===

When users ask to update or remove signals (decisions, action items, key points, insights):

**Signal Update Tools:**
- update_signal: Change status, content, owner, or due date on a signal
- delete_signal: Permanently remove a signal from the knowledge base

**CRITICAL RULES:**
1. ALWAYS use search_signals FIRST to find the signal ID (pass entity_id when you have one,
   otherwise filter by signal_type / status / date range)
2. NEVER guess a signal_id — always look it up
3. For DELETE: ALWAYS confirm with the user before calling delete_signal
4. Valid status values: open, in_progress, done

=== WORKFLOW PRIORITY ===

**Step 1: Pick ONE best starting tool.** Do NOT fire multiple tools to "cover your bases."

- Complex filtering/traversal -> query_graph_cypher (compound WHERE, multi-hop MATCH, regex)
- Listing/filtering entities -> list_entities (returns ALL entities + relationships in ONE call)
- Simple entity lookup -> get_entity_by_name (partial match supported)
- Keyword search -> search_knowledge_graph (use only if you don't know entity names/types)
- Decisions/action items/insights -> search_signals (pass entity_id when you have one for graph-first lookup)
- Graph modification -> graph maintenance tools

**Step 2: Analyze the result.** Write what you found. Often this is enough to answer.

**Step 3: If needed, make ONE follow-up call** (e.g., read_document for details on a specific entity).

**Step 4: Answer.** Synthesize from the data you have. Do NOT keep searching for marginal extra data.

=== IMPORTANT TIPS ===

- Entity IDs look like: {entity_id_examples}
- **ALWAYS** use get_entity_by_name FIRST to get the entity ID before using other graph tools
- **NEVER GUESS** relationship type names - use find_related_entities(entity_id=..., mode='types_only') to see available_outgoing_types
- The domain schema above shows relationship names in snake_case (for graph tools) and UPPERCASE (for Cypher)
- find_related_entities returns entities with relationship metadata (type, strength, shared documents)
- Don't read every document - prioritize based on relationship strength and relevance
- If a relationship type doesn't work, check find_related_entities(mode='types_only') output for the correct name
- For queries about multiple entities (e.g., "who focuses on X?"), use list_entities FIRST — it's much faster than individual lookups"""
        )

        return base_prompt

    # ------------------------------------------------------------------
    # SDK permission handler
    # ------------------------------------------------------------------

    async def _can_use_tool(self, tool_name: str, input_data: dict, context: dict):
        """Only allow chat MCP tools — block Claude Code built-ins."""
        if tool_name.startswith("mcp__chat__"):
            return PermissionResultAllow(updated_input=input_data)
        sys.stderr.write(f"[CHAT_PERMISSION] Denied tool: {tool_name}\n")
        sys.stderr.flush()
        return PermissionResultDeny(
            reason=f"Tool '{tool_name}' is not available. You only have mcp__chat__* knowledge graph tools. "
            f"Process any data you've already received directly in your response — do not try to use "
            f"file system tools, shell commands, or sub-agents."
        )

    # ------------------------------------------------------------------
    # Citation extraction (preserved from old ChatAgent)
    # ------------------------------------------------------------------

    def _extract_citations(self, text: str) -> list[str]:
        """Extract document citations from response text."""
        pattern = r"([a-zA-Z0-9\-_/]+\.md)"
        matches = re.findall(pattern, text)
        return list(set(matches))

    # ------------------------------------------------------------------
    # Core query processing — SDK-based
    # ------------------------------------------------------------------

    async def process_query(
        self,
        query: str,
        manual_context: list[str] | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Process a query with optional manual context and conversation history.

        Creates a per-request SDK client, connects, sends the query,
        collects the response (including automatic tool execution),
        then disconnects.

        Args:
            query: User's question
            manual_context: Optional list of context file paths
            conversation_history: Optional list of prior messages [{role, content}]

        Returns:
            Dict with keys: answer, context_files, tool_calls, cited_documents
        """
        # Input validation
        if not isinstance(query, str) or not query.strip():
            return {
                "error": "Invalid query: empty or non-string input",
                "answer": "Please provide a non-empty question.",
                "context_files": [],
                "tool_calls": [],
                "cited_documents": [],
            }
        if manual_context is not None:
            if not isinstance(manual_context, list):
                logger.warning("manual_context is not a list (%s), ignoring", type(manual_context).__name__)
                manual_context = None
            else:
                manual_context = [p for p in manual_context if isinstance(p, str) and p.strip()]
        if conversation_history is not None:
            if not isinstance(conversation_history, list):
                logger.warning("conversation_history is not a list (%s), ignoring", type(conversation_history).__name__)
                conversation_history = None
            else:
                cleaned = []
                for msg in conversation_history:
                    if not isinstance(msg, dict):
                        continue
                    role = str(msg.get("role", "")).strip().lower()
                    content = msg.get("content", "")
                    if role not in ("user", "assistant"):
                        continue
                    if not isinstance(content, str) or not content.strip():
                        continue
                    cleaned.append({"role": role, "content": content.strip()})
                conversation_history = cleaned or None
        if not SDK_AVAILABLE:
            logger.error("Claude Agent SDK is not installed — cannot process query")
            return {
                "error": "Claude Agent SDK is not available",
                "answer": "I'm sorry, the chat system is not properly configured. Please contact support.",
                "context_files": [],
                "tool_calls": [],
                "cited_documents": [],
            }

        start_time = time.time()
        query_id = f"q_{int(start_time * 1000) % 100000}"

        # OTEL root span for the entire query lifecycle
        _root_span = None
        if self.tracer:
            _root_span = self.tracer.start_span("chat_agent.process_query", attributes={
                "chat_agent.query_length": len(query),
                "chat_agent.model": self.model,
                "chat_agent.has_manual_context": bool(manual_context),
                "chat_agent.has_history": bool(conversation_history),
                "chat_agent.query_preview": _sanitize_span_text(query, 200),
                "chat_agent.query_id": query_id,
            })

        # Emit agent_start SSE event
        await self._emit_sse_event(
            "agent_start",
            {
                "query_len": len(query),
                "model": self.model,
                "manual_context": bool(manual_context),
                "query_preview": query[:100] + "..." if len(query) > 100 else query,
            },
        )

        sys.stderr.write(
            f"[AGENT_START] {query_id} | query_len={len(query)} chars | "
            f"manual_context={bool(manual_context)} | model={self.model} | sdk={SDK_AVAILABLE}\n"
        )
        sys.stderr.flush()

        logger.info(f"ChatAgent processing query: {query[:100]}...")

        tool_calls: list[dict[str, Any]] = []
        context_files: list[str] = list(manual_context or [])
        final_answer = ""
        _streamed_text_len = 0  # track how much text we've already emitted
        _saw_stream_deltas = False  # whether any StreamEvent deltas arrived
        _reasoning_since_last_tool = ""  # accumulate reasoning text between tool calls
        _current_tool_span = None  # OTEL child span for active tool call
        _current_tool_had_error = False  # whether current tool received an error subtype
        _last_tool_start: float | None = None  # wall-clock time when current tool started
        _last_tool_name: str | None = None  # name of current tool (for timing log)

        try:
            # 1. Create MCP server with context for this query
            mcp_server = create_chat_tools_server(
                execution_id=self.execution_id,
                bot_id=self.bot_id,
            )

            # 2. Build allowed tools list
            allowed_tools = [
                "mcp__chat__search_knowledge_graph",
                "mcp__chat__read_document",
                "mcp__chat__list_entities",
                "mcp__chat__extract_entities",
                "mcp__chat__get_entity_by_name",
                "mcp__chat__find_related_entities",
                "mcp__chat__list_entity_profiles",
                "mcp__chat__query_graph_cypher",
                # Meeting transcript & document tools
                "mcp__chat__search_meeting_transcripts",
                "mcp__chat__list_meeting_documents",
                "mcp__chat__get_entity_context_summary",
                # Signal query tools
                "mcp__chat__search_signals",
                # Signal mutation tools
                "mcp__chat__update_signal",
                "mcp__chat__delete_signal",
                # Graph maintenance tools
                "mcp__chat__graph_add_node",
                "mcp__chat__graph_update_node",
                "mcp__chat__graph_delete_node",
                "mcp__chat__graph_merge_nodes",
                "mcp__chat__graph_add_edge",
                "mcp__chat__graph_update_edge",
                "mcp__chat__graph_delete_edge",
                # Decision intelligence tools
                "mcp__chat__find_decision_precedents",
                "mcp__chat__decision_chain",
                "mcp__chat__decision_stats",
                # Temporal tools (Issue #864)
                "mcp__chat__entity_at_time",
                "mcp__chat__active_relationships_at_time",
                "mcp__chat__get_entity_provenance",
                "mcp__chat__what_changed",
                "mcp__chat__graph_as_of",
                "mcp__chat__find_contradictions",
                "mcp__chat__temporal_blast_radius",
            ]
            # 3. Configure SDK client options
            system_prompt = self._build_system_prompt()
            options = ClaudeAgentOptions(
                model=self.model,
                system_prompt=system_prompt,
                mcp_servers={"chat": mcp_server},
                allowed_tools=allowed_tools,
                disallowed_tools=[
                    "Read", "Write", "Edit", "MultiEdit",
                    "Bash", "Grep", "Glob", "ToolSearch",
                    "Agent", "Skill",
                ],
                max_turns=20,
                permission_mode="acceptEdits",
                can_use_tool=self._can_use_tool,
                include_partial_messages=True,
            )

            # 4. Create per-request SDK client
            client = ClaudeSDKClient(options=options)

            try:
                # 5. Connect with timeout
                async with asyncio.timeout(10.0):
                    await client.connect()

                # 6. Build prompt with conversation history
                prompt_parts = []

                # Include conversation history for multi-turn context
                if conversation_history:
                    # Cap history to last N turns to stay within token limits
                    max_history_turns = 20
                    recent_history = conversation_history[-max_history_turns:]
                    prompt_parts.append("=== CONVERSATION HISTORY ===")
                    for msg in recent_history:
                        role_label = "User" if msg["role"] == "user" else "Assistant"
                        prompt_parts.append(f"{role_label}: {msg['content']}")
                    prompt_parts.append("=== END CONVERSATION HISTORY ===\n")

                prompt_parts.append(query)

                if manual_context:
                    context_str = "\n".join(f"- {f}" for f in manual_context)
                    prompt_parts.append(f"\nManual context files provided:\n{context_str}")

                prompt = "\n".join(prompt_parts)

                # 7. Send query + collect response with timeout
                async with asyncio.timeout(120.0):
                    await client.query(prompt)

                    # 8. Collect response — filter SDK message types
                    #
                    # With include_partial_messages=True the SDK yields
                    # StreamEvent objects containing raw Anthropic API deltas
                    # (content_block_delta with text) *before* the full
                    # AssistantMessage.  We emit each delta as an SSE event
                    # so the frontend can render text progressively.
                    #
                    # Full AssistantMessage objects still arrive (for tool_use
                    # blocks, etc.) and are handled below.  To avoid
                    # double-counting text that was already streamed via
                    # deltas, we track _streamed_text_len.

                    async def _emit_delta(text: str):
                        """Emit an incremental text delta via SSE."""
                        nonlocal final_answer, _streamed_text_len, _saw_stream_deltas, _reasoning_since_last_tool
                        if not text:
                            return
                        _saw_stream_deltas = True
                        _reasoning_since_last_tool += text
                        final_answer += text
                        _streamed_text_len = len(final_answer)
                        await self._emit_sse_event(
                            "claude_response",
                            {
                                "content": text,
                                "is_final": False,
                                "content_length": _streamed_text_len,
                                "response_type": "text_delta",
                            },
                        )

                    async def _emit_full_if_missing(full_text: str):
                        """Emit remaining text if deltas were partial or absent."""
                        nonlocal final_answer, _streamed_text_len
                        if not full_text:
                            return
                        if not _saw_stream_deltas:
                            await _emit_delta(full_text)
                            return
                        # Deltas arrived — check if they covered the full response
                        if full_text.startswith(final_answer):
                            remainder = full_text[len(final_answer):]
                            if remainder:
                                await _emit_delta(remainder)
                        elif len(full_text) > _streamed_text_len:
                            # Mismatch: reset accumulated state to avoid corruption
                            logger.warning(
                                "[CHAT_AGENT] Delta stream mismatch (streamed %d, full %d); replacing with full text",
                                _streamed_text_len, len(full_text),
                            )
                            final_answer = ""
                            _streamed_text_len = 0
                            await _emit_delta(full_text)

                    async for chunk in client.receive_response():
                        type_name = type(chunk).__name__

                        # --- StreamEvent: real-time text deltas + tool tracking ---
                        if SDK_AVAILABLE and isinstance(chunk, StreamEvent):
                            event = chunk.event or {}
                            event_type = event.get("type", "")
                            if event_type == "content_block_delta":
                                delta = event.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    await _emit_delta(delta.get("text", ""))
                            elif event_type == "content_block_start":
                                cb = event.get("content_block", {})
                                if cb.get("type") == "tool_use":
                                    raw_name = cb.get("name", "unknown")
                                    block_id = cb.get("id")
                                    short_name = raw_name.split("mcp__chat__")[-1] if "mcp__chat__" in raw_name else raw_name

                                    # End previous tool span/timing if still open
                                    if _current_tool_span:
                                        if _last_tool_start:
                                            _current_tool_span.set_attribute("chat_agent.tool.duration_ms", int((time.time() - _last_tool_start) * 1000))
                                        # Capture reasoning that followed this tool as result context
                                        if _reasoning_since_last_tool.strip():
                                            _current_tool_span.set_attribute(
                                                "chat_agent.tool.result_context",
                                                _sanitize_span_text(_reasoning_since_last_tool.strip(), 1000)
                                            )
                                        _tool_status = StatusCode.ERROR if _current_tool_had_error else StatusCode.OK
                                        _current_tool_span.set_status(Status(_tool_status))
                                        _current_tool_span.end()
                                        _current_tool_span = None
                                        _current_tool_had_error = False
                                    if _last_tool_start and _last_tool_name:
                                        elapsed_ms = int((time.time() - _last_tool_start) * 1000)
                                        sys.stderr.write(
                                            f"[CHAT_AGENT] Tool complete: {_last_tool_name} | {elapsed_ms}ms\n"
                                        )
                                        sys.stderr.flush()

                                    # Start new tool span + timing
                                    _last_tool_start = time.time()
                                    _last_tool_name = short_name
                                    _current_tool_had_error = False
                                    if self.tracer:
                                        _current_tool_span = self.tracer.start_span(
                                            f"chat_agent.tool.{short_name}",
                                            attributes={"chat_agent.tool.name": short_name},
                                        )

                                    # Log reasoning that preceded this tool call
                                    if _reasoning_since_last_tool.strip():
                                        if _root_span:
                                            _root_span.add_event(
                                                "agent_reasoning",
                                                attributes={
                                                    "reasoning.text": _sanitize_span_text(_reasoning_since_last_tool.strip(), 2000),
                                                    "reasoning.preceding_tool": short_name,
                                                    "reasoning.step": len(tool_calls),
                                                },
                                            )
                                        sys.stderr.write(
                                            f"[CHAT_AGENT] Reasoning before tool call: {_reasoning_since_last_tool.strip()}\n"
                                        )
                                        sys.stderr.flush()
                                        _reasoning_since_last_tool = ""
                                    tool_calls.append(
                                        {
                                            "tool": short_name,
                                            "input": {},  # populated incrementally via deltas
                                            "output": None,
                                            "block_id": block_id,
                                        }
                                    )
                            continue

                        # Skip SDK internal messages
                        if hasattr(chunk, "subtype"):
                            subtype = getattr(chunk, "subtype", "")
                            if subtype == "error":
                                if _current_tool_span:
                                    error_msg = _sanitize_span_text(getattr(chunk, "message", str(chunk)), 500)
                                    _current_tool_span.set_attribute("chat_agent.tool.error", error_msg)
                                _current_tool_had_error = True
                                continue
                            if subtype in ("init", "success"):
                                continue

                        if type_name in ("SystemMessage", "ResultMessage"):
                            continue

                        # Handle plain string responses
                        if isinstance(chunk, str):
                            await _emit_full_if_missing(chunk)
                            continue

                        # Handle AssistantMessage with result attribute
                        if hasattr(chunk, "result") and isinstance(chunk.result, str):
                            await _emit_full_if_missing(chunk.result)
                            continue

                        # Handle message objects with content
                        if hasattr(chunk, "content"):
                            content = chunk.content
                            if isinstance(content, str):
                                await _emit_full_if_missing(content)
                            elif isinstance(content, list):
                                # Collect all text blocks first, then emit once
                                # to avoid _emit_delta flipping _saw_stream_deltas
                                # mid-loop and dropping subsequent text blocks.
                                text_blocks: list[str] = []
                                for block in content:
                                    if hasattr(block, "text") and not hasattr(block, "tool_use_id"):
                                        text_blocks.append(block.text or "")
                                    elif SDK_AVAILABLE and ToolUseBlock and isinstance(block, ToolUseBlock):
                                        # Track tool calls from AssistantMessage content
                                        tool_name = block.name
                                        tool_input = block.input or {}
                                        short_name = tool_name.split("mcp__chat__")[-1] if "mcp__chat__" in tool_name else tool_name
                                        block_id = getattr(block, "id", None)
                                        # Update existing placeholder (from StreamEvent) by block ID,
                                        # or by name if no ID match found and input is empty
                                        matched = False
                                        if block_id:
                                            for tc in tool_calls:
                                                if tc.get("block_id") == block_id:
                                                    tc["input"] = tool_input
                                                    matched = True
                                                    break
                                        if not matched:
                                            for tc in tool_calls:
                                                if tc["tool"] == short_name and not tc["input"]:
                                                    tc["input"] = tool_input
                                                    matched = True
                                                    break
                                        if not matched:
                                            tool_calls.append(
                                                {
                                                    "tool": short_name,
                                                    "input": tool_input,
                                                    "output": None,
                                                    "block_id": block_id,
                                                }
                                            )
                                        # Log tool call with input (input is fully available here)
                                        input_preview = json.dumps(tool_input)[:200] if tool_input else "{}"
                                        sys.stderr.write(
                                            f"[CHAT_AGENT] Tool call: {short_name} | input={input_preview}\n"
                                        )
                                        sys.stderr.flush()
                                        # Attach tool input to OTEL span
                                        if _current_tool_span and tool_input:
                                            _current_tool_span.set_attribute(
                                                "chat_agent.tool.input", json.dumps(tool_input)[:500]
                                            )
                                        # Track context files from search results
                                        if tool_name == "mcp__chat__read_document":
                                            path = tool_input.get("path")
                                            if path:
                                                context_files.append(path)
                                if text_blocks:
                                    await _emit_full_if_missing("".join(text_blocks))
                            continue

                        # Log unexpected chunk types
                        logger.warning(f"[CHAT_AGENT] Unexpected chunk type: {type_name}")

            except asyncio.CancelledError:
                raise  # Let the route handle cancellation
            except TimeoutError:
                logger.warning("[CHAT_AGENT] Query+streaming timed out after 120s")
                if not final_answer:
                    final_answer = "I apologize, but the request timed out. Please try a simpler question or try again."
            finally:
                # End any still-open tool span
                if _current_tool_span:
                    if _last_tool_start:
                        _current_tool_span.set_attribute("chat_agent.tool.duration_ms", int((time.time() - _last_tool_start) * 1000))
                    # Capture reasoning that followed this tool as result context
                    if _reasoning_since_last_tool.strip():
                        _current_tool_span.set_attribute(
                            "chat_agent.tool.result_context",
                            _sanitize_span_text(_reasoning_since_last_tool.strip(), 1000)
                        )
                    _tool_status = StatusCode.ERROR if _current_tool_had_error else StatusCode.OK
                    _current_tool_span.set_status(Status(_tool_status))
                    _current_tool_span.end()
                    _current_tool_span = None
                if _last_tool_start and _last_tool_name:
                    elapsed_ms = int((time.time() - _last_tool_start) * 1000)
                    sys.stderr.write(
                        f"[CHAT_AGENT] Tool complete: {_last_tool_name} | {elapsed_ms}ms\n"
                    )
                    sys.stderr.flush()
                    _last_tool_start = None

                try:
                    await client.disconnect()
                except Exception as e:
                    logger.warning(f"Error disconnecting SDK client: {e}")

            # 9. Validate answer
            if not final_answer or not final_answer.strip():
                logger.warning("Empty answer from SDK client")
                final_answer = "I apologize, but I couldn't generate a proper response. Please try again."

            # 10. Emit final claude_response SSE to signal completion
            #     Do NOT include content — the frontend already has the
            #     full text from incremental text_delta events.
            await self._emit_sse_event(
                "claude_response",
                {
                    "is_final": True,
                    "content_length": len(final_answer),
                    "tools_used_total": len(tool_calls),
                    "response_type": "stream_end",
                },
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            total_duration = time.time() - start_time
            error_preview = str(e)[:100].replace("\n", " ")

            await self._emit_sse_event(
                "agent_error",
                {
                    "error_message": str(e),
                    "total_duration": total_duration,
                    "error_type": type(e).__name__,
                },
            )

            sys.stderr.write(f"[AGENT_ERROR] {query_id} | duration={total_duration:.2f}s | error={error_preview}\n")
            sys.stderr.flush()

            # Close any open tool span with error status
            if _current_tool_span:
                _current_tool_span.set_status(Status(StatusCode.ERROR, str(e)[:500]))
                _current_tool_span.set_attribute("chat_agent.tool.error", str(e)[:500])
                _current_tool_span.end()
                _current_tool_span = None

            # Mark OTEL root span as error
            if _root_span:
                _root_span.set_attribute("chat_agent.status", "error")
                _root_span.set_status(Status(StatusCode.ERROR, str(e)))
                _root_span.set_attribute("chat_agent.error_type", type(e).__name__)
                _root_span.end()
                _root_span = None  # prevent double-end

            logger.exception(f"Error processing query: {e}")
            error_message = (
                f"I apologize, but I encountered an error while processing your request: {e}. "
                "Please try rephrasing your question or check if the knowledge base has been properly initialized."
            )
            return {
                "error": f"Failed to process query: {e}",
                "answer": error_message,
                "context_files": [],
                "tool_calls": [],
                "cited_documents": [],
            }

        # Remove duplicate context files
        context_files = list(set(context_files))

        # Extract citations from response
        cited_documents = self._extract_citations(final_answer)

        # Add to conversation history (capped to prevent unbounded growth)
        self.conversation_history.append({"role": "user", "content": query})
        self.conversation_history.append({"role": "assistant", "content": final_answer})
        if len(self.conversation_history) > self.max_history_messages * 2:
            self.conversation_history = self.conversation_history[-self.max_history_messages * 2 :]

        total_duration = time.time() - start_time
        unique_tools = list(set(tc["tool"] for tc in tool_calls))

        # Emit agent_complete SSE
        await self._emit_sse_event(
            "agent_complete",
            {
                "total_duration": total_duration,
                "iterations": len(tool_calls),
                "tools_called": len(tool_calls),
                "unique_tools": unique_tools,
                "context_files": len(context_files),
                "answer_length": len(final_answer),
            },
        )

        sys.stderr.write(
            f"[AGENT_COMPLETE] {query_id} | total_duration={total_duration:.2f}s | "
            f"tools_called={len(tool_calls)} | unique_tools={unique_tools} | "
            f"context_files={len(context_files)} | answer_len={len(final_answer)} | "
            f"total_text_streamed={_streamed_text_len}\n"
        )
        sys.stderr.flush()

        # Emit final reasoning event for text after the last tool call
        if _reasoning_since_last_tool.strip() and _root_span:
            _root_span.add_event(
                "agent_reasoning",
                attributes={
                    "reasoning.text": _sanitize_span_text(_reasoning_since_last_tool.strip(), 2000),
                    "reasoning.preceding_tool": "final_answer",
                    "reasoning.step": len(tool_calls),
                },
            )

        # Finalize OTEL root span with success attributes
        if _root_span:
            _root_span.set_attribute("chat_agent.tool_calls", len(tool_calls))
            _root_span.set_attribute("chat_agent.unique_tools", len(unique_tools))
            _root_span.set_attribute("chat_agent.answer_length", len(final_answer))
            _root_span.set_attribute("chat_agent.duration_s", round(total_duration, 2))
            _root_span.set_attribute("chat_agent.context_files", len(context_files))
            _root_span.set_attribute("chat_agent.answer_preview", _sanitize_span_text(final_answer, 200))
            _root_span.set_attribute("chat_agent.unique_tools_list", ",".join(unique_tools))
            _root_span.set_attribute("chat_agent.status", "ok")
            _root_span.set_status(Status(StatusCode.OK))
            _root_span.end()

        # Record metrics via existing counters (best-effort)
        try:
            from app.metrics import record_conversation_duration, record_llm_usage

            record_conversation_duration(query_id, total_duration)
            record_llm_usage(
                model=self.model,
                operation="chat_agent",
                input_tokens=0,  # SDK doesn't expose token usage yet
                output_tokens=0,
                cost=0.0,
            )
        except Exception:
            pass  # Metrics should never break the query

        return {
            "answer": final_answer,
            "context_files": context_files,
            "tool_calls": tool_calls,
            "cited_documents": cited_documents,
        }

    # ------------------------------------------------------------------
    # AgentBase contract
    # ------------------------------------------------------------------

    async def make_decision(self, context: dict[str, Any]) -> DecisionOutcome:
        """Make a decision based on context (inherited from AgentBase)."""
        query = context.get("query", "")
        manual_context = context.get("manual_context", None)

        result = await self.process_query(query, manual_context)

        confidence = 0.9 if not result.get("error") else 0.1
        reasoning = f"Processed query with {len(result.get('tool_calls', []))} tool calls"
        logger.info(
            "[CHAT_DECISION] decision=%s confidence=%.2f reasoning=%s",
            "chat_response",
            confidence,
            reasoning,
        )
        return DecisionOutcome(
            decision="chat_response",
            confidence=confidence,
            reasoning=reasoning,
            actions=["query_processed"],
            metadata=result,
        )
