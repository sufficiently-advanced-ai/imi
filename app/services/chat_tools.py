"""
Chat Tools - Tool implementations for ChatAgent
Provides search_knowledge_graph, read_document, and extract_entities functionality

Uses SemanticaKnowledge (preferred) or legacy KnowledgeGraph for all graph operations.
"""

import json
import logging
import re
from datetime import date, datetime
from typing import Any

from app.domain.entities.services import EntityService
from app.git_ops import git_ops
from app.services.frontmatter import frontmatter
from app.services.graph import get_knowledge_graph
from app.services.signal_audit import SignalAuditStore
from app.services.signal_store import SignalStore

logger = logging.getLogger(__name__)


def _get_semantica():
    """Get SemanticaKnowledge instance if available."""
    try:
        from app.services.graph.factory import get_semantica_knowledge
        return get_semantica_knowledge()
    except Exception:
        return None


def _extract_records(result: Any) -> list[dict[str, Any]]:
    """Normalize a Semantica execute_query() return value to a row list.

    Semantica's execute_query returns ``{"success": bool, "records": [...]}``
    rather than a plain list. Iterating the dict directly yields the dict's
    *keys* (strings like ``"records"``), which is the source of multiple
    silent failures upstream. Use this helper at every call site.
    """
    if isinstance(result, dict):
        return result.get("records", []) or []
    if isinstance(result, list):
        return result
    return []


def _serialize_for_json(obj: Any) -> Any:
    """Recursively convert date/datetime objects to ISO strings for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, date):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    return obj

# Initialize services
entity_brain = EntityService()


async def search_knowledge_graph(
    query: str, entity_types: list[str] | None = None, max_results: int = 10
) -> list[dict[str, Any]]:
    """
    Fuzzy/keyword search across entity names and string attributes.

    Returns ranked entities whose name or attribute values match the query.
    For exact name lookup use get_entity_by_name. For type-scoped retrieval
    (all entities of a given type) use list_entities — this tool no longer
    treats a query that matches a type name as a "list all" shortcut.

    Args:
        query: Search query text (matched against names and string attributes)
        entity_types: Optional list of entity types to filter by
        max_results: Maximum number of results to return

    Returns:
        List of ranked match dicts with path, score, title, snippet, entity
    """
    try:
        # Handle empty query
        if not query or not query.strip():
            return []

        logger.info(
            f"[SEARCH_TOOL] Searching domain graph for '{query}' with entity_types={entity_types}"
        )

        sk = _get_semantica()
        if sk is None:
            raise RuntimeError(
                "Semantica is not initialized. Cannot search knowledge graph — "
                "the legacy substring-match fallback was removed because its "
                "ranking diverged from Semantica's hybrid vector+keyword scores."
            )

        # Normalize entity_types
        if isinstance(entity_types, str):
            if entity_types.lower() in ("all", "*"):
                entity_types = None
            elif "," in entity_types:
                entity_types = [t.strip() for t in entity_types.split(",")]
            else:
                entity_types = [entity_types]

        results = await sk.hybrid_search(query, entity_types=entity_types, limit=max_results)
        formatted = []
        for r in results:
            snippet = f"Type: {r.get('type', '')}, Score: {r.get('score', 0):.2f}"
            if r.get("matched_attribute"):
                snippet += f", Matched: {r['matched_attribute']}"
            formatted.append({
                "path": r.get("file_path", ""),
                "score": r.get("score", 0),
                "title": r.get("name", ""),
                "snippet": snippet,
                "entity": r,
            })
        logger.info(f"[SEARCH_TOOL] Semantica search found {len(formatted)} results")
        return formatted

    except Exception as e:
        logger.error(f"Error searching knowledge graph: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
        return []


async def read_document(path: str) -> dict[str, Any]:
    """
    Read document content from the repository

    Args:
        path: Path to the document

    Returns:
        Dict with path, content, and metadata
    """
    try:
        # Read file content
        content = await git_ops.read_file(path)

        # Extract metadata
        metadata = frontmatter.extract_all(content) or {}

        return {"path": path, "content": content, "metadata": metadata}

    except FileNotFoundError:
        logger.error(f"Document not found: {path}")
        raise
    except Exception as e:
        logger.error(f"Error reading document {path}: {str(e)}")
        raise


async def extract_entities(text: str) -> dict[str, list[dict[str, Any]]]:
    """
    Extract entities from text using Semantica (preferred) or entity brain.

    Args:
        text: Text to analyze

    Returns:
        Dict with entity type → list of entity dicts
    """
    try:
        if not text:
            return {"people": [], "projects": [], "teams": []}

        sk = _get_semantica()
        if sk is None:
            raise RuntimeError(
                "Semantica is not initialized. Cannot extract entities — "
                "the legacy entity_brain fallback was removed because its "
                "outputs diverged from the canonical Semantica NER results."
            )

        grouped = await sk.extract_entities_grouped(text)
        # Map Semantica types to expected keys
        result = {
            "people": grouped.get("person", []),
            "projects": grouped.get("project", []),
            "teams": grouped.get("team", []),
        }
        # Include any other types Semantica found
        for key, entities in grouped.items():
            if key not in ("person", "project", "team"):
                result[key] = entities
        total = sum(len(v) for v in result.values())
        logger.info(f"Semantica extracted {total} entities")
        return result

    except Exception as e:
        logger.error(f"Error extracting entities: {str(e)}")
        return {"people": [], "projects": [], "teams": []}


async def _list_entities_semantica(
    sk,
    entity_type: str,
    include_relationships: bool,
    max_results: int,
    attribute_filter: str | None,
) -> list[dict[str, Any]]:
    """List entities via SemanticaKnowledge (Cypher-based)."""
    from app.services.semantica_config import entity_type_to_label

    label = entity_type_to_label(entity_type)

    # Build Cypher query
    cypher = f"MATCH (n:{label}:Entity) "
    params: dict[str, Any] = {"limit": max_results}

    if attribute_filter:
        # Search across all string properties
        cypher += (
            "WHERE ANY(key IN keys(n) WHERE "
            "n[key] IS NOT NULL AND toString(n[key]) CONTAINS $filter) "
        )
        params["filter"] = attribute_filter.lower()

    cypher += "RETURN n LIMIT $limit"

    raw = sk.graph_store.execute_query(cypher, params)
    rows = _extract_records(raw)

    entities = []
    for r in rows:
        node = r.get("n", {})
        node_id = node.get("id", "")
        name = node.get("name", node_id)

        skip_keys = {"id", "name", "entity_type", "canonical_name", "is_archived", "file_path"}
        attrs = {k: v for k, v in node.items() if k not in skip_keys and v is not None}

        entry: dict[str, Any] = {
            "id": node_id,
            "name": name,
            "type": entity_type,
            "attributes": _serialize_for_json(attrs),
        }

        if include_relationships and node_id:
            rels = await sk.get_relationships(node_id)
            entry["relationships"] = [
                {
                    "type": rel.get("relationship_type", ""),
                    "direction": rel.get("direction", ""),
                    "target_id": rel.get("target", rel.get("source", "")),
                    "target_name": rel.get("target_name", rel.get("source_name", "")),
                }
                for rel in rels
            ]

        entities.append(entry)

    logger.info(f"[LIST_ENTITIES] Semantica found {len(entities)} entities of type '{entity_type}'")
    return _serialize_for_json(entities)


async def list_entities(
    entity_type: str,
    include_relationships: bool = True,
    max_results: int = 50,
    attribute_filter: str | None = None,
) -> list[dict[str, Any]]:
    """
    List all entities of a given type with optional inline relationships.

    Collapses N+1 lookups (1 search + N relationship calls) into a single call,
    making bulk queries like "who are the members?" or "which members focus on X?"
    dramatically more efficient.

    Args:
        entity_type: Entity type to list (e.g., "member", "focus_area", "cohort")
        include_relationships: Whether to include relationships inline (default True)
        max_results: Maximum number of entities to return (default 50)
        attribute_filter: Optional text to filter by attribute values (e.g., "west coast", "healthcare")

    Returns:
        List of entity dicts with optional relationships:
        [{
            "id": "member-nicole-brown",
            "name": "Nicole Brown",
            "type": "member",
            "attributes": {...},
            "relationships": [
                {"type": "focus_areas", "target_id": "focus_area-healthcare-access", "target_name": "Healthcare Access"},
                ...
            ]
        }]
    """
    try:
        if max_results <= 0:
            return []

        logger.info(f"[LIST_ENTITIES] Listing entities of type '{entity_type}', include_relationships={include_relationships}, attribute_filter={attribute_filter}")

        sk = _get_semantica()
        if sk is None:
            raise RuntimeError(
                "Semantica is not initialized. Cannot list entities — the legacy "
                "graph fallback was removed because it scanned the entire repo and "
                "produced inconsistent results vs. the Neo4j-backed source of truth."
            )

        return await _list_entities_semantica(
            sk, entity_type, include_relationships, max_results, attribute_filter
        )

    except Exception as e:
        logger.error(f"Error listing entities: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return []




# ============================================================================
# SIGNAL QUERY TOOLS
# ============================================================================


def _get_semantica_for_signals():
    """Get SemanticaKnowledge instance for signal indexing/search.

    Separated from _get_semantica() so signal-index tests can monkeypatch a
    narrower target without disrupting entity-search tests.
    """
    try:
        from app.services.graph.factory import get_semantica_knowledge
        return get_semantica_knowledge()
    except Exception:
        return None


async def search_signals_semantic(
    query: str,
    signal_types: list[str] | None = None,
    status: str | None = None,
    authority: str = "evidence",
    limit: int = 10,
    recency_weight: float = 0.0,
    include_rejected: bool = False,
) -> dict[str, Any]:
    """Governance-aware semantic search over indexed signals (G3 wiring).

    Obtains the vector store and embedder from the live SemanticaKnowledge
    facade and delegates to signal_retrieval.search_signals_semantic.

    Returns {"results": [...], "count": n} on success, or
    {"error": "semantic index unavailable", "results": []} when the vector
    stack is not initialized.
    """
    try:
        sk = _get_semantica_for_signals()
        if sk is None:
            return {"error": "semantic index unavailable", "results": []}
        from app.core.middleware.request_context import current_tenant_id
        from app.services import signal_indexing, signal_retrieval

        # Resolve the tenant's store (pgvector on hosted) and scope the filter —
        # reading sk.vector_store directly dropped both (Phase 3 fix).
        store = signal_indexing.resolve_vector_store(sk.vector_store)
        results = signal_retrieval.search_signals_semantic(
            store,
            sk.embedder,
            query,
            signal_types=signal_types,
            status=status,
            tenant_id=current_tenant_id.get(),
            authority=authority,
            limit=limit,
            recency_weight=recency_weight,
            include_rejected=include_rejected,
        )
        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.warning("[SEARCH_SIGNALS_SEMANTIC] unavailable: %s", e)
        return {"error": "semantic index unavailable", "results": []}


async def capture_thought(
    content: str,
    source: str = "manual",
    source_id: str | None = None,
    tags: list[str] | None = None,
    source_date: str | None = None,
) -> dict[str, Any]:
    """Capture a thought into the general memory layer (G4 wiring).

    Thin delegate to capture_service.capture_and_persist — persist-first, then
    enrichment/indexing/git best-effort. Governance fields are server-injected
    (ADR-002): captures enter as imported, evidence-grade memory.
    """
    from app.services import capture_service

    return await capture_service.capture_and_persist(
        content,
        source=source,
        source_id=source_id,
        tags=tags,
        source_date=source_date,
        actor="mcp",
    )


async def memory_writeback(
    memory_payload: dict[str, Any],
    task_id: str | None = None,
    flow_id: str | None = None,
    runtime_name: str | None = None,
    runtime_version: str | None = None,
    confidence: float = 0.5,
    provenance_default_status: str = "generated",
    stale_after: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Persist a typed memory batch (Phase 2 wiring).

    Maps the flat MCP params onto WritebackRequest (which enforces the
    ADR-002 provenance clamp and the schema version) and delegates to
    memory_writeback.writeback.
    """
    from app.services import memory_writeback as writeback_service

    try:
        request = writeback_service.WritebackRequest(
            memory_payload=memory_payload,
            task_id=task_id,
            flow_id=flow_id,
            runtime=(
                {"name": runtime_name, "version": runtime_version}
                if runtime_name
                else None
            ),
            confidence=confidence,
            provenance={"default_status": provenance_default_status},
            stale_after=stale_after,
            idempotency_key=idempotency_key,
        )
    except Exception as e:
        return {"success": False, "error": str(e)}
    return await writeback_service.writeback(request)


