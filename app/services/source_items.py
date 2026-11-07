"""Library captures by source: "recent videos from AI Engineer".

The LINK phase derives each library capture's source (a YouTube channel, a
newsletter, a site) and writes ``(Document)-[:FROM_SOURCE]->(source)``
(ADR-011 §3, the deterministic tier). This module is the read side of that
edge, used by two surfaces:

- ``list_source_items``: one source's captures, newest first by event time
  (ADR-004), for "pull the latest from X" questions that are not a semantic
  query at all.
- ``source_capture_ids`` / ``capture_origins``: the ``source`` filter on
  ``memory_recall`` and the source/title it reports on each capture.

The graph supplies ids, titles and dates only. Governance (lane, decay, review
state, tenant) is re-hydrated from the authoritative capture record, exactly
as recall does (ADR-002, ADR-003 §5): the graph is a projection and its
properties are never trusted for access decisions.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.utils.event_time import to_utc

logger = logging.getLogger(__name__)

MAX_SOURCE_ITEMS = 200
# A source with more captures than this is listed from its newest; recall's
# source filter considers at most this many of them.
MAX_SOURCE_CAPTURES = 5000


class SourceError(ValueError):
    """A request that names no usable source (unknown, or the role is off)."""


def _graph() -> Any:
    from app.services.graph.factory import get_knowledge_graph

    return get_knowledge_graph()


def _neo4j(kg: Any) -> Any | None:
    return getattr(kg, "neo4j", None)


def source_type(kg: Any) -> str | None:
    """The active domain's type for the source role (lanes.yaml), or None."""
    from app.services.capture_link_policy import SOURCE_ROLE, linking_policy
    from app.services.storylines import domain_types

    return linking_policy().role_type(SOURCE_ROLE, domain_types(kg) or None)


def _label(entity_type: str) -> str:
    from app.services.graph.neo4j_schema import entity_type_to_label

    return entity_type_to_label(entity_type)


def _aliases(value: Any) -> list[str]:
    if isinstance(value, list):
        return [a for a in value if isinstance(a, str)]
    if isinstance(value, str) and value:
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return [a for a in parsed if isinstance(a, str)] if isinstance(parsed, list) else [value]
    return []


async def resolve_source(source: str, *, kg: Any | None = None) -> dict[str, Any]:
    """The source entity a caller means by ``source``: an id, a name, or a
    near-spelling of one ("AI Engineer", "ai-engineer", "source-ai-engineer").

    Matching is ``entity_resolver.resolve_against`` over the domain's source
    entities, the same tiers ingest uses. Raises ``SourceError`` when nothing
    matches."""
    from app.services.entity_resolver import resolve_against

    text = (source or "").strip()
    if not text:
        raise SourceError("source must be a non-empty string")
    kg = kg or _graph()
    neo4j = _neo4j(kg)
    if neo4j is None:
        raise SourceError("source lookups need the Neo4j graph backend")
    entity_type = source_type(kg)
    if entity_type is None:
        raise SourceError("this deployment has no source role (lanes.yaml library.linking.roles)")
    rows = await neo4j.execute_read(
        f"MATCH (s:{_label(entity_type)}) WHERE coalesce(s.is_archived, false) = false "
        "RETURN s.id AS id, s.name AS name, s.aliases AS aliases, s.medium AS medium, "
        "s.url AS url",
        {},
    )
    candidates = [
        {"id": r["id"], "name": r.get("name") or "", "aliases": _aliases(r.get("aliases")),
         "medium": r.get("medium"), "url": r.get("url")}
        for r in rows if r.get("id")
    ]
    by_id = {c["id"]: c for c in candidates}
    match = by_id.get(text)
    matched_via = "id"
    if match is None:
        resolved = resolve_against(entity_type, text, candidates)
        match = by_id.get(resolved.id) if resolved.matched_via != "new" else None
        matched_via = resolved.matched_via
    if match is None:
        raise SourceError(
            f"no source matches {text!r}; search_knowledge_graph with "
            f"entity_types=[{entity_type!r}] lists close names"
        )
    return {"id": match["id"], "name": match["name"], "type": entity_type,
            "medium": match.get("medium"), "url": match.get("url"), "matched_via": matched_via}


async def source_capture_ids(source_id: str, *, kg: Any | None = None) -> list[str]:
    """Capture ids linked ``FROM_SOURCE`` to one source, newest first."""
    neo4j = _neo4j(kg or _graph())
    if neo4j is None:
        return []
    rows = await neo4j.execute_read(
        "MATCH (d:Document)-[:FROM_SOURCE]->(s {id: $source_id}) "
        "WHERE d.capture_id IS NOT NULL "
        "RETURN DISTINCT d.capture_id AS capture_id, d.occurred_at AS occurred_at "
        "ORDER BY occurred_at DESC LIMIT $limit",
        {"source_id": source_id, "limit": MAX_SOURCE_CAPTURES},
    )
    return [r["capture_id"] for r in rows if r.get("capture_id")]