def _memory_ops_session_factory():
    """Async session factory for memory-ops tables (monkeypatchable in tests)."""
    from app.database import create_database_session, get_database_config

    return create_database_session(get_database_config())


async def memory_recall(
    query: str,
    authority: str = "evidence",
    record_kinds: list[str] | None = None,
    limit: int = 10,
    recency_weight: float = 0.0,
    task_id: str | None = None,
    runtime_name: str | None = None,
) -> dict[str, Any]:
    """Unified governed recall (Phase 3 wiring). Delegates to memory_recall.recall."""
    from app.services import memory_recall as recall_service

    try:
        request = recall_service.RecallRequest(
            query=query,
            authority=authority,
            record_kinds=record_kinds,
            limit=limit,
            recency_weight=recency_weight,
            task_id=task_id,
            runtime_name=runtime_name,
            surface="mcp",
        )
    except Exception as e:
        return {"success": False, "error": str(e)}
    return await recall_service.recall(request)


async def record_memory_usage(
    request_id: str,
    used_memory_ids: list[str] | None = None,
    ignored: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Report used/ignored memories for a recall trace (Phase 3 wiring)."""
    from app.services import recall_trace_store

    try:
        factory = _memory_ops_session_factory()
        async with factory() as session:
            updated = await recall_trace_store.apply_usage(
                session,
                request_id,
                used_memory_ids=used_memory_ids,
                ignored=ignored,
            )
            await session.commit()
        return {"request_id": request_id, "updated": updated}
    except Exception as e:
        logger.warning("[RECORD_MEMORY_USAGE] failed: %s", e)
        return {"success": False, "error": str(e)}


async def inspect_memory(record_id: str) -> dict[str, Any]:
    """Inspect a governed memory record (Phase 5 wiring)."""
    from app.services import memory_inspector

    result = await memory_inspector.inspect_memory(record_id)
    if result is None:
        return {"success": False, "error": f"Record '{record_id}' not found"}
    return result


async def search_signals(
    entity_id: str | None = None,
    signal_type: str | None = None,
    status: str | None = None,
    client_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    max_results: int = 20,
) -> list[dict[str, Any]]:
    """Search persisted meeting signals (decisions, action items, key points, insights).

    Source of truth is the SignalStore JSON files. When entity_id is provided
    AND the knowledge graph exposes find_signals_for_entity, prefers a Neo4j
    graph-relationship lookup (signals explicitly linked to the entity in the
    graph) and only falls back to JSON content-mention filtering if the graph
    path returns nothing or errors. Without entity_id, filters the JSON store
    directly.

    Args:
        entity_id: Filter to signals linked to this entity slug ID
            (e.g. "person-chris-fernandes"). Triggers graph-first lookup.
        signal_type: Filter by type — decision, action_item, key_point, insight.
        status: Filter by status — open, in_progress, done.
        client_id: Filter to signals scoped to this client slug ID
            (e.g. "client-acme-corp"). Omit to sweep across all clients.
        date_from: Inclusive start date (YYYY-MM-DD).
        date_to: Inclusive end date (YYYY-MM-DD).
        max_results: Cap on returned signals (default 20).

    Returns:
        List of signal dicts with content, entities, owner, status, and metadata.
    """
    if client_id is not None:
        client_id = client_id.strip() or None

    # Graph-first lookup when an entity_id is given. Neo4j only gives us
    # candidate signal IDs — we hydrate from SignalStore (the source of truth)
    # so update_signal/delete_signal best-effort sync gaps don't surface stale
    # records, and so the same status/date_from/date_to predicates apply.
    if entity_id:
        candidate_ids: set[str] = set()
        try:
            kg = get_knowledge_graph()
            if hasattr(kg, "find_signals_for_entity"):
                graph_results = await kg.find_signals_for_entity(
                    entity_id, signal_type, max_results
                )
                candidate_ids = {r["id"] for r in graph_results if r.get("id")}
        except Exception as e:
            logger.warning(
                f"[SEARCH_SIGNALS] Neo4j path failed for {entity_id}, "
                f"falling back to file scan: {e}"
            )

        if candidate_ids:
            store = SignalStore()
            all_meeting_signals = store.load_all()
            signal_by_id = {
                s.id: s for ms in all_meeting_signals for s in ms.signals
            }
            hydrated: list[dict[str, Any]] = []
            for sid in candidate_ids:
                signal = signal_by_id.get(sid)
                if signal is None:
                    continue  # Stale Neo4j reference — signal deleted from JSON store
                if signal_type and signal.type != signal_type:
                    continue
                if status and signal.status != status:
                    continue
                if client_id and signal.client_id != client_id:
                    continue
                if not _signal_matches_date(signal, date_from, date_to):
                    continue
                hydrated.append(_signal_to_dict(signal))
            if hydrated:
                hydrated.sort(
                    key=lambda s: (s["source_timestamp"], -s["position"]),
                    reverse=True,
                )
                result = hydrated[:max_results]
                logger.info(
                    f"[SEARCH_SIGNALS] Hydrated {len(result)} signals from store "
                    f"for {entity_id} (Neo4j candidates: {len(candidate_ids)})"
                )
                return result

    try:
        store = SignalStore()
        all_meeting_signals = store.load_all()

        if not all_meeting_signals:
            return []

        filtered: list[dict[str, Any]] = []
        for ms in all_meeting_signals:
            for signal in ms.signals:
                if signal_type and signal.type != signal_type:
                    continue
                if entity_id and not _signal_matches_entity(signal, entity_id):
                    continue
                if status and signal.status != status:
                    continue
                if client_id and signal.client_id != client_id:
                    continue
                if not _signal_matches_date(signal, date_from, date_to):
                    continue

                filtered.append(_signal_to_dict(signal))

        filtered.sort(key=lambda s: (s["source_timestamp"], -s["position"]), reverse=True)

        result = filtered[:max_results]
        logger.info(
            f"[SEARCH_SIGNALS] Found {len(result)} signals "
            f"(filtered from {sum(ms.signal_count for ms in all_meeting_signals)} total)"
        )
        return result

    except Exception as e:
        logger.error(f"Error searching signals: {e}", exc_info=True)
        return []


def _signal_matches_entity(signal, entity_id: str) -> bool:
    """Check if a signal references the given entity slug ID."""
    for ref in signal.entities:
        if ref.id == entity_id:
            return True
    if signal.owner and signal.owner.id == entity_id:
        return True
    return False


def _signal_matches_date(signal, date_from: str | None, date_to: str | None) -> bool:
    """Check if a signal falls within the date range (inclusive)."""
    try:
        ts = signal.source_timestamp[:10]  # YYYY-MM-DD
    except (TypeError, IndexError):
        return True  # Include on error
    if date_from and ts < date_from:
        return False
    if date_to and ts > date_to:
        return False
    return True


def _signal_to_dict(signal) -> dict[str, Any]:
    """Convert a persisted Signal model to an agent-friendly dict."""
    from collections import defaultdict

    entities_dict: dict[str, list[str]] = defaultdict(list)
    for ref in signal.entities:
        if ref.name not in entities_dict[ref.type]:
            entities_dict[ref.type].append(ref.name)

    return {
        "id": signal.id,
        "type": signal.type,
        "content": signal.content,
        "source_meeting_id": signal.source_meeting_id,
        "source_meeting_title": signal.source_meeting_title,
        "source_timestamp": signal.source_timestamp,
        "participants": signal.participants,
        "entities": dict(entities_dict),
        "entity_refs": [{"id": ref.id, "type": ref.type, "name": ref.name} for ref in signal.entities],
        "confidence": signal.confidence,
        "status": signal.status,
        "owner": signal.owner.name if signal.owner else None,
        "position": signal.position,
        "client_id": signal.client_id,
    }


# ============================================================================
# SIGNAL MUTATION TOOLS
# ============================================================================


async def update_signal(
    signal_id: str,
    status: str | None = None,
    content: str | None = None,
    owner_id: str | None = None,
    due_date: str | None = None,
    review_action: str | None = None,
    actor: str | None = None,
    superseded_by: str | None = None,
) -> dict[str, Any]:
    """Update a persisted signal (decision, action item, key point, insight).

    Two mutually exclusive update paths:

    **Plain field update** (status / content / owner_id / due_date): writes
    directly to the SignalStore JSON, git-commits the file, then syncs to Neo4j
    (best-effort). No audit row is emitted.

    **Governance transition** (review_action): routes through
    ``review_with_audit`` (which composes ``apply_review`` + audit record),
    persists the updated signal to the SignalStore, appends the audit row via
    ``SignalAuditStore``, and syncs to Neo4j (all best-effort after the primary
    write). The audit row is the G2 guarantee (ADR-002 "append-only, survives");
    git-commit of the audit JSONL is best-effort (failure logged, not raised).

    Authority / governance fields (can_use_as_evidence, can_use_as_instruction,
    provenance_status, review_status) are NOT settable directly — review_action
    is the only governance entry point (ADR-002 server-injected).

    Args:
        signal_id: The signal's UUID5 identifier.
        status: New status — open, in_progress, or done.
        content: Updated content text.
        owner_id: Entity slug ID of the new owner (e.g. "person-sarah-chen").
        due_date: New due date string (YYYY-MM-DD).
        review_action: Governance transition — one of: confirm, reject,
            evidence_only, dispute, supersede. Triggers audited path.
        actor: Who is performing the review action (for the audit record).
        superseded_by: Required when review_action=="supersede" — successor id.

    Returns:
        Dict with success, signal data, and sync status. On governance
        transitions also includes review_applied=True and audit_row_id.
    """
    try:
        store = SignalStore()

        # ----------------------------------------------------------------
        # Governance transition path (review_action provided)
        # ----------------------------------------------------------------
        if review_action is not None:
            # 1. Look up the signal (needed for review_with_audit)
            lookup = store.find_signal_by_id(signal_id)
            if lookup is None:
                return {"success": False, "error": f"Signal '{signal_id}' not found"}
            current_signal, container = lookup

            # 2. Apply review transition + emit audit record (pure, no I/O)
            try:
                from app.services.signal_audit import review_with_audit
                new_signal, audit_record = review_with_audit(
                    current_signal,
                    review_action,
                    actor=actor,
                    superseded_by=superseded_by,
                )
            except ValueError as e:
                return {"success": False, "error": str(e)}

            # 3. Persist the updated signal to SignalStore (source of truth)
            store.replace_signal(new_signal, container)

            # 4. Append audit row (the G2 guarantee — log loudly on failure)
            audit_path: str | None = None
            try:
                audit_store = SignalAuditStore()
                audit_file = audit_store.append(audit_record)
                audit_path = audit_store.relative_path(signal_id)
                logger.info(
                    "[UPDATE_SIGNAL] Audit row appended: %s action=%s actor=%s",
                    audit_file, review_action, actor,
                )
            except Exception as e:
                logger.error(
                    "[UPDATE_SIGNAL] AUDIT APPEND FAILED for signal %s action=%s: %s",
                    signal_id, review_action, e,
                    exc_info=True,
                )
                # Audit failure: still return success (signal was saved) but surface
                # audit_error so the caller can detect the gap.
                return {
                    "success": True,
                    "review_applied": True,
                    "audit_error": str(e),
                    "signal": _signal_to_dict(new_signal),
                    "neo4j_synced": False,
                }

            # 5. Git commit of audit JSONL (best-effort — after successful append)
            if audit_path:
                try:
                    audit_file_path = audit_store._file_path(signal_id)
                    audit_content = audit_file_path.read_text(encoding="utf-8")
                    await git_ops.commit_file(
                        audit_path,
                        audit_content,
                        f"audit: {review_action} signal {signal_id}",
                    )
                except Exception as e:
                    logger.warning(
                        "[UPDATE_SIGNAL] Git commit of audit JSONL failed (non-fatal): %s", e
                    )

            # 6. Neo4j governance mirror (best-effort)
            neo4j_synced = False
            try:
                from app.neo4j_client import get_neo4j_client
                from app.services.graph.signal_graph_writer import SignalGraphWriter

                client = get_neo4j_client()
                if client:
                    writer = SignalGraphWriter(client)
                    neo4j_synced = await writer.update_signal_properties(
                        signal_id,
                        review_status=new_signal.review_status,
                        provenance_status=new_signal.provenance_status,
                        can_use_as_evidence=new_signal.can_use_as_evidence,
                        can_use_as_instruction=new_signal.can_use_as_instruction,
                        tenant_id=new_signal.tenant_id,
                    )
            except Exception as e:
                logger.warning(
                    "[UPDATE_SIGNAL] Neo4j governance mirror failed (best-effort): %s", e
                )

            signal_dict = _signal_to_dict(new_signal)
            # Include governance fields in the review response
            signal_dict["review_status"] = new_signal.review_status
            signal_dict["provenance_status"] = new_signal.provenance_status
            signal_dict["can_use_as_evidence"] = new_signal.can_use_as_evidence
            signal_dict["can_use_as_instruction"] = new_signal.can_use_as_instruction

            logger.info(
                "[UPDATE_SIGNAL] Governance transition: %s → %s (gate=%s) signal=%s",
                review_action, new_signal.review_status, audit_record.gate_response, signal_id,
            )
            return {
                "success": True,
                "review_applied": True,
                "audit_row_id": audit_record.id,
                "gate_response": audit_record.gate_response,
                "signal": signal_dict,
                "neo4j_synced": neo4j_synced,
            }

        # ----------------------------------------------------------------
        # Plain field update path (original behaviour, unchanged)
        # ----------------------------------------------------------------
        if not any([status, content, owner_id, due_date]):
            return {"success": False, "error": "Provide at least one field to update"}

        # Resolve owner name/type if owner_id provided
        owner_name: str | None = None
        owner_type: str | None = None
        if owner_id:
            try:
                from app.services.entity_utils import extract_entity_type_from_id

                owner_type = extract_entity_type_from_id(owner_id) or "person"
                # Derive a readable name from the slug
                slug_part = owner_id.split("-", 1)[-1] if "-" in owner_id else owner_id
                owner_name = slug_part.replace("-", " ").title()

                # Try to resolve a better name from the knowledge graph
                sk_inner = _get_semantica()
                if sk_inner:
                    entity = await sk_inner.get_entity(owner_id)  # noqa: F841 (used below)
                    if entity:
                        owner_name = entity.get("name", owner_name)
                else:
                    kg = get_knowledge_graph()
                    node = kg.nodes.get(owner_id)
                    if node and hasattr(node, "attributes") and node.attributes:
                        owner_name = node.attributes.get("name", owner_name)
            except Exception as e:
                logger.debug(f"Owner resolution fallback for {owner_id}: {e}")

        # 1. Write to JSON (source of truth)
        updated = store.update_signal(
            signal_id,
            status=status,
            content=content,
            owner_id=owner_id,
            owner_name=owner_name,
            owner_type=owner_type,
            due_date=due_date,
        )
        if updated is None:
            return {"success": False, "error": f"Signal '{signal_id}' not found"}

        # 2. Git commit (non-fatal)
        result = store.find_signal_by_id(signal_id)
        if result:
            _, container = result
            rel_path = store.relative_path(container.bot_id)
            try:
                changes = []
                if status:
                    changes.append(f"status={status}")
                if content:
                    changes.append("content updated")
                if owner_id:
                    changes.append(f"owner={owner_id}")
                if due_date:
                    changes.append(f"due={due_date}")
                msg = f"signal: Update {updated.type} ({', '.join(changes)})"
                await git_ops.commit_and_push([rel_path], msg)
            except Exception as e:
                logger.warning(f"[UPDATE_SIGNAL] Git commit failed (non-fatal): {e}")

        # 3. Neo4j sync (best-effort)
        neo4j_synced = False
        try:
            from app.neo4j_client import get_neo4j_client
            from app.services.graph.signal_graph_writer import SignalGraphWriter

            client = get_neo4j_client()
            if client:
                writer = SignalGraphWriter(client)
                neo4j_synced = await writer.update_signal_properties(
                    signal_id,
                    status=status,
                    content=content,
                    owner_name=owner_name,
                )
        except Exception as e:
            logger.warning(f"[UPDATE_SIGNAL] Neo4j sync failed (best-effort): {e}")

        signal_dict = _signal_to_dict(updated)
        logger.info(f"[UPDATE_SIGNAL] Updated signal {signal_id}, neo4j_synced={neo4j_synced}")
        return {"success": True, "signal": signal_dict, "neo4j_synced": neo4j_synced}

    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error updating signal: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def delete_signal(signal_id: str) -> dict[str, Any]:
    """Permanently remove a signal from persistence.

    Deletes from JSON file (source of truth), commits to git,
    then removes from Neo4j (best-effort).

    Args:
        signal_id: The signal's UUID5 identifier.

    Returns:
        Dict with success, deleted signal data, and sync status.
    """
    try:
        store = SignalStore()

        # Look up the container before deleting (need bot_id for git path)
        lookup = store.find_signal_by_id(signal_id)
        if lookup is None:
            return {"success": False, "error": f"Signal '{signal_id}' not found"}
        _, container = lookup
        bot_id = container.bot_id

        # 1. Delete from JSON (source of truth)
        deleted = store.delete_signal(signal_id)
        if deleted is None:
            return {"success": False, "error": f"Signal '{signal_id}' not found"}

        # 2. Git commit (non-fatal)
        try:
            rel_path = store.relative_path(bot_id)
            msg = f"signal: Delete {deleted.type} '{deleted.content[:60]}'"
            await git_ops.commit_and_push([rel_path], msg)
        except Exception as e:
            logger.warning(f"[DELETE_SIGNAL] Git commit failed (non-fatal): {e}")

        # 3. Neo4j removal (best-effort)
        neo4j_synced = False
        try:
            from app.neo4j_client import get_neo4j_client
            from app.services.graph.signal_graph_writer import SignalGraphWriter

            client = get_neo4j_client()
            if client:
                writer = SignalGraphWriter(client)
                neo4j_synced = await writer.delete_signal_node(signal_id)
        except Exception as e:
            logger.warning(f"[DELETE_SIGNAL] Neo4j sync failed (best-effort): {e}")

        signal_dict = _signal_to_dict(deleted)
        logger.info(f"[DELETE_SIGNAL] Deleted signal {signal_id}, neo4j_synced={neo4j_synced}")
        return {"success": True, "deleted_signal": signal_dict, "neo4j_synced": neo4j_synced}

    except Exception as e:
        logger.error(f"Error deleting signal: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


# ============================================================================
# RELATIONSHIP TRAVERSAL TOOLS (Issue TBD - Graph Reasoning Enhancement)
# ============================================================================


async def get_entity_by_name(
    name: str, entity_type: str | None = None
) -> dict[str, Any] | None:
    """
    Find an entity in the knowledge graph by its name.

    This is a focused lookup tool that returns the entity ID and metadata,
    which can then be used with other relationship traversal tools.

    Args:
        name: The name of the entity to find (case-insensitive)
        entity_type: Optional entity type filter (e.g., "person", "role", "competency")

    Returns:
        Dict with entity details if found, None otherwise
        {
            "id": "person-abc123",
            "name": "Sarah Chen",
            "type": "person",
            "file_path": "candidates/sarah-chen.md",
            "document_count": 5,
            "connection_count": 12
        }
    """
    try:
        logger.info(
            f"[GET_ENTITY_BY_NAME] Looking up entity: name='{name}', type={entity_type}"
        )

        sk = _get_semantica()
        if sk is None:
            raise RuntimeError(
                "Semantica is not initialized. Cannot look up entity by name — "
                "the legacy in-memory graph fallback was removed because it "
                "diverged from the Neo4j source of truth."
            )

        from app.services.semantica_config import entity_type_to_label

        params: dict[str, Any] = {"name": name}
        match_clause = "MATCH (n:Entity) "
        if entity_type:
            label = entity_type_to_label(entity_type)
            match_clause = f"MATCH (n:{label}:Entity) "

        # Exact match (case-insensitive)
        cypher = (
            match_clause
            + "WHERE toLower(n.name) = toLower($name) "
            "RETURN n LIMIT 1"
        )
        rows = _extract_records(sk.graph_store.execute_query(cypher, params))
        if not rows:
            # Try a partial match before giving up.
            cypher = (
                match_clause
                + "WHERE toLower(n.name) CONTAINS toLower($name) "
                "RETURN n LIMIT 1"
            )
            rows = _extract_records(sk.graph_store.execute_query(cypher, params))

        if not rows:
            logger.info(f"[GET_ENTITY_BY_NAME] No entity found for '{name}'")
            return None

        node = rows[0].get("n", {})
        entity = {
            "id": node.get("id", ""),
            "name": node.get("name", ""),
            "type": node.get("entity_type", ""),
            "file_path": node.get("file_path", ""),
        }
        logger.info(f"[GET_ENTITY_BY_NAME] Found via Semantica: {entity['id']}")
        return _serialize_for_json(entity)

    except Exception:
        logger.exception("Error looking up entity by name")
        return None


async def get_entity_relationships(entity_id: str) -> dict[str, Any]:
    """
    Get all relationships for a specific entity using Neo4j graph queries.

    Returns both outgoing relationships (this entity -> others) and
    incoming relationships (others -> this entity).

    Args:
        entity_id: The entity ID (e.g., "person-abc123", "role-xyz789")

    Returns:
        Dict with outgoing and incoming relationships
    """
    try:
        logger.info(f"[GET_ENTITY_RELATIONSHIPS] Getting relationships for {entity_id}")

        sk = _get_semantica()
        if sk is None:
            raise RuntimeError(
                "Semantica is not initialized. Cannot fetch relationships — "
                "the legacy in-memory graph fallback was removed because it "
                "produced inconsistent edge directions vs. Neo4j."
            )

        all_rels = await sk.get_relationships(entity_id)
        outgoing = [
            {
                "relationship_type": r.get("relationship_type", ""),
                "target_id": r.get("target", ""),
                "target_type": r.get("target_type", "unknown"),
                "target_name": r.get("target_name", r.get("target", "")),
            }
            for r in all_rels if r.get("direction") == "outgoing"
        ]
        incoming = [
            {
                "relationship_type": r.get("relationship_type", ""),
                "source_id": r.get("source", ""),
                "source_type": r.get("source_type", "unknown"),
                "source_name": r.get("source_name", r.get("source", "")),
            }
            for r in all_rels if r.get("direction") == "incoming"
        ]
        result = {
            "entity_id": entity_id,
            "outgoing": outgoing,
            "incoming": incoming,
            "total_outgoing": len(outgoing),
            "total_incoming": len(incoming),
            "available_outgoing_types": sorted(set(r["relationship_type"] for r in outgoing)),
            "available_incoming_types": sorted(set(r["relationship_type"] for r in incoming)),
        }
        logger.info(f"[GET_ENTITY_RELATIONSHIPS] Semantica: {len(outgoing)} outgoing, {len(incoming)} incoming")
        return _serialize_for_json(result)

    except Exception as e:
        logger.exception("Error getting entity relationships")
        return {
            "entity_id": entity_id,
            "outgoing": [],
            "incoming": [],
            "total_outgoing": 0,
            "total_incoming": 0,
            "error": str(e),
        }


async def find_related_entities(
    entity_id: str,
    relationship_types: list[str] | None = None,
    max_results: int = 20,
) -> list[dict[str, Any]]:
    """
    Find entities that are directly related to the given entity.

    This performs a one-hop traversal of the knowledge graph, following
    relationships from the specified entity. Optionally filter by
    relationship type(s).

    Args:
        entity_id: The entity ID to start from
        relationship_types: Optional list of relationship types to filter by
                          (e.g., ["works_on", "reports_to"])
        max_results: Maximum number of related entities to return (default: 20)

    Returns:
        List of related entities with relationship details:
        [
            {
                "entity": {
                    "id": "role-xyz789",
                    "name": "VP Engineering",
                    "type": "role",
                    "file_path": "roles/vp-engineering.md"
                },
                "relationship": {
                    "type": "works_on",
                    "strength": 0.95,
                    "shared_documents": 3
                }
            },
            ...
        ]
    """
    try:
        logger.info(
            f"[FIND_RELATED_ENTITIES] Finding entities related to {entity_id}, "
            f"relationship_types={relationship_types}, max={max_results}"
        )

        # Normalize relationship_types
        if isinstance(relationship_types, str):
            relationship_types = [rt.strip() for rt in relationship_types.split(",") if rt.strip()]

        sk = _get_semantica()
        if sk is None:
            raise RuntimeError(
                "Semantica is not initialized. Cannot find related entities — "
                "the legacy in-memory graph fallback was removed because it "
                "produced inconsistent results vs. the Neo4j source of truth."
            )

        all_rels = await sk.get_relationships(entity_id)
        related = []
        for rel in all_rels:
            rel_type = rel.get("relationship_type", "")
            if relationship_types and rel_type not in relationship_types:
                continue

            # Pick the endpoint that isn't the entity we're querying
            if rel.get("direction") == "incoming":
                related_id = rel.get("source", rel.get("target", ""))
            else:
                related_id = rel.get("target", rel.get("source", ""))
            related_entity = await sk.get_entity(related_id)

            related.append({
                "entity": {
                    "id": related_id,
                    "name": related_entity.get("name", related_id) if related_entity else related_id,
                    "type": related_entity.get("type", "unknown") if related_entity else "unknown",
                    "file_path": related_entity.get("file_path", "") if related_entity else "",
                },
                "relationship": {
                    "type": rel_type,
                    "strength": rel.get("properties", {}).get("strength", 1.0),
                },
            })
            if len(related) >= max_results:
                break

        logger.info(f"[FIND_RELATED_ENTITIES] Semantica found {len(related)} related entities")
        return _serialize_for_json(related)

    except Exception:
        logger.exception("Error finding related entities")
        return []


async def list_entity_profiles(
    entity_ids: list[str],
    include_relationships: bool = True,
    max_relationships_per_entity: int = 20,
) -> list[dict[str, Any]]:
    """
    Bulk-fetch profiles (attributes + relationships) for a specific set of entity IDs.

    Sibling of list_entities, but selected by ID rather than by type. Use this
    when you already have a list of entity IDs (e.g. from search_knowledge_graph
    or find_related_entities) and want their full attribute and relationship
    data in one call instead of calling find_related_entities once per entity.

    Args:
        entity_ids: List of entity IDs to look up (max 50)
        include_relationships: Whether to include relationships (default True)
        max_relationships_per_entity: Max relationships per entity (default 20)

    Returns:
        List of entity profiles. Missing entities appear as {"id": ..., "error": "not_found"}.
    """
    # Normalize and validate entity_ids before any iteration so a bad input
    # doesn't trigger len()/iteration failures (which would also blow up the
    # exception handler that re-iterates the same value).
    if isinstance(entity_ids, str):
        entity_ids = [eid.strip() for eid in entity_ids.split(",") if eid.strip()]
    elif not isinstance(entity_ids, list):
        return [{"id": "", "error": "invalid_entity_ids"}]

    try:
        logger.info(
            f"[LIST_ENTITY_PROFILES] Getting profiles for {len(entity_ids)} entities: {entity_ids}"
        )

        sk = _get_semantica()
        if sk is None:
            raise RuntimeError(
                "Semantica is not initialized. Cannot fetch entity profiles — "
                "the legacy in-memory graph fallback was removed because it "
                "diverged from the Neo4j source of truth."
            )

        profiles = []
        for eid in entity_ids[:50]:
            entity = await sk.get_entity(eid)
            if not entity:
                profiles.append({"id": eid, "error": "not_found"})
                continue

            profile: dict[str, Any] = {
                "id": eid,
                "name": entity.get("name", eid),
                "type": entity.get("type", "unknown"),
                "attributes": _serialize_for_json(entity.get("attributes", {})),
            }

            if include_relationships:
                rels = await sk.get_relationships(eid)
                profile["relationships"] = [
                    {
                        "type": r.get("relationship_type", ""),
                        "direction": r.get("direction", ""),
                        "related_id": r.get("target", r.get("source", "")),
                        "related_name": r.get("target_name", r.get("source_name", "")),
                        "related_type": r.get("target_type", r.get("source_type", "unknown")),
                    }
                    for r in rels[:max_relationships_per_entity]
                ]

            profiles.append(profile)

        logger.info(
            f"[LIST_ENTITY_PROFILES] Returned {len(profiles)} profiles "
            f"({sum(1 for p in profiles if 'error' not in p)} found, "
            f"{sum(1 for p in profiles if 'error' in p)} not found)"
        )
        return _serialize_for_json(profiles)

    except Exception:
        logger.exception("Error getting entity profiles")
        return [{"id": eid, "error": "internal_error"} for eid in entity_ids]


# ============================================================================
# READ-ONLY CYPHER QUERY TOOL
# ============================================================================

# Regex to detect write/mutation keywords in Cypher queries
_WRITE_PATTERN = re.compile(
    r'\b(CREATE|MERGE|SET\s|DELETE|DETACH|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b|CALL\s*\{',
    re.IGNORECASE,
)


# ============================================================================
# MEETING TRANSCRIPT & DOCUMENT TOOLS
# ============================================================================

# Regex to parse transcript lines: [MM:SS] **Speaker Name**: text
_TRANSCRIPT_LINE_RE = re.compile(r'^\[(\d+:\d+)\]\s+\*\*(.+?)\*\*:\s+(.+)$')


async def search_meeting_transcripts(
    query: str,
    speaker: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """Search meeting transcripts for specific text, quotes, or topics.

    Full-text search across meeting markdown files with optional speaker filter.

    Args:
        query: Text to search for in transcript lines
        speaker: Optional speaker name filter (case-insensitive partial match)
        date_from: Filter start date (YYYY-MM-DD, inclusive)
        date_to: Filter end date (YYYY-MM-DD, inclusive)
        max_results: Max results to return (default 5)

    Returns:
        List of transcript matches with context
    """
    try:
        if not query or not query.strip():
            return []

        logger.info(f"[SEARCH_TRANSCRIPTS] query='{query}', speaker={speaker}, date_from={date_from}, date_to={date_to}")

        files = await git_ops.read_markdown_files(folder="meetings")
        if not files:
            return []

        query_lower = query.lower().strip()
        query_words = query_lower.split()
        matches = []

        for f in files:
            # Parse frontmatter for metadata
            fm = frontmatter.extract_all(f.content)
            if not fm:
                continue

            # Date filter
            start_time = fm.get("start_time", "")
            if isinstance(start_time, datetime):
                file_date = start_time.strftime("%Y-%m-%d")
            elif isinstance(start_time, date):
                file_date = start_time.isoformat()
            elif isinstance(start_time, str):
                file_date = start_time[:10]
            else:
                file_date = ""

            if date_from and file_date and file_date < date_from:
                continue
            if date_to and file_date and file_date > date_to:
                continue

            # Parse transcript lines
            lines = f.content.split("\n")
            transcript_lines = []
            for i, line in enumerate(lines):
                m = _TRANSCRIPT_LINE_RE.match(line.strip())
                if m:
                    transcript_lines.append({
                        "index": i,
                        "timestamp": m.group(1),
                        "speaker": m.group(2),
                        "text": m.group(3),
                        "raw": line.strip(),
                    })

            if not transcript_lines:
                continue

            # Search transcript lines
            for tl_idx, tl in enumerate(transcript_lines):
                # Speaker filter
                if speaker and speaker.lower() not in tl["speaker"].lower():
                    continue

                text_lower = tl["text"].lower()

                # Score: exact phrase > all words > partial
                score = 0
                if query_lower in text_lower:
                    score = 3  # exact phrase match
                elif all(w in text_lower for w in query_words):
                    score = 2  # all words present
                else:
                    matching_words = sum(1 for w in query_words if w in text_lower)
                    if matching_words > 0:
                        score = matching_words / len(query_words)  # partial

                if score > 0:
                    # Build context window (±2 transcript lines)
                    ctx_start = max(0, tl_idx - 2)
                    ctx_end = min(len(transcript_lines), tl_idx + 3)
                    excerpt_lines = [transcript_lines[j]["raw"] for j in range(ctx_start, ctx_end)]

                    matches.append({
                        "file_path": f.path,
                        "meeting_title": fm.get("title", ""),
                        "meeting_date": file_date,
                        "meeting_id": fm.get("meeting_id", fm.get("bot_id", "")),
                        "speaker": tl["speaker"],
                        "timestamp": tl["timestamp"],
                        "matched_text": tl["text"],
                        "excerpt": "\n".join(excerpt_lines),
                        "score": score,
                    })

        # Sort by score descending, then by date descending
        matches.sort(key=lambda m: (m["score"], m["meeting_date"]), reverse=True)
        result = matches[:max_results]
        logger.info(f"[SEARCH_TRANSCRIPTS] Found {len(result)} matches (from {len(matches)} total)")
        return _serialize_for_json(result)

    except Exception as e:
        logger.error(f"Error searching transcripts: {e}", exc_info=True)
        return []


async def list_meetings(
    max_results: int = 20,
    status: str = "all",
) -> dict[str, Any]:
    """List meetings with titles, dates, and bot_ids.

    Reads meeting markdown files from /app/repo/meetings/ and parses each
    via MeetingState.from_markdown. Results are sorted by updated_at desc
    and capped at max_results (clamped to [1, 100]).

    This function was previously inlined into mcp_server.py's call_tool
    dispatcher; extracted in Phase E1 of the MCP cleanup so both MCP
    surfaces use the same codepath.

    Args:
        max_results: Cap on returned meetings (default 20, clamped 1..100).
        status: Filter — 'finalized', 'in_progress', or 'all' (default).

    Returns:
        Dict with `meetings` (list of metadata dicts) and `count`.
    """
    from pathlib import Path

    from app.models.meeting.state import MeetingState

    meetings_dir = Path("/app/repo/meetings")
    try:
        limit = int(max_results)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))

    summaries = []
    if meetings_dir.is_dir():
        # Parse + filter every file first — don't slice by filesystem mtime
        # ordering, which can drop meetings whose metadata is newer than
        # their file mtime. Sort by ms.updated_at after.
        for f in sorted(meetings_dir.glob("meeting-*.md")):
            try:
                ms = MeetingState.from_markdown(f.read_text())
            except (OSError, ValueError) as exc:
                logger.warning("list_meetings: skipping %s (%s)", f.name, exc)
                continue
            if status == "finalized" and not ms.is_finalized:
                continue
            if status == "in_progress" and ms.is_finalized:
                continue
            summaries.append({
                "bot_id": ms.bot_id,
                "meeting_id": ms.meeting_id,
                "title": ms.title,
                "start_time": ms.start_time.isoformat() if ms.start_time else None,
                "updated_at": ms.updated_at.isoformat(),
                "duration_minutes": round(ms.duration, 1) if ms.duration else None,
                "participants": ms.participants,
                "status": ms.status,
                "is_finalized": ms.is_finalized,
                "has_transcript": ms.transcript is not None and len(ms.transcript) > 0,
            })

    summaries.sort(key=lambda s: s["updated_at"] or "", reverse=True)
    summaries = summaries[:limit]
    return {"meetings": summaries, "count": len(summaries)}


async def get_meeting_transcript(
    bot_id: str,
    max_length: int = 50000,
) -> dict[str, Any]:
    """Get the full transcript and metadata for one meeting by bot_id.

    Reads the meeting file at /app/repo/meetings/meeting-{bot_id}.md and
    parses via MeetingState. Truncates the transcript to max_length chars
    when set; pass max_length=0 to disable truncation.

    Extracted from mcp_server.py's inline implementation in Phase E1.

    Args:
        bot_id: The bot ID of the meeting (from list_meetings results).
        max_length: Max transcript char length (default 50000, 0 = no truncation).

    Returns:
        Dict with meeting metadata and transcript, or {"error": ...} when
        the file is missing.
    """
    from pathlib import Path

    from app.models.meeting.state import MeetingState

    try:
        max_length_int = int(max_length)
    except (TypeError, ValueError):
        max_length_int = 50000
    if max_length_int < 0:
        # `0` means "no truncation"; any other negative value falls back to
        # the default so we never accidentally dump a multi-hour transcript.
        max_length_int = 50000

    meeting_file = Path("/app/repo/meetings") / f"meeting-{bot_id}.md"
    if not meeting_file.exists():
        return {"error": f"Meeting not found: no file for bot_id '{bot_id}'"}

    try:
        ms = MeetingState.from_markdown(meeting_file.read_text())
    except (OSError, ValueError) as exc:
        logger.warning(
            "[GET_MEETING_TRANSCRIPT] failed to load %s (%s)",
            meeting_file.name,
            exc,
        )
        return {
            "error": (
                f"Failed to load transcript for bot_id '{bot_id}': {exc}"
            )
        }
    transcript = ms.transcript or ""
    truncated = False
    if max_length_int > 0 and len(transcript) > max_length_int:
        transcript = transcript[:max_length_int] + "\n\n[... truncated — use max_length=0 for full transcript]"
        truncated = True

    return {
        "bot_id": ms.bot_id,
        "meeting_id": ms.meeting_id,
        "title": ms.title,
        "start_time": ms.start_time.isoformat() if ms.start_time else None,
        "updated_at": ms.updated_at.isoformat(),
        "duration_minutes": round(ms.duration, 1) if ms.duration else None,
        "participants": ms.participants,
        "status": ms.status,
        "is_finalized": ms.is_finalized,
        "key_points": ms.key_points,
        "entities_mentioned": ms.entities_mentioned,
        "transcript": transcript,
        "transcript_length": len(ms.transcript or ""),
        "truncated": truncated,
    }


# Recorder sources that map to the call_transcript pipeline path. Restricting
# add_call_transcript to these means the source→content_type map in
# ingest_classifier can never pick a non-transcript type for this tool.
_TRANSCRIPT_SOURCES = {
    "local_recording",
    "plaud",
    "grain",
    "otter",
    "fathom",
    "fireflies",
}


def _parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an ISO 8601 string (or pass a datetime through) or return None.

    Accepts a trailing 'Z' (UTC) which datetime.fromisoformat rejects on older
    Pythons. Returns None for anything unparseable so callers can hard-reject.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


async def add_call_transcript(
    transcript: str,
    start_time: Any,
    participants: Any,
    title: str | None = None,
    source: str = "local_recording",
    duration_minutes: float | None = None,
    conversation_id: str | None = None,
    source_id: str | None = None,
    wait_timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Ingest a call transcript and run the full enrichment pipeline on it.

    Forces the call_transcript path (no LLM classification), builds an Observation,
    promotes signals, writes them to the graph, and persists the meeting +
    signals to git — reusing the same machinery as POST /api/ingest, in-process.

    Hard-requires `start_time` and a non-empty `participants` list: without them
    the meeting lands at the wrong point in time and with no people linked, so
    this tool rejects the call rather than create a low-quality record.

    Blocks up to `wait_timeout_seconds` for the pipeline to finish. On the fast
    path it returns an extraction summary (bot_id + signal/entity counts); if
    enrichment is still running at the timeout it returns {status:"processing",
    job_id, poll_url} so the caller can poll /api/ingest/{job_id}/status later.

    Args:
        transcript: Full transcript text. Inline `[mm:ss] Speaker:` markers are
            preserved and improve downstream attribution.
        start_time: ISO 8601 timestamp of when the call started (required).
        participants: Non-empty list of attendee names (required).
        title: Optional call subject.
        source: Recorder source — one of local_recording (default), plaud,
            grain, otter, fathom, fireflies.
        duration_minutes: Optional call length, passed through as metadata.
        conversation_id: Optional stable key linking multi-source captures of
            the same call; passed through as metadata for future reconciliation.
        source_id: Optional external ID for idempotency (exact-dup suppression).
        wait_timeout_seconds: Max seconds to block (default 30, clamped 1..60).

    Returns:
        On completion: dict with status="completed", bot_id, content_type, and
        extraction counts. On timeout: status="processing" with job_id/poll_url.
        On invalid input or pipeline failure: {"error": ...}.
    """
    # --- Validate transcript ---
    if not isinstance(transcript, str) or not transcript.strip():
        return {"error": "transcript is required and must be a non-empty string"}

    # --- Validate participants (hard-require) ---
    if not isinstance(participants, list):
        return {"error": "participants is required: provide a list of attendee names"}
    names = [p.strip() for p in participants if isinstance(p, str) and p.strip()]
    if not names:
        return {
            "error": (
                "participants is required: provide at least one attendee name "
                "(resolve who was on the call before ingesting)"
            )
        }

    # --- Validate start_time (hard-require) ---
    parsed_start = _parse_iso_datetime(start_time)
    if parsed_start is None:
        return {
            "error": (
                "start_time is required and must be an ISO 8601 timestamp "
                "(e.g. '2026-06-04T14:30:00Z') — it places the call on the timeline"
            )
        }

    # --- Validate source (transcript recorders only) ---
    src = (source or "local_recording").strip().lower()
    if src not in _TRANSCRIPT_SOURCES:
        return {
            "error": (
                f"source '{source}' is not a transcript recorder; expected one of "
                f"{sorted(_TRANSCRIPT_SOURCES)}"
            )
        }

    # --- Clamp timeout ---
    try:
        timeout_s = float(wait_timeout_seconds)
    except (TypeError, ValueError):
        timeout_s = 30.0
    timeout_s = max(1.0, min(timeout_s, 60.0))

    from app.models.ingestion.models import ContentSource, IngestRequest
    from app.routes.ingest import submit_and_wait

    metadata: dict[str, Any] = {}
    if conversation_id:
        metadata["conversation_id"] = conversation_id
    if duration_minutes is not None:
        metadata["duration_minutes"] = duration_minutes

    request = IngestRequest(
        content=transcript,
        source=ContentSource(src),
        source_id=source_id,
        title=title,
        participants=names,
        timestamp=parsed_start,
        metadata=metadata or None,
    )

    outcome = await submit_and_wait(request, timeout_s=timeout_s)
    state = outcome.get("state")

    if state == "failed":
        return {
            "error": f"Ingestion failed: {outcome.get('error') or 'unknown error'}",
            "job_id": outcome.get("job_id"),
        }

    if state == "pending":
        return {
            "status": "processing",
            "job_id": outcome.get("job_id"),
            "poll_url": outcome.get("poll_url"),
            "message": (
                f"Enrichment still running after {int(timeout_s)}s. Poll the job "
                "for the result, then use list_meetings / get_meeting_transcript."
            ),
        }

    # state == "completed"
    result = outcome.get("result") or {}
    content_hash = result.get("content_hash", "")
    bot_id = f"ingest-{content_hash[:12]}" if content_hash else None
    return {
        "status": "completed",
        "bot_id": bot_id,
        "content_type": outcome.get("content_type"),
        "title": title,
        "participants": names,
        "start_time": parsed_start.isoformat(),
        "entities_extracted": result.get("entities_extracted", 0),
        "decisions_found": result.get("decisions_found", 0),
        "insights_generated": result.get("insights_generated", 0),
        "relationships_created": result.get("relationships_created", 0),
        "graph_nodes_created": result.get("graph_nodes_created", []),
        "processing_time_ms": result.get("processing_time_ms", 0),
        "next": (
            "Inspect with get_meeting_transcript(bot_id) or search_signals "
            "(filter by entity or date)."
        ),
    }


async def list_meeting_documents(
    meeting_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    participant: str | None = None,
    max_results: int = 20,
) -> list[dict[str, Any]]:
    """List meeting documents with metadata for path discovery.

    Args:
        meeting_id: Partial match on meeting_id or bot_id (e.g., "demo-006")
        date_from: Filter start date (YYYY-MM-DD, inclusive)
        date_to: Filter end date (YYYY-MM-DD, inclusive)
        participant: Filter by participant name (case-insensitive)
        max_results: Max results to return (default 20)

    Returns:
        List of meeting metadata dicts with file paths
    """
    try:
        logger.info(f"[LIST_MEETINGS] meeting_id={meeting_id}, date_from={date_from}, date_to={date_to}, participant={participant}")

        files = await git_ops.read_markdown_files(folder="meetings")
        if not files:
            return []

        results = []
        for f in files:
            fm = frontmatter.extract_all(f.content)
            if not fm:
                continue

            # Extract metadata
            fm_meeting_id = fm.get("meeting_id", fm.get("bot_id", ""))
            start_time = fm.get("start_time", "")
            if isinstance(start_time, datetime):
                file_date = start_time.strftime("%Y-%m-%d")
            elif isinstance(start_time, date):
                file_date = start_time.isoformat()
            elif isinstance(start_time, str):
                file_date = start_time[:10]
            else:
                file_date = ""

            participants_list = fm.get("participants", [])
            if isinstance(participants_list, str):
                participants_list = [participants_list]

            # Apply filters
            if meeting_id and meeting_id.lower() not in str(fm_meeting_id).lower():
                continue
            if date_from and file_date and file_date < date_from:
                continue
            if date_to and file_date and file_date > date_to:
                continue
            if participant:
                participant_lower = participant.lower()
                if not any(participant_lower in p.lower() for p in participants_list):
                    continue

            # Extract duration
            duration_seconds = fm.get("duration_seconds", fm.get("duration", 0))
            if isinstance(duration_seconds, (int, float)):
                duration_minutes = round(duration_seconds / 60) if duration_seconds else None
            else:
                duration_minutes = None

            results.append({
                "file_path": f.path,
                "meeting_id": fm_meeting_id,
                "title": fm.get("title", ""),
                "date": file_date,
                "participants": participants_list,
                "duration_minutes": duration_minutes,
                "key_points": fm.get("key_points", []),
            })

        # Sort by date descending
        results.sort(key=lambda r: r["date"], reverse=True)
        result = results[:max_results]
        logger.info(f"[LIST_MEETINGS] Found {len(result)} meetings (from {len(results)} total)")
        return _serialize_for_json(result)

    except Exception as e:
        logger.error(f"Error listing meeting documents: {e}", exc_info=True)
        return []


async def get_entity_context_summary(
    entity_id: str,
) -> dict[str, Any]:
    """Get a context-aware summary for an entity with recency data.

    Includes last meeting attended, recent signals, open action items,
    and related people with their recency data.

    Args:
        entity_id: Entity slug ID (e.g., "person-dana-rourke")

    Returns:
        Context summary dict with recency and activity data
    """
    try:
        logger.info(f"[ENTITY_CONTEXT] Getting context summary for {entity_id}")

        # 1. Get entity basic info from knowledge graph
        sk = _get_semantica()
        if sk:
            entity = await sk.get_entity(entity_id)
            if not entity:
                return {"error": f"Entity '{entity_id}' not found"}
            entity_name = entity.get("name", entity_id)
            entity_type = entity.get("type", "unknown")
        else:
            knowledge_graph = get_knowledge_graph()
            await knowledge_graph.build_graph()
            node = knowledge_graph.nodes.get(entity_id)
            if not node:
                return {"error": f"Entity '{entity_id}' not found"}
            entity_name = node.name or (node.metadata or {}).get("name", entity_id)
            entity_type = node.type or (node.metadata or {}).get("entity_type", "unknown")

        # 2. Get signals for this entity
        store = SignalStore()
        all_meeting_signals = store.load_all()

        signal_summary: dict[str, int] = {}
        open_action_items: list[dict[str, Any]] = []
        last_signal_date = ""

        for ms in all_meeting_signals:
            for signal in ms.signals:
                if not _signal_matches_entity(signal, entity_id):
                    continue

                # Count by type
                signal_summary[signal.type] = signal_summary.get(signal.type, 0) + 1

                # Track most recent signal date
                sig_date = signal.source_timestamp[:10] if signal.source_timestamp else ""
                if sig_date > last_signal_date:
                    last_signal_date = sig_date

                # Collect open action items owned by this entity
                if (
                    signal.type == "action_item"
                    and signal.status in ("open", "in_progress")
                    and signal.owner
                    and signal.owner.id == entity_id
                ):
                    open_action_items.append({
                        "content": signal.content,
                        "status": signal.status,
                        "due_date": getattr(signal, "due_date", None),
                        "source_meeting": signal.source_meeting_title or signal.source_meeting_id,
                        "source_date": sig_date,
                    })

        # 3. Get meeting participation
        meeting_files = await git_ops.read_markdown_files(folder="meetings")
        meetings_attended = []

        for f in meeting_files:
            fm = frontmatter.extract_all(f.content)
            if not fm:
                continue

            participants = fm.get("participants", [])
            if isinstance(participants, str):
                participants = [participants]

            # Check if entity name appears in participants
            name_lower = entity_name.lower()
            if any(name_lower in p.lower() for p in participants):
                start_time = fm.get("start_time", "")
                if isinstance(start_time, datetime):
                    mtg_date = start_time.strftime("%Y-%m-%d")
                elif isinstance(start_time, date):
                    mtg_date = start_time.isoformat()
                elif isinstance(start_time, str):
                    mtg_date = start_time[:10]
                else:
                    mtg_date = ""

                meetings_attended.append({
                    "title": fm.get("title", ""),
                    "date": mtg_date,
                    "path": f.path,
                    "meeting_id": fm.get("meeting_id", fm.get("bot_id", "")),
                })

        # Sort meetings by date descending
        meetings_attended.sort(key=lambda m: m["date"], reverse=True)
        last_meeting = meetings_attended[0] if meetings_attended else None

        # 4. Get relationships and build related people context
        if sk:
            all_rels = await sk.get_relationships(entity_id)
            related_people = []
            for r in all_rels:
                related_id = r.get("target", r.get("source", ""))
                related_name = r.get("target_name", r.get("source_name", related_id))
                related_type = r.get("target_type", r.get("source_type", ""))
                if related_type == "person" or "person" in related_id:
                    related_people.append({
                        "id": related_id,
                        "name": related_name,
                        "relationship": r.get("relationship_type", ""),
                    })
        else:
            directed = await knowledge_graph.find_related_entities_directed(entity_id, max_results=50)
            related_people = []
            for r in directed.get("outgoing", []):
                if r["entity"].get("type") == "person" or "person" in r["entity"].get("id", ""):
                    related_people.append({
                        "id": r["entity"]["id"],
                        "name": r["entity"].get("name", r["entity"]["id"]),
                        "relationship": r["relationship"]["type"],
                    })
            for r in directed.get("incoming", []):
                if r["entity"].get("type") == "person" or "person" in r["entity"].get("id", ""):
                    related_people.append({
                        "id": r["entity"]["id"],
                        "name": r["entity"].get("name", r["entity"]["id"]),
                        "relationship": r["relationship"]["type"],
                    })

        related_people_context = []

        # For each related person, find their recency data
        for rp in related_people:
            rp_name = rp["name"]
            rp_last_signal = ""
            rp_open_items = 0

            # Check signals
            for ms in all_meeting_signals:
                for signal in ms.signals:
                    if not _signal_matches_entity(signal, rp["id"]):
                        continue
                    sig_date = signal.source_timestamp[:10] if signal.source_timestamp else ""
                    if sig_date > rp_last_signal:
                        rp_last_signal = sig_date
                    if (
                        signal.type == "action_item"
                        and signal.status in ("open", "in_progress")
                        and signal.owner
                        and signal.owner.id == rp["id"]
                    ):
                        rp_open_items += 1

            # Check shared meetings
            rp_name_lower = rp_name.lower()
            last_shared_meeting = ""
            for m in meetings_attended:  # already sorted by date desc
                # Check if this person was in the same meeting
                for mf in meeting_files:
                    if mf.path != m["path"]:
                        continue
                    fm = frontmatter.extract_all(mf.content)
                    if not fm:
                        continue
                    participants = fm.get("participants", [])
                    if isinstance(participants, str):
                        participants = [participants]
                    if any(rp_name_lower in p.lower() for p in participants):
                        last_shared_meeting = m["date"]
                        break
                if last_shared_meeting:
                    break

            related_people_context.append({
                "name": rp["name"],
                "relationship": rp["relationship"],
                "last_shared_meeting": last_shared_meeting or None,
                "last_signal_date": rp_last_signal or None,
                "open_action_items_count": rp_open_items,
            })

        result = {
            "entity": {"id": entity_id, "name": entity_name, "type": entity_type},
            "last_signal_date": last_signal_date or None,
            "last_meeting": last_meeting,
            "total_meetings_attended": len(meetings_attended),
            "signal_summary": signal_summary,
            "open_action_items": open_action_items,
            "related_people_context": related_people_context,
        }

        logger.info(
            f"[ENTITY_CONTEXT] {entity_name}: {len(meetings_attended)} meetings, "
            f"{sum(signal_summary.values())} signals, {len(open_action_items)} open items, "
            f"{len(related_people_context)} related people"
        )
        return _serialize_for_json(result)

    except Exception as e:
        logger.error(f"Error getting entity context summary: {e}", exc_info=True)
        return {"entity_id": entity_id, "error": str(e)}


# ============================================================================
# READ-ONLY CYPHER QUERY TOOL
# ============================================================================

async def execute_cypher_query(
    cypher: str,
    parameters: dict[str, Any] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Execute a read-only Cypher query against Neo4j.

    Three layers of safety:
    1. Regex blocks write keywords before reaching Neo4j
    2. execute_read() runs in a Neo4j read transaction — server rejects writes
    3. Query length cap (2000 chars) and result row cap (default 100)

    Args:
        cypher: The Cypher query string (read-only).
        parameters: Optional dict of query parameters.
        limit: Max rows to return (default 100). Auto-injected if LIMIT not present.

    Returns:
        Dict with rows and count, or error dict.
    """
    # Validate read-only
    if _WRITE_PATTERN.search(cypher):
        return {"error": "Only read-only queries allowed. Use graph maintenance tools for mutations."}

    # Length guard
    if len(cypher) > 2000:
        return {"error": "Query too long (max 2000 chars)."}

    # Coerce parameters to dict (agent may send string, None, or other types)
    if parameters is None:
        parameters = {}
    elif isinstance(parameters, str):
        parameters = parameters.strip()
        if not parameters or parameters in ("null", "None", "{}", "''", '""'):
            parameters = {}
        else:
            try:
                parsed = json.loads(parameters)
                parameters = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                parameters = {}
    elif not isinstance(parameters, dict):
        parameters = {}

    sk = _get_semantica()
    if sk is None:
        return {
            "error": (
                "Semantica is not initialized. Cannot run Cypher query. "
                "Other graph tools (search_knowledge_graph, list_entities, "
                "get_entity_by_name) also depend on Semantica and will fail "
                "the same way until it is configured. To recover: ensure the "
                "Semantica service is reachable and that "
                "app.services.graph.factory.get_semantica_knowledge() can "
                "construct a client (typically a missing env var, network "
                "issue, or the embedded Neo4j instance not yet ready)."
            ),
        }

    try:
        rows = await sk.query_cypher(cypher, parameters, limit)
        if rows and isinstance(rows[0], dict) and "error" in rows[0]:
            return rows[0]
        logger.info(f"[CYPHER_QUERY] Semantica returned {len(rows)} rows")
        return {"rows": _serialize_for_json(rows), "count": len(rows)}
    except Exception as e:
        logger.error(f"[CYPHER_QUERY] Error executing query: {e}")
        return {"error": f"Cypher execution error: {str(e)}"}


# ============================================================================
# DECISION INTELLIGENCE TOOLS (NEW — powered by Semantica)
# ============================================================================


async def find_decision_precedents(
    query: str,
    category: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Find similar past decisions as precedents.

    Uses Semantica's ContextGraph for semantic similarity matching
    across historical decisions.

    Args:
        query: Description of the current decision context.
        category: Optional category filter (e.g. 'strategy', 'technical').
        limit: Maximum precedents to return.

    Returns:
        List of precedent dicts with similarity scores.
    """
    sk = _get_semantica()
    if not sk:
        return [{"error": "Decision intelligence requires Semantica (not initialized)"}]

    try:
        precedents = await sk.find_precedents(query, category=category, limit=limit)
        logger.info(f"[DECISION_PRECEDENTS] Found {len(precedents)} precedents for '{query[:50]}...'")
        return _serialize_for_json(precedents)
    except Exception as e:
        logger.error(f"Error finding decision precedents: {e}")
        return [{"error": str(e)}]


async def trace_decision_chain(
    decision_id: str,
) -> list[dict[str, Any]]:
    """Trace the causal chain of a decision.

    Shows how decisions are connected and what influenced what.

    Args:
        decision_id: ID of the decision to trace.

    Returns:
        List of decisions in the causal chain.
    """
    sk = _get_semantica()
    if not sk:
        return [{"error": "Decision intelligence requires Semantica (not initialized)"}]

    try:
        chain = await sk.trace_decision_chain(decision_id)
        logger.info(f"[DECISION_CHAIN] Traced {len(chain)} decisions for {decision_id}")
        return _serialize_for_json(chain)
    except Exception as e:
        logger.error(f"Error tracing decision chain: {e}")
        return [{"error": str(e)}]


# ============================================================================
# TEMPORAL TOOLS (Issue #864 — powered by Semantica)
# ============================================================================


def _get_temporal_query_service():
    """Get a TemporalQueryService instance if Semantica is available."""
    sk = _get_semantica()
    if not sk:
        return None
    from app.services.temporal_queries import TemporalQueryService
    return TemporalQueryService(sk)


def _parse_iso_timestamp(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp string."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


async def entity_at_time(
    entity_id: str,
    timestamp: str,
) -> dict[str, Any]:
    """Get entity state at a specific point in time.

    Args:
        entity_id: Entity ID or name.
        timestamp: ISO 8601 timestamp.
    """
    sk = _get_semantica()
    if not sk:
        return {"error": "Temporal queries require Semantica (not initialized)"}

    try:
        at_time = _parse_iso_timestamp(timestamp)
        result = await sk.get_state_at(entity_id, at_time)
        if result is None:
            return {"error": f"Entity '{entity_id}' not found at {timestamp}"}
        logger.info(f"[ENTITY_AT_TIME] Retrieved state for {entity_id} at {timestamp}")
        return _serialize_for_json(result)
    except Exception as e:
        logger.error(f"Error in entity_at_time: {e}")
        return {"error": str(e)}


async def active_relationships_at_time(
    entity_id: str,
    timestamp: str,
) -> list[dict[str, Any]]:
    """Get relationships active at a specific time.

    Args:
        entity_id: Entity ID.
        timestamp: ISO 8601 timestamp.
    """
    sk = _get_semantica()
    if not sk:
        return [{"error": "Temporal queries require Semantica (not initialized)"}]

    try:
        at_time = _parse_iso_timestamp(timestamp)
        rels = await sk.get_active_relationships(entity_id, at_time)
        logger.info(f"[ACTIVE_RELS_AT_TIME] Found {len(rels)} relationships for {entity_id} at {timestamp}")
        return _serialize_for_json(rels)
    except Exception as e:
        logger.error(f"Error in active_relationships_at_time: {e}")
        return [{"error": str(e)}]


async def get_entity_provenance(
    entity_id: str,
) -> dict[str, Any]:
    """Get provenance chain for an entity.

    Args:
        entity_id: Entity ID.
    """
    sk = _get_semantica()
    if not sk:
        return {"error": "Provenance queries require Semantica (not initialized)"}

    try:
        result = await sk.get_provenance(entity_id)
        logger.info(f"[ENTITY_PROVENANCE] Retrieved provenance for {entity_id}: {len(result.get('history', []))} entries")
        return _serialize_for_json(result)
    except Exception as e:
        logger.error(f"Error in get_entity_provenance: {e}")
        return {"error": str(e)}


async def decision_influence(
    decision_id: str,
) -> dict[str, Any]:
    """Analyze the influence/impact of a specific decision.

    Args:
        decision_id: Decision ID.
    """
    sk = _get_semantica()
    if not sk:
        return {"error": "Decision intelligence requires Semantica (not initialized)"}

    try:
        result = await sk.decisions.analyze_influence(decision_id)
        logger.info(f"[DECISION_INFLUENCE] Analyzed influence for {decision_id}")
        return _serialize_for_json(result)
    except Exception as e:
        logger.error(f"Error in decision_influence: {e}")
        return {"error": str(e)}


async def decision_stats() -> dict[str, Any]:
    """Get decision statistics and insights."""
    sk = _get_semantica()
    if not sk:
        return {"error": "Decision intelligence requires Semantica (not initialized)"}

    try:
        result = await sk.decisions.get_decision_stats()
        logger.info("[DECISION_STATS] Retrieved decision statistics")
        return _serialize_for_json(result)
    except Exception as e:
        logger.error(f"Error in decision_stats: {e}")
        return {"error": str(e)}


async def what_changed(
    entity_id: str,
    since: str,
) -> dict[str, Any]:
    """Diff entity state between a past timestamp and now.

    Args:
        entity_id: Entity ID or name.
        since: ISO 8601 timestamp.
    """
    svc = _get_temporal_query_service()
    if not svc:
        return {"error": "Temporal queries require Semantica (not initialized)"}

    try:
        since_dt = _parse_iso_timestamp(since)
        result = await svc.what_changed(entity_id, since=since_dt)
        logger.info(f"[WHAT_CHANGED] {len(result.get('changes', []))} changes for {entity_id} since {since}")
        return _serialize_for_json(result)
    except Exception as e:
        logger.error(f"Error in what_changed: {e}")
        return {"error": str(e)}


async def what_changed_between(
    entity_id: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    """Diff entity state between two arbitrary timestamps.

    Args:
        entity_id: Entity ID or name.
        start: ISO 8601 timestamp for start.
        end: ISO 8601 timestamp for end.
    """
    svc = _get_temporal_query_service()
    if not svc:
        return {"error": "Temporal queries require Semantica (not initialized)"}

    try:
        start_dt = _parse_iso_timestamp(start)
        end_dt = _parse_iso_timestamp(end)
        result = await svc.what_changed_between(entity_id, start=start_dt, end=end_dt)
        logger.info(f"[WHAT_CHANGED_BETWEEN] {len(result.get('changes', []))} changes for {entity_id}")
        return _serialize_for_json(result)
    except Exception as e:
        logger.error(f"Error in what_changed_between: {e}")
        return {"error": str(e)}


async def graph_as_of(
    entity_id: str,
    timestamp: str,
    depth: int = 2,
) -> dict[str, Any]:
    """Reconstruct subgraph around entity at a past point in time.

    Args:
        entity_id: Entity ID or name.
        timestamp: ISO 8601 timestamp.
        depth: Maximum traversal depth (default 2).
    """
    svc = _get_temporal_query_service()
    if not svc:
        return {"error": "Temporal queries require Semantica (not initialized)"}

    try:
        ts_dt = _parse_iso_timestamp(timestamp)
        result = await svc.graph_as_of(entity_id, timestamp=ts_dt, depth=depth)
        logger.info(f"[GRAPH_AS_OF] Reconstructed {len(result.get('nodes', []))} nodes for {entity_id} at {timestamp}")
        return _serialize_for_json(result)
    except Exception as e:
        logger.error(f"Error in graph_as_of: {e}")
        return {"error": str(e)}


async def find_contradictions(
    entity_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Detect signals that conflict with prior signals for same entity.

    Args:
        entity_id: Entity ID.
        date_from: Optional ISO 8601 start of date range.
        date_to: Optional ISO 8601 end of date range.
    """
    svc = _get_temporal_query_service()
    if not svc:
        return {"error": "Temporal queries require Semantica (not initialized)"}

    try:
        from_dt = _parse_iso_timestamp(date_from) if date_from else None
        to_dt = _parse_iso_timestamp(date_to) if date_to else None
        result = await svc.find_contradictions(entity_id, date_from=from_dt, date_to=to_dt)
        logger.info(f"[FIND_CONTRADICTIONS] Found {len(result.get('contradictions', []))} for {entity_id}")
        return _serialize_for_json(result)
    except Exception as e:
        logger.error(f"Error in find_contradictions: {e}")
        return {"error": str(e)}


async def temporal_blast_radius(
    entity_id: str,
    at_time: str,
    max_depth: int = 3,
) -> dict[str, Any]:
    """BFS traversal filtered to relationships active at a specific time.

    Args:
        entity_id: Entity ID or name.
        at_time: ISO 8601 timestamp.
        max_depth: Maximum BFS depth (default 3).
    """
    svc = _get_temporal_query_service()
    if not svc:
        return {"error": "Temporal queries require Semantica (not initialized)"}

    try:
        at_dt = _parse_iso_timestamp(at_time)
        result = await svc.temporal_blast_radius(entity_id, at_time=at_dt, max_depth=max_depth)
        logger.info(f"[TEMPORAL_BLAST_RADIUS] {len(result.get('nodes', []))} nodes in blast radius for {entity_id}")
        return _serialize_for_json(result)
    except Exception as e:
        logger.error(f"Error in temporal_blast_radius: {e}")
        return {"error": str(e)}