async def capture_origins(capture_ids: list[str], *, kg: Any | None = None) -> dict[str, dict]:
    """``{capture_id: {"title", "url", "source": {id, name, medium} | None}}``
    from the graph, for annotating recall results. Missing ids are absent."""
    ids = [i for i in dict.fromkeys(capture_ids) if i]
    if not ids:
        return {}
    neo4j = _neo4j(kg or _graph())
    if neo4j is None:
        return {}
    rows = await neo4j.execute_read(
        "MATCH (d:Document) WHERE d.capture_id IN $ids "
        "OPTIONAL MATCH (d)-[:FROM_SOURCE]->(s) "
        "RETURN d.capture_id AS capture_id, d.title AS title, d.url AS url, "
        "s.id AS source_id, s.name AS source_name, s.medium AS medium",
        {"ids": ids},
    )
    out: dict[str, dict] = {}
    for r in rows:
        cid = r.get("capture_id")
        if not cid or (cid in out and out[cid]["source"] is not None):
            continue
        out[cid] = {
            "title": r.get("title"),
            "url": r.get("url"),
            "source": ({"id": r["source_id"], "name": r.get("source_name"), "medium": r.get("medium")}
                       if r.get("source_id") else None),
        }
    return out


def _resolve_capture(record_id: str):
    from app.services.memory_capture import CaptureStore

    return CaptureStore().get(record_id)


async def list_source_items(
    source: str,
    *,
    published_after: Any = None,
    max_results: int = 20,
    include_stale: bool = False,
    kg: Any | None = None,
    resolve_capture: Callable[[str], Any] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """One source's captures, newest first by ``occurred_at`` (ADR-004).

    Each item is re-hydrated from its capture record: another tenant's, a
    deleted one, or (unless ``include_stale``) a decayed library record that
    no owner link pins is left out, matching recall. Captures with no known
    event time sort last and are excluded by ``published_after``."""
    from app.core.middleware.request_context import current_tenant_id
    from app.services.memory_recall import _GOVERNANCE_FIELDS, _decay_enabled, _is_stale
    from app.services.signal_retrieval import _passes_governance, tenant_matches

    kg = kg or _graph()
    resolved = await resolve_source(source, kg=kg)
    cutoff: datetime | None = None
    if published_after not in (None, ""):
        cutoff = to_utc(published_after)
        if cutoff is None:
            raise SourceError(f"published_after: not a date or time: {published_after!r}")
    limit = max(1, min(int(max_results or 20), MAX_SOURCE_ITEMS))
    if tenant_id is None:
        tenant_id = current_tenant_id.get()
    resolve_capture = resolve_capture or _resolve_capture
    decay = _decay_enabled()

    where = "WHERE d.capture_id IS NOT NULL"
    params: dict[str, Any] = {"source_id": resolved["id"], "limit": MAX_SOURCE_CAPTURES}
    if cutoff is not None:
        where += " AND d.occurred_at >= $cutoff"
        params["cutoff"] = cutoff
    rows = await _neo4j(kg).execute_read(
        "MATCH (d:Document)-[:FROM_SOURCE]->(s {id: $source_id}) " + where + " "
        "RETURN d.capture_id AS capture_id, d.title AS title, d.url AS url, "
        "d.summary AS summary, d.occurred_at AS occurred_at, d.time_source AS time_source "
        "ORDER BY d.occurred_at IS NULL, d.occurred_at DESC LIMIT $limit",
        params,
    )

    def hydrate() -> tuple[list[dict[str, Any]], int, int]:
        # One file read per capture: off the event loop, so a busy source
        # never stalls other requests while its counts are taken.
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        skipped_stale = 0
        total = 0
        for r in rows:
            cid = r["capture_id"]
            if cid in seen:
                continue
            seen.add(cid)
            record = resolve_capture(cid)
            if record is None or not tenant_matches(getattr(record, "tenant_id", None), tenant_id):
                continue  # deleted, or another tenant's: never serve ghosts
            governance = {f: getattr(record, f, None) for f in _GOVERNANCE_FIELDS}
            if not _passes_governance(governance, "evidence", False):
                continue  # rejected, or not evidence-grade: recall would not serve it
            stale = (
                (getattr(record, "lane", None) or "record") == "library"
                and decay
                and not getattr(record, "pinned_by", None)
                and _is_stale(getattr(record, "stale_after", None))
            )
            if stale and not include_stale:
                skipped_stale += 1
                continue
            total += 1
            if len(items) >= limit:
                continue
            items.append({
                "record_id": cid,
                "title": r.get("title"),
                "url": r.get("url") or getattr(record, "source_id", None),
                "published_at": r.get("occurred_at") or getattr(record, "source_date", None),
                "time_source": r.get("time_source") or getattr(record, "time_source", None),
                "summary": getattr(record, "summary", None) or r.get("summary"),
                "lane": getattr(record, "lane", None) or "record",
                "stale": stale,
                "review_status": record.review_status,
                "use_policy": {
                    "can_use_as_instruction": record.can_use_as_instruction,
                    "can_use_as_evidence": record.can_use_as_evidence,
                    "requires_confirmation": record.review_status == "pending",
                },
            })
        return items, total, skipped_stale

    items, total, skipped_stale = await asyncio.to_thread(hydrate)

    out: dict[str, Any] = {
        "source": {k: resolved[k] for k in ("id", "name", "type", "medium", "url", "matched_via")},
        "published_after": cutoff.isoformat() if cutoff else None,
        "items": items,
        "returned": len(items),
        "matching": total,
    }
    if skipped_stale:
        out["stale_excluded"] = skipped_stale
    return out
